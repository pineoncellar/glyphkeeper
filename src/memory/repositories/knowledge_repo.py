from typing import List, Optional
from uuid import UUID
from sqlalchemy import select
from ..models import Knowledge
from .base_repo import BaseRepository

class KnowledgeRepository(BaseRepository[Knowledge]):
    """
    知识数据仓库
    负责 Knowledge (线索) 表的 CRUD 操作。
    """
    def __init__(self, session):
        super().__init__(session, Knowledge)

    async def get_by_rag_key(self, rag_key: str) -> Optional[Knowledge]:
        """根据 RAG Key 获取线索"""
        result = await self.session.execute(select(Knowledge).where(Knowledge.rag_key == rag_key))
        return result.scalar_one_or_none()

    async def create(self, rag_key: str, tags_granted: List[str] = None) -> Knowledge:
        """创建新线索"""
        knowledge = Knowledge(
            rag_key=rag_key,
            tags_granted=tags_granted or []
        )
        return await self._save(knowledge)

    async def mark_as_known(self, knowledge_id: UUID) -> Optional[Knowledge]:
        """标记线索为已获取"""
        knowledge = await self.get_by_id(knowledge_id)
        if knowledge:
            knowledge.is_known = True
            await self.session.commit()
            await self.session.refresh(knowledge)
        return knowledge
