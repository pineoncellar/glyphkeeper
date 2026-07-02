# -*- coding: utf-8 -*-
"""
@File     :   read_models.py
@Desc     :   静态读模型存储 — CQRS 读端，含 locations/interactables/entities/clue_discoveries/knowledge_registry
@Note     :   摄入期由 StateProjector 唯一写入，运行时只读；不可变世界蓝图
"""

from __future__ import annotations

import uuid
import json
from datetime import datetime, timezone
from typing import Any, Optional

from src.tools import get_logger

logger = get_logger(__name__)


class StaticReadStore:
    """静态读模型存储

    管理一组只读表：所有写入在模组摄入期由 StateProjector 完成，
    运行时仅执行 SELECT 查询。这些表是不可变的世界蓝图。
    每张表含 world_id 列用于多世界隔离。
    """

    def __init__(self, world_id: str = ""):
        self._conn = None
        self._world_id = world_id

    # ── 连接管理 ──

    async def _get_conn(self):
        """获取 asyncpg 连接（与 EventStore 共用 PgManager）"""
        if self._conn and not self._conn.is_closed():
            return self._conn
        from src.tools.pg_manager import PgManager
        mgr = await PgManager.get_instance()
        if mgr.available:
            await mgr.start()
            from src.memory.event_store import create_event_store
            es = await create_event_store()
            self._conn = await es._get_conn()
        else:
            raise RuntimeError("pgembed 不可用")
        await self._init_db()
        return self._conn

    async def close(self):
        if self._conn and not self._conn.is_closed():
            await self._conn.close()
            self._conn = None

    # ── 建表 ──

    async def _init_db(self):
        """幂等地创建所有静态读模型表，含 world_id 列和索引"""
        conn = await self._get_conn()

        # 场景拓扑表 — 房间定义、出口映射、氛围标签
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS locations (
                id UUID PRIMARY KEY,
                key TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                base_desc TEXT NOT NULL DEFAULT '',
                tags TEXT[] DEFAULT '{}',
                exits_json JSONB DEFAULT '{}'::jsonb,
                world_id TEXT NOT NULL DEFAULT ''
            )
        """)

        # 可交互物品表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS interactables (
                id UUID PRIMARY KEY,
                key TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                location_id UUID REFERENCES locations(id),
                tags TEXT[] DEFAULT '{}',
                state TEXT DEFAULT '',
                world_id TEXT NOT NULL DEFAULT ''
            )
        """)

        # NPC/实体表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS entities (
                id UUID PRIMARY KEY,
                key TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                location_id UUID REFERENCES locations(id),
                tags TEXT[] DEFAULT '{}',
                stats_json JSONB DEFAULT '{}'::jsonb,
                world_id TEXT NOT NULL DEFAULT ''
            )
        """)

        # 知识注册表
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_registry (
                id UUID PRIMARY KEY,
                knowledge_id TEXT UNIQUE NOT NULL,
                rag_key TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                tags_granted TEXT[] DEFAULT '{}',
                world_id TEXT NOT NULL DEFAULT ''
            )
        """)

        # 多对多线索映射
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS clue_discoveries (
                id UUID PRIMARY KEY,
                interactable_id UUID REFERENCES interactables(id),
                entity_key TEXT,
                knowledge_id UUID REFERENCES knowledge_registry(id),
                required_check JSONB DEFAULT '{}'::jsonb,
                flavor_text TEXT NOT NULL DEFAULT '',
                world_id TEXT NOT NULL DEFAULT ''
            )
        """)

        # 为已有表追加 world_id 列（幂等迁移）
        for tbl in ("locations", "interactables", "entities", "knowledge_registry", "clue_discoveries"):
            col = await conn.fetchval(f"""
                SELECT data_type FROM information_schema.columns
                WHERE table_name='{tbl}' AND column_name='world_id'
            """)
            if not col:
                logger.info(f"read_models: 为 {tbl} 追加 world_id 列...")
                await conn.execute(f"ALTER TABLE {tbl} ADD COLUMN world_id TEXT NOT NULL DEFAULT ''")

        # 索引加速运行时查询
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_clue_interactable
            ON clue_discoveries(interactable_id)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_clue_entity
            ON clue_discoveries(entity_key)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_interactable_location
            ON interactables(location_id)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_entity_location
            ON entities(location_id)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_locations_world
            ON locations(world_id, key)
        """)

        logger.debug("static_read_store: 读模型表已就绪")

    # ── 清除 — 测试或重摄入时调用 ──

    async def clear_all(self, world_id: str = ""):
        """清空所有读模型表（保持表结构）

        world_id 非空时只清空指定世界的数据。
        """
        conn = await self._get_conn()
        if world_id:
            await conn.execute("DELETE FROM clue_discoveries WHERE world_id = $1", world_id)
            await conn.execute("DELETE FROM knowledge_registry WHERE world_id = $1", world_id)
            await conn.execute("DELETE FROM entities WHERE world_id = $1", world_id)
            await conn.execute("DELETE FROM interactables WHERE world_id = $1", world_id)
            await conn.execute("DELETE FROM locations WHERE world_id = $1", world_id)
            logger.info(f"static_read_store: 已清空世界 {world_id} 的读模型表")
        else:
            await conn.execute("DELETE FROM clue_discoveries")
            await conn.execute("DELETE FROM knowledge_registry")
            await conn.execute("DELETE FROM entities")
            await conn.execute("DELETE FROM interactables")
            await conn.execute("DELETE FROM locations")
            logger.info("static_read_store: 已清空所有读模型表")

    # ── 批量写入 — 仅在摄入期由 StateProjector 调用 ──

    async def bulk_insert_locations(self, locations: list[dict], world_id: str = "") -> dict[str, str]:
        """批量插入场景，返回 {key: actual_db_id} 映射

        重新摄入时，已有场景的 key 不变但 ID 可能不同。
        此映射确保外键（interactables/entities 的 location_id）指向正确的 DB 行。
        """
        conn = await self._get_conn()
        wid = world_id or self._world_id
        id_map: dict[str, str] = {}
        for loc in locations:
            lid = loc.get("id", str(uuid.uuid4()))
            try:
                existing = await conn.fetchval(
                    "SELECT id FROM locations WHERE key = $1 AND world_id = $2", loc["key"], wid
                )
                if existing:
                    id_map[loc["key"]] = existing
                    continue
                await conn.execute(
                    """INSERT INTO locations (id, key, name, base_desc, tags, exits_json, world_id)
                       VALUES ($1,$2,$3,$4,$5::text[],$6::jsonb,$7)""",
                    lid, loc["key"], loc["name"], loc.get("base_desc", ""),
                    loc.get("tags", []),
                    json.dumps(loc.get("exits", {}), ensure_ascii=False),
                    wid,
                )
                id_map[loc["key"]] = lid
            except Exception as e:
                logger.warning(f"插入场景失败 ({loc.get('key')}): {e}")
        return id_map

    async def bulk_insert_interactables(self, interactables: list[dict], world_id: str = "") -> int:
        """批量插入物品，key 冲突时跳过（幂等，外键用 location_id，无需返回映射）"""
        conn = await self._get_conn()
        wid = world_id or self._world_id
        count = 0
        for item in interactables:
            iid = item.get("id", str(uuid.uuid4()))
            try:
                await conn.execute(
                    """INSERT INTO interactables (id, key, name, location_id, tags, state, world_id)
                       VALUES ($1,$2,$3,$4,$5::text[],$6,$7)
                       ON CONFLICT (key) DO NOTHING""",
                    iid, item["key"], item["name"],
                    item.get("location_id"),
                    item.get("tags", []),
                    item.get("state", ""),
                    wid,
                )
                count += 1
            except Exception as e:
                logger.warning(f"插入物品失败 ({item.get('key')}): {e}")
        return count

    async def bulk_insert_entities(self, entities: list[dict], world_id: str = "") -> int:
        """批量插入实体（NPC），key 冲突时跳过（幂等）"""
        conn = await self._get_conn()
        wid = world_id or self._world_id
        count = 0
        for ent in entities:
            eid = ent.get("id", str(uuid.uuid4()))
            try:
                existing = await conn.fetchval(
                    "SELECT id FROM entities WHERE key = $1 AND world_id = $2", ent["key"], wid
                )
                if existing:
                    count += 1
                    continue
                await conn.execute(
                    """INSERT INTO entities (id, key, name, location_id, tags, stats_json, world_id)
                       VALUES ($1,$2,$3,$4,$5::text[],$6::jsonb,$7)""",
                    eid, ent["key"], ent["name"],
                    ent.get("location_id"),
                    ent.get("tags", []),
                    json.dumps(ent.get("stats", {}), ensure_ascii=False),
                    wid,
                )
                count += 1
            except Exception as e:
                logger.warning(f"插入实体失败 ({ent.get('key')}): {e}")
        return count

    async def bulk_insert_knowledge(self, knowledge_list: list[dict], world_id: str = "") -> int:
        """批量插入知识注册表，knowledge_id 冲突时跳过"""
        conn = await self._get_conn()
        wid = world_id or self._world_id
        count = 0
        for k in knowledge_list:
            kid = k.get("id", str(uuid.uuid4()))
            try:
                await conn.execute(
                    """INSERT INTO knowledge_registry (id, knowledge_id, rag_key, description, tags_granted, world_id)
                       VALUES ($1,$2,$3,$4,$5::text[],$6)
                       ON CONFLICT (knowledge_id) DO NOTHING""",
                    kid, k["knowledge_id"], k.get("rag_key", ""),
                    k.get("description", ""), k.get("tags_granted", []),
                    wid,
                )
                count += 1
            except Exception as e:
                logger.warning(f"插入知识失败 ({k.get('knowledge_id')}): {e}")
        return count

    async def bulk_insert_clues(self, clues: list[dict], world_id: str = "") -> int:
        """批量插入线索映射"""
        conn = await self._get_conn()
        wid = world_id or self._world_id
        count = 0
        for c in clues:
            cid = c.get("id", str(uuid.uuid4()))
            try:
                await conn.execute(
                    """INSERT INTO clue_discoveries
                       (id, interactable_id, entity_key, knowledge_id, required_check, flavor_text, world_id)
                       VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7)""",
                    cid, c.get("interactable_id"), c.get("entity_key"),
                    c.get("knowledge_id"),
                    json.dumps(c.get("required_check", {}), ensure_ascii=False),
                    c.get("flavor_text", ""),
                    wid,
                )
                count += 1
            except Exception as e:
                logger.warning(f"插入线索失败: {e}")
        return count

    # ── 运行时查询 ──

    async def get_location(self, key: str, world_id: str = "") -> Optional[dict]:
        """按 key 查询场景"""
        conn = await self._get_conn()
        wid = world_id or self._world_id
        if wid:
            row = await conn.fetchrow(
                "SELECT * FROM locations WHERE key=$1 AND world_id=$2", key, wid,
            )
        else:
            row = await conn.fetchrow(
                "SELECT * FROM locations WHERE key=$1", key,
            )
        return dict(row) if row else None

    async def get_all_locations(self, world_id: str = "") -> list[dict]:
        """获取所有场景"""
        conn = await self._get_conn()
        wid = world_id or self._world_id
        if wid:
            rows = await conn.fetch(
                "SELECT * FROM locations WHERE world_id=$1 ORDER BY key", wid,
            )
        else:
            rows = await conn.fetch("SELECT * FROM locations ORDER BY key")
        return [dict(r) for r in rows]

    async def get_interactable(self, key: str, world_id: str = "") -> Optional[dict]:
        """按 key 查询物品"""
        conn = await self._get_conn()
        wid = world_id or self._world_id
        if wid:
            row = await conn.fetchrow(
                "SELECT * FROM interactables WHERE key=$1 AND world_id=$2", key, wid,
            )
        else:
            row = await conn.fetchrow(
                "SELECT * FROM interactables WHERE key=$1", key,
            )
        return dict(row) if row else None

    async def get_interactables_by_location(self, location_key: str, world_id: str = "") -> list[dict]:
        """查询某个场景下的所有物品"""
        conn = await self._get_conn()
        wid = world_id or self._world_id
        if wid:
            rows = await conn.fetch(
                """SELECT i.* FROM interactables i
                   JOIN locations l ON i.location_id = l.id
                   WHERE l.key = $1 AND l.world_id = $2""",
                location_key, wid,
            )
        else:
            rows = await conn.fetch(
                """SELECT i.* FROM interactables i
                   JOIN locations l ON i.location_id = l.id
                   WHERE l.key = $1""",
                location_key,
            )
        return [dict(r) for r in rows]

    async def get_entities_by_location(self, location_key: str, world_id: str = "") -> list[dict]:
        """查询某个场景下的所有 NPC 实体"""
        conn = await self._get_conn()
        wid = world_id or self._world_id
        if wid:
            rows = await conn.fetch(
                """SELECT e.* FROM entities e
                   JOIN locations l ON e.location_id = l.id
                   WHERE l.key = $1 AND l.world_id = $2""",
                location_key, wid,
            )
        else:
            rows = await conn.fetch(
                """SELECT e.* FROM entities e
                   JOIN locations l ON e.location_id = l.id
                   WHERE l.key = $1""",
                location_key,
            )
        return [dict(r) for r in rows]

    async def get_clues_for_interactable(self, interactable_key: str, world_id: str = "") -> list[dict]:
        """查询某个物品关联的所有线索"""
        conn = await self._get_conn()
        wid = world_id or self._world_id
        if wid:
            rows = await conn.fetch(
                """SELECT cd.*, kr.knowledge_id, kr.description, kr.tags_granted
                   FROM clue_discoveries cd
                   JOIN interactables i ON cd.interactable_id = i.id
                   LEFT JOIN knowledge_registry kr ON cd.knowledge_id = kr.id
                   WHERE i.key = $1 AND cd.world_id = $2""",
                interactable_key, wid,
            )
        else:
            rows = await conn.fetch(
                """SELECT cd.*, kr.knowledge_id, kr.description, kr.tags_granted
                   FROM clue_discoveries cd
                   JOIN interactables i ON cd.interactable_id = i.id
                   LEFT JOIN knowledge_registry kr ON cd.knowledge_id = kr.id
                   WHERE i.key = $1""",
                interactable_key,
            )
        return [dict(r) for r in rows]

    async def get_clues_for_interactable_name(self, name: str, world_id: str = "") -> list[dict]:
        """按物品名查线索——降级用，玩家输入 target 通常是中文名而非系统 key"""
        conn = await self._get_conn()
        wid = world_id or self._world_id
        if wid:
            rows = await conn.fetch(
                """SELECT cd.*, kr.knowledge_id, kr.description, kr.tags_granted
                   FROM clue_discoveries cd
                   JOIN interactables i ON cd.interactable_id = i.id
                   LEFT JOIN knowledge_registry kr ON cd.knowledge_id = kr.id
                   WHERE i.name = $1 AND cd.world_id = $2""",
                name, wid,
            )
        else:
            rows = await conn.fetch(
                """SELECT cd.*, kr.knowledge_id, kr.description, kr.tags_granted
                   FROM clue_discoveries cd
                   JOIN interactables i ON cd.interactable_id = i.id
                   LEFT JOIN knowledge_registry kr ON cd.knowledge_id = kr.id
                   WHERE i.name = $1""",
                name,
            )
        return [dict(r) for r in rows]

    async def get_clues_for_entity(self, entity_key: str, world_id: str = "") -> list[dict]:
        """查询某个 NPC 关联的所有线索"""
        conn = await self._get_conn()
        wid = world_id or self._world_id
        if wid:
            rows = await conn.fetch(
                """SELECT cd.*, kr.knowledge_id, kr.description, kr.tags_granted
                   FROM clue_discoveries cd
                   LEFT JOIN knowledge_registry kr ON cd.knowledge_id = kr.id
                   WHERE cd.entity_key = $1 AND cd.world_id = $2""",
                entity_key, wid,
            )
        else:
            rows = await conn.fetch(
                """SELECT cd.*, kr.knowledge_id, kr.description, kr.tags_granted
                   FROM clue_discoveries cd
                   LEFT JOIN knowledge_registry kr ON cd.knowledge_id = kr.id
                   WHERE cd.entity_key = $1""",
                entity_key,
            )
        return [dict(r) for r in rows]

    async def get_knowledge(self, knowledge_id: str, world_id: str = "") -> Optional[dict]:
        """按 knowledge_id 查询知识"""
        conn = await self._get_conn()
        wid = world_id or self._world_id
        if wid:
            row = await conn.fetchrow(
                "SELECT * FROM knowledge_registry WHERE knowledge_id=$1 AND world_id=$2",
                knowledge_id, wid,
            )
        else:
            row = await conn.fetchrow(
                "SELECT * FROM knowledge_registry WHERE knowledge_id=$1",
                knowledge_id,
            )
        return dict(row) if row else None

    async def get_all_knowledge(self, world_id: str = "") -> list[dict]:
        """获取所有注册知识"""
        conn = await self._get_conn()
        wid = world_id or self._world_id
        if wid:
            rows = await conn.fetch(
                "SELECT * FROM knowledge_registry WHERE world_id=$1 ORDER BY knowledge_id", wid,
            )
        else:
            rows = await conn.fetch("SELECT * FROM knowledge_registry ORDER BY knowledge_id")
        return [dict(r) for r in rows]
