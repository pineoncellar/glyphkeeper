from typing import List, Optional
from uuid import UUID
from sqlalchemy import select
from ..models import GameSession, TimeSlot
from .base_repo import BaseRepository

class SessionRepository(BaseRepository[GameSession]):
    """
    会话数据仓库
    负责 GameSession 表的 CRUD 操作。
    """
    def __init__(self, session):
        super().__init__(session, GameSession)

    async def get_current_session(self) -> Optional[GameSession]:
        # 暂时假设只有一个会话，或者获取最新的会话
        result = await self.session.execute(select(GameSession).order_by(GameSession.id.desc()).limit(1))
        return result.scalar_one_or_none()

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
