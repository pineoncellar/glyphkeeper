"""
@File     :   event_store.py
@Desc     :   事件溯源存储 — 基于 pgembed 嵌入式 PostgreSQL
@Note     :   使用 asyncpg + JSONB，通过 PgManager 自动管理连接

使用方式:
    store = EventStore()
    event = await store.append("session-1", "SkillCheck", {"skill": "侦查"})
    events = await store.get_events("session-1")
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional

from src.tools import get_logger

logger = get_logger(__name__)


class EventStore:
    """事件溯源存储 — 基于 asyncpg + JSONB"""

    def __init__(self, pg_uri: Optional[str] = None):
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
        # 先检查是否有旧版 UUID 表需要迁移
        col_type = await conn.fetchval("""
            SELECT data_type FROM information_schema.columns
            WHERE table_name='events' AND column_name='session_id'
        """)
        if col_type and col_type == 'uuid':
            await conn.execute("DROP TABLE IF EXISTS events CASCADE")
            logger.info("event_store: 已重建 events 表（迁移 UUID→TEXT）")

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id UUID PRIMARY KEY, session_id TEXT NOT NULL, type TEXT NOT NULL,
                data JSONB NOT NULL, version INTEGER NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL, source_node TEXT DEFAULT '',
                parent_event_id TEXT
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id, version)
        """)

    async def close(self):
        if self._conn and not self._conn.is_closed():
            await self._conn.close()
            self._conn = None

    async def append(self, session_id, event_type, data, source_node="", parent_event_id=None) -> dict:
        conn = await self._get_conn()
        version = await self.get_latest_version(session_id) + 1
        now = datetime.now(timezone.utc)
        event = {"id": str(uuid.uuid4()), "session_id": session_id, "type": event_type,
                 "data": data, "version": version,
                 "timestamp": now.isoformat(),
                 "source_node": source_node, "parent_event_id": parent_event_id}
        await conn.execute(
            "INSERT INTO events (id,session_id,type,data,version,timestamp,source_node,parent_event_id) "
            "VALUES ($1,$2,$3,$4::jsonb,$5,$6::timestamptz,$7,$8)",
            event["id"], event["session_id"], event["type"],
            json.dumps(event["data"], ensure_ascii=False),
            event["version"], now,  # 传 datetime 对象而非字符串
            event["source_node"], event["parent_event_id"],
        )
        return event

    async def get_events(self, session_id, since_version=0) -> list[dict]:
        conn = await self._get_conn()
        rows = await conn.fetch(
            "SELECT * FROM events WHERE session_id=$1 AND version>$2 ORDER BY version ASC",
            session_id, since_version,
        )
        return [self._row_to_event(r) for r in rows]

    async def replay(self, session_id) -> AsyncGenerator[dict, None]:
        conn = await self._get_conn()
        for row in await conn.fetch(
            "SELECT * FROM events WHERE session_id=$1 ORDER BY version ASC", session_id,
        ):
            yield self._row_to_event(row)

    async def get_latest_version(self, session_id) -> int:
        conn = await self._get_conn()
        return (await conn.fetchval(
            "SELECT COALESCE(MAX(version),0) FROM events WHERE session_id=$1", session_id,
        )) or 0

    async def get_event_count(self, session_id) -> int:
        conn = await self._get_conn()
        return (await conn.fetchval(
            "SELECT COUNT(*) FROM events WHERE session_id=$1", session_id,
        )) or 0

    @staticmethod
    def _row_to_event(row) -> dict:
        return {"id": str(row["id"]), "session_id": str(row["session_id"]),
                "type": row["type"],
                "data": row["data"] if isinstance(row["data"], dict) else json.loads(row["data"]),
                "version": row["version"],
                "timestamp": row["timestamp"].isoformat() if hasattr(row["timestamp"], "isoformat") else row["timestamp"],
                "source_node": row["source_node"],
                "parent_event_id": str(row["parent_event_id"]) if row["parent_event_id"] else None}

    async def clear_session(self, session_id):
        conn = await self._get_conn()
        await conn.execute("DELETE FROM events WHERE session_id=$1", session_id)

    async def clear_all(self):
        conn = await self._get_conn()
        await conn.execute("DELETE FROM events")


# ====================================================================
# AsyncPgEventStore — PostgreSQL 事件溯源存储
# ====================================================================


