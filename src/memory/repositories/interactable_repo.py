from typing import List, Optional
from uuid import UUID
from sqlalchemy import select
from ..models import Interactable
from .base_repo import TaggableRepository

class InteractableRepository(TaggableRepository[Interactable]):
    """
    交互物数据仓库
    负责 Interactable 表的 CRUD 操作。
    """
    def __init__(self, session):
        super().__init__(session, Interactable)

    async def get_by_location(self, location_id: UUID) -> List[Interactable]:
        """获取指定地点的所有交互物"""
        result = await self.session.execute(select(Interactable).where(Interactable.location_id == location_id))
        return result.scalars().all()

    async def create(self, name: str, location_id: UUID, tags: List[str] = None, linked_clue_id: Optional[UUID] = None) -> Interactable:
        """创建新交互物"""
        interactable = Interactable(
            name=name, 
            location_id=location_id, 
            tags=tags or [], 
            linked_clue_id=linked_clue_id
        )
        return await self._save(interactable)
