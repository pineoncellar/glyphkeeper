from typing import List, Optional
from uuid import UUID
from sqlalchemy import select
from ..models import Location
from .base_repo import TaggableRepository

class LocationRepository(TaggableRepository[Location]):
    """
    地点数据仓库
    负责 Location 表的 CRUD 操作。
    """
    def __init__(self, session):
        super().__init__(session, Location)

    async def get_by_name(self, name: str) -> Optional[Location]:
        """根据名称获取地点"""
        result = await self.session.execute(select(Location).where(Location.name == name))
        return result.scalar_one_or_none()

    async def create(self, name: str, base_desc: str, tags: List[str] = None, exits: dict = None) -> Location:
        """创建新地点"""
        location = Location(name=name, base_desc=base_desc, tags=tags or [], exits=exits or {})
        return await self._save(location)

    async def update_tags(self, location_id: UUID, tags: List[str]) -> Optional[Location]:
        """更新地点的 Tags (覆盖)"""
        location = await self.get_by_id(location_id)
        if location:
            location.tags = tags
            await self.session.commit()
            await self.session.refresh(location)
        return location

