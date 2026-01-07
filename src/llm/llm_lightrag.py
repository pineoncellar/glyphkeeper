"""
LightRAG 模型层封装
将 OpenAI/Ollama/HuggingFace 的 API 调用逻辑与业务逻辑分离
适配 LightRAG 的 llm_model_func 和 embedding_func 接口
"""
import time
import numpy as np
from typing import Optional, List
from lightrag.llm.openai import openai_complete_if_cache, openai_embed
from lightrag.utils import EmbeddingFunc

from ..core import get_logger, get_settings
from ..utils import track_tokens
from .llm_base import Message

logger = get_logger(__name__)


def create_llm_model_func(tier: str = "standard"):
    """创建适配 LightRAG 的异步 LLM 函数"""
    settings = get_settings()
    model_config, provider_config = settings.get_full_model_config(tier)
    
    async def llm_model_func(
        prompt: str,
        system_prompt: Optional[str] = None,
        history_messages: Optional[List[Message]] = None,
        **kwargs
    ) -> str:
        """LightRAG 兼容的 LLM 调用函数"""
        start_time = time.perf_counter()
        logger.debug(f"LLM 调用: model={model_config.model_name}, tier={tier}")
        payload_history = history_messages or []
        
        try:
            result = await openai_complete_if_cache(
                model=model_config.model_name,
                prompt=prompt,
                system_prompt=system_prompt,
                history_messages=payload_history,
                api_key=provider_config.api_key,
                base_url=provider_config.base_url,
                **kwargs
            )
            
            # 估算 token 用量 (LightRAG 不返回 usage 信息)
            prompt_text = f"{system_prompt or ''}\n{prompt}\n" + "\n".join(
                f"{m.get('role', '')}: {m.get('content', '')}" for m in payload_history
            )
            prompt_tokens = max(1, (len(prompt_text) + 3) // 4)
            completion_tokens = max(1, (len(result) + 3) // 4)
            
            # 记录用量
            try:
                track_tokens(
                    model=model_config.model_name,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    operation="lightrag_llm",
                )
                elapsed_ms = int((time.perf_counter() - start_time) * 1000)
                logger.debug(f"已记录 LightRAG LLM 用量: model={model_config.model_name}, elapsed_ms={elapsed_ms}")
            except Exception as log_err:
                logger.warning(f"记录模型用量失败: {log_err}")
            
            return result
        except Exception as e:
            error_msg = f"LLM 调用失败 [{model_config.model_name}]: {e}"
            logger.error(error_msg, exc_info=True)
            raise RuntimeError(error_msg)
    
    return llm_model_func


def create_embedding_func(
    model_name: str = "text-embedding-3-small",
    embedding_dim: int = 1024,
    max_token_size: int = 8192,
    provider: str = "openai"
    ):
    """适配 LightRAG 的 embedding 模型调用函数"""
    settings = get_settings()
    provider_config = settings.get_provider_config(provider)
    
    if provider_config is None:
        raise ValueError(
            f"未找到提供方 '{provider}' 的配置。"
            f"请检查 providers.ini 文件是否包含 [{provider.upper()}] 配置节"
        )
    
        
    # openai_embed 已经是一个 EmbeddingFunc 对象 (embedding_dim=1536)，直接调用会导致双重包装和维度验证冲突
    # 使用 openai_embed.func 获取原始函数，避免内部维度验证
    raw_openai_embed = openai_embed.func
    
    async def embedding_func(texts: List[str]) -> np.ndarray:
        """LightRAG 兼容的 Embedding 函数"""
        start_time = time.perf_counter()
        logger.debug(f"texts:{texts}")
        logger.debug(f"Embedding 调用: model={model_name}, texts_count={len(texts)}")
        
        try:
            result = await raw_openai_embed(
                texts=texts,
                model=model_name,
                api_key=provider_config.api_key,
                base_url=provider_config.base_url,
            )
            
            # 估算 token 用量
            total_text = " ".join(texts)
            prompt_tokens = max(1, (len(total_text) + 3) // 4)
            
            # 记录用量 (embedding 没有 completion tokens)
            try:
                track_tokens(
                    model=model_name,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=0,
                    operation="lightrag_embedding",
                )
                elapsed_ms = int((time.perf_counter() - start_time) * 1000)
                logger.debug(f"已记录 LightRAG Embedding 用量: model={model_name}, elapsed_ms={elapsed_ms}")
            except Exception as log_err:
                logger.warning(f"记录 Embedding 用量失败: {log_err}")
            
            return result
        except Exception as e:
            logger.error(f"Embedding 调用失败: {e}")
            raise
    
    # 返回包装好的 EmbeddingFunc 对象，使用正确的维度配置
    return EmbeddingFunc(
        embedding_dim=embedding_dim,
        max_token_size=max_token_size,
        func=embedding_func
    )


# ============================================
# 预配置的默认函数 (便捷使用)
# ============================================

# async def default_llm_model_func(
#     prompt: str,
#     system_prompt: Optional[str] = None,
#     history_messages: Optional[List[Message]] = None,
#     **kwargs
# ) -> str:
#     """默认 LLM 函数 (使用 standard 层级)"""
#     settings = get_settings()
#     model_config, provider_config = settings.get_full_model_config("standard")
#     payload_history = history_messages or []
    
#     return await openai_complete_if_cache(
#         model=model_config.model_name,
#         prompt=prompt,
#         system_prompt=system_prompt,
#         history_messages=payload_history,
#         api_key=provider_config.api_key,
#         base_url=provider_config.base_url,
#         temperature=model_config.temperature,
#         max_tokens=model_config.max_tokens,
#         **kwargs
#     )


# def get_default_embedding_func() -> EmbeddingFunc:
#     """
#     获取默认 Embedding 函数 (从配置读取)
#     """
#     settings = get_settings()
#     vector_config = settings.vector_store
    
#     return create_embedding_func(
#         model_name=vector_config.embedding_model_name,
#         embedding_dim=1536,  # text-embedding-3-small 默认维度
#         max_token_size=8192,
#         provider=vector_config.provider
#     )
