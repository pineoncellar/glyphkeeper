from typing import List, Optional
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from ..models import Entity, InvestigatorProfile
from .base_repo import TaggableRepository

class EntityRepository(TaggableRepository[Entity]):
    """
    实体数据仓库
    负责 Entity 表的 CRUD 操作。
    """
    def __init__(self, session):
        super().__init__(session, Entity)

    async def create(self, name: str, tags: List[str] = None, stats: dict = None, attacks: list = None, location_id: Optional[UUID] = None) -> Entity:
        """创建新实体"""
        entity = Entity(
            name=name, 
            tags=tags or [], 
            stats=stats or {}, 
            attacks=attacks or [],
            location_id=location_id
        )
        return await self._save(entity)

    async def get_by_id_with_profile(self, entity_id: UUID) -> Optional[Entity]:
        """获取实体并加载其关联的调查员档案"""
        result = await self.session.execute(
            select(Entity)
            .where(Entity.id == entity_id)
            .options(selectinload(Entity.profile))
        )
        return result.scalar_one_or_none()

    async def update_location(self, entity_id: UUID, location_id: UUID) -> Optional[Entity]:
        """更新实体的位置"""
        entity = await self.get_by_id(entity_id)
        if entity:
            entity.location_id = location_id
            await self.session.commit()
            await self.session.refresh(entity)
        return entity

    async def create_with_profile(
        self,
        name: str,
        tags: List[str] = None,
        stats: dict = None,
        attacks: list = None,
        location_id: Optional[UUID] = None,
        profile_data: dict = None,
    ) -> Entity:
        """创建实体并关联调查员档案"""
        entity = await self.create(name, tags, stats, attacks, location_id)
        
        # 如果提供了profile数据，创建对应的InvestigatorProfile
        if profile_data:
            profile = InvestigatorProfile(
                entity_id=entity.id,
                **profile_data
            )
            self.session.add(profile)
            await self.session.commit()
            await self.session.refresh(entity)
        
        return entity


