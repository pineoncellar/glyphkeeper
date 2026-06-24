from typing import Generic, TypeVar, Type, Optional, List
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from ..database import Base

T = TypeVar("T", bound=Base)

class BaseRepository(Generic[T]):
    """通用仓库基类，提供基本的 CRUD 操作"""
    def __init__(self, session: AsyncSession, model: Type[T]):
        self.session = session
        self.model = model

    async def get_by_id(self, id: UUID) -> Optional[T]:
        """根据 ID 获取对象"""
        result = await self.session.execute(select(self.model).where(self.model.id == id))
        return result.scalar_one_or_none()

    async def _save(self, obj: T) -> T:
        """保存对象（内部辅助方法）"""
        self.session.add(obj)
        await self.session.commit()
        await self.session.refresh(obj)
        return obj

class TaggableRepository(BaseRepository[T]):
    """支持 Tags 操作的仓库基类，适用于拥有 'tags' 字段的模型 (Location, Entity, Interactable)
    """
    async def add_tag(self, id: UUID, tag: str) -> Optional[T]:
        """添加单个 Tag"""
        obj = await self.get_by_id(id)
        if obj and hasattr(obj, 'tags') and tag not in obj.tags:
            # 使用列表拼接触发 SQLAlchemy 更新检测
            obj.tags = obj.tags + [tag]
            await self.session.commit()
            await self.session.refresh(obj)
        return obj

    async def remove_tag(self, id: UUID, tag: str) -> Optional[T]:
        """移除单个 Tag"""
        obj = await self.get_by_id(id)
        if obj and hasattr(obj, 'tags') and tag in obj.tags:
            new_tags = list(obj.tags)
            new_tags.remove(tag)
            obj.tags = new_tags
            await self.session.commit()
            await self.session.refresh(obj)
        return obj
