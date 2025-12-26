from typing import List, Optional
from uuid import UUID
from sqlalchemy import select
from ..models import Entity
from .base_repo import TaggableRepository

class EntityRepository(TaggableRepository[Entity]):
    """
    实体数据仓库
    负责 Entity 表的 CRUD 操作。
    """
    def __init__(self, session):
        super().__init__(session, Entity)

    async def create(self, name: str, tags: List[str] = None, stats: dict = None, location_id: Optional[UUID] = None) -> Entity:
        """创建新实体"""
        entity = Entity(name=name, tags=tags or [], stats=stats or {}, location_id=location_id)
        return await self._save(entity)

    async def update_location(self, entity_id: UUID, location_id: UUID) -> Optional[Entity]:
        """更新实体的位置"""
        entity = await self.get_by_id(entity_id)
        if entity:
            entity.location_id = location_id
            await self.session.commit()
            await self.session.refresh(entity)
        return entity

