"""
@File     :   event_store.py
@Desc     :   事件溯源存储 — 不可变事件流的追加、查询与回放

职责:
  - 存储不可变的事件流（Event Sourcing 模式）
  - 提供事件追加与范围查询接口
  - 支持事件回放（replay）重建状态
  - 初期使用 SQLite，后期可迁移至 PostgreSQL

事件结构:
  {
    "id": str,              # UUID
    "session_id": str,      # 会话 UUID
    "type": str,            # 事件类型
    "data": dict,           # 事件负载（JSON）
    "version": int,         # 乐观锁版本号
    "timestamp": str,       # ISO 时间戳
    "source_node": str,     # 产生此事件的 Node 名
    "parent_event_id": Optional[str],  # 父事件 ID（因果链）
  }

接口:
  class EventStore:
    async def append(self, session_id, event_type, data, ...) -> dict
    async def get_events(self, session_id, since_version=0) -> list[dict]
    async def replay(self, session_id) -> AsyncGenerator[dict, None]
    async def get_latest_version(self, session_id) -> int
"""

import json
import uuid
import aiosqlite
from pathlib import Path
from datetime import datetime, timezone
from typing import AsyncGenerator, Optional
from src.tools import get_settings, PROJECT_ROOT


class EventStore:
    """事件溯源存储 — 基于 SQLite"""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = str(PROJECT_ROOT / "data" / "events.db")
        self.db_path = db_path
        # 确保父目录存在
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn: Optional[aiosqlite.Connection] = None

    # ── 连接管理 ──

    async def _get_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            self._conn = await aiosqlite.connect(self.db_path)
            self._conn.row_factory = aiosqlite.Row
            await self._init_db()
        return self._conn

    async def _init_db(self):
        """初始化事件表"""
        conn = await self._get_conn()
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                type TEXT NOT NULL,
                data TEXT NOT NULL,
                version INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                source_node TEXT DEFAULT '',
                parent_event_id TEXT
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_session
            ON events(session_id, version)
        """)
        await conn.commit()

    async def close(self):
        """关闭数据库连接"""
        if self._conn:
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
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event["id"],
                event["session_id"],
                event["type"],
                json.dumps(event["data"], ensure_ascii=False),
                event["version"],
                event["timestamp"],
                event["source_node"],
                event["parent_event_id"],
            ),
        )
        await conn.commit()
        return event

    async def get_events(
        self, session_id: str, since_version: int = 0
    ) -> list[dict]:
        """获取指定会话的事件流（按版本升序）"""
        conn = await self._get_conn()
        cursor = await conn.execute(
            "SELECT * FROM events WHERE session_id = ? AND version > ? ORDER BY version ASC",
            (session_id, since_version),
        )
        rows = await cursor.fetchall()
        return [self._row_to_event(row) for row in rows]

    async def replay(self, session_id: str) -> AsyncGenerator[dict, None]:
        """按版本顺序回放事件（异步生成器）"""
        conn = await self._get_conn()
        cursor = await conn.execute(
            "SELECT * FROM events WHERE session_id = ? ORDER BY version ASC",
            (session_id,),
        )
        while True:
            row = await cursor.fetchone()
            if row is None:
                break
            yield self._row_to_event(row)

    async def get_latest_version(self, session_id: str) -> int:
        """获取会话的最新版本号"""
        conn = await self._get_conn()
        cursor = await conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM events WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def get_event_count(self, session_id: str) -> int:
        """获取会话的事件总数"""
        conn = await self._get_conn()
        cursor = await conn.execute(
            "SELECT COUNT(*) FROM events WHERE session_id = ?", (session_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    # ── 辅助方法 ──

    @staticmethod
    def _row_to_event(row: aiosqlite.Row) -> dict:
        return {
            "id": row["id"],
            "session_id": row["session_id"],
            "type": row["type"],
            "data": json.loads(row["data"]),
            "version": row["version"],
            "timestamp": row["timestamp"],
            "source_node": row["source_node"],
            "parent_event_id": row["parent_event_id"],
        }

    async def clear_session(self, session_id: str):
        """清空指定会话的事件（仅用于测试）"""
        conn = await self._get_conn()
        await conn.execute("DELETE FROM events WHERE session_id = ?", (session_id,))
        await conn.commit()

    async def clear_all(self):
        """清空所有事件（仅用于测试）"""
        conn = await self._get_conn()
        await conn.execute("DELETE FROM events")
        await conn.commit()
