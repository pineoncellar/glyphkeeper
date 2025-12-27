from typing import List, Optional
from uuid import UUID
from sqlalchemy import select
from ..models import ClueDiscovery
from .base_repo import BaseRepository

class ClueDiscoveryRepository(BaseRepository[ClueDiscovery]):
    """
    线索发现数据仓库
    负责 ClueDiscovery 表的 CRUD 操作。
    """
    def __init__(self, session):
        super().__init__(session, ClueDiscovery)

    async def create(self, knowledge_id: UUID, discovery_flavor_text: str, interactable_id: Optional[UUID] = None, entity_id: Optional[UUID] = None, required_check: dict = None) -> ClueDiscovery:
        """创建新线索发现记录"""
        if interactable_id and entity_id:
            raise ValueError("ClueDiscovery cannot be linked to both an interactable and an entity.")
        if not interactable_id and not entity_id:
            raise ValueError("ClueDiscovery must be linked to either an interactable or an entity.")

        discovery = ClueDiscovery(
            knowledge_id=knowledge_id,
            interactable_id=interactable_id,
            entity_id=entity_id,
            required_check=required_check or {},
            discovery_flavor_text=discovery_flavor_text
        )
        return await self._save(discovery)

    async def get_by_interactable(self, interactable_id: UUID) -> List[ClueDiscovery]:
        """获取指定交互物的所有线索发现"""
        result = await self.session.execute(select(ClueDiscovery).where(ClueDiscovery.interactable_id == interactable_id))
        return result.scalars().all()

    async def get_by_entity(self, entity_id: UUID) -> List[ClueDiscovery]:
        """获取指定实体的所有线索发现"""
        result = await self.session.execute(select(ClueDiscovery).where(ClueDiscovery.entity_id == entity_id))
        return result.scalars().all()
