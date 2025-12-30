"""
OpenAI 兼容格式的通用 LLM 适配器
支持所有遵循 OpenAI Chat Completions API 格式的模型服务
不依赖 openai 库，使用 aiohttp 独立实现
"""
import json
import time
from typing import List, AsyncGenerator, Optional, Dict
import aiohttp
from ..core import get_logger
from ..utils import track_tokens
from .llm_base import LLMBase, Message

logger = get_logger(__name__)


class OpenAICompatibleLLM(LLMBase):
    """通用 OpenAI 格式适配器，支持流式传输"""
    
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
        """构建完整的api url"""
        base_url = base_url.rstrip("/")
        # 如果 base_url 已经包含完整路径，直接使用
        if base_url.endswith("/chat/completions"):
            return base_url
        # 如果以 /v1 结尾，添加 /chat/completions
        if base_url.endswith("/v1"):
            return f"{base_url}/chat/completions"
        # 否则添加完整路径
        return f"{base_url}/v1/chat/completions"

    def _build_request_body(self, messages: List[Message], stream: bool = True) -> dict:
        """构建请求体"""
        return {
            "model": self.model_name,
            "messages": messages,
            "stream": stream,
            **self.kwargs
        }

    def _estimate_tokens(self, text: str) -> int:
        """粗略估算 token 数（在服务端不返回 usage 时兜底）"""
        # 经验估算：英文约 4 chars/token；中文会偏差，但用于兜底记录
        if not text:
            return 0
        return max(1, (len(text) + 3) // 4)

    def _estimate_prompt_tokens(self, messages: List[Message]) -> int:
        joined = "\n".join(
            f"{m.get('role', '')}: {m.get('content', '')}" for m in messages
        )
        return self._estimate_tokens(joined)

    async def chat(self, messages: List[Message]) -> AsyncGenerator[str, None]:
        """统一对话接口"""
        start_time = time.perf_counter()
        usage_holder: Dict[str, int] = {}
        generated_text_parts: List[str] = []
        try:
            logger.debug(f"发起 LLM 流式调用: model={self.model_name}, messages_count={len(messages)}")
            
            request_body = self._build_request_body(messages, stream=True)
            
            async with aiohttp.ClientSession() as session:
                # 尝试开启 stream usage（部分 OpenAI 兼容服务可能不支持，失败则自动回退）
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
                        # 可能是不支持 stream_options，回退到原始请求体
                        error_text = await response.text()
                        await response.release()
                        logger.debug(f"服务端不支持 stream_options，回退无 usage 流式请求: {error_text}")
                        response = await session.post(
                            self.api_url,
                            headers=self.headers,
                            json=request_body,
                        )

                    if response.status != 200:
                        error_text = await response.text()
                        raise Exception(f"API 返回错误 {response.status}: {error_text}")

                    # 解析 SSE 流并逐块输出
                    async for chunk in self._parse_sse_stream(response, usage_holder):
                        generated_text_parts.append(chunk)
                        yield chunk
                finally:
                    if response is not None:
                        await response.release()
                        
            logger.debug(f"LLM 流式调用完成: model={self.model_name}")
        except Exception as e:
            error_msg = f"LLM 调用失败 [{self.model_name}]: {e}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg)
        finally:
            # 记录 token 与额度：优先使用服务端 usage，其次用本地估算
            elapsed_ms = int((time.perf_counter() - start_time) * 1000)
            prompt_tokens = usage_holder.get("prompt_tokens")
            completion_tokens = usage_holder.get("completion_tokens")

            if prompt_tokens is None:
                prompt_tokens = self._estimate_prompt_tokens(messages)
            if completion_tokens is None:
                completion_tokens = self._estimate_tokens("".join(generated_text_parts))

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

    async def _parse_sse_stream(
        self,
        response: aiohttp.ClientResponse,
        usage_holder: Optional[Dict[str, int]] = None,
    ) -> AsyncGenerator[str, None]:
        """解析SSE流"""
        buffer = ""
        async for chunk in response.content.iter_any():
            # 将字节解码为字符串并添加到缓冲区
            buffer += chunk.decode("utf-8")
            # 按行处理
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                # 跳过空行和注释
                if not line or line.startswith(":"):
                    continue
                # 处理 SSE data 字段
                if line.startswith("data:"):
                    data = line[5:].strip()
                    # 检测流结束
                    if data == "[DONE]":
                        return
                    try:
                        # 解析 JSON 数据
                        parsed = json.loads(data)

                        # 兼容 OpenAI stream usage：末尾可能会给 usage 对象
                        if usage_holder is not None and isinstance(parsed, dict):
                            usage = parsed.get("usage")
                            if isinstance(usage, dict):
                                pt = usage.get("prompt_tokens")
                                ct = usage.get("completion_tokens")
                                if isinstance(pt, int):
                                    usage_holder["prompt_tokens"] = pt
                                if isinstance(ct, int):
                                    usage_holder["completion_tokens"] = ct

                        # 提取 delta 中的内容
                        choices = parsed.get("choices", [])
                        if choices:
                            delta = choices[0].get("delta", {})
                            content = delta.get("content")
                            if content:
                                yield content
                    except json.JSONDecodeError:
                        # 忽略无法解析的数据
                        logger.warning(f"无法解析 SSE 数据: {data}")
