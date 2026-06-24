from typing import Optional
from uuid import UUID
from sqlalchemy import select
from ..models import InvestigatorProfile, Entity
from .base_repo import BaseRepository

class InvestigatorProfileRepository(BaseRepository[InvestigatorProfile]):
    """
    调查员档案数据仓库
    负责 InvestigatorProfile 表的 CRUD 操作。
    """
    def __init__(self, session):
        super().__init__(session, InvestigatorProfile)

    async def get_by_entity_id(self, entity_id: UUID) -> Optional[InvestigatorProfile]:
        """根据关联的实体ID获取调查员档案"""
        result = await self.session.execute(
            select(InvestigatorProfile).where(InvestigatorProfile.entity_id == entity_id)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        entity_id: UUID,
        player_name: Optional[str] = None,
        occupation: str = "Unknown",
        age: int = 25,
        gender: str = "Unknown",
        residence: Optional[str] = None,
        birthplace: Optional[str] = None,
        backstory: dict = None,
        assets_detail: Optional[str] = None,
    ) -> InvestigatorProfile:
        """创建新调查员档案"""
        profile = InvestigatorProfile(
            entity_id=entity_id,
            player_name=player_name,
            occupation=occupation,
            age=age,
            gender=gender,
            residence=residence,
            birthplace=birthplace,
            backstory=backstory or {},
            assets_detail=assets_detail,
        )
        return await self._save(profile)

    async def update_basic_info(
        self,
        profile_id: UUID,
        player_name: Optional[str] = None,
        occupation: Optional[str] = None,
        age: Optional[int] = None,
        gender: Optional[str] = None,
        residence: Optional[str] = None,
        birthplace: Optional[str] = None,
    ) -> Optional[InvestigatorProfile]:
        """更新调查员的基本信息"""
        profile = await self.get_by_id(profile_id)
        if profile:
            if player_name is not None:
                profile.player_name = player_name
            if occupation is not None:
                profile.occupation = occupation
            if age is not None:
                profile.age = age
            if gender is not None:
                profile.gender = gender
            if residence is not None:
                profile.residence = residence
            if birthplace is not None:
                profile.birthplace = birthplace
            await self.session.commit()
            await self.session.refresh(profile)
        return profile

    async def update_backstory(self, profile_id: UUID, backstory: dict) -> Optional[InvestigatorProfile]:
        """更新调查员的背景故事"""
        profile = await self.get_by_id(profile_id)
        if profile:
            profile.backstory = backstory
            await self.session.commit()
            await self.session.refresh(profile)
        return profile

    async def update_assets(self, profile_id: UUID, assets_detail: str) -> Optional[InvestigatorProfile]:
        """更新调查员的资产描述"""
        profile = await self.get_by_id(profile_id)
        if profile:
            profile.assets_detail = assets_detail
            await self.session.commit()
            await self.session.refresh(profile)
        return profile

    async def list_all_profiles(self) -> list[InvestigatorProfile]:
        """列出所有调查员档案"""
        result = await self.session.execute(select(InvestigatorProfile))
        return result.scalars().all()