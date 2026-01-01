"""
OpenAI 兼容格式的通用 LLM 适配器
支持所有遵循 OpenAI Chat Completions API 格式的模型服务
不依赖 openai 库，使用 aiohttp 独立实现
"""
import json
import time
import codecs
from typing import List, AsyncGenerator, Optional, Dict, Union, Any
import aiohttp
from ..core import get_logger
from ..utils import track_tokens
from .llm_base import LLMBase, Message

logger = get_logger(__name__)

class OpenAICompatibleLLM(LLMBase):
    """通用 OpenAI 格式适配器，支持流式传输与 Function Calling"""
    
    supports_streaming = True

    def __init__(
        self, 
        model_name: str, 
        base_url: str, 
        api_key: str, 
        **kwargs
    ):
        super().__init__(model_name, base_url, api_key, **kwargs)
        self.api_url = self._build_api_url(base_url)
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}"
        }

    def _build_api_url(self, base_url: str) -> str:
        base_url = base_url.rstrip("/")
        if base_url.endswith("/chat/completions"):
            return base_url
        if base_url.endswith("/v1"):
            return f"{base_url}/chat/completions"
        return f"{base_url}/v1/chat/completions"

    def _build_request_body(
        self, 
        messages: List[Message], 
        stream: bool = True,
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[Union[str, Dict]] = None
    ) -> dict:
        """构建请求体，增加 tools 支持"""
        body = {
            "model": self.model_name,
            "messages": messages,
            "stream": stream,
            **self.kwargs
        }
        if tools:
            body["tools"] = tools
            # 注意：某些模型如果不传 tool_choice 可能默认为 auto，但显式传更好
            if tool_choice:
                body["tool_choice"] = tool_choice
        return body

    def _estimate_tokens(self, text: str) -> int:
        if not text:
            return 0
        return max(1, (len(text) + 3) // 4)

    def _estimate_prompt_tokens(self, messages: List[Message], tools: Optional[List[Dict]] = None) -> int:
        joined = "\n".join(
            f"{m.get('role', '')}: {m.get('content', '')}" for m in messages
        )
        count = self._estimate_tokens(joined)
        if tools:
            count += self._estimate_tokens(json.dumps(tools))
        return count

    async def chat(
        self, 
        messages: List[Message], 
        tools: Optional[List[Dict]] = None,
        tool_choice: Optional[Union[str, Dict]] = None
    ) -> AsyncGenerator[Union[str, Dict[str, Any]], None]:
        """
        统一对话接口。
        
        Yields:
            str: 普通文本内容片段 (content delta)
            dict: 特殊字典 {"tool_calls": [...]} 或 {"reasoning": "..."}
        """
        start_time = time.perf_counter()
        usage_holder: Dict[str, int] = {}
        generated_text_parts: List[str] = []
        
        # 聚合缓冲
        tool_calls_buffer: Dict[int, Dict[str, Any]] = {}

        try:
            logger.debug(f"发起 LLM 流式调用: model={self.model_name}, tools={'Yes' if tools else 'No'}")
            
            request_body = self._build_request_body(messages, stream=True, tools=tools, tool_choice=tool_choice)
            
            async with aiohttp.ClientSession() as session:
                # 尝试开启 stream usage
                request_body_with_usage = dict(request_body)
                request_body_with_usage["stream_options"] = {"include_usage": True}

                response: Optional[aiohttp.ClientResponse] = None
                try:
                    response = await session.post(
                        self.api_url,
                        headers=self.headers,
                        json=request_body_with_usage,
                    )

                    if response.status == 400:
                        error_text = await response.text()
                        await response.release()
                        # 回退
                        logger.debug(f"回退到无 stream_options 模式: {error_text[:100]}")
                        response = await session.post(
                            self.api_url, headers=self.headers, json=request_body,
                        )

                    if response.status != 200:
                        error_text = await response.text()
                        raise Exception(f"API 返回错误 {response.status}: {error_text}")

                    # 解析 SSE 流
                    async for chunk_type, chunk_data in self._parse_sse_stream(response, usage_holder):
                        
                        if chunk_type == "content":
                            generated_text_parts.append(chunk_data)
                            yield chunk_data
                        
                        elif chunk_type == "reasoning_content":
                            # DeepSeek 特有的思维链内容
                            # 我们把它作为 content yield 出去，或者作为特殊 dict yield
                            # 为了让 Narrator 显示思考过程，我们直接 yield 文本
                            # 或者你可以 yield {"reasoning": chunk_data} 方便前端区分
                            yield chunk_data # 暂时当做普通文本输出，方便调试
                        
                        elif chunk_type == "tool_call_chunk":
                            self._aggregate_tool_call_chunk(tool_calls_buffer, chunk_data)

                finally:
                    if response is not None:
                        await response.release()
                        
            # 流结束，处理 Tool Calls
            if tool_calls_buffer:
                final_tool_calls = [
                    tool_calls_buffer[idx] 
                    for idx in sorted(tool_calls_buffer.keys())
                ]
                logger.debug(f"LLM 最终生成工具调用: {len(final_tool_calls)} 个")
                yield {"tool_calls": final_tool_calls}
                
            logger.debug(f"LLM 流式调用完成: model={self.model_name}")

        except Exception as e:
            error_msg = f"LLM 调用失败 [{self.model_name}]: {e}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg)
            
        finally:
            # 记录 token
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            prompt_tokens = usage_holder.get("prompt_tokens")
            completion_tokens = usage_holder.get("completion_tokens")

            if prompt_tokens is None:
                prompt_tokens = self._estimate_prompt_tokens(messages, tools)
            if completion_tokens is None:
                # 估算包含 tool args 的长度
                tool_args_len = sum(len(str(t)) for t in tool_calls_buffer.values())
                completion_tokens = self._estimate_tokens("".join(generated_text_parts)) + self._estimate_tokens(str(tool_args_len))

            try:
                track_tokens(
                    model=self.model_name,
                    prompt_tokens=int(prompt_tokens or 0),
                    completion_tokens=int(completion_tokens or 0),
                    operation="chat",
                )
                logger.debug(f"已记录模型用量: model={self.model_name}, elapsed_ms={elapsed_ms}")
            except Exception as log_err:
                logger.warning(f"记录模型用量失败: {log_err}")

    def _aggregate_tool_call_chunk(self, buffer: Dict[int, Dict], chunk: Dict):
        """聚合 tool_call 的各个片段"""
        index = chunk.get("index")
        if index is None: return

        if index not in buffer:
            buffer[index] = {
                "index": index,
                "id": "",
                "type": "function",
                "function": {"name": "", "arguments": ""}
            }
        
        item = buffer[index]
        if chunk.get("id"): item["id"] += chunk["id"]
        if chunk.get("type"): item["type"] = chunk["type"]
        
        func_delta = chunk.get("function", {})
        if func_delta.get("name"): item["function"]["name"] += func_delta["name"]
        if func_delta.get("arguments"): item["function"]["arguments"] += func_delta["arguments"]

    async def _parse_sse_stream(
        self,
        response: aiohttp.ClientResponse,
        usage_holder: Optional[Dict[str, int]] = None,
    ) -> AsyncGenerator[tuple[str, Any], None]:
        """
        解析SSE流
        """
        # 使用增量解码器，防止中文字符被 chunk 切断导致乱码
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        buffer = ""
        
        async for chunk in response.content.iter_any():
            buffer += decoder.decode(chunk, final=False)
            
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line or line.startswith(":"):
                    continue
                
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        parsed = json.loads(data)

                        # 1. 提取 Usage
                        if usage_holder is not None and isinstance(parsed, dict):
                            usage = parsed.get("usage")
                            if isinstance(usage, dict):
                                if "prompt_tokens" in usage: usage_holder["prompt_tokens"] = usage["prompt_tokens"]
                                if "completion_tokens" in usage: usage_holder["completion_tokens"] = usage["completion_tokens"]

                        # 2. 提取 Content / ToolCalls / Reasoning
                        choices = parsed.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            
                            # A. 普通内容
                            content = delta.get("content")
                            if content:
                                yield "content", content
                            
                            # B. DeepSeek 思维链 (关键！)
                            reasoning = delta.get("reasoning_content")
                            if reasoning:
                                yield "reasoning_content", reasoning
                            
                            # C. 工具调用
                            tool_calls = delta.get("tool_calls")
                            if tool_calls:
                                for tc in tool_calls:
                                    yield "tool_call_chunk", tc
                                    
                    except json.JSONDecodeError:
                        logger.warning(f"无法解析 SSE 数据: {data[:100]}")