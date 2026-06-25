"""
@File     :   snapshot.py
@Desc     :   状态快照管理 — 基于 pgembed PostgreSQL
@Note     :   使用 JSONB 存储完整 GameState 快照

使用方式:
    mgr = SnapshotManager(event_store)
    snap_id = await mgr.create(state)
    restored = await mgr.restore(snap_id)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Optional
from src.state.game_state import GameState
from src.state.reducer import apply_events_to_state
from src.memory.event_store import EventStore
from src.tools import get_logger

logger = get_logger(__name__)

MAX_SNAPSHOTS_PER_SESSION = 20


class SnapshotManager:
    """状态快照管理器 — 基于 asyncpg + JSONB"""

    def __init__(self, event_store=None, pg_uri=None):
        self._event_store = event_store
        self._uri = pg_uri or ""
        self._conn = None

    async def _get_conn(self):
        if self._conn and not self._conn.is_closed():
            return self._conn
        uri = self._uri
        if not uri:
            from src.tools.pg_manager import PgManager
            mgr = await PgManager.get_instance()
            if mgr.available:
                await mgr.start()
                uri = mgr.uri
            else:
                raise RuntimeError("pgembed 不可用")
        import asyncpg
        self._conn = await asyncpg.connect(uri)
        await self._init_db()
        return self._conn

    async def _init_db(self):
        conn = await self._get_conn()
        # 检查旧版 UUID 表并迁移
        col_type = await conn.fetchval("""
            SELECT data_type FROM information_schema.columns
            WHERE table_name='snapshots' AND column_name='session_id'
        """)
        if col_type and col_type == 'uuid':
            await conn.execute("DROP TABLE IF EXISTS snapshots CASCADE")
            logger.info("snapshot: 已重建 snapshots 表（迁移 UUID→TEXT）")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id UUID PRIMARY KEY, session_id TEXT NOT NULL,
                version INTEGER NOT NULL, state_json JSONB NOT NULL,
                event_version INTEGER NOT NULL,
                created_at TIMESTAMPTZ NOT NULL, label TEXT DEFAULT ''
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_snapshots_session
            ON snapshots(session_id, version DESC)
        """)

    async def close(self):
        if self._conn and not self._conn.is_closed():
            await self._conn.close()
            self._conn = None

    async def create(self, state, label=""):
        conn = await self._get_conn()
        session_id = state.get("session_id", "")
        snapshot_id = str(uuid.uuid4())
        next_version = (await self._get_next_version(session_id))
        event_version = (await self._event_store.get_latest_version(session_id)) if self._event_store else 0
        state_json = json.dumps(state, ensure_ascii=False, default=str)
        await conn.execute(
            "INSERT INTO snapshots (id,session_id,version,state_json,event_version,created_at,label) "
            "VALUES ($1::uuid,$2,$3,$4::jsonb,$5,$6::timestamptz,$7)",
            snapshot_id, session_id, next_version, state_json,
            event_version, datetime.now(timezone.utc), label,
        )
        await self._enforce_retention(session_id)
        return snapshot_id

    async def restore(self, snapshot_id):
        conn = await self._get_conn()
        try:
            row = await conn.fetchrow("SELECT * FROM snapshots WHERE id=$1::uuid", snapshot_id)
        except Exception:
            row = None
        if row is None:
            return None
        state = json.loads(row["state_json"])
        if self._event_store and row["event_version"]:
            new_events = await self._event_store.get_events(str(row["session_id"]), since_version=row["event_version"])
            if new_events:
                state = apply_events_to_state(state, new_events)
        return state

    async def list_snapshots(self, session_id, limit=10):
        conn = await self._get_conn()
        rows = await conn.fetch(
            "SELECT id,session_id,version,event_version,created_at,label "
            "FROM snapshots WHERE session_id=$1 ORDER BY version DESC LIMIT $2",
            session_id, limit,
        )
        result = []
        for r in rows:
            d = dict(r)
            d["id"] = str(d["id"])
            result.append(d)
        return result

    async def delete(self, snapshot_id):
        conn = await self._get_conn()
        try:
            r = await conn.execute("DELETE FROM snapshots WHERE id=$1::uuid", snapshot_id)
            return "DELETE 1" in r
        except Exception:
            return False

    async def get_latest(self, session_id):
        conn = await self._get_conn()
        row = await conn.fetchrow(
            "SELECT * FROM snapshots WHERE session_id=$1 ORDER BY version DESC LIMIT 1", session_id,
        )
        if row is None:
            return None
        d = dict(row)
        d["id"] = str(d["id"])
        return d

    async def clear_all(self):
        """清空所有快照（仅用于测试）"""
        conn = await self._get_conn()
        await conn.execute("DELETE FROM snapshots")

    async def _get_next_version(self, session_id):
        conn = await self._get_conn()
        return ((await conn.fetchval(
            "SELECT COALESCE(MAX(version),0) FROM snapshots WHERE session_id=$1", session_id,
        )) or 0) + 1

    async def _enforce_retention(self, session_id):
        conn = await self._get_conn()
        row = await conn.fetchrow(
            "SELECT version FROM snapshots WHERE session_id=$1 ORDER BY version DESC LIMIT 1 OFFSET $2",
            session_id, MAX_SNAPSHOTS_PER_SESSION - 1,
        )
        if row:
            await conn.execute(
                "DELETE FROM snapshots WHERE session_id=$1 AND version<$2",
                session_id, row["version"],
            )
