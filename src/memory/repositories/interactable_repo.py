from typing import List, Optional
from uuid import UUID
from sqlalchemy import select
from ..models import Interactable
from .base_repo import TaggableRepository
from ...core import get_logger

logger = get_logger(__name__)

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
    
    async def get_by_name(self, name: str) -> Optional[Interactable]:
        """
        智能查找交互物。查找优先级：
        1. 精确匹配 (Name="生锈的钥匙")
        2. 模糊匹配 (Name contains "钥匙" -> "生锈的钥匙")
        3. 别名/Tag匹配 (Tags=["钥匙"] -> "生锈的钥匙")
        """
        if not name:
            return None

        # 精确匹配，重名返回第一个
        stmt_exact = select(Interactable).where(Interactable.name == name)
        result = await self.session.execute(stmt_exact)
        interactable = result.scalars().first()
        if interactable:
            return interactable

        # 模糊匹配
        stmt_fuzzy = select(Interactable).where(Interactable.name.ilike(f"%{name}%"))
        result = await self.session.execute(stmt_fuzzy)
        candidates = result.scalars().all()
        if len(candidates) == 1:
            return candidates[0]
        elif len(candidates) > 1:
            logger.warning(f"Ambiguous name '{name}'. Found: {[i.name for i in candidates]}")
            return None

        # Tag 匹配（别名）
        try:
            stmt_tag = select(Interactable).where(Interactable.tags.contains([name]))
            result = await self.session.execute(stmt_tag)
            interactable = result.scalars().first()
            if interactable:
                return interactable
        except Exception:
            pass

        return None