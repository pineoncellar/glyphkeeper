# -*- coding: utf-8 -*-
"""
@File     :   session_state.py
@Desc     :   动态状态表 — 追踪玩家会话中已发现的知识（session_knowledge_state）
@Note     :   运行时由 StateProjector 投影写入，与 EventStore 事务解耦
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from src.tools import get_logger

logger = get_logger(__name__)


class SessionKnowledgeState:
    """动态状态存储 — 追踪玩家会话中已发现的知识（PgManager 连接池版）

    运行时由 ClueDiscovered 事件投影写入，采用尽力而为模式。
    投影失败不影响事件存储本身，只影响后续防剧透过滤的精确性。
    """

    def __init__(self):
        self._conn = None
        self._inited = False

    # ── 连接管理 ──

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

    async def close(self):
        if self._conn and not self._conn.is_closed():
            from src.tools.pg_manager import PgManager
            mgr = await PgManager.get_instance()
            await mgr.release_conn(self._conn)
            self._conn = None

    # ── 建表 ──

    async def _init_db(self):
        """创建 session_knowledge_state 表（幂等）"""
        conn = await self._get_conn()

        await conn.execute("""
            CREATE TABLE IF NOT EXISTS session_knowledge_state (
                id UUID PRIMARY KEY,
                world_id TEXT NOT NULL,
                character_name TEXT NOT NULL DEFAULT '',
                knowledge_id TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'auto',
                discovered_at TIMESTAMPTZ NOT NULL
            )
        """)

        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sks_world
            ON session_knowledge_state(world_id, knowledge_id)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sks_world_source
            ON session_knowledge_state(world_id, source)
        """)

        logger.debug("session_knowledge_state: 表已就绪")

    # ── 写入 ──

    async def record_discovery(
        self,
        world_id: str,
        knowledge_id: str,
        source: str = "auto",
        character_name: str = "",
    ) -> bool:
        """记录一条知识发现，重复记录会被自动跳过（幂等）

        world_id 决定所属世界，source 区分 examine/dialogue/auto 三种来源。
        """
        try:
            conn = await self._get_conn()
            existing = await conn.fetchval(
                """SELECT COUNT(*) FROM session_knowledge_state
                   WHERE world_id=$1 AND knowledge_id=$2""",
                world_id, knowledge_id,
            )
            if existing and existing > 0:
                logger.debug(f"知识已记录 (跳过重复): {knowledge_id}")
                return True

            await conn.execute(
                """INSERT INTO session_knowledge_state
                   (id, world_id, character_name, knowledge_id, source, discovered_at)
                   VALUES ($1,$2,$3,$4,$5,$6::timestamptz)""",
                str(uuid.uuid4()), world_id, character_name,
                knowledge_id, source, datetime.now(timezone.utc),
            )
            logger.info(f"知识已记录: world={world_id} knowledge={knowledge_id} source={source}")
            return True
        except Exception as e:
            logger.warning(f"知识记录失败 (不影响主流程): {e}")
            return False

    # ── 查询 ──

    async def get_discovered(self, world_id: str) -> list[dict]:
        """获取指定世界中所有已发现的知识"""
        conn = await self._get_conn()
        rows = await conn.fetch(
            """SELECT * FROM session_knowledge_state
               WHERE world_id=$1
               ORDER BY discovered_at ASC""",
            world_id,
        )
        return [dict(r) for r in rows]

    async def get_discovered_knowledge_ids(self, world_id: str) -> list[str]:
        """获取指定世界中已发现的知识 ID 列表"""
        conn = await self._get_conn()
        rows = await conn.fetch(
            "SELECT knowledge_id FROM session_knowledge_state WHERE world_id=$1",
            world_id,
        )
        return [r["knowledge_id"] for r in rows]

    async def has_discovered(self, world_id: str, knowledge_id: str) -> bool:
        """检查某条知识是否已被发现"""
        conn = await self._get_conn()
        val = await conn.fetchval(
            """SELECT COUNT(*) FROM session_knowledge_state
               WHERE world_id=$1 AND knowledge_id=$2""",
            world_id, knowledge_id,
        )
        return bool(val and val > 0)

    async def get_discovered_by_source(self, world_id: str, source: str) -> list[dict]:
        """按来源筛选已发现的知识"""
        conn = await self._get_conn()
        rows = await conn.fetch(
            """SELECT * FROM session_knowledge_state
               WHERE world_id=$1 AND source=$2
               ORDER BY discovered_at ASC""",
            world_id, source,
        )
        return [dict(r) for r in rows]

    # ── 清除 ──

    async def clear_world(self, world_id: str):
        """清除某个世界的所有记录（测试/重置用）"""
        conn = await self._get_conn()
        await conn.execute(
            "DELETE FROM session_knowledge_state WHERE world_id=$1",
            world_id,
        )
        logger.info(f"已清除 world {world_id} 的所有知识记录")

    async def restore_from_ids(
        self,
        world_id: str,
        knowledge_ids: list[str],
        character_name: str = "",
    ) -> int:
        """清除世界现有记录后批量恢复知识 ID，供读档和回滚重建使用"""
        await self.clear_world(world_id)
        if not knowledge_ids:
            return 0
        conn = await self._get_conn()
        now = datetime.now(timezone.utc)
        count = 0
        for kid in knowledge_ids:
            try:
                await conn.execute(
                    """INSERT INTO session_knowledge_state
                       (id, world_id, character_name, knowledge_id, source, discovered_at)
                       VALUES ($1,$2,$3,$4,$5,$6::timestamptz)""",
                    str(uuid.uuid4()), world_id, character_name,
                    kid, "archive", now,
                )
                count += 1
            except Exception as e:
                logger.warning(f"批量恢复知识跳过 (kid={kid}): {e}")
        logger.info(
            f"知识恢复: session={session_id[:8]} "
            f"count={count}/{len(knowledge_ids)}"
        )
        return count

    async def clear_all(self):
        conn = await self._get_conn()
        await conn.execute("DELETE FROM session_knowledge_state")
