"""
GlyphKeeper 的逻辑核心
以原子化操作的形式向Narrator暴露工具接口。
这里只包含轻量级的原语；更高级的故事逻辑不在此处。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from collections import deque

from ..core import get_logger
from ..memory.models import Entity
from ..memory.repositories import EntityRepository
from ..memory.repositories import LocationRepository
from ..memory.repositories import InteractableRepository
from ..memory.repositories import ClueDiscoveryRepository
from ..memory.repositories import KnowledgeRepository
from ..memory.repositories import SessionRepository
from ..memory import RAGEngine
from ..memory.database import db_manager

logger = get_logger(__name__)


class Archivist:
    """以原子化操作的形式向Narrator暴露工具接口的核心逻辑类"""

    def __init__(self):
        self.db_manager = db_manager

    async def recall_knowledge(self, entity_name: str, query: str) -> Dict[str, Any]:
        """回忆以前解锁的知识、剧情或模组背景。"""
        try:
            # 获取 RAG 引擎单例
            engine = await RAGEngine.get_instance()
            
            # 执行检索 (Hybrid 模式能同时查到关键词和语义)
            # 这里可以根据 entity_name 做进一步的权限过滤，目前先不做
            results = await engine.query(query, mode="hybrid", top_k=3)
            
            if not results:
                return {"ok": True, "results": "你努力回忆，但脑海中一片模糊（没有找到相关记忆）。"}
                
            return {
                "ok": True, 
                "results": results, 
                "system_note": "这是从右脑(LightRAG)检索到的相关记忆，请据此生成剧情。"
            }
        except Exception as e:
            logger.error(f"Recall failed: {e}")
            return {"ok": False, "error": str(e)}

    # --- 感知 ---
    async def get_location_view(self, entity_name: str) -> Dict[str, Any]:
        """获取指定实体当前视野的描述。"""
        async with self.db_manager.session_factory() as session:
            entity_repo = EntityRepository(session)
            location_repo = LocationRepository(session)
            
            # 找人
            entity = await entity_repo.get_by_name(entity_name)
            if not entity:
                return {"ok": False, "reason": f"Entity not found: {entity_name}"}

            # 找地点
            if not entity.location_id:
                return {
                    "ok": True, 
                    "location_name": "Unknown", 
                    "description": "你在一片虚空之中。", 
                    "exits": []
                }
                
            location = await location_repo.get_by_id(entity.location_id)
            if not location:
                return {"ok": False, "reason": "Location data corruption."}

            # 构造视野数据
            # 这里暂时只返回基础描述，后续可扩充 items 和 npcs
            return {
                "ok": True,
                "entity": entity.name,
                "location_name": location.name,
                "description": location.base_desc, # 对应 models.py 的 base_desc
                "exits": list(location.exits.keys()) if location.exits else [], # 将 dict keys 转为 list
                "environment_tags": location.tags
            }

    async def inspect_target(self, viewer_name: str, target_name: str) -> Dict[str, Any]:
        """详细检查目标，可能触发线索发现。"""
        raise NotImplementedError

    async def get_entity_status(self, entity_name: str) -> Dict[str, Any]:
        """返回实体的属性值，用于决策判断。"""
        async with self.db_manager.session_factory() as session:
            entity_repo = EntityRepository(session)
            location_repo = LocationRepository(session)
            
            entity = await entity_repo.get_by_name(entity_name)
            if not entity:
                return {"ok": False, "reason": f"找不到实体: {entity_name}"}
            
            # 获取位置信息
            location_name = "未知"
            if entity.location_id:
                location = await location_repo.get_by_id(entity.location_id)
                if location:
                    location_name = location.name
            
            # 构建状态摘要
            stats = entity.stats or {}
            return {
                "ok": True,
                "entity": entity.name,
                "location": location_name,
                "hp": stats.get("hp", 0),
                "san": stats.get("san", 0),
                "mp": stats.get("mp", 0),
                "tags": entity.tags or [],
                "stats": stats,  # 完整属性
                "system_note": "以上是实体的当前状态。"
            }

    # --- 交互 ---
    async def move_entity(self, entity_name: str, direction: str) -> Dict[str, Any]:
        """尝试移动实体。"""
        async with self.db_manager.session_factory() as session:
            entity_repo = EntityRepository(session)
            location_repo = LocationRepository(session)
            
            # 获取人与当前位置
            entity = await entity_repo.get_by_name(entity_name)
            if not entity or not entity.location_id:
                return {"ok": False, "reason": "无法移动：找不到实体或不在任何地点。"}

            current_loc = await location_repo.get_by_id(entity.location_id)
            
            # 解析出口
            target_ref = None # 可能是 Name，也可能是 Key
            direction_key = None
            
            # 遍历 exits (忽略大小写匹配方向)
            for exit_dir, target in (current_loc.exits or {}).items():
                if exit_dir.lower() == direction.lower():
                    direction_key = exit_dir
                    target_ref = target
                    break
            
            if not target_ref:
                return {"ok": False, "reason": f"方向 '{direction}' 没有路。"}

            # 先尝试 Key 查找 (loc_neighborhood)
            target_loc = await location_repo.get_by_key(target_ref)
            
            # 如果没找到，再尝试当做中文名查找
            if not target_loc:
                target_loc = await location_repo.get_by_name(target_ref)
                
            if not target_loc:
                 return {"ok": False, "reason": f"地图连接错误：找不到目标 '{target_ref}'。"}

            # 执行移动
            entity.location_id = target_loc.id
            await entity_repo.save(entity)

            # 返回新地点的视野
            # 这样 Narrator 可以在一轮对话中完成 "移动+描述"
            new_view = await self.get_location_view(entity_name)
            new_view["system_note"] = f"已成功向 {direction_key} 移动到 {target_loc.name}。"
            return new_view

    async def travel_to_location(self, entity_name: str, target_ref: str) -> Dict[str, Any]:
        """
        [叙事旅行] 计算路径并自动移动。
        :param target_ref: 目标地点的中文名 (如 "图书馆") 或 Key (如 "loc_library")
        """
        async with self.db_manager.session_factory() as session:
            entity_repo = EntityRepository(session)
            location_repo = LocationRepository(session)
            
            # 出发点
            entity = await entity_repo.get_by_name(entity_name)
            if not entity or not entity.location_id:
                return {"ok": False, "reason": "找不到实体或实体不在地图上。"}
            
            start_loc_id = entity.location_id

            # 构建导航图
            raw_locs = await location_repo.get_navigation_graph_data()
            
            graph = {}
            key_to_id = {}   # 辅助索引: Key -> ID
            name_to_id = {}  # 辅助索引: Name -> ID

            for row in raw_locs:
                # { loc_id: { "key": key, "name": name, "exits": {dir: target_key}, "tags": [] } }
                loc_id = row.id
                graph[loc_id] = {
                    "key": row.key,
                    "name": row.name,
                    "exits": row.exits or {},
                    "tags": row.tags or []
                }
                if row.key: key_to_id[row.key] = loc_id
                if row.name: name_to_id[row.name] = loc_id

            # 解析目标 ID
            target_id = None
            if target_ref in key_to_id:
                target_id = key_to_id[target_ref]
            elif target_ref in name_to_id:
                target_id = name_to_id[target_ref]
            
            if not target_id:
                return {"ok": False, "reason": f"地图上不存在名为 '{target_ref}' 的地点。"}

            if start_loc_id == target_id:
                return {"ok": False, "reason": "你已经在这里了。"}

            # BFS 寻路算法，随便加的
            queue = deque([(start_loc_id, [])]) # (current_id, path_of_ids)
            visited = {start_loc_id}
            found_path = None

            while queue:
                curr_id, path = queue.popleft()
                
                if curr_id == target_id:
                    found_path = path
                    break
                
                # 获取当前节点的邻居
                curr_node = graph[curr_id]
                for direction, neighbor_key in curr_node["exits"].items():
                    # 注意：Exits 里存的是 Key，我们需要转成 ID
                    neighbor_id = key_to_id.get(neighbor_key)
                    
                    if neighbor_id and neighbor_id not in visited:
                        visited.add(neighbor_id)
                        # 记录路径：路径中存的是 (target_id, direction_used)
                        new_path = path + [{"id": neighbor_id, "dir": direction}]
                        queue.append((neighbor_id, new_path))

            if found_path is None:
                return {"ok": False, "reason": f"你无法到达 '{target_ref}'，这似乎是个完全封闭或隔离的区域。"}

            # 模拟行走与阻挡检查，看有没有哪个房间被封锁了
            final_loc_id = start_loc_id
            travel_log = [] # 记录路过的地名，给 Narrator 做素材
            interrupted_reason = None

            for step in found_path:
                next_id = step["id"]
                direction = step["dir"]
                next_node = graph[next_id]
                
                # 检查该地点是否有 "locked", "blocked", "police_line" 等等 Tag
                block_tags = {"blocked", "sealed", "locked", "police_line"} 
                # 求交集，如果不为空，说明有阻挡
                if set(next_node["tags"]) & block_tags:
                    interrupted_reason = f"试图前往 {next_node['name']} 时受阻（状态: 被封锁/锁住）。"
                    break # 停止移动，停在上一个节点
                
                # 通过检查，更新当前位置
                final_loc_id = next_id
                travel_log.append(f"{direction} -> {next_node['name']}")

            # 执行最终移动
            if final_loc_id != start_loc_id:
                entity.location_id = final_loc_id
                await entity_repo.save(entity)

            final_loc_node = graph[final_loc_id]
            
            # 获取标准视野
            view = await self.get_location_view(entity_name)
            
            # 注入旅行摘要
            path_desc = " -> ".join(travel_log)
            if interrupted_reason:
                view["system_note"] = f"旅行中断！{interrupted_reason} 当前停留在: {final_loc_node['name']}。"
            else:
                view["system_note"] = f"快速旅行完成。路径: [{path_desc}]。"
                
            return view

    async def transfer_item(self, item_name: str, from_container: str, to_container: str) -> Dict[str, Any]:
        """在携带者/地点之间转移物品。"""
        raise NotImplementedError

    # --- 生理 ---
    async def update_entity_resource(self, entity_name: str, resource: str, delta: int) -> Dict[str, Any]:
        """
        改变通用资源（HP、SAN、MP）。
        当超过阈值时自动更新状态标记。
        """
        async with self.db_manager.session_factory() as session:
            entity_repo = EntityRepository(session)
            
            resource_key = resource.lower()
            if resource_key not in {"hp", "mp", "san"}:
                return {"ok": False, "reason": f"Unsupported resource: {resource}"}

            entity = await entity_repo.get_by_name(entity_name)
            if not entity:
                return {"ok": False, "reason": f"Entity not found: {entity_name}"}

            stats = dict(entity.stats or {})
            before = int(stats.get(resource_key, 0))
            # mp 扣减时判断是否足够
            if resource_key == "mp" and delta < 0 and before < abs(delta):
                return {
                    "ok": False,
                    "reason": "MP 不足，无法扣除。",
                    "entity": entity.name,
                    "resource": resource_key,
                    "before": before,
                    "delta": delta,
                    "after": before,
                    "status_flags": [],
                }
            after = before + delta
            stats[resource_key] = after

            status_flags: List[str] = []
            if resource_key == "hp" and after <= 0:
                stats["status"] = "DEAD"
                status_flags.append("DEAD")
            if resource_key == "san" and after <= 0:
                stats["insanity_state"] = "TEMPORARY_INSANITY"
                status_flags.append("TEMPORARY_INSANITY")

            entity.stats = stats
            try:
                await entity_repo.save(entity)
            except Exception as e:
                logger.error(f"Failed to update entity resource: {e}")
                return {"ok": False, "reason": "Database error"}

            return {
                "ok": True,
                "entity": entity.name,
                "resource": resource_key,
                "before": before,
                "delta": delta,
                "after": after,
                "status_flags": status_flags,
            }

    async def set_entity_state(self, entity_name: str, key: str, value: Any) -> Dict[str, Any]:
        """设置离散状态标记，例如中毒/倒地/隐身。"""
        raise NotImplementedError

    # --- 知识 ---
    async def recall_knowledge(self, entity_name: str, query: str) -> Dict[str, Any]:
        """回忆解锁的知识并进行范围限制的 LightRAG 检索。"""
        raise NotImplementedError

    # --- 工具模式生成 ---
    def get_openai_tools_schema(self) -> List[dict]:
        """返回 OpenAI 可调用工具的列表。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_location_view",
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
                    "name": "move_entity",
                    # 关键点：强调“相邻”、“一步”
                    "description": "【战术移动】移动到当前房间的直接相邻出口（exits）。适用于密室探索、逃跑或具体的每一步行动。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "entity_name": {"type": "string"},
                            "direction": {"type": "string"},
                        },
                        "required": ["entity_name", "direction"],
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "travel_to_location",
                    # 关键点：强调“远距离”、“已知地点”、“跳过过程”
                    "description": "【叙事旅行】前往一个已知的、非相邻的远距离地点。系统会自动处理中间的路程。适用于城市内赶路或跳过无聊的移动过程。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "entity_name": {"type": "string", "description": "移动者的名称"},
                            "target_ref": {"type": "string", "description": "目标地点的名称或Key，如'图书馆'或'loc_library'"},
                        },
                        "required": ["entity_name", "target_ref"],
                    }
                }
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
                    "name": "update_entity_resource",
                    "description": "更新实体的状态 HP/SAN/MP 并处理死亡/疯狂逻辑。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "entity_name": {"type": "string"},
                            "resource": {"type": "string", "enum": ["hp", "san", "mp"]},
                            "delta": {"type": "integer", "description": "改变量（负数表示伤害）。"},
                        },
                        "required": ["entity_name", "resource", "delta"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "set_entity_state",
                    "description": "为实体设置离散状态标记。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "entity_name": {"type": "string"},
                            "key": {"type": "string"},
                            "value": {"description": "任意 JSON 可序列化的值。"},
                        },
                        "required": ["entity_name", "key", "value"],
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
