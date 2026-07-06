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
from src.state.session_state import SessionKnowledgeState
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
                created_at TIMESTAMPTZ NOT NULL, label TEXT DEFAULT '',
                known_knowledge_ids JSONB DEFAULT '[]'::jsonb,
                trigger_states JSONB DEFAULT '{}'::jsonb
            )
        """)

        # 迁移旧版表（无 known_knowledge_ids 列时追加）
        kcol = await conn.fetchval("""
            SELECT data_type FROM information_schema.columns
            WHERE table_name='snapshots' AND column_name='known_knowledge_ids'
        """)
        if not kcol:
            await conn.execute(
                "ALTER TABLE snapshots ADD COLUMN known_knowledge_ids JSONB DEFAULT '[]'::jsonb"
            )
            logger.info("snapshot: 追加 known_knowledge_ids 列")
        # 迁移旧版表（无 trigger_states 列时追加）
        tscol = await conn.fetchval("""
            SELECT data_type FROM information_schema.columns
            WHERE table_name='snapshots' AND column_name='trigger_states'
        """)
        if not tscol:
            await conn.execute(
                "ALTER TABLE snapshots ADD COLUMN trigger_states JSONB DEFAULT '{}'::jsonb"
            )
            logger.info("snapshot: 追加 trigger_states 列")
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

        # 记录当前会话的已知知识 ID，供读档恢复防剧透状态
        known_ids: list[str] = []
        try:
            sks = SessionKnowledgeState()
            known_ids = await sks.get_discovered_knowledge_ids(session_id)
        except Exception as e:
            logger.debug(f"snapshot: 获取 known_knowledge_ids 失败: {e}")

        # 记录当前会话的触发器运行时状态（供读档恢复）
        trigger_states: dict = {}
        try:
            from src.state.read_models import StaticReadStore
            ts_store = StaticReadStore()
            conn_ts = await ts_store._get_conn()
            rows = await conn_ts.fetch(
                "SELECT trigger_id, fired_count, fired_this_turn, is_disabled "
                "FROM session_trigger_state WHERE session_id=$1",
                session_id,
            )
            for r in rows:
                trigger_states[r["trigger_id"]] = {
                    "fired_count": r["fired_count"],
                    "fired_this_turn": r["fired_this_turn"],
                    "is_disabled": r["is_disabled"],
                }
        except Exception as e:
            logger.debug(f"snapshot: 获取 trigger_states 失败: {e}")

        state_json = json.dumps(state, ensure_ascii=False, default=str)
        known_ids_json = json.dumps(known_ids, ensure_ascii=False)
        trigger_states_json = json.dumps(trigger_states, ensure_ascii=False)
        await conn.execute(
            "INSERT INTO snapshots "
            "(id,session_id,version,state_json,event_version,created_at,"
            "label,known_knowledge_ids,trigger_states) "
            "VALUES ($1::uuid,$2,$3,$4::jsonb,$5,$6::timestamptz,$7,$8::jsonb,$9::jsonb)",
            snapshot_id, session_id, next_version, state_json,
            event_version, datetime.now(timezone.utc), label,
            known_ids_json, trigger_states_json,
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

        # 解析 known_knowledge_ids（兼容旧存档无此列的情况）
        known_ids_raw = row.get("known_knowledge_ids")
        known_ids: list[str] = []
        if known_ids_raw:
            if isinstance(known_ids_raw, str):
                known_ids = json.loads(known_ids_raw)
            elif isinstance(known_ids_raw, (list, tuple)):
                known_ids = list(known_ids_raw)

        # 解析 trigger_states（兼容旧存档无此列的情况）
        ts_raw = row.get("trigger_states")
        trigger_states: dict = {}
        if ts_raw:
            if isinstance(ts_raw, str):
                trigger_states = json.loads(ts_raw)
            elif isinstance(ts_raw, dict):
                trigger_states = dict(ts_raw)

        return {
            "state": state,
            "known_knowledge_ids": known_ids,
            "trigger_states": trigger_states,
        }

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
