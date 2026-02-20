"""
PhysicalComponent
负责处理物理世界的交互。
采用LLM调用函数的方式，循环调用函数至LLM判断不需要调用。
"""
import json
import uuid
from typing import List, Dict, Any, Optional
from dataclasses import asdict

from ..core import get_logger
from ..core.events import ResolutionResult, IntentPhysicalInteractData
from .base import BaseComponent
from ..llm import LLMFactory
from ..memory.bridge import read_model, queue_model_update, commit_model_changes
from ..memory import RAGEngine

logger = get_logger(__name__)

class PhysicalComponent(BaseComponent):
    def initialize(self):
        self.llm = LLMFactory.get_llm("smart")
        self.tools_schema = self._get_tools_schema()

    async def handle_interaction(self, character_name: str, data: IntentPhysicalInteractData) -> ResolutionResult:
        """
        处理物理交互意图
        """
        logger.info(f"PhysicalComponent handling interaction for {character_name}: {data.action_description}")
        
        # 构建初始 Prompt
        system_prompt = f"""
你是一个负责判定物理交互结果的游戏引擎模块。
玩家试图进行以下行动：
【行动描述】{data.action_description}
【原始输入】{data.raw_input}
【指定工具】{data.explicit_tool if data.explicit_tool else "无"}

请作为守密人（KP）和物理引擎，判断该行动的结果。
你可以使用提供的工具来查询当前场景、物品状态、实体状态，或进行物品转移、知识回忆等操作。
请根据查询到的信息，逻辑严密地推演行动结果。

要求：
1. 必须基于当前场景的实际情况（如物品是否存在、是否上锁、距离远近等）进行判定。
2. 如果信息不足，请调用工具查询。
3. 当你得出结论后，请调用 `complete_interaction` 工具来提交最终结果。
4. 结果描述应该是第三人称的叙事文本，描述发生了什么，以及行动是否成功。
"""
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"请开始判定。角色：{character_name}"}
        ]

        # ReAct 循环
        max_iterations = 10
        iteration = 0
        
        while iteration < max_iterations:
            iteration += 1
            
            # 调用 LLM
            response_content = ""
            tool_calls = []
            
            async for chunk in self.llm.chat(messages, tools=self.tools_schema):
                if isinstance(chunk, str):
                    response_content += chunk
                elif isinstance(chunk, dict) and "tool_calls" in chunk:
                    tool_calls = chunk["tool_calls"]
            
            # 将 LLM 回复加入历史
            messages.append({
                "role": "assistant",
                "content": response_content,
                "tool_calls": tool_calls
            })
            
            # 如果没有工具调用
            if not tool_calls:
                logger.warning("LLM did not call any tools. Prompting to complete.")
                messages.append({
                    "role": "user", 
                    "content": "请务必调用 `complete_interaction` 工具来结束本次判定，告知我成功与否及结果描述。"
                })
                continue

            # 处理工具调用
            for tool_call in tool_calls:
                func_name = tool_call["function"]["name"]
                args_str = tool_call["function"]["arguments"]
                call_id = tool_call["id"]
                
                try:
                    args = json.loads(args_str)
                    
                    # 特殊处理 complete_interaction
                    if func_name == "complete_interaction":
                        return ResolutionResult(
                            state=True,
                            success=args.get("success", False),
                            outcome_desc=args.get("outcome", "")
                        )
                    
                    # 调用本地工具方法
                    if hasattr(self, func_name):
                        method = getattr(self, func_name)
                        # 注入 hidden args
                        if "entity_name" in args and not args["entity_name"]:
                             args["entity_name"] = character_name
                        if "viewer_name" in args and not args["viewer_name"]:
                             args["viewer_name"] = character_name
                             
                        result_data = await method(**args)
                        result_str = json.dumps(result_data, ensure_ascii=False)
                    else:
                        result_str = json.dumps({"error": f"Tool {func_name} not found"})
                        
                except Exception as e:
                    logger.error(f"Tool execution failed: {e}")
                    result_str = json.dumps({"error": str(e)})
                
                # 添加工具结果到消息
                messages.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": result_str
                })
        
        # 超出最大轮数
        return ResolutionResult(
            state=False,
            success=False,
            outcome_desc="判定过程超时，无法确定结果。"
        )

    async def get_location_stat(self, entity_name: str) -> Dict[str, Any]:
        """获取指定实体当前场景的完整信息（包括物品、NPC、线索等）"""
        # 找人
        entity = await read_model("Entity", {"name": entity_name})
        if not entity:
            return {"ok": False, "reason": f"Entity not found: {entity_name}"}

        # 找地点
        location_id = entity.get("location_id")
        if not location_id:
            return {
                "ok": True, 
                "location_name": "Unknown", 
                "description": "你在一片虚空之中。", 
                "exits": [],
                "interactables": [],
                "entities": [],
                "environment_tags": []
            }
            
        location = await read_model("Location", {"id": location_id})
        if not location:
            return {"ok": False, "reason": "Location data corruption."}

        # 获取当前地点的所有物品/可交互对象
        interactables = await read_model("Interactable", {"location_id": location_id}, one=False) or []
        interactables_info = []
        for item in interactables:
            interactables_info.append({
                "name": item["name"],
                "key": item.get("key"),
                "state": item.get("state"),
                "tags": item.get("tags") or []
            })
        
        # 获取当前地点的所有其他实体（NPC 或其他角色）
        all_entities = await read_model("Entity", {"location_id": location_id}, one=False) or []
        entities_info = []
        for ent in all_entities:
            # 排除查询者自己
            if ent["name"] != entity_name:
                entities_info.append({
                    "name": ent["name"],
                    "tags": ent.get("tags") or [],
                    "stats": ent.get("stats") or {}
                })

        # 构造完整场景数据
        return {
            "ok": True,
            "entity": entity["name"],
            "location_id": str(location["id"]),
            "location_key": location.get("key"),
            "location_name": location.get("name"),
            "description": location.get("base_desc"),
            "exits": list(location.get("exits", {}).keys()) if location.get("exits") else [],
            "exits_detail": location.get("exits") or {},
            "environment_tags": location.get("tags") or [],
            "interactables": interactables_info,
            "entities": entities_info,
            "system_note": f"场景中有 {len(interactables_info)} 个可交互对象和 {len(entities_info)} 个其他实体。"
        }

    async def inspect_target(self, viewer_name: str, target_name: str) -> Dict[str, Any]:
        """详细检查目标物品，可能触发线索发现。"""
        viewer = await read_model("Entity", {"name": viewer_name})
        if not viewer:
            return {"ok": False, "reason": f"找不到观察者: {viewer_name}"}
        
        # 获取目标
        target = await read_model("Interactable", {"name": target_name})
        if not target:
            return {"ok": False, "reason": f"找不到目标: {target_name}"}
        
        # 检查目标是否在观察者当前地点
        # 注意：这里需要处理 UUID 比较，read_model 返回的 id 可能是字符串
        target_loc_id = str(target.get("location_id")) if target.get("location_id") else None
        viewer_loc_id = str(viewer.get("location_id")) if viewer.get("location_id") else None
        
        if target_loc_id != viewer_loc_id:
            return {"ok": False, "reason": f"{target_name} 不在你当前所在的地点。"}
        
        clue_list = []
        
        # 获取所有关联的线索发现逻辑
        related_clues = await read_model("ClueDiscovery", {"interactable_id": target["id"]}, one=False) or []
        
        for clue_discovery in related_clues:
            # 取线索自身属性
            raw_clue = await read_model("Knowledge", {"id": clue_discovery["knowledge_id"]})
            if raw_clue:
                clue_list.append({
                    "id": raw_clue.get("tags_granted"),
                    "required_check": clue_discovery.get("required_check") or {},
                    "discovery_flavor_text": clue_discovery.get("discovery_flavor_text")
                })
        
        # 返回检查结果
        return {
            "ok": True,
            "target_name": target["name"],
            "state": target.get("state"),
            "tags": target.get("tags") or [],
            "clue_discovered": clue_list,
            "system_note": "这是对目标的详细检查结果，请据此生成后续剧情。若发现了线索，请特别注意触发条件。"
        }

    async def get_entity_status(self, entity_name: str) -> Dict[str, Any]:
        """返回实体的属性值，用于决策判断。"""
        entity = await read_model("Entity", {"name": entity_name})
        if not entity:
            return {"ok": False, "reason": f"找不到实体: {entity_name}"}
        
        # 获取位置信息
        location_name = "未知"
        location_id = entity.get("location_id")
        if location_id:
            location = await read_model("Location", {"id": location_id})
            if location:
                location_name = location.get("name", "未知")
        
        # 构建状态摘要
        stats = entity.get("stats") or {}
        return {
            "ok": True,
            "entity": entity["name"],
            "location": location_name,
            "hp": stats.get("hp", 0),
            "san": stats.get("san", 0),
            "mp": stats.get("mp", 0),
            "tags": entity.get("tags") or [],
            "stats": stats,
            "system_note": "以上是实体的当前状态。"
        }

    async def transfer_item(self, item_name: str, from_container: str, to_container: str) -> Dict[str, Any]:
        """在携带者/地点之间转移物品。"""
        # 查找物品
        item = await read_model("Interactable", {"name": item_name})
        if not item:
            return {"ok": False, "reason": f"物品不存在: {item_name}"}
        
        # 解析来源容器 (可能是实体，也可能是地点)
        from_entity = await read_model("Entity", {"name": from_container})
        from_location = None
        if not from_entity:
            from_location = await read_model("Location", {"name": from_container})
        
        if not from_entity and not from_location:
            return {"ok": False, "reason": f"来源容器不存在: {from_container}"}
        
        # 验证物品当前位置
        # 注意 UUID/String 比较
        item_carrier_id = str(item.get("carrier_id")) if item.get("carrier_id") else None
        item_loc_id = str(item.get("location_id")) if item.get("location_id") else None
        
        if from_entity:
            from_entity_id = str(from_entity["id"])
            if item_carrier_id != from_entity_id:
                 return {"ok": False, "reason": f"{item_name} 不在 {from_container} 的物品栏中。"}
        elif from_location:
            from_location_id = str(from_location["id"])
            if item_loc_id != from_location_id:
                return {"ok": False, "reason": f"{item_name} 不在 {from_container} 中。"}
        
        # 解析目标容器
        to_entity = await read_model("Entity", {"name": to_container})
        to_location = None
        if not to_entity:
            to_location = await read_model("Location", {"name": to_container})
        
        if not to_entity and not to_location:
            return {"ok": False, "reason": f"目标容器不存在: {to_container}"}
        
        # 执行转移
        # 必须先 copy item，避免直接修改缓存（虽然 read_model 返回的是 dict 副本，但为了安全起见）
        item_update = item.copy()
        
        if to_entity:
            item_update["carrier_id"] = to_entity["id"]
            item_update["location_id"] = None
        elif to_location:
            item_update["location_id"] = to_location["id"]
            item_update["carrier_id"] = None
        
        # 提交修改
        queue_model_update("Interactable", item_update)
        await commit_model_changes()
        
        return {
            "ok": True,
            "item": item_name,
            "from": from_container,
            "to": to_container,
            "system_note": f"已将 {item_name} 从 {from_container} 转移到 {to_container}。"
        }

    async def recall_knowledge(self, entity_name: str, query: str) -> Dict[str, Any]:
        """回忆以前解锁的知识、剧情或模组背景。"""
        try:
            # 获取 RAG 引擎单例
            engine = await RAGEngine.get_instance()
            
            # 执行检索
            results = await engine.query(query, mode="hybrid", top_k=3)
            
            if not results:
                return {"ok": True, "results": "没有找到相关记忆。"}
                
            return {
                "ok": True, 
                "results": results, 
                "system_note": "这是从右脑(LightRAG)检索到的相关记忆，请据此生成剧情。"
            }
        except Exception as e:
            logger.error(f"Recall failed: {e}")
            return {"ok": False, "error": str(e)}

    # --- Tool Definitions ---

    def _get_tools_schema(self) -> List[dict]:
        """定义提供给 LLM 的工具列表"""
        return [
            {
                "type": "function",
                "function": {
                    "name": "complete_interaction",
                    "description": "【必须】当判定完成后，调用此工具提交结果。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "success": {"type": "boolean", "description": "行动是否成功"},
                            "outcome": {"type": "string", "description": "结果的详细叙事描述（第三人称）"}
                        },
                        "required": ["success", "outcome"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "get_location_stat",
                    "description": "获取当前房间的描述和可见的物品。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "entity_name": {"type": "string", "description": "观察者的名称。"}
                        },
                        "required": ["entity_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "inspect_target",
                    "description": "检查目标以获取详情和潜在线索。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "viewer_name": {"type": "string"},
                            "target_name": {"type": "string"},
                        },
                        "required": ["viewer_name", "target_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_entity_status",
                    "description": "获取实体的当前属性和物品栏摘要。",
                    "parameters": {
                        "type": "object",
                        "properties": {"entity_name": {"type": "string"}},
                        "required": ["entity_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "transfer_item",
                    "description": "在容器（实体或位置）之间转移物品。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "item_name": {"type": "string"},
                            "from_container": {"type": "string"},
                            "to_container": {"type": "string"},
                        },
                        "required": ["item_name", "from_container", "to_container"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "recall_knowledge",
                    "description": "回忆以前解锁的知识并搜索 LightRAG 范围。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "entity_name": {"type": "string"},
                            "query": {"type": "string"},
                        },
                        "required": ["entity_name", "query"],
                    },
                },
            },
        ]
