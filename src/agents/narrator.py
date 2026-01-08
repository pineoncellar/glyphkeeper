"""
Narrator Agent - 基于融合架构的叙事引擎
核心职责：
1. 接收用户输入，构建动态 Prompt
2. 通过 ReAct 模式调用 Archivist 工具和 RuleKeeper 工具
3. 生成沉浸式的克苏鲁风格叙事
4. 管理记忆的读写

TODO: 开幕描写、结团判断与描写
"""
import json
import asyncio
import uuid
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, List, Dict, Any, Optional

from ..core import get_logger, get_settings
from .archivist import Archivist
from .rule_keeper import RuleKeeper
from ..memory import MemoryManager
from ..llm import LLMFactory
from .tools import NarratorInput, PromptAssembler, SceneMode

logger = get_logger(__name__)


class Narrator:
    """Narrator 叙事引擎"""

    def __init__(
        self, 
        memory_manager: MemoryManager
    ):
        self.default_player_name = "调查员"
        self.llm = LLMFactory.get_llm("smart")
        self.archivist = Archivist()
        self.rule_keeper = RuleKeeper()
        self.memory = memory_manager
        settings = get_settings()
        self.trace_log_path: Path = settings.get_absolute_path("logs/llm_traces.jsonl")
        
        # 获取工具定义
        self.tools = self.archivist.get_openai_tools_schema()
        self.tools.append(self.rule_keeper.get_tool_schema())
        
        logger.info(f"Narrator 初始化完成")

    def _log_llm_trace(self, trace_id: str, stage: str, payload: Dict[str, Any]):
        """将调用上下文写入日志记录"""
        record = {
            "trace_id": trace_id,
            "stage": stage,
            "timestamp": datetime.now().isoformat(),
            **payload,
        }

        try:
            self.trace_log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.trace_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error(f"写入 LLM trace 失败: {e}")

    async def _get_game_stat(self, session_id: str, player_name: str) -> Dict[str, Any]:
        """获取当前游戏状态"""
        try:
            # 获取完整全局状态信息
            game_session_stat = await self.archivist.get_game_session_stat(session_id)
            
            if game_session_stat.get("ok"):
                time_slot = game_session_stat.get("time_slot", "未知时间")
                active_global_tags = game_session_stat.get("active_global_tags", [])
                beat_counter = game_session_stat.get("beat_counter", 0) if game_session_stat.get("beat_counter", 0) >=5  else 0
            else:
                logger.error(f"无法获取全局状态信息: {game_session_stat}")
                return self._default_game_state()
            
            # 获取调查员名称列表
            investigator_list_stat = await self.archivist.list_investigators(session_id)
            if investigator_list_stat.get("ok"):
                global_investigator_list = investigator_list_stat.get("investigators", [])
            else:
                logger.error(f"无法获取调查员列表: {investigator_list_stat}")
                global_investigator_list = []
            
            # 获取地点状态
            location_stat = await self.archivist.get_location_stat(player_name)
            if location_stat.get("ok"):
                location_stat.pop("ok")
            else:
                logger.error(f"无法获取地点状态信息: {location_stat}")
                location_stat = {
                    "location_name": "Unknown", 
                    "description": "你在一片虚空之中。", 
                    "exits": [],
                    "interactables": [],
                    "entities": [],
                    "environment_tags": []
                }
            
            return {
                "time_slot": time_slot,
                "active_global_tags": active_global_tags,
                "beat_counter": beat_counter,
                "global_investigator_list": global_investigator_list,
                "location_stat": location_stat,
                "player_condition": "健康"
            }
                
        except Exception as e:
            logger.error(f"获取游戏状态失败: {e}")
            return self._default_game_state()

    def _default_game_state(self) -> Dict[str, Any]:
        """返回默认游戏状态"""
        return {
                "time_slot": "未知时间",
                "active_global_tags": [],
                "beat_counter": 0,
                "location_stat": {},
                "player_condition": "Unknown"
            }

    async def _build_rag_context(self, user_input: str) -> Dict[str, str]:
        """构建三段式 RAG 上下文"""
        try:
            # 调用 MemoryManager 的上下文构建方法
            # manager.py 中的 build_prompt_context 现在直接返回包含三段内容的字典
            rag_context = await self.memory.build_prompt_context(user_input)
            
            # 确保返回字典包含所需的键
            return {
                "semantic": rag_context.get("semantic", ""),
                "episodic": rag_context.get("episodic", ""),
                "keeper_notes": rag_context.get("keeper_notes", "")
            }
        except Exception as e:
            logger.error(f"构建 RAG 上下文失败: {e}")
            return {
                "semantic": "",
                "episodic": "",
                "keeper_notes": ""
            }

    async def chat(
        self, 
        user_input: NarratorInput,
        forced_scene_mode: Optional[SceneMode] = None
    ) -> AsyncGenerator[str, None]:
        """
        Narrator 主对话函数，基于 ReAct 模式生成叙事
        目前仅支持单人模式
        """
        # 兼容性处理：如果传入的是字符串，尝试包装 (仅用于简单的单人测试兼容)
        if isinstance(user_input, str):
            user_input = NarratorInput(
                session_id=str(uuid.uuid4()),
                character_name=self.default_player_name,
                content=user_input
            )

        active_char = user_input.character_name
        content_text = user_input.content
        
        logger.debug(f"Narrator 收到输入: [{active_char}] {content_text}")

        trace_id = f"narrator-{uuid.uuid4().hex}"

        # 记录用户发言到记忆
        # 加上角色名前缀以便在历史中区分
        await self.memory.add_dialogue("user", f"[{active_char}] {content_text}")

        # 收集数据：游戏状态 + RAG 上下文 + 对话历史
        game_state = await self._get_game_stat(user_input.session_id, active_char)
        rag_context = await self._build_rag_context(content_text)
        history_list = await self.memory.get_recent_context()
        
        # 排除最后一条（即本次的输入），避免 Prompt 中重复
        past_history = history_list[:-1] if history_list else []
        
        # 格式化对话历史
        history_str = "\n".join([f"[{r.role}]: {r.content}" for r in past_history])

        # 构建第一轮prompt
        system_prompt = PromptAssembler.build(
            player_name=active_char,
            game_state=game_state,
            rag_context=rag_context,
            history_str=history_str,
            user_input=content_text,
            tool_results=None,  # 第一轮没有工具结果
            scene_mode=forced_scene_mode
        )

        # 构造消息列表
        user_message_content = f"[Actor: {active_char}] {content_text}"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message_content}
        ]

        self._log_llm_trace(
            trace_id,
            "llm_request_primary",
            {
                "actor": active_char,
                "scene_mode": forced_scene_mode.name if forced_scene_mode else None,
                "game_state": game_state,
                "rag_context": rag_context,
                "history": history_str,
                "messages": messages,
                "tools": self.tools,
            },
        )

        # 第一轮：推理与意图识别
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

        self._log_llm_trace(
            trace_id,
            "llm_response_primary",
            {
                "raw_content": full_response_content,
                "tool_calls": tool_calls,
            },
        )

        # 判断是否需要调用工具
        if not tool_calls:
            # 没有工具调用，直接结束
            logger.debug("无需调用工具，对话结束")
            await self.memory.add_dialogue("assistant", full_response_content)
            self._log_llm_trace(
                trace_id,
                "llm_session_complete",
                {"final_narrative": full_response_content},
            )
            return

        # 进入 ReAct 循环，支持多轮工具调用
        # 硬性轮次限制
        max_iterations = 5  # 最大工具调用轮数，防止无限循环
        iteration = 0
        all_tool_results = []  # 累积所有工具结果
        
        # 重复调用检测
        tool_call_history = []  # 记录每次工具调用的签名 (func_name, args)
        
        while tool_calls and iteration < max_iterations:
            iteration += 1
            logger.debug(f"第 {iteration} 轮工具调用，检测到 {len(tool_calls)} 个工具")
            
            # 将 LLM 的响应加入历史
            messages.append({
                "role": "assistant",
                "content": full_response_content,
                "tool_calls": tool_calls
            })

            # 执行所有工具调用
            current_round_results = []
            detected_loop = False  # 标记是否检测到循环
            
            for tool_call in tool_calls:
                func_name = tool_call["function"]["name"]
                args_str = tool_call["function"]["arguments"]
                call_id = tool_call["id"]

                logger.debug(f"执行工具: {func_name}")

                try:
                    args = json.loads(args_str)
                    
                    # 创建工具调用签名（函数名 + 参数的标准化表示）
                    call_signature = (func_name, json.dumps(args, sort_keys=True, ensure_ascii=False))
                    
                    # 检查是否与最近的调用重复
                    if call_signature in tool_call_history[-2:]:  # 检查最近2次调用
                        logger.warning(f"检测到重复工具调用: {func_name}({args})")
                        detected_loop = True
                        result_data = {
                            "error": "重复调用检测",
                            "message": "系统检测到您正在尝试重复相同的操作，这可能不会带来新的结果。请换一个思路。"
                        }
                        result_str = json.dumps(result_data, ensure_ascii=False)
                        
                        # 将错误结果加入消息历史
                        messages.append({
                            "role": "tool",
                            "tool_call_id": call_id,
                            "content": result_str
                        })
                        current_round_results.append(result_data)
                        
                        self._log_llm_trace(
                            trace_id,
                            f"loop_detected_round_{iteration}",
                            {"func_name": func_name, "args": args, "signature": call_signature},
                        )
                        
                        continue  # 跳过实际执行
                    
                    # 记录本次调用
                    tool_call_history.append(call_signature)
                    logger.debug(f"工具参数: {args}")

                    # 动态调用 Archivist 方法
                    if hasattr(self.archivist, func_name):
                        method = getattr(self.archivist, func_name)
                        result_data = await method(**args)
                        result_str = json.dumps(result_data, ensure_ascii=False)
                        current_round_results.append(result_data)
                    
                    # 调用 RuleKeeper 方法
                    elif hasattr(self.rule_keeper, func_name):
                        method = getattr(self.rule_keeper, func_name)
                        result_text = await method(**args)
                        result_data = {"rule_judgment": result_text}
                        result_str = json.dumps(result_data, ensure_ascii=False)
                        current_round_results.append(result_data)
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
            
            # 累积本轮结果
            all_tool_results.extend(current_round_results)
            
            self._log_llm_trace(
                trace_id,
                f"tool_results_round_{iteration}",
                {"tool_results": current_round_results, "loop_detected": detected_loop},
            )

            # 如果检测到循环，提前终止并强制生成叙事
            if detected_loop:
                logger.warning(f"检测到工具调用循环，在第 {iteration} 轮提前终止")
                full_response_content += "\n\n[系统检测到重复操作，已停止进一步查询]"
                tool_calls = []  # 清空，强制退出循环
                break

            # 继续调用 LLM，检查是否还需要更多工具
            logger.debug(f"第 {iteration} 轮工具执行完成，继续 LLM 调用...")
            
            full_response_content = ""
            tool_calls = []
            
            # 接近上限时的警告 prompt
            approaching_limit = (iteration >= max_iterations - 1)
            
            # 更新 system prompt 以包含最新的工具结果
            system_prompt_with_results = PromptAssembler.build(
                player_name=active_char,
                game_state=game_state,
                rag_context=rag_context,
                history_str=history_str,
                user_input=content_text,
                tool_results=all_tool_results,
                scene_mode=forced_scene_mode
            )
            
            # 如果接近上限，在 system prompt 末尾添加强制结案指令
            if approaching_limit:
                system_prompt_with_results += "\n\n**系统强制要求**：你已接近最大推理轮次，必须立即基于当前已知信息生成最终叙事回复，不得再调用任何工具。请直接输出 <narrative> 标签包裹的叙事内容。"
                logger.warning(f"已达到最大轮次 ({max_iterations})，强制要求 LLM 生成叙事")
            
            messages[0] = {"role": "system", "content": system_prompt_with_results}
            
            self._log_llm_trace(
                trace_id,
                f"llm_request_round_{iteration + 1}",
                {
                    "messages": messages,
                    "tools": self.tools if not approaching_limit else None,
                    "accumulated_tool_results": all_tool_results,
                    "approaching_limit": approaching_limit,
                },
            )
            
            # 如果接近上限，禁用工具调用
            tools_param = None if approaching_limit else self.tools
            
            async for chunk in self.llm.chat(messages, tools=tools_param):
                if isinstance(chunk, str):
                    full_response_content += chunk
                    # 在推理阶段也可以输出思考过程（可选）
                    # yield chunk
                elif isinstance(chunk, dict) and "tool_calls" in chunk:
                    tool_calls = chunk["tool_calls"]
            
            self._log_llm_trace(
                trace_id,
                f"llm_response_round_{iteration + 1}",
                {
                    "raw_content": full_response_content,
                    "tool_calls": tool_calls,
                },
            )
            
            # 如果没有新的工具调用，跳出循环
            if not tool_calls:
                logger.debug("无更多工具调用需求，准备生成最终叙事")
                break
        
        # 检查是否达到最大迭代次数（理论上不应该触发，因为达到上限会提前阻止）
        if iteration >= max_iterations:
            logger.warning(f"达到最大工具调用轮数 ({max_iterations})，强制结束")
            if tool_calls:
                logger.error(f"LLM 在强制结案指令下仍尝试调用工具: {[tc['function']['name'] for tc in tool_calls]}")
            full_response_content += "\n\n[系统提示：调查过程过于复杂，暂时告一段落]"

        # 最终叙事生成：不再允许工具调用
        logger.debug("开始最终叙事生成...")
        
        # 如果最后一轮没有 tool_calls，说明 LLM 已经准备好输出叙事了
        # 此时 full_response_content 就是最终内容
        # 但为了保险起见，再调用一次，明确禁用 tools
        final_narrative = ""
        buffer = ""
        in_narrative = False
        has_output_started = False
        
        # 如果上一轮已经输出了 narrative，直接使用
        if "<narrative>" in full_response_content:
            final_narrative = full_response_content
        else:
            # 否则再调用一次 LLM，明确生成叙事
            self._log_llm_trace(
                trace_id,
                "llm_request_final_narrative",
                {
                    "messages": messages + [{"role": "assistant", "content": full_response_content}],
                    "tools": None,
                },
            )
            
            async for chunk in self.llm.chat(
                messages + [{"role": "assistant", "content": full_response_content}], 
                tools=None
            ):
                if isinstance(chunk, str):
                    final_narrative += chunk
        
        # 流式输出叙事内容
        buffer = final_narrative
        if "<narrative>" in buffer and "</narrative>" in buffer:
            start = buffer.find("<narrative>") + len("<narrative>")
            end = buffer.find("</narrative>")
            clean_narrative = buffer[start:end].strip()
            
            # 流式输出
            for char in clean_narrative:
                yield char
                has_output_started = True
        elif final_narrative.strip():
            # 如果没有标签，直接输出全部内容
            yield final_narrative
            clean_narrative = final_narrative.strip()
            has_output_started = True
        else:
            clean_narrative = full_response_content.strip()
            if clean_narrative:
                yield clean_narrative
                has_output_started = True

        # 记录最终叙事到记忆
        if clean_narrative:
            await self.memory.add_dialogue("assistant", clean_narrative)
        
        self._log_llm_trace(
            trace_id,
            "llm_session_complete",
            {
                "final_narrative": clean_narrative,
                "total_iterations": iteration,
                "all_tool_results": all_tool_results,
            },
        )
        
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
