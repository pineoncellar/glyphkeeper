"""
LLM 模块
封装大语言模型相关功能
"""
from .llm_base import LLMBase, Message
from .llm_factory import LLMFactory
from .llm_lightrag import (
    create_llm_model_func,
    create_embedding_func,
    # default_llm_model_func,
    # get_default_embedding_func,
)

__all__ = [
    # 基础类
    "LLMBase",
    "Message",
    "LLMFactory",
    # LightRAG 集成
    "create_llm_model_func",
    "create_embedding_func",
    # "default_llm_model_func",
    # "get_default_embedding_func",
]
