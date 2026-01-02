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

    async def create(self, name: str, tags: List[str] = None, state: str = "default", location_id: Optional[UUID] = None, carrier_id: Optional[UUID] = None, key: str = None) -> Interactable:
        """创建新交互物"""
        if location_id and carrier_id:
            raise ValueError("Interactable cannot be in a location and carried by an entity at the same time.")
        
        interactable = Interactable(
            key=key,
            name=name, 
            tags=tags or [], 
            state=state,
            location_id=location_id,
            carrier_id=carrier_id
        )
        return await self._save(interactable)
    
    async def get_by_carrier(self, carrier_id: UUID) -> List[Interactable]:
        """获取指定实体持有的所有交互物"""
        result = await self.session.execute(select(Interactable).where(Interactable.carrier_id == carrier_id))
        return result.scalars().all()