class AsyncPgEventStore:
    """PG 事件溯源存储 — 与 EventStore 接口兼容

    使用 asyncpg，存储使用 JSONB + UUID 类型以充分利用 PG 特性。
    接口与 EventStore 完全一致，可互换使用。

    使用方式:
        store = AsyncPgEventStore(pg_uri="postgresql://...")
        event = await store.append("session-1", "SkillCheck", {"skill": "侦查"})
        events = await store.get_events("session-1")
    """

    def __init__(self, pg_uri: Optional[str] = None):
        """
        参数:
            pg_uri: PostgreSQL 连接 URI。None 则从 PgManager 自动获取
        """
        self._uri = pg_uri or ""
        self._pool = None
        self._conn = None

    # ── 连接管理 ──

    async def _get_conn(self):
        """获取 asyncpg 连接（单连接模式，足够事件写入场景）"""
        if self._conn and not self._conn.is_closed():
            return self._conn

        uri = self._uri
        if not uri:
            from src.tools.pg_manager import PgManager, ensure_pg_started
            mgr = await PgManager.get_instance()
            if mgr.available:
                await mgr.start()
                uri = mgr.uri
            else:
                raise RuntimeError("PG 不可用，无法创建 AsyncPgEventStore")

        import asyncpg
        self._conn = await asyncpg.connect(uri)
        await self._init_db()
        return self._conn

    async def _init_db(self):
        """初始化事件表（PG 语法）"""
        conn = await self._get_conn()
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id UUID PRIMARY KEY,
                session_id UUID NOT NULL,
                type TEXT NOT NULL,
                data JSONB NOT NULL,
                version INTEGER NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL,
                source_node TEXT DEFAULT '',
                parent_event_id UUID
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_session
            ON events(session_id, version)
        """)
        logger.info("AsyncPgEventStore: 表已就绪")

    async def close(self):
        """关闭数据库连接"""
        if self._conn and not self._conn.is_closed():
            await self._conn.close()
            self._conn = None

    # ── 核心操作 ──

    async def append(
        self,
        session_id: str,
        event_type: str,
        data: dict,
        source_node: str = "",
        parent_event_id: Optional[str] = None,
    ) -> dict:
        """追加一条新事件到事件流"""
        conn = await self._get_conn()
        version = await self.get_latest_version(session_id) + 1

        event = {
            "id": str(uuid.uuid4()),
            "session_id": session_id,
            "type": event_type,
            "data": data,
            "version": version,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "source_node": source_node,
            "parent_event_id": parent_event_id,
        }

        await conn.execute(
            """INSERT INTO events (id, session_id, type, data, version, timestamp, source_node, parent_event_id)
               VALUES ($1, $2, $3, $4::jsonb, $5, $6::timestamptz, $7, $8)""",
            event["id"], event["session_id"], event["type"],
            json.dumps(event["data"], ensure_ascii=False),
            event["version"], event["timestamp"],
            event["source_node"], event["parent_event_id"],
        )
        return event

    async def get_events(
        self, session_id: str, since_version: int = 0
    ) -> list[dict]:
        """获取指定会话的事件流（按版本升序）"""
        conn = await self._get_conn()
        rows = await conn.fetch(
            "SELECT * FROM events WHERE session_id = $1 AND version > $2 ORDER BY version ASC",
            session_id, since_version,
        )
        return [self._row_to_event(row) for row in rows]

    async def replay(self, session_id: str) -> AsyncGenerator[dict, None]:
        """按版本顺序回放事件（异步生成器）"""
        conn = await self._get_conn()
        rows = await conn.fetch(
            "SELECT * FROM events WHERE session_id = $1 ORDER BY version ASC",
            session_id,
        )
        for row in rows:
            yield self._row_to_event(row)

    async def get_latest_version(self, session_id: str) -> int:
        """获取会话的最新版本号"""
        conn = await self._get_conn()
        val = await conn.fetchval(
            "SELECT COALESCE(MAX(version), 0) FROM events WHERE session_id = $1",
            session_id,
        )
        return val or 0

    async def get_event_count(self, session_id: str) -> int:
        """获取会话的事件总数"""
        conn = await self._get_conn()
        val = await conn.fetchval(
            "SELECT COUNT(*) FROM events WHERE session_id = $1", session_id,
        )
        return val or 0

    # ── 辅助方法 ──

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
# 工厂函数
# ====================================================================


async def create_event_store(force_local: bool = True) -> "EventStore | AsyncPgEventStore":
    """创建事件存储实例 — PG 优先，SQLite 兜底

    参数:
        force_local: True=仅尝试本地 pgembed, False=允许远程 PG

    返回:
        AsyncPgEventStore（PG 可用时）或 EventStore（SQLite 兜底）
    """
    from src.tools.pg_manager import PgManager
    mgr = await PgManager.get_instance(force_local=force_local)
    if mgr.available:
        return AsyncPgEventStore(pg_uri=mgr.uri)
    return EventStore()
