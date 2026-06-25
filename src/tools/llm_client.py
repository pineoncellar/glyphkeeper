# -*- coding: utf-8 -*-
"""
@File     :   llm_client.py
@Desc     :   统一 LLM 调用客户端 — 替代旧的 sys.path hack 方式
@Note     :   使用 aiohttp 直接调用 OpenAI 兼容 API，不依赖旧备份代码

职责:
  - 封装对 OpenAI 兼容 API 的异步调用
  - 支持三级模型（fast / standard / smart），从 config.yaml 读取配置
  - 超时重试、错误处理
  - Token 用量追踪（可选，由 config.yaml 中 model_cost_tracking 控制）

使用方式:
    from src.tools.llm_client import call_llm, call_llm_stream

    # 非流式（Node 默认使用）
    response = await call_llm("fast", [
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "你好"},
    ])

    # 流式
    async for chunk in call_llm_stream("standard", messages):
        print(chunk, end="")
"""

from __future__ import annotations

import json
import asyncio
from typing import AsyncGenerator, Optional

import aiohttp

from src.tools import get_logger, get_settings

logger = get_logger(__name__)

# ── 默认参数 ──
DEFAULT_TIMEOUT = 60.0
MAX_RETRIES = 2
RETRY_DELAY_BASE = 1.0  # 退避基础秒数


# ====================================================================
# 内部工具
# ====================================================================


def _build_api_url(base_url: str) -> str:
    """构建完整的 /chat/completions URL

    处理多种 base_url 格式:
      - "https://api.openai.com/v1"           → "https://api.openai.com/v1/chat/completions"
      - "https://api.openai.com/v1/"           → "https://api.openai.com/v1/chat/completions"
      - "https://api.deepseek.com/chat/completions" → 不变
      - "https://api.siliconflow.cn/v1"        → "https://api.siliconflow.cn/v1/chat/completions"
    """
    base_url = base_url.rstrip("/")
    if base_url.endswith("/chat/completions"):
        return base_url
    if base_url.endswith("/v1"):
        return f"{base_url}/chat/completions"
    return f"{base_url}/v1/chat/completions"


