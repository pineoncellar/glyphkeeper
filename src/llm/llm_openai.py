"""
OpenAI 兼容格式的通用 LLM 适配器
支持所有遵循 OpenAI Chat Completions API 格式的模型服务
不依赖 openai 库，使用 aiohttp 独立实现
"""
import json
from typing import List, AsyncGenerator
import aiohttp
from ..core import get_logger
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

    async def chat(self, messages: List[Message]) -> AsyncGenerator[str, None]:
        """统一对话接口"""
        try:
            logger.debug(f"发起 LLM 流式调用: model={self.model_name}, messages_count={len(messages)}")
            
            request_body = self._build_request_body(messages, stream=True)
            
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.api_url,
                    headers=self.headers,
                    json=request_body
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        raise Exception(f"API 返回错误 {response.status}: {error_text}")
                    
                    # 解析 SSE 流并逐块输出
                    async for chunk in self._parse_sse_stream(response):
                        yield chunk
                        
            logger.debug(f"LLM 流式调用完成: model={self.model_name}")
        except Exception as e:
            error_msg = f"LLM 调用失败 [{self.model_name}]: {e}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg)

    async def _parse_sse_stream(self, response: aiohttp.ClientResponse) -> AsyncGenerator[str, None]:
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
