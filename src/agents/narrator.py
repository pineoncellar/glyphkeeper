"""
Narrator Agent - 基于融合架构的叙事引擎
实现模块化、动态化和高上下文感知的 Prompt 生成
"""
import json
import asyncio
from typing import AsyncGenerator, List, Dict, Any, Optional

from ..core import get_logger
from ..agents import Archivist, RuleKeeper
from ..memory import MemoryManager
from ..llm import LLMFactory
from .assembler import PromptAssembler, SceneMode

logger = get_logger(__name__)


class Narrator:
    """
    Narrator 叙事引擎
    
    核心职责：
    1. 接收用户输入，构建动态 Prompt
    2. 通过 ReAct 模式调用 Archivist 工具
    3. 生成沉浸式的 Lovecraftian 叙事
    4. 管理记忆的读写
    """

    def __init__(
        self, 
        memory_manager: MemoryManager,
        player_name: str = "调查员"
    ):
        """
        初始化 Narrator
        
        Args:
            memory_manager: 记忆管理器实例
            player_name: 玩家角色名称，默认为"调查员"
        """
        self.llm = LLMFactory.get_llm("smart")
        self.archivist = Archivist()
        self.rule_keeper = RuleKeeper()
        self.memory = memory_manager
        self.player_name = player_name
        
        # 获取工具定义
        self.tools = self.archivist.get_openai_tools_schema()
        self.tools.append(self.rule_keeper.get_tool_schema())
        
        logger.info(f"Narrator 初始化完成，玩家: {self.player_name}")

    async def _get_game_state(self) -> Dict[str, Any]:
        """
        获取当前游戏状态
        
        Returns:
            游戏状态字典，包含 location, time_slot, environment 等
        """
        try:
            # 从 Archivist 获取玩家当前位置
            location_info = await self.archivist.get_entity_location(self.player_name)
            
            if location_info.get("status") == "success":
                current_location = location_info.get("location", "未知地点")
                
                # 获取地点详细信息（包含环境标签）
                location_detail = await self.archivist.get_location_details(current_location)
                
                environment_tags = location_detail.get("tags", [])
                time_slot = location_detail.get("time_slot", "未知时间")
                
                # 构建环境描述
                environment_desc = self._format_environment(environment_tags)
                
                return {
                    "location": current_location,
                    "time_slot": time_slot,
                    "environment": environment_desc,
                    "environment_tags": environment_tags,
                    "special_conditions": location_detail.get("special_conditions")
                }
            else:
                logger.warning(f"无法获取玩家位置: {location_info}")
                return self._default_game_state()
                
        except Exception as e:
            logger.error(f"获取游戏状态失败: {e}")
            return self._default_game_state()

    def _default_game_state(self) -> Dict[str, Any]:
        """返回默认游戏状态"""
        return {
            "location": "未知地点",
            "time_slot": "未知时间",
            "environment": "未知",
            "environment_tags": [],
            "special_conditions": None
        }

    def _format_environment(self, tags: List[str]) -> str:
        """
        将环境标签格式化为可读描述
        
        Args:
            tags: 环境标签列表
        
        Returns:
            格式化后的环境描述
        """
        if not tags:
            return "平静"
        
        # 简单的标签映射
        tag_map = {
            "dark": "昏暗",
            "rainy": "雨天",
            "foggy": "多雾",
            "cold": "寒冷",
            "hot": "炎热",
            "noisy": "嘈杂",
            "quiet": "寂静",
            "danger": "危险",
            "safe": "安全"
        }
        
        descriptions = [tag_map.get(tag, tag) for tag in tags]
        return ", ".join(descriptions)

    async def _build_rag_context(self, user_input: str) -> Dict[str, str]:
        """
        构建三段式 RAG 上下文
        
        Args:
            user_input: 用户输入
        
        Returns:
            包含 semantic, episodic, keeper_notes 的字典
        """
        try:
            # 调用 MemoryManager 的上下文构建方法
            context_str = await self.memory.build_prompt_context(user_input)
            
            # 解析返回的上下文字符串（假设有特定格式）
            # 这里简化处理，实际可能需要更复杂的解析
            return {
                "semantic": context_str,  # 世界知识
                "episodic": "",           # 情景记忆（如果单独提供）
                "keeper_notes": ""        # KP 笔记（如果单独提供）
            }
        except Exception as e:
            logger.error(f"构建 RAG 上下文失败: {e}")
            return {
                "semantic": "",
                "episodic": "",
                "keeper_notes": ""
            }

    async def _get_recent_history(self, limit: int = 10) -> str:
        """
        获取最近对话历史
        
        Args:
            limit: 获取最近 N 条对话
        
        Returns:
            格式化的对话历史字符串
        """
        try:
            # 从记忆中获取最近对话
            # 这里假设 MemoryManager 有获取对话历史的方法
            # 如果没有，可以简化为空字符串
            return ""  # 待实现
        except Exception as e:
            logger.error(f"获取对话历史失败: {e}")
            return ""

    async def chat(
        self, 
        user_input: str,
        forced_scene_mode: Optional[SceneMode] = None
    ) -> AsyncGenerator[str, None]:
        """
        Narrator 主对话循环
        
        Args:
            user_input: 用户输入
            forced_scene_mode: 强制指定场景模式（可选）
        
        Yields:
            str: 流式文本片段（包含思考过程和最终剧情）
        """
        logger.info(f"Narrator 收到输入: {user_input}")

        # 1. 记录用户发言到记忆
        await self.memory.add_dialogue("user", user_input)

        # 2. 收集数据：游戏状态 + RAG 上下文 + 对话历史
        game_state = await self._get_game_state()
        rag_context = await self._build_rag_context(user_input)
        history_str = await self._get_recent_history()

        # 3. 使用 PromptAssembler 构建系统 Prompt
        system_prompt = PromptAssembler.build(
            player_name=self.player_name,
            game_state=game_state,
            rag_context=rag_context,
            history_str=history_str,
            user_input=user_input,
            tool_results=None,  # 第一轮没有工具结果
            scene_mode=forced_scene_mode
        )

        # 4. 构造消息列表
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input}
        ]

        # 5. 第一轮：推理与意图识别
        full_response_content = ""
        tool_calls = []

        logger.debug("开始第一轮 LLM 调用（推理阶段）...")
        
        async for chunk in self.llm.chat(messages, tools=self.tools):
            if isinstance(chunk, str):
                # 流式输出文本（思考过程 + 可能的初步回复）
                full_response_content += chunk
                yield chunk
            elif isinstance(chunk, dict) and "tool_calls" in chunk:
                # 收到工具调用请求
                tool_calls = chunk["tool_calls"]

        # 6. 判断是否需要调用工具
        if not tool_calls:
            # 没有工具调用，直接结束
            logger.info("无需调用工具，对话结束")
            await self.memory.add_dialogue("assistant", full_response_content)
            return

        # 7. 进入 ReAct 循环：执行工具
        logger.info(f"检测到 {len(tool_calls)} 个工具调用")
        
        # 将第一轮 LLM 的响应加入历史
        messages.append({
            "role": "assistant",
            "content": full_response_content,
            "tool_calls": tool_calls
        })

        # 执行所有工具调用
        tool_results_for_prompt = []
        
        for tool_call in tool_calls:
            func_name = tool_call["function"]["name"]
            args_str = tool_call["function"]["arguments"]
            call_id = tool_call["id"]

            # 输出工具执行提示
            yield f"\n\n[System] 正在执行: {func_name}...\n\n"
            logger.info(f"执行工具: {func_name}")

            try:
                args = json.loads(args_str)
                logger.debug(f"工具参数: {args}")

                # 动态调用 Archivist 方法
                if hasattr(self.archivist, func_name):
                    method = getattr(self.archivist, func_name)
                    result_data = await method(**args)
                    result_str = json.dumps(result_data, ensure_ascii=False)
                    
                    # 保存结果用于下一轮 Prompt 构建
                    tool_results_for_prompt.append(result_data)
                
                # 调用 RuleKeeper 方法
                elif hasattr(self.rule_keeper, func_name):
                    method = getattr(self.rule_keeper, func_name)
                    result_text = await method(**args)
                    result_data = {"rule_judgment": result_text}
                    result_str = json.dumps(result_data, ensure_ascii=False)
                    
                    tool_results_for_prompt.append(result_data)
                else:
                    result_data = {"error": f"Tool {func_name} not found"}
                    result_str = json.dumps(result_data)

            except Exception as e:
                logger.error(f"工具执行失败: {e}", exc_info=True)
                result_data = {"error": str(e)}
                result_str = json.dumps(result_data)

            # 将结果加入消息历史
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": result_str
            })

        # 8. 第二轮：基于工具结果重新构建 Prompt + 生成最终叙事
        logger.debug("开始第二轮 LLM 调用（叙事生成）...")
        
        # 重新构建 Prompt，这次包含工具结果
        system_prompt_with_results = PromptAssembler.build(
            player_name=self.player_name,
            game_state=game_state,
            rag_context=rag_context,
            history_str=history_str,
            user_input=user_input,
            tool_results=tool_results_for_prompt,
            scene_mode=forced_scene_mode
        )

        # 更新系统消息
        messages[0] = {"role": "system", "content": system_prompt_with_results}

        # 第二轮调用（不再传 tools，避免无限循环）
        final_narrative = ""
        async for chunk in self.llm.chat(messages, tools=None):
            if isinstance(chunk, str):
                final_narrative += chunk
                yield chunk

        # 9. 记录最终叙事到记忆
        await self.memory.add_dialogue("assistant", final_narrative)
        
        logger.info("Narrator 回合结束")

    async def start_session(self, scenario_name: str = "未命名冒险") -> str:
        """
        开始一个新的游戏会话
        
        Args:
            scenario_name: 剧本名称
        
        Returns:
            开场白
        """
        logger.info(f"开始新会话: {scenario_name}")
        
        opening = f"""
欢迎来到《克苏鲁的呼唤》。

你扮演的是 **{self.player_name}**，一位调查员。
在这个充满未知与恐怖的世界中，你将面对那些不应被凝视之物。

记住：
- 保持理智
- 记录线索
- 相信直觉

剧本：{scenario_name}

你的冒险即将开始...
"""
        
        await self.memory.add_dialogue("system", opening)
        return opening

    def get_player_name(self) -> str:
        """获取当前玩家名称"""
        return self.player_name

    def set_player_name(self, new_name: str):
        """
        更新玩家名称
        
        Args:
            new_name: 新的玩家名称
        """
        logger.info(f"玩家名称更新: {self.player_name} -> {new_name}")
        self.player_name = new_name
