"""
情景记忆模块
负责动态历史记录与情境检索，记录运行时发生的事件，并根据当前情境检索相关记忆。
对应认知架构中的“短期/长期情景记忆”及“工作记忆检索”
"""
from typing import List, Dict, Any
import datetime
from .RAG_engine import RAGEngine
from ..core.logger import get_logger

logger = get_logger(__name__)

class EpisodicMemory:
    """情景记忆管理器"""
    async def get_rag_engine(self) -> RAGEngine:
        return await RAGEngine.get_instance()

    async def insert_game_event(self, event_text: str, related_tags: List[str]) -> bool:
        """记录游戏事件"""
        engine = await self.get_rag_engine()
        
        meta = {
            "tags": related_tags,
            "timestamp": datetime.datetime.now().isoformat(),
            "type": "dynamic_event"
        }
        
        # [Metadata Injection] 注入 Tags 到文本
        # 格式: [TAG: tag1] [TAG: tag2] Content...
        injected_text = event_text
        if related_tags:
            tags_str = " ".join([f"[TAG: {tag}]" for tag in related_tags])
            injected_text = f"{tags_str}\n{event_text}"
        
        try:
            return await engine.insert(injected_text, metadata=meta)
        except Exception as e:
            logger.error(f"Error inserting game event: {e}")
            return False

    async def retrieve_context(self, query: str, context_tags: List[str], top_k: int = 5) -> str:
        """检索上下文"""
        engine = await self.get_rag_engine()
        
        # [Metadata Injection] 注入 Context Tags 到查询
        # 格式: [TAG: tag1] [TAG: tag2] Query...
        injected_query = query
        if context_tags:
            tags_str = " ".join([f"[TAG: {tag}]" for tag in context_tags])
            injected_query = f"{tags_str} {query}"
        
        try:
            # 混合检索 + Tag 过滤 (尝试传递 filters 以备未来支持，同时使用注入后的 Query)
            filters = {"tags": {"$in": context_tags}}
            return await engine.query(injected_query, mode="hybrid", top_k=top_k, filters=filters)
                
        except Exception as e:
            logger.error(f"Error retrieving context: {e}")
            return ""
