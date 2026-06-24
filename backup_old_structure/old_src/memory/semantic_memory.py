"""
语义记忆模块
负责管理模组的静态文本知识，支持 ETL 阶段的导入与存储。
对应认知架构中的“长期语义记忆”。
"""
from typing import Dict, Any
from .RAG_engine import RAGEngine
from ..core.logger import get_logger

logger = get_logger(__name__)

class SemanticMemory:
    """语义记忆管理器"""
    async def get_rag_engine(self) -> RAGEngine:
        return await RAGEngine.get_instance()

    async def insert_static_knowledge(self, content: str, meta: Dict[str, Any]) -> bool:
        """存入静态知识"""
        engine = await self.get_rag_engine()
        
        # [Metadata Injection] 将元数据注入到文本内容中，实现“软过滤”
        # 格式: [KEY: VALUE] Content...
        injected_content = content
        if meta:
            tags_str = " ".join([f"[{k}: {v}]" for k, v in meta.items()])
            injected_content = f"{tags_str}\n{content}"
            
        try:
            # 尝试传递 metadata (为了兼容未来版本)，同时使用注入后的文本
            return await engine.insert(injected_content, metadata=meta)
        except Exception as e:
            logger.error(f"Error inserting static knowledge: {e}")
            return False
