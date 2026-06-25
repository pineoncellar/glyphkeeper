"""
@File     :   snapshot.py
@Desc     :   状态快照管理 — 快速恢复与时间线回溯
@Note     :   快照是 GameState 的完整序列化副本，避免每次恢复都回放全部事件

职责:
  - 定期创建游戏状态的完整快照
  - 支持基于快照的快速恢复
  - 管理快照版本与过期策略
  - 提供时间线回溯能力

使用方式:
    mgr = SnapshotManager(event_store)
    snap_id = await mgr.create(state)
    restored = await mgr.restore(snap_id)
"""

from __future__ import annotations

import json
import uuid
import aiosqlite
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional
from src.state.game_state import GameState, create_initial_state
from src.state.reducer import apply_events_to_state
from src.memory.event_store import EventStore
from src.tools import PROJECT_ROOT


# 快照保留策略
MAX_SNAPSHOTS_PER_SESSION = 20       # 每个会话最多保留的快照数
DEFAULT_SNAPSHOT_DIR = PROJECT_ROOT / "data" / "snapshots"


class SnapshotManager:
    """
    状态快照管理器。

    快照存储为 SQLite 表中的 JSON 行，同时每个快照记录
    对应的事件版本号，支持基于事件回放的增量恢复。
    """

    def __init__(
        self,
        event_store: Optional[EventStore] = None,
        db_path: Optional[str] = None,
    ):
        self._event_store = event_store
        if db_path is None:
            db_path = str(DEFAULT_SNAPSHOT_DIR / "snapshots.db")
        self._db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    # ── 连接管理 ──

    async def _get_conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            # 确保目录存在
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(self._db_path)
            self._conn.row_factory = aiosqlite.Row
            await self._init_db()
        return self._conn

    async def _init_db(self):
        conn = await self._get_conn()
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                state_json TEXT NOT NULL,
                event_version INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                label TEXT DEFAULT ''
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_snapshots_session
            ON snapshots(session_id, version DESC)
        """)
        await conn.commit()

    async def close(self):
        """关闭数据库连接"""
        if self._conn:
            await self._conn.close()
            self._conn = None

    # ── 核心操作 ──

    async def create(
        self,
        state: GameState,
        label: str = "",
    ) -> str:
        """
        创建当前状态的快照。

        参数:
          state: 当前游戏状态
          label: 可选的快照标签（如 "before_combat", "checkpoint_1"）

        返回:
          snapshot_id: 快照 UUID
        """
        conn = await self._get_conn()
        session_id = state.get("session_id", "")
        snapshot_id = str(uuid.uuid4())

        # 获取当前版本号
        next_version = await self._get_next_version(session_id)

        # 获取事件版本
        event_version = 0
        if self._event_store:
            event_version = await self._event_store.get_latest_version(session_id)

        # 序列化 state（处理不可 JSON 序列化的类型）
        state_json = json.dumps(state, ensure_ascii=False, default=str)

        await conn.execute(
            """INSERT INTO snapshots (id, session_id, version, state_json, event_version, created_at, label)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot_id,
                session_id,
                next_version,
                state_json,
                event_version,
                datetime.now(timezone.utc).isoformat(),
                label,
            ),
        )
        await conn.commit()

        # 清理过期快照
        await self._enforce_retention(session_id)

        return snapshot_id

    async def restore(self, snapshot_id: str) -> Optional[GameState]:
        """
        从快照恢复状态。

        如果提供了 EventStore，会尝试回放快照之后的新事件，
        实现"快照 + 增量事件"的恢复策略。
        """
        conn = await self._get_conn()
        cursor = await conn.execute(
            "SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None

        state: GameState = json.loads(row["state_json"])
        event_version = row["event_version"]
        session_id = row["session_id"]

        # 如果有 EventStore，回放快照之后的新事件
        if self._event_store:
            new_events = await self._event_store.get_events(
                session_id, since_version=event_version
            )
            if new_events:
                state = apply_events_to_state(state, new_events)

        return state

    async def list_snapshots(
        self, session_id: str, limit: int = 10
    ) -> list[dict]:
        """列出会话的快照列表（按版本降序）"""
        conn = await self._get_conn()
        cursor = await conn.execute(
            """SELECT id, session_id, version, event_version, created_at, label
               FROM snapshots WHERE session_id = ?
               ORDER BY version DESC LIMIT ?""",
            (session_id, limit),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def delete(self, snapshot_id: str) -> bool:
        """删除指定快照"""
        conn = await self._get_conn()
        cursor = await conn.execute(
            "DELETE FROM snapshots WHERE id = ?", (snapshot_id,)
        )
        await conn.commit()
        return cursor.rowcount > 0

    async def get_latest(self, session_id: str) -> Optional[dict]:
        """获取会话的最新快照"""
        conn = await self._get_conn()
        cursor = await conn.execute(
            """SELECT * FROM snapshots WHERE session_id = ?
               ORDER BY version DESC LIMIT 1""",
            (session_id,),
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        return dict(row)

    # ── 内部方法 ──

    async def _get_next_version(self, session_id: str) -> int:
        conn = await self._get_conn()
        cursor = await conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM snapshots WHERE session_id = ?",
            (session_id,),
        )
        row = await cursor.fetchone()
        return (row[0] if row else 0) + 1

    async def _enforce_retention(self, session_id: str):
        """删除超出保留数量的旧快照"""
        conn = await self._get_conn()
        # 获取应保留的最小版本号
        cursor = await conn.execute(
            """SELECT version FROM snapshots WHERE session_id = ?
               ORDER BY version DESC LIMIT 1 OFFSET ?""",
            (session_id, MAX_SNAPSHOTS_PER_SESSION - 1),
        )
        row = await cursor.fetchone()
        if row:
            min_keep = row[0]
            await conn.execute(
                "DELETE FROM snapshots WHERE session_id = ? AND version < ?",
                (session_id, min_keep),
            )
            await conn.commit()
