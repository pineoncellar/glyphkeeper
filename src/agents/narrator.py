import json
import asyncio
from typing import AsyncGenerator, List, Dict, Any, Optional

from ..core import get_logger
from ..agents import Archivist
from ..memory import MemoryManager
from ..llm import LLMFactory

logger = get_logger(__name__)

class Narrator:
    def __init__(
        self, 
        memory_manager: MemoryManager
    ):
        
        self.llm = LLMFactory.get_llm("smart")
        self.archivist = Archivist()
        self.memory = memory_manager
        
        # 获取工具定义
        self.tools = self.archivist.get_openai_tools_schema()
        
        # system prompt
        self.system_prompt = """
        你是一个克苏鲁风格跑团游戏的守密人(Keeper/KP)。
        当前玩家控制的角色名称为：【{self.character_name}】。
        当调用工具需要传入 entity_name 时，请务必使用："{self.character_name}"。
        
        你的职责是根据玩家行动和档案员(Archivist)的数据，生成沉浸式的剧情。

        ### 核心原则
        1. **基于事实**：必须基于工具返回的数据（如位置、物品）。如果工具报错，要在剧情中解释原因。
        2. **氛围营造**：风格阴郁、神秘，多感官描写（气味、光影、触感）。
        3. **行动逻辑**：
           - 探索未知/危险区域 -> 调用 `move_entity`。
           - 城市间长途移动 -> 调用 `travel_to_location`。
           - 除非非常确定，否则尽量先用工具查询环境(`get_location_view`)再描述。
        4. **禁止扮演玩家**：你只描述环境和NPC的反应，不要替玩家决定行动。
        """

    async def chat(self, user_input: str) -> AsyncGenerator[str, None]:
        """
        Narrator 主循环。
        Yields: str (流式文本片段，包含思考过程和最终剧情)
        """
        logger.info(f"Narrator 收到输入: {user_input}")

        # 记录用户发言
        await self.memory.add_dialogue("user", user_input)

        # 从右脑提取相关记忆 + 最近对话
        context_str = await self.memory.build_prompt_context(user_input)
        
        # 构造 Prompt
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "system", "content": f"### 当前上下文 (Context)\n{context_str}"},
            {"role": "user", "content": user_input}
        ]

        # 第一轮：推理与意图识别
        # 缓冲全量的回复，以便最后存入记忆
        full_response_content = "" 
        tool_calls = []

        # 调用 LLM
        async for chunk in self.llm.chat(messages, tools=self.tools):
            if isinstance(chunk, str):
                # 这是一个文本片段 (可能是 reasoning_content 或 content)
                # 直接流式输出给用户看
                full_response_content += chunk
                yield chunk
            elif isinstance(chunk, dict) and "tool_calls" in chunk:
                # 这是一个工具调用请求 (流结束时产生)
                tool_calls = chunk["tool_calls"]

        # 分支判断
        if not tool_calls:
            # 没有调用工具，则结束
            # 记录 KP 回复到记忆
            await self.memory.add_dialogue("assistant", full_response_content)
            return

        # 需要调用工具，则进入 ReAct 流程
        # 先把 LLM 第一轮的“思考/意图”加入历史
        messages.append({"role": "assistant", "content": full_response_content, "tool_calls": tool_calls})
        
        # 执行工具
        for tool_call in tool_calls:
            func_name = tool_call["function"]["name"]
            args_str = tool_call["function"]["arguments"]
            call_id = tool_call["id"]
        
            logger.debug(f"\n\n[System] 正在执行: {func_name}...\n\n")

            try:
                args = json.loads(args_str)
                logger.info(f"Executing Tool: {func_name} args={args}")

                # 动态调用 Archivist
                if hasattr(self.archivist, func_name):
                    method = getattr(self.archivist, func_name)
                    # 执行 Python 逻辑
                    result_data = await method(**args)
                    result_str = json.dumps(result_data, ensure_ascii=False)
                else:
                    result_str = json.dumps({"error": f"Tool {func_name} not found"})
            
            except Exception as e:
                logger.error(f"Tool execution failed: {e}")
                result_str = json.dumps({"error": str(e)})

            # 将结果塞回消息列表
            messages.append({
                "role": "tool",
                "tool_call_id": call_id,
                "content": result_str
            })

        # 第二轮：基于结果生成剧情
        # 再次调用 LLM，这次不传tools, 防止死循环
        final_narrative = ""
        async for chunk in self.llm.chat(messages, tools=None):
            if isinstance(chunk, str):
                final_narrative += chunk
                yield chunk
        
        # 记忆录入
        await self.memory.add_dialogue("assistant", final_narrative)