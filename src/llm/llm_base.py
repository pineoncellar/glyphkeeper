"""
大语言模型抽象基类
定义了所有 LLM 实现必须遵守的接口契约
"""
from abc import ABC, abstractmethod
from typing import List, Dict, AsyncGenerator, Optional
from ..core import get_logger

logger = get_logger(__name__)

# 通用消息格式类型
Message = Dict[str, str]  # {"role": "user", "content": "早上好"}


class LLMBase(ABC):
    """LLM 抽象基类"""
    
    # 标记该实现是否支持流式传输
    supports_streaming: bool = False
    
    def __init__(
        self, 
        model_name: str, 
        base_url: Optional[str] = None, 
        api_key: Optional[str] = None, 
        **kwargs
    ):
        self.model_name = model_name
        self.base_url = base_url
        self.api_key = api_key
        self.kwargs = kwargs

    @abstractmethod
    def chat(self, messages: List[Message]) -> AsyncGenerator[str, None]:
        """统一对话接口"""
        pass

