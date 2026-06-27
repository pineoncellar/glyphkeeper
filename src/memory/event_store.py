# -*- coding: utf-8 -*-
"""
@File     :   event_store.py
@Desc     :   事件溯源存储 — 基于 pgembed 嵌入式 PostgreSQL 的不可变事件流
@Note     :   使用 asyncpg + JSONB，通过 PgManager 自动管理连接
              append 写入时自动维护 version 递增，session_id 支持任意字符串
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

from src.tools import get_logger

logger = get_logger(__name__)


# ====================================================================
# EventStore — 事件溯源存储核心
# ====================================================================


class EventStore:
    """事件溯源存储 — 基于 asyncpg + JSONB

    append 写入不可变事件流，get_events/replay 按 version 升序读取。
    session_id 接受任意字符串（含非 UUID 格式），建表时自动迁移旧版 UUID 列。
    """

    def __init__(self, pg_uri: Optional[str] = None):
        self._uri = pg_uri or ""
        self._conn = None

    # ------- 连接管理 -------

    async def _get_conn(self):
        """获取 asyncpg 连接，延迟建表"""
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
        """建 events 表，自动迁移旧版 UUID 列到 TEXT"""
        conn = await self._get_conn()

        # 迁移旧版 UUID 列到 TEXT — DROP 会丢数据，ALTER 保平安
        col_type = await conn.fetchval("""
            SELECT data_type FROM information_schema.columns
            WHERE table_name='events' AND column_name='session_id'
        """)
        if col_type and col_type == 'uuid':
            logger.warning("EventStore: 迁移 session_id UUID->TEXT...")
            await conn.execute("ALTER TABLE events ALTER COLUMN session_id TYPE TEXT")
            logger.info("EventStore: session_id 迁移完成")

        parent_type = await conn.fetchval("""
            SELECT data_type FROM information_schema.columns
            WHERE table_name='events' AND column_name='parent_event_id'
        """)
        if parent_type and parent_type == 'uuid':
            logger.warning("EventStore: 迁移 parent_event_id UUID->TEXT...")
            await conn.execute("ALTER TABLE events ALTER COLUMN parent_event_id TYPE TEXT")
            logger.info("EventStore: parent_event_id 迁移完成")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id UUID PRIMARY KEY,
                session_id TEXT NOT NULL,
                type TEXT NOT NULL,
                data JSONB NOT NULL,
                version INTEGER NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL,
                source_node TEXT DEFAULT '',
                parent_event_id TEXT
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_session
            ON events(session_id, version)
        """)

    async def close(self):
        """关闭数据库连接"""
        if self._conn and not self._conn.is_closed():
            await self._conn.close()
            self._conn = None

    # ------- 核心读写 -------

    async def append(
        self,
        session_id: str,
        event_type: str,
        data: dict,
        source_node: str = "",
        parent_event_id: Optional[str] = None,
    ) -> dict:
        """追加一条新事件到事件流，自动递增 version"""
        conn = await self._get_conn()
        version = await self.get_latest_version(session_id) + 1
        now = datetime.now(timezone.utc)

        event = {
            "id": str(uuid.uuid4()),
            "session_id": session_id,
            "type": event_type,
            "data": data,
            "version": version,
            "timestamp": now.isoformat(),
            "source_node": source_node,
            "parent_event_id": parent_event_id,
        }

        await conn.execute(
            """INSERT INTO events (id, session_id, type, data, version, timestamp, source_node, parent_event_id)
               VALUES ($1, $2, $3, $4::jsonb, $5, $6::timestamptz, $7, $8)""",
            event["id"], event["session_id"], event["type"],
            json.dumps(event["data"], ensure_ascii=False),
            event["version"], now,
            event["source_node"], event["parent_event_id"],
        )
        return event

    async def get_events(
        self, session_id: str, since_version: int = 0
    ) -> list[dict]:
        """获取指定会话的事件流（按 version 升序）"""
        conn = await self._get_conn()
        rows = await conn.fetch(
            "SELECT * FROM events WHERE session_id = $1 AND version > $2 ORDER BY version ASC",
            session_id, since_version,
        )
        return [self._row_to_event(row) for row in rows]

    async def replay(self, session_id: str) -> AsyncGenerator[dict, None]:
        """按 version 顺序回放事件（异步生成器）"""
        conn = await self._get_conn()
        rows = await conn.fetch(
            "SELECT * FROM events WHERE session_id = $1 ORDER BY version ASC",
            session_id,
        )
        for row in rows:
            yield self._row_to_event(row)

    async def get_latest_version(self, session_id: str) -> int:
        """获取指定会话的最新 version 号"""
        conn = await self._get_conn()
        val = await conn.fetchval(
            "SELECT COALESCE(MAX(version), 0) FROM events WHERE session_id = $1",
            session_id,
        )
        return val or 0

    async def get_event_count(self, session_id: str) -> int:
        """获取指定会话的事件总数"""
        conn = await self._get_conn()
        val = await conn.fetchval(
            "SELECT COUNT(*) FROM events WHERE session_id = $1", session_id,
        )
        return val or 0

    # ------- 辅助方法 -------

    @staticmethod
    def _row_to_event(row) -> dict:
        return {
            "id": str(row["id"]),
            "session_id": str(row["session_id"]),
            "type": row["type"],
            "data": row["data"] if isinstance(row["data"], dict) else json.loads(row["data"]),
            "version": row["version"],
            "timestamp": row["timestamp"].isoformat() if hasattr(row["timestamp"], 'isoformat') else row["timestamp"],
            "source_node": row["source_node"],
            "parent_event_id": str(row["parent_event_id"]) if row["parent_event_id"] else None,
        }

    async def clear_session(self, session_id: str):
        """清空指定会话的事件（仅用于测试）"""
        conn = await self._get_conn()
        await conn.execute("DELETE FROM events WHERE session_id = $1", session_id)

    async def clear_all(self):
        """清空所有事件（仅用于测试）"""
        conn = await self._get_conn()
        await conn.execute("DELETE FROM events")


# ====================================================================
# 工厂函数 — PG 可用时直接返回 EventStore，无需区分两个类
# ====================================================================


async def create_event_store() -> EventStore:
    """创建 EventStore 实例，通过 PgManager 自动获取连接 URI

    PG 可用时使用嵌入式 PostgreSQL，否则抛异常让调用方处理。
    不再需要区分 EventStore 与 AsyncPgEventStore — 两者已合并。
    """
    from src.tools.pg_manager import PgManager
    mgr = await PgManager.get_instance()
    if mgr.available:
        await mgr.start()
        return EventStore(pg_uri=mgr.uri)
    raise RuntimeError("pgembed 不可用")
