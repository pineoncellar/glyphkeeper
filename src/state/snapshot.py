"""
@File     :   snapshot.py
@Desc     :   状态快照管理 — 原子存档/读档
@Note     :   snapshots 表以 world_id 为主键域，不再使用 session_id。
              save_checkpoints 表记录存档时刻的知识状态和触发器状态，
              读档时通过 ACID 事务一次性恢复全部动态行。

使用方式:
    mgr = SnapshotManager(event_store)
    snap_id = await mgr.create_atomic(state, world_id)    # 原子存档
    restored = await mgr.restore_atomic(snap_id, world_id)  # 原子读档
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

MAX_SNAPSHOTS_PER_WORLD = 20


class SnapshotManager:
    """状态快照管理器 — 基于 asyncpg + JSONB（原子存档/读档版）

    snapshots 表:
      (world_id, snapshot_id) 复合主键。snapshot_id 由 create_atomic 生成。
    save_checkpoints 表:
      存档时刻的知识状态和触发器运行时的完整快照。
    """

    def __init__(self, event_store=None):
        self._event_store = event_store
        self._conn = None
        self._inited = False

    # ------- 连接管理 -------

    async def _get_conn(self):
        if self._conn and not self._conn.is_closed():
            return self._conn
        from src.tools.pg_manager import PgManager
        mgr = await PgManager.get_instance()
        if not mgr.available:
            raise RuntimeError("pgembed 不可用")
        await mgr.start()
        self._conn = await mgr.get_conn()
        if not self._inited:
            await self._init_db()
            self._inited = True
        return self._conn

    async def _init_db(self):
        conn = await self._get_conn()
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                world_id TEXT NOT NULL,
                snapshot_id UUID NOT NULL,
                version INTEGER NOT NULL,
                state_json JSONB NOT NULL,
                event_version INTEGER NOT NULL,
                created_at TIMESTAMPTZ NOT NULL,
                label TEXT DEFAULT '',
                PRIMARY KEY (world_id, snapshot_id)
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_snapshots_world
            ON snapshots(world_id, version DESC)
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS save_checkpoints (
                snapshot_id UUID NOT NULL,
                world_id TEXT NOT NULL,
                event_version INTEGER NOT NULL,
                knowledge_state JSONB NOT NULL DEFAULT '{}'::jsonb,
                trigger_state JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (snapshot_id, world_id)
            )
        """)

    async def close(self):
        if self._conn and not self._conn.is_closed():
            from src.tools.pg_manager import PgManager
            mgr = await PgManager.get_instance()
            await mgr.release_conn(self._conn)
            self._conn = None

    # ------- 原子存档 -------

    async def create_atomic(self, state: dict, world_id: str, label: str = "") -> str:
        """事务内原子存档：GameState + 知识状态 + 触发器状态 + 事件版本

        返回 snapshot_id。存档中途崩溃则自动回滚，不产生垃圾数据。
        """
        conn = await self._get_conn()
        snapshot_id = str(uuid.uuid4())
        next_version = (await self._get_next_version(world_id))
        event_version = (await self._event_store.get_latest_version(world_id)) if self._event_store else 0
        now = datetime.now(timezone.utc)

        # 收集知识状态
        known_ids: list[str] = []
        try:
            sks = SessionKnowledgeState()
            known_ids = await sks.get_discovered_knowledge_ids(world_id)
        except Exception as e:
            logger.debug(f"snapshot: 获取 known_knowledge_ids 失败: {e}")

        # 收集触发器状态
        trigger_states: dict = {}
        try:
            from src.state.read_models import StaticReadStore
            ts_store = StaticReadStore()
            ts_conn = await ts_store._get_conn()
            rows = await ts_conn.fetch(
                "SELECT trigger_id, fired_count, fired_this_turn, is_disabled "
                "FROM session_trigger_state WHERE session_id=$1",
                world_id,
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
        known_json = json.dumps(known_ids, ensure_ascii=False)
        trigger_json = json.dumps(trigger_states, ensure_ascii=False)

        # 事务内写入 snapshots + save_checkpoints
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO snapshots "
                "(world_id,snapshot_id,version,state_json,event_version,created_at,label) "
                "VALUES ($1,$2::uuid,$3,$4::jsonb,$5,$6::timestamptz,$7)",
                world_id, snapshot_id, next_version, state_json,
                event_version, now, label,
            )
            await conn.execute(
                "INSERT INTO save_checkpoints "
                "(snapshot_id,world_id,event_version,knowledge_state,trigger_state,created_at) "
                "VALUES ($1::uuid,$2,$3,$4::jsonb,$5::jsonb,$6::timestamptz)",
                snapshot_id, world_id, event_version,
                known_json, trigger_json, now,
            )
            # 超量快照清理
            stale = await conn.fetchval(
                "SELECT version FROM snapshots WHERE world_id=$1 ORDER BY version DESC LIMIT 1 OFFSET $2",
                world_id, MAX_SNAPSHOTS_PER_WORLD - 1,
            )
            if stale:
                await conn.execute(
                    "DELETE FROM snapshots WHERE world_id=$1 AND version<$2",
                    world_id, stale,
                )

        logger.info(f"snapshot: 原子存档完成 world={world_id[:8]} snap={snapshot_id[:8]} v{next_version}")
        return snapshot_id

    # ------- 原子读档 -------

    async def restore_atomic(self, snapshot_id: str, target_world_id: str) -> Optional[dict]:
        """事务内原子读档：清空目标世界旧数据 → 恢复快照 → COMMIT

        返回 {state, known_knowledge_ids, trigger_states}。
        中途中断则回滚，旧数据不受影响。
        """
        conn = await self._get_conn()

        # 先查快照是否存在
        row = await conn.fetchrow(
            "SELECT * FROM snapshots WHERE snapshot_id=$1::uuid", snapshot_id,
        )
        if row is None:
            return None

        row_world_id = row["world_id"]

        # 查检查点
        cp = await conn.fetchrow(
            "SELECT * FROM save_checkpoints WHERE snapshot_id=$1::uuid", snapshot_id,
        )

        async with conn.transaction():
            # 清空目标世界的旧事件流（仅存档快照后的增量事件）
            if self._event_store:
                await self._event_store.clear_world(target_world_id)

            # 从快照恢复 GameState
            state = json.loads(row["state_json"])
            state["world_id"] = target_world_id

            # 追加重放快照之后、存档之前的新事件（如有）
            if self._event_store and row["event_version"]:
                new_events = await self._event_store.get_events(
                    row_world_id, since_version=row["event_version"],
                )
                if new_events:
                    state = apply_events_to_state(state, new_events)

            # 解析知识状态
            known_ids: list[str] = []
            trigger_states: dict = {}
            if cp:
                kr_raw = cp["knowledge_state"]
                if isinstance(kr_raw, str):
                    known_ids = json.loads(kr_raw)
                elif isinstance(kr_raw, (list, tuple)):
                    known_ids = list(kr_raw)

                ts_raw = cp["trigger_state"]
                if isinstance(ts_raw, str):
                    trigger_states = json.loads(ts_raw)
                elif isinstance(ts_raw, dict):
                    trigger_states = dict(ts_raw)

        # 事务外：恢复 session_knowledge_state 和 trigger_state
        if known_ids:
            try:
                sks = SessionKnowledgeState()
                await sks.restore_from_ids(target_world_id, known_ids)
            except Exception as e:
                logger.warning(f"读档: 恢复知识状态失败: {e}")

        if trigger_states:
            try:
                from src.state.read_models import StaticReadStore
                ts_store = StaticReadStore()
                await ts_store.restore_trigger_states(target_world_id, trigger_states)
            except Exception as e:
                logger.warning(f"读档: 恢复触发器状态失败: {e}")

        logger.info(f"snapshot: 原子读档完成 snap={snapshot_id[:8]} → world={target_world_id[:8]}")
        return {"state": state, "known_knowledge_ids": known_ids, "trigger_states": trigger_states}

    # ------- 旧接口（保留供过渡期使用） -------

    async def create(self, state, label=""):
        """旧版存档 — 使用 world_id 作为 session_id"""
        world_id = state.get("world_id", state.get("session_id", ""))
        return await self.create_atomic(state, world_id, label=label)

    async def restore(self, snapshot_id):
        """旧版读档 — 从 snapshots 表按 id 读取"""
        conn = await self._get_conn()
        row = await conn.fetchrow(
            "SELECT * FROM snapshots WHERE snapshot_id=$1::uuid", snapshot_id,
        )
        if row is None:
            return None
        state = json.loads(row["state_json"])
        return {"state": state, "known_knowledge_ids": [], "trigger_states": {}}

    async def list_snapshots(self, world_id: str, limit: int = 10) -> list[dict]:
        """列出指定世界的所有存档"""
        conn = await self._get_conn()
        rows = await conn.fetch(
            "SELECT snapshot_id,world_id,version,event_version,created_at,label "
            "FROM snapshots WHERE world_id=$1 ORDER BY version DESC LIMIT $2",
            world_id, limit,
        )
        result = []
        for r in rows:
            d = dict(r)
            d["snapshot_id"] = str(d["snapshot_id"])
            d["id"] = d["snapshot_id"]
            result.append(d)
        return result

    async def delete(self, snapshot_id: str) -> bool:
        """删除快照及对应的检查点"""
        conn = await self._get_conn()
        try:
            await conn.execute("DELETE FROM snapshots WHERE snapshot_id=$1::uuid", snapshot_id)
            await conn.execute("DELETE FROM save_checkpoints WHERE snapshot_id=$1::uuid", snapshot_id)
            return True
        except Exception:
            return False

    async def get_latest(self, world_id: str) -> Optional[dict]:
        """获取指定世界的最新快照"""
        conn = await self._get_conn()
        row = await conn.fetchrow(
            "SELECT * FROM snapshots WHERE world_id=$1 ORDER BY version DESC LIMIT 1",
            world_id,
        )
        if row is None:
            return None
        d = dict(row)
        d["snapshot_id"] = str(d["snapshot_id"])
        d["id"] = d["snapshot_id"]
        return d

    async def clear_all(self):
        conn = await self._get_conn()
        await conn.execute("DELETE FROM snapshots")
        await conn.execute("DELETE FROM save_checkpoints")

    async def _get_next_version(self, world_id: str) -> int:
        conn = await self._get_conn()
        val = await conn.fetchval(
            "SELECT COALESCE(MAX(version),0) FROM snapshots WHERE world_id=$1", world_id,
        )
        return (val or 0) + 1
