"""
记忆固化策略
定义何时触发将短期记忆（DialogueRecord）转化为长期记忆（MemoryTrace/LightRAG）的逻辑。
"""
from abc import ABC, abstractmethod
from typing import List
from tokenizers import Tokenizer
from .models import DialogueRecord
from ..core import get_logger

logger = get_logger(__name__)

# 初始化 tokenizer
try:
    _tokenizer = Tokenizer.from_pretrained("gpt2")
except Exception as e:
    logger.warning(f"无法加载预训练 tokenizer: {e}，使用简单字符计数进行降级")
    _tokenizer = None

class ConsolidationStrategy(ABC):
    @abstractmethod
    def should_consolidate(self, buffer: List[DialogueRecord]) -> bool:
        """
        判断是否应该触发固化
        :param buffer: 当前未固化的对话记录列表
        :return: 是否应该触发固化
        """
        pass

class TokenCountStrategy(ConsolidationStrategy):
    """基于 Token 数量的硬触发策略"""
    def __init__(self, max_tokens: int = 2000):
        self.max_tokens = max_tokens

    def _count_tokens(self, text: str) -> int:
        """计算文本的 token 数量"""
        if _tokenizer is None:
            # 降级方案：简单估算 1个汉字/单词 ≈ 1 token
            return len(text)
        
        try:
            # 使用 tokenizer 计算
            encoding = _tokenizer.encode(text)
            return len(encoding.ids)
        except Exception as e:
            logger.warning(f"Token 计数失败: {e}，使用字符数降级")
            return len(text)

    def should_consolidate(self, buffer: List[DialogueRecord]) -> bool:
        if not buffer:
            return False
            
        # 合并所有对话内容，计算总 token 数
        combined_text = "\n".join([f"{record.role}: {record.content}" for record in buffer])
        total_tokens = self._count_tokens(combined_text)
        
        logger.debug(f"Buffer token count: {total_tokens}/{self.max_tokens}")
        return total_tokens >= self.max_tokens

class TopicEndStrategy(ConsolidationStrategy):
    """
    基于话题结束标记的触发策略
    TODO: 需要在对话中明确插入结束标记，如 "<END_TOPIC>"
    """
    def __init__(self, end_marker: str = "<END_TOPIC>"):
        self.end_marker = end_marker

    def should_consolidate(self, buffer: List[DialogueRecord]) -> bool:
        if not buffer:
            return False
            
        # 检查最后一条记录是否包含结束标记
        last_record = buffer[-1]
        return self.end_marker in last_record.content
