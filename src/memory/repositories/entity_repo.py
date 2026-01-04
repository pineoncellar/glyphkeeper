from typing import List, Optional
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from ..models import Entity, InvestigatorProfile
from .base_repo import TaggableRepository
from ...core import get_logger

logger = get_logger(__name__)

class EntityRepository(TaggableRepository[Entity]):
    """
    实体数据仓库
    负责 Entity 表的 CRUD 操作。
    """
    def __init__(self, session):
        super().__init__(session, Entity)

    async def create(self, name: str, tags: List[str] = None, stats: dict = None, attacks: list = None, location_id: Optional[UUID] = None, key: str = None) -> Entity:
        """创建新实体"""
        entity = Entity(
            key=key,
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
        key: str = None,
    ) -> Entity:
        """创建实体并关联调查员档案，支持 key"""
        entity = await self.create(name, tags, stats, attacks, location_id, key=key)
        
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
    
    async def get_by_name(self, name: str) -> Optional[Entity]:
        """
        智能查找实体。查找优先级：
        1. 精确匹配 (Name="Thomas Mathers")
        2. 模糊匹配 (Name contains "Mathers" -> "Thomas Mathers")
        3. 别名/Tag匹配 (Tags=["Mathers"] -> "Thomas Mathers")
        """
        if not name:
            return None

        # 精确匹配，重名返回第一个
        stmt_exact = select(Entity).where(Entity.name == name)
        result = await self.session.execute(stmt_exact)
        entity = result.scalars().first()
        if entity:
            return entity

        # 模糊匹配
        stmt_fuzzy = select(Entity).where(Entity.name.ilike(f"%{name}%"))
        result = await self.session.execute(stmt_fuzzy)
        candidates = result.scalars().all()
        if len(candidates) == 1:
            return candidates[0]
        elif len(candidates) > 1:
            logger.warning(f"Ambiguous name '{name}'. Found: {[e.name for e in candidates]}")
            return None

        # Tag 匹配（昵称/别名）
        # 大概用不上
        try:
            stmt_tag = select(Entity).where(Entity.tags.contains([name]))
            result = await self.session.execute(stmt_tag)
            entity = result.scalars().first()
            if entity:
                return entity
        except Exception:
            pass

        return None

    async def save(self, entity: Entity) -> Entity:
        """
        保存对实体的修改
        Archivist 在修改完 entity.stats 后应调用此方法。
        """
        self.session.add(entity)
        await self.session.commit()
        await self.session.refresh(entity)
        return entity