def _get_tier_config(tier: str):
    """获取指定层级的完整模型+提供商配置"""
    settings = get_settings()
    return settings.get_full_model_config(tier)


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数（4 字符 ≈ 1 token）"""
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


# ====================================================================
# 公开接口
# ====================================================================


async def call_llm(
    tier: str,
    messages: list[dict],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    max_retries: int = MAX_RETRIES,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> str | None:
    """调用 LLM 并返回完整响应文本。

    参数:
        tier:        模型等级，必须是 config.yaml 中 model_tiers 的 key
                     （如 "fast" / "standard" / "smart"）
        messages:    消息列表 [{"role": "user", "content": "..."}]
        timeout:     单次请求超时秒数
        max_retries: 失败重试次数
        temperature: 覆盖配置中的 temperature（None=使用配置值）
        max_tokens:  覆盖配置中的 max_tokens（None=使用配置值）

    返回:
        响应文本字符串，失败返回 None（不抛异常，调用方自行降级）
    """
    try:
        model_config, provider_config = _get_tier_config(tier)
    except (KeyError, ValueError) as e:
        logger.error(f"call_llm: 配置错误 (tier={tier}): {e}")
        return None

    if not provider_config or not provider_config.api_key:
        logger.error(f"call_llm: 提供商 '{model_config.provider}' 未配置 API Key")
        return None

    api_url = _build_api_url(provider_config.base_url)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {provider_config.api_key}",
    }
    body = {
        "model": model_config.model_name,
        "messages": messages,
        "temperature": temperature if temperature is not None else model_config.temperature,
        "max_tokens": max_tokens if max_tokens is not None else model_config.max_tokens,
        "stream": False,
    }

    last_error: str | None = None

    for attempt in range(max_retries + 1):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    api_url,
                    headers=headers,
                    json=body,
                    timeout=aiohttp.ClientTimeout(total=timeout),
                ) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        logger.warning(
                            f"LLM API {resp.status} (tier={tier}, "
                            f"尝试 {attempt + 1}/{max_retries + 1}): "
                            f"{error_text[:200]}"
                        )
                        if attempt < max_retries:
                            await asyncio.sleep(RETRY_DELAY_BASE * (attempt + 1))
                            continue
                        return None

                    data = await resp.json()
                    choices = data.get("choices", [])
                    if not choices:
                        logger.warning(f"LLM API 返回空 choices (tier={tier})")
                        return None

                    content = choices[0].get("message", {}).get("content", "")

                    # Token 用量追踪
                    if get_settings().project.model_cost_tracking:
                        _track_usage(tier, data, messages)

                    return content

        except asyncio.TimeoutError:
            last_error = f"超时 ({timeout}s)"
            logger.warning(
                f"LLM 超时 (tier={tier}, "
                f"尝试 {attempt + 1}/{max_retries + 1})"
            )
            if attempt < max_retries:
                await asyncio.sleep(RETRY_DELAY_BASE * (attempt + 1))
                continue

        except aiohttp.ClientError as e:
            last_error = f"网络错误: {e}"
            logger.warning(
                f"LLM 网络错误 (tier={tier}, "
                f"尝试 {attempt + 1}/{max_retries + 1}): {e}"
            )
            if attempt < max_retries:
                await asyncio.sleep(RETRY_DELAY_BASE * (attempt + 1))
                continue

        except Exception as e:
            logger.error(f"LLM 未知错误 (tier={tier}): {e}")
            return None

    logger.error(f"LLM 最终失败 (tier={tier}): {last_error}")
    return None


async def call_llm_stream(
    tier: str,
    messages: list[dict],
    *,
    timeout: float = DEFAULT_TIMEOUT,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> AsyncGenerator[str, None]:
    """调用 LLM 并以生成器方式流式返回文本片段。

    用法:
        async for chunk in call_llm_stream("standard", messages):
            print(chunk, end="")
    """
    try:
        model_config, provider_config = _get_tier_config(tier)
    except (KeyError, ValueError) as e:
        logger.error(f"call_llm_stream: 配置错误 (tier={tier}): {e}")
        return

    if not provider_config or not provider_config.api_key:
        logger.error(f"call_llm_stream: 提供商未配置 API Key")
        return

    api_url = _build_api_url(provider_config.base_url)
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {provider_config.api_key}",
    }
    body = {
        "model": model_config.model_name,
        "messages": messages,
        "temperature": temperature if temperature is not None else model_config.temperature,
        "max_tokens": max_tokens if max_tokens is not None else model_config.max_tokens,
        "stream": True,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                api_url,
                headers=headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    logger.warning(
                        f"LLM 流式 API {resp.status} (tier={tier}): "
                        f"{error_text[:200]}"
                    )
                    return

                async for raw_line in resp.content:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or line.startswith(":"):
                        continue
                    if line == "data: [DONE]":
                        break
                    if line.startswith("data: "):
                        try:
                            chunk = json.loads(line[6:])
                            delta = chunk.get("choices", [{}])[0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except json.JSONDecodeError:
                            continue

    except Exception as e:
        logger.warning(f"LLM 流式调用失败 (tier={tier}): {e}")
        return


# ====================================================================
# Token 用量追踪（内部）
# ====================================================================


def _track_usage(tier: str, response_data: dict, messages: list[dict]):
    """记录 Token 用量到日志（model_cost_tracking=True 时调用）"""
    try:
        usage = response_data.get("usage", {})
        if not usage:
            return

        settings = get_settings()
        model_config = settings.get_model_config(tier)

        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)

        input_cost = (model_config.input_cost or 0) * prompt_tokens / 1_000_000
        output_cost = (model_config.output_cost or 0) * completion_tokens / 1_000_000

        logger.info(
            f"Token 用量 [tier={tier}, model={model_config.model_name}]: "
            f"prompt={prompt_tokens}, completion={completion_tokens}, "
            f"total={total_tokens}, cost=¥{input_cost + output_cost:.6f}"
        )
    except Exception as e:
        logger.debug(f"Token 用量追踪失败: {e}")


# ====================================================================
# 便捷包装 — 单条消息快捷调用
# ====================================================================


async def ask_llm(
    tier: str,
    system_prompt: str,
    user_message: str,
    **kwargs,
) -> str | None:
    """便捷版：传入 system prompt 和 user message，返回响应文本。

    示例:
        reply = await ask_llm("fast", "你是一个助手", "你好")
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]
    return await call_llm(tier, messages, **kwargs)
