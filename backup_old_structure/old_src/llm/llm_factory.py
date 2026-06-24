"""
LLM 工厂模式实现
提供基于配置的分级 LLM 调用能力
"""
from typing import Optional
from ..core import get_logger, get_settings
from .llm_base import LLMBase
from .llm_openai import OpenAICompatibleLLM

logger = get_logger(__name__)


class LLMFactory:
    """LLM 工厂类"""

    @staticmethod
    def get_llm(tier: str) -> LLMBase:
        """根据传入的等级名称，返回实例化好的 LLM 对象"""
        settings = get_settings()
        
        # 获取模型配置和提供方配置
        model_config, provider_config = settings.get_full_model_config(tier)
        
        logger.info(
            f"创建 LLM 实例: tier={tier}, "
            f"provider={model_config.provider}, "
            f"model={model_config.model_name}"
        )
        
        # 根据 provider 选择实现类
        return OpenAICompatibleLLM(
            model_name=model_config.model_name,
            base_url=provider_config.base_url,
            api_key=provider_config.api_key,
            temperature=model_config.temperature or 0.7,
            max_tokens=model_config.max_tokens or 1024,
        )

    @staticmethod
    def list_available_tiers() -> list[str]:
        """列出所有可用的模型等级"""
        settings = get_settings()
        return list(settings.model_tiers.keys())
