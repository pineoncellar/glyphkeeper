from typing import List, Optional
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from ..models import GameSession, TimeSlot, Entity, InvestigatorProfile
from .base_repo import BaseRepository

class SessionRepository(BaseRepository[GameSession]):
    """
    会话数据仓库
    负责 GameSession 表的 CRUD 操作。
    """
    def __init__(self, session):
        super().__init__(session, GameSession)

    async def create(self) -> GameSession:
        session = GameSession()
        return await self._save(session)

    async def update_time(self, session_id: UUID, time_slot: TimeSlot, beat_counter: int) -> Optional[GameSession]:
        session = await self.get_by_id(session_id)
        if session:
            session.time_slot = time_slot
            session.beat_counter = beat_counter
            await self.session.commit()
            await self.session.refresh(session)
        return session
    
    async def add_global_tag(self, session_id: UUID, tag: str) -> Optional[GameSession]:
        session = await self.get_by_id(session_id)
        if session:
            if tag not in session.active_global_tags:
                # 创建一个新列表以确保 SQLAlchemy 检测到更改
                session.active_global_tags = session.active_global_tags + [tag]
                await self.session.commit()
                await self.session.refresh(session)
        return session
    
    async def add_investigator(self, session_id: UUID, entity_id: UUID) -> Optional[GameSession]:
        """将调查员添加到会话中"""
        game_session = await self.get_by_id(session_id)
        if game_session:
            entity_id_str = str(entity_id)
            if entity_id_str not in game_session.investigator_ids:
                # 创建新列表以确保 SQLAlchemy 检测到更改
                game_session.investigator_ids = game_session.investigator_ids + [entity_id_str]
                await self.session.commit()
                await self.session.refresh(game_session)
        return game_session
    
    async def remove_investigator(self, session_id: UUID, entity_id: UUID) -> Optional[GameSession]:
        """从会话中移除调查员"""
        game_session = await self.get_by_id(session_id)
        if game_session:
            entity_id_str = str(entity_id)
            if entity_id_str in game_session.investigator_ids:
                # 创建新列表以确保 SQLAlchemy 检测到更改
                new_list = [inv_id for inv_id in game_session.investigator_ids if inv_id != entity_id_str]
                game_session.investigator_ids = new_list
                await self.session.commit()
                await self.session.refresh(game_session)
        return game_session
    
    async def get_investigators(self, session_id: UUID) -> List[Entity]:
        """
        获取会话中所有调查员的 Entity 对象（包含 InvestigatorProfile）
        """
        game_session = await self.get_by_id(session_id)
        if not game_session or not game_session.investigator_ids:
            return []
        
        # 将字符串 ID 转换为 UUID
        investigator_uuids = [UUID(inv_id) for inv_id in game_session.investigator_ids]
        
        # 查询所有调查员 Entity，并预加载 profile
        stmt = (
            select(Entity)
            .where(Entity.id.in_(investigator_uuids))
            .options(selectinload(Entity.profile))
        )
        result = await self.session.execute(stmt)
        investigators = result.scalars().all()
        
        return list(investigators)
