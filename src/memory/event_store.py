# -*- coding: utf-8 -*-
"""
@File     :   event_store.py
@Desc     :   事件溯源存储 — 基于 pgembed 嵌入式 PostgreSQL 的不可变事件流
@Note     :   使用 asyncpg + JSONB，通过 PgManager 连接池共享连接
              append 写入时自动维护 version 递增。
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
    """事件溯源存储 — 基于 asyncpg + JSONB（PgManager 连接池版）

    append 写入不可变事件流，get_events/replay 按 version 升序读取。
    所有连接通过 PgManager 连接池管理，不自持独立连接。
    """

    def __init__(self):
        self._conn = None
        self._inited = False

    # ------- 连接管理 -------

    async def _get_conn(self):
        """从 PgManager 连接池获取连接，延迟建表"""
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
        """幂等地创建 events 表（以 world_id 为主键域，无 session_id）"""
        conn = await self._get_conn()

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id UUID PRIMARY KEY,
                type TEXT NOT NULL,
                data JSONB NOT NULL,
                version INTEGER NOT NULL,
                timestamp TIMESTAMPTZ NOT NULL,
                source_node TEXT DEFAULT '',
                parent_event_id TEXT,
                world_id TEXT NOT NULL DEFAULT ''
            )
        """)

        # 事件流索引：按 world_id + version 查询
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_events_world_version
            ON events(world_id, version)
        """)

    async def close(self):
        """归还连接到 PgManager 连接池"""
        if self._conn and not self._conn.is_closed():
            from src.tools.pg_manager import PgManager
            mgr = await PgManager.get_instance()
            await mgr.release_conn(self._conn)
            self._conn = None

    # ------- 核心读写 -------

    async def append(
        self,
        world_id: str,
        event_type: str,
        data: dict,
        source_node: str = "",
        parent_event_id: Optional[str] = None,
    ) -> dict:
        """追加一条新事件到事件流，自动递增 version

        world_id 标识所属世界（存储层唯一键），session_id 已移除。
        """
        conn = await self._get_conn()
        version = await self.get_latest_version(world_id) + 1
        now = datetime.now(timezone.utc)

        event = {
            "id": str(uuid.uuid4()),
            "type": event_type,
            "data": data,
            "version": version,
            "timestamp": now.isoformat(),
            "source_node": source_node,
            "parent_event_id": parent_event_id,
            "world_id": world_id,
        }

        await conn.execute(
            """INSERT INTO events (id, type, data, version, timestamp, source_node, parent_event_id, world_id)
               VALUES ($1, $2, $3::jsonb, $4, $5::timestamptz, $6, $7, $8)""",
            event["id"], event["type"],
            json.dumps(event["data"], ensure_ascii=False, default=str),
            event["version"], now,
            event["source_node"], event["parent_event_id"],
            event["world_id"],
        )
        return event

    async def get_events(
        self, world_id: str, since_version: int = 0,
    ) -> list[dict]:
        """获取指定世界的事件流（按 version 升序）"""
        conn = await self._get_conn()
        rows = await conn.fetch(
            "SELECT * FROM events WHERE world_id = $1 AND version > $2 ORDER BY version ASC",
            world_id, since_version,
        )
        return [self._row_to_event(row) for row in rows]

    async def get_events_range(
        self, world_id: str, up_to_version: int,
    ) -> list[dict]:
        """获取指定世界在 target_version 之前（含）的事件，用于回档重建"""
        conn = await self._get_conn()
        rows = await conn.fetch(
            "SELECT * FROM events WHERE world_id = $1 AND version <= $2 ORDER BY version ASC",
            world_id, up_to_version,
        )
        return [self._row_to_event(row) for row in rows]

    async def replay(self, world_id: str) -> AsyncGenerator[dict, None]:
        """按 version 顺序回放指定世界的事件（异步生成器）"""
        conn = await self._get_conn()
        rows = await conn.fetch(
            "SELECT * FROM events WHERE world_id = $1 ORDER BY version ASC",
            world_id,
        )
        for row in rows:
            yield self._row_to_event(row)

    async def get_latest_version(self, world_id: str) -> int:
        """获取指定世界的最新 version 号"""
        conn = await self._get_conn()
        val = await conn.fetchval(
            "SELECT COALESCE(MAX(version), 0) FROM events WHERE world_id = $1",
            world_id,
        )
        return val or 0

    async def get_event_count(self, world_id: str) -> int:
        """获取指定世界的事件总数"""
        conn = await self._get_conn()
        val = await conn.fetchval(
            "SELECT COUNT(*) FROM events WHERE world_id = $1", world_id,
        )
        return val or 0

    # ------- 辅助方法 -------

    @staticmethod
    def _row_to_event(row) -> dict:
        return {
            "id": str(row["id"]),
            "type": row["type"],
            "data": row["data"] if isinstance(row["data"], dict) else json.loads(row["data"]),
            "version": row["version"],
            "timestamp": row["timestamp"].isoformat() if hasattr(row["timestamp"], 'isoformat') else row["timestamp"],
            "source_node": row["source_node"],
            "parent_event_id": str(row["parent_event_id"]) if row["parent_event_id"] else None,
            "world_id": str(row.get("world_id", "")),
        }

    async def delete_module_events(self, module_name: str) -> bool:
        """删除指定模名的所有事件（用于 /module delete）

        从 __seed__ world 中删除 data->>'module_name' 匹配的事件。
        """
        conn = await self._get_conn()
        result = await conn.execute(
            "DELETE FROM events WHERE world_id LIKE '__seed__%' AND data->>'module_name' = $1",
            module_name,
        )
        affected = result.split()[1] if "DELETE" in result else "0"
        logger.info("delete_module_events: 已删除 %s 条事件 (module=%s)", affected, module_name)
        return int(affected) > 0

    async def clear_world(self, world_id: str):
        """清空指定世界的事件（仅用于测试）"""
        conn = await self._get_conn()
        await conn.execute("DELETE FROM events WHERE world_id = $1", world_id)

    async def clear_all(self):
        """清空所有事件（仅用于测试）"""
        conn = await self._get_conn()
        await conn.execute("DELETE FROM events")


# ====================================================================
# 工厂函数 — PG 可用时直接返回 EventStore，无需区分两个类
# ====================================================================


async def create_event_store() -> EventStore:
    """创建 EventStore 实例（连接统一走 PgManager 连接池）

    PG 可用时返回 EventStore，否则抛异常让调用方处理。
    不再需要区分 EventStore 与 AsyncPgEventStore — 两者已合并。
    """
    from src.tools.pg_manager import PgManager
    mgr = await PgManager.get_instance()
    if mgr.available:
        await mgr.start()
        return EventStore()
    raise RuntimeError("pgembed 不可用")
