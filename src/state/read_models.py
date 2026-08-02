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

    async def connect_script(self):
        """脚本模式连接：绕过 PgManager 生命周期，只做纯客户端连接

        供 trigger_inject.py / trigger_seed_modify.py 等外部脚本使用。
        不启动/停止 PG，不初始化表结构。
        """
        from src.tools.pg_manager import get_script_connection
        self._conn = await get_script_connection()
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
                loot_items TEXT[] DEFAULT '{}',
                world_id TEXT NOT NULL DEFAULT ''
            )
        """)

        # 触发器静态注册表 — 摄入期写入种子工作区，/start 时复制到目标世界
        # 复合主键 (trigger_id, world_id) 允许多世界中存在同名触发器但不同定义
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS static_triggers (
                trigger_id      VARCHAR(64) NOT NULL,
                module_name     VARCHAR(64) NOT NULL,
                description     TEXT,
                priority        INT DEFAULT 0,
                is_one_off      BOOLEAN DEFAULT TRUE,
                conditions_json JSONB NOT NULL,
                actions_json    JSONB NOT NULL,
                world_id        TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (trigger_id, world_id)
            )
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_triggers_module
            ON static_triggers(module_name)
        """)
        await conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_triggers_world
            ON static_triggers(world_id)
        """)
        # 为已有行追加 world_id 列（幂等迁移）
        await conn.execute("""
            ALTER TABLE static_triggers
            ADD COLUMN IF NOT EXISTS world_id TEXT NOT NULL DEFAULT ''
        """)

        # 触发器运行时动态状态表 — 跑团会话级
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS session_trigger_state (
                session_id      VARCHAR(64) NOT NULL,
                trigger_id      VARCHAR(64) NOT NULL,
                fired_count     INT DEFAULT 0,
                fired_this_turn INT DEFAULT 0,
                is_disabled     BOOLEAN DEFAULT FALSE,
                last_fired_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (session_id, trigger_id)
            )
        """)

        # 为已有表追加 world_id 列（幂等迁移）
        # 再追加 loot_items 列（幂等，已存在时不报错）
        await conn.execute("""
            ALTER TABLE clue_discoveries
            ADD COLUMN IF NOT EXISTS loot_items TEXT[] DEFAULT '{}'
        """)
        await conn.execute("""
            ALTER TABLE clue_discoveries
            ADD COLUMN IF NOT EXISTS required_item TEXT DEFAULT ''
        """)
        await conn.execute("""
            ALTER TABLE clue_discoveries
            ADD COLUMN IF NOT EXISTS deterministic_changes JSONB DEFAULT '{}'::jsonb
        """)
        logger.debug("read_models: 确保 required_item/deterministic_changes 列存在")
        for tbl in ("locations", "interactables", "entities", "knowledge_registry", "clue_discoveries"):
            col = await conn.fetchval(f"""
                SELECT data_type FROM information_schema.columns
                WHERE table_name='{tbl}' AND column_name='world_id'
            """)
            if not col:
                logger.info(f"read_models: 为 {tbl} 追加 world_id 列...")
                await conn.execute(f"ALTER TABLE {tbl} ADD COLUMN world_id TEXT NOT NULL DEFAULT ''")

        # 将单列 UNIQUE 升级为复合 UNIQUE (key, world_id)，使 copy_static_data_to_world 的 ON CONFLICT 生效
        for tbl, col_name, constraint_name in [
            ("locations", "key", "locations_key_key"),
            ("interactables", "key", "interactables_key_key"),
            ("entities", "key", "entities_key_key"),
            ("knowledge_registry", "knowledge_id", "knowledge_registry_knowledge_id_key"),
        ]:
            try:
                await conn.execute(f"ALTER TABLE {tbl} DROP CONSTRAINT IF EXISTS {constraint_name}")
                await conn.execute(
                    f"ALTER TABLE {tbl} ADD CONSTRAINT {constraint_name} UNIQUE ({col_name}, world_id)"
                )
            except Exception as e:
                logger.debug(f"read_models: 迁移约束 {constraint_name} 跳过 ({e})")
        # clue_discoveries 主键从 (id) 升级为 (id, world_id)，匹配 ON CONFLICT (id, world_id)
        try:
            await conn.execute("ALTER TABLE clue_discoveries DROP CONSTRAINT IF EXISTS clue_discoveries_pkey")
            await conn.execute(
                "ALTER TABLE clue_discoveries ADD PRIMARY KEY (id, world_id)"
            )
        except Exception as e:
            logger.debug(f"read_models: 迁移 clue_discoveries 主键跳过 ({e})")

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
            await conn.execute("DELETE FROM session_trigger_state WHERE session_id = $1", world_id)
            # static_triggers 按 module_name 而非 world_id 隔离
            logger.info(f"static_read_store: 已清空世界 {world_id} 的读模型表")
        else:
            await conn.execute("DELETE FROM clue_discoveries")
            await conn.execute("DELETE FROM knowledge_registry")
            await conn.execute("DELETE FROM entities")
            await conn.execute("DELETE FROM interactables")
            await conn.execute("DELETE FROM locations")
            await conn.execute("DELETE FROM static_triggers")
            await conn.execute("DELETE FROM session_trigger_state")
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
                       ON CONFLICT (key, world_id) DO NOTHING""",
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
                       ON CONFLICT (knowledge_id, world_id) DO NOTHING""",
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
                       (id, interactable_id, entity_key, knowledge_id, required_check,
                        flavor_text, loot_items, required_item, deterministic_changes, world_id)
                       VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7::text[],$8,$9::jsonb,$10)""",
                    cid, c.get("interactable_id"), c.get("entity_key"),
                    c.get("knowledge_id"),
                    json.dumps(c.get("required_check", {}), ensure_ascii=False),
                    c.get("flavor_text", ""),
                    c.get("loot_items", []),
                    c.get("required_item", ""),
                    json.dumps(c.get("deterministic_changes", {}), ensure_ascii=False),
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

    async def get_clues_by_required_item(self, item_name: str, world_id: str = "") -> list[dict]:
        """按 required_item 查询线索——用于背包物品消耗时匹配 use_item 型线索"""
        conn = await self._get_conn()
        wid = world_id or self._world_id
        if wid:
            rows = await conn.fetch(
                """SELECT cd.*, kr.knowledge_id, kr.description, kr.tags_granted
                   FROM clue_discoveries cd
                   LEFT JOIN knowledge_registry kr ON cd.knowledge_id = kr.id
                   WHERE cd.required_item = $1 AND cd.world_id = $2""",
                item_name, wid,
            )
        else:
            rows = await conn.fetch(
                """SELECT cd.*, kr.knowledge_id, kr.description, kr.tags_granted
                   FROM clue_discoveries cd
                   LEFT JOIN knowledge_registry kr ON cd.knowledge_id = kr.id
                   WHERE cd.required_item = $1""",
                item_name,
            )
        return [self._normalize_jsonb_row(dict(r)) for r in rows]

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

    async def bulk_insert_triggers(self, triggers: list[dict], world_id: str = "") -> int:
        """批量插入静态触发器，trigger_id + world_id 冲突时跳过

        Args:
            triggers: 触发器列表，每项含 trigger_id/module_name/conditions_json/actions_json 等
            world_id: 目标工作区 ID。种子写入 '__seed__{module_name}'，运行时写入当前 world_id
        """
        conn = await self._get_conn()
        wid = world_id or self._world_id
        count = 0
        for t in triggers:
            tid = t.get("trigger_id", "")
            if not tid:
                continue
            try:
                await conn.execute("""
                    INSERT INTO static_triggers
                        (trigger_id, module_name, description, priority,
                         is_one_off, conditions_json, actions_json, world_id)
                    VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8)
                    ON CONFLICT (trigger_id, world_id) DO NOTHING
                """,
                    tid, t.get("module_name", ""),
                    t.get("description", ""), t.get("priority", 0),
                    t.get("is_one_off", True),
                    json.dumps(t.get("conditions_json", {}), ensure_ascii=False),
                    json.dumps(t.get("actions_json", []), ensure_ascii=False),
                    wid,
                )
                count += 1
            except Exception as e:
                logger.warning(f"插入触发器失败 ({tid}): {e}")
        return count

    # ── 触发器读模型查询 ──

    @staticmethod
    def _normalize_jsonb_row(row: dict) -> dict:
        """将 asyncpg 行中的 JSONB 字符串字段反序列化为 Python 对象

        某些历史数据中 conditions_json / actions_json 存储为 JSON 字符串
        而非 JSON 对象（\"...\" 而非 {...}），asyncpg 返回 Python str 而非 dict。
        此函数检测并修复之。
        """
        for col in ("conditions_json", "actions_json"):
            val = row.get(col)
            if isinstance(val, str):
                try:
                    row[col] = json.loads(val)
                except (json.JSONDecodeError, TypeError):
                    logger.warning(f"read_models: {col} 反序列化失败, val={val[:80]}")
        return row

    async def get_triggers_by_module(self, module_name: str, world_id: str = "") -> list[dict]:
        """按模组名 + 世界 ID 查询静态触发器列表，按 priority 降序

        先查精确 world_id 匹配的行，再回退到空 world_id（旧格式兼容）。
        运行时查询传入当前 world_id。
        """
        conn = await self._get_conn()
        wid = world_id or self._world_id
        rows = []
        if wid:
            rows = await conn.fetch(
                "SELECT * FROM static_triggers WHERE module_name=$1 AND world_id=$2 ORDER BY priority DESC",
                module_name, wid,
            )
        # 空 world_id 回退（旧数据或种子数据）
        if not rows:
            rows = await conn.fetch(
                "SELECT * FROM static_triggers WHERE module_name=$1 AND world_id='' ORDER BY priority DESC",
                module_name,
            )
        return [self._normalize_jsonb_row(dict(r)) for r in rows]

    async def get_triggers_by_world(self, world_id: str) -> list[dict]:
        """按世界 ID 查询所有静态触发器，不依赖 module_name

        当 scenario_name 为空时的回退路径，查询精度与 /ev 命令一致。
        先查精确 world_id，再回退到空 world_id（旧格式兼容）。
        """
        conn = await self._get_conn()
        wid = world_id or self._world_id
        rows = []
        if wid:
            rows = await conn.fetch(
                "SELECT * FROM static_triggers WHERE world_id=$1 ORDER BY priority DESC",
                wid,
            )
        if not rows:
            rows = await conn.fetch(
                "SELECT * FROM static_triggers WHERE world_id='' ORDER BY priority DESC",
            )
        return [self._normalize_jsonb_row(dict(r)) for r in rows]

    async def get_trigger_states(self, session_id: str) -> dict[str, dict]:
        """查询指定会话的所有触发器运行时状态

        返回 {trigger_id: {...}} 映射，方便调用方随机查找。
        """
        conn = await self._get_conn()
        rows = await conn.fetch(
            "SELECT * FROM session_trigger_state WHERE session_id=$1",
            session_id,
        )
        return {r["trigger_id"]: dict(r) for r in rows}

    async def upsert_trigger_state(
        self,
        session_id: str,
        trigger_id: str,
        *,
        increment_fired: bool = False,
        disable: bool = False,
        reset_turn_count: bool = False,
    ) -> None:
        """原子更新触发器运行时状态

        参数:
          session_id:       会话 ID
          trigger_id:       触发器 ID
          increment_fired:  是否递增 fired_count 和 fired_this_turn
          disable:          是否将 is_disabled 置为 True
          reset_turn_count: 是否将 fired_this_turn 归零（每轮推进结束时调用）
        """
        conn = await self._get_conn()
        if increment_fired:
            await conn.execute("""
                INSERT INTO session_trigger_state (session_id, trigger_id, fired_count, fired_this_turn, last_fired_at)
                VALUES ($1, $2, 1, 1, CURRENT_TIMESTAMP)
                ON CONFLICT (session_id, trigger_id) DO UPDATE SET
                    fired_count     = session_trigger_state.fired_count + 1,
                    fired_this_turn = session_trigger_state.fired_this_turn + 1,
                    last_fired_at   = CURRENT_TIMESTAMP
            """, session_id, trigger_id)
        if disable:
            await conn.execute("""
                INSERT INTO session_trigger_state (session_id, trigger_id, is_disabled)
                VALUES ($1, $2, TRUE)
                ON CONFLICT (session_id, trigger_id) DO UPDATE SET
                    is_disabled = TRUE
            """, session_id, trigger_id)
        if reset_turn_count:
            await conn.execute("""
                UPDATE session_trigger_state
                SET fired_this_turn = 0
                WHERE session_id = $1
            """, session_id)

    async def reset_all_turn_counters(self, session_id: str) -> None:
        """每轮推进结束时调用：清空本轮触发计数"""
        conn = await self._get_conn()
        await conn.execute(
            "UPDATE session_trigger_state SET fired_this_turn = 0 WHERE session_id = $1",
            session_id,
        )

    async def restore_trigger_states(
        self,
        session_id: str,
        trigger_states: dict[str, dict],
    ) -> int:
        """读档时恢复触发器运行时状态

        先清除会话现有 trigger_state 记录，然后按存档数据批量插入。
        trigger_states 格式: {trigger_id: {fired_count, fired_this_turn, is_disabled}}
        返回恢复的记录数。
        """
        conn = await self._get_conn()
        # 清除现有记录
        await conn.execute(
            "DELETE FROM session_trigger_state WHERE session_id=$1",
            session_id,
        )
        if not trigger_states:
            return 0
        count = 0
        for tid, ts in trigger_states.items():
            try:
                await conn.execute("""
                    INSERT INTO session_trigger_state
                        (session_id, trigger_id, fired_count, fired_this_turn, is_disabled)
                    VALUES ($1, $2, $3, $4, $5)
                """,
                    session_id, tid,
                    ts.get("fired_count", 0),
                    ts.get("fired_this_turn", 0),
                    ts.get("is_disabled", False),
                )
                count += 1
            except Exception as e:
                logger.warning(f"恢复触发器状态失败 ({tid}): {e}")
        logger.info(f"read_models: 已恢复 {count} 条触发器状态 (session={session_id[:8]})")
        return count

    # ── 世界级触发器复制（种子 → 目标世界） ──

    async def copy_triggers_to_world(self, source_world_id: str, target_world_id: str) -> int:
        """将种子工作区的触发器复制到目标世界

        从 source_world_id 读取，以 target_world_id 写入，跳过已存在的 trigger_id。
        """
        conn = await self._get_conn()
        source_rows = await conn.fetch(
            "SELECT * FROM static_triggers WHERE world_id=$1",
            source_world_id,
        )
        if not source_rows:
            return 0

        count = 0
        for row in source_rows:
            try:
                await conn.execute("""
                    INSERT INTO static_triggers
                        (trigger_id, module_name, description, priority,
                         is_one_off, conditions_json, actions_json, world_id)
                    VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8)
                    ON CONFLICT (trigger_id, world_id) DO NOTHING
                """,
                    row["trigger_id"], row["module_name"],
                    row.get("description", ""), row.get("priority", 0),
                    row.get("is_one_off", True),
                    json.dumps(row.get("conditions_json", {}), ensure_ascii=False),
                    json.dumps(row.get("actions_json", []), ensure_ascii=False),
                    target_world_id,
                )
                count += 1
            except Exception as e:
                logger.warning(f"复制触发器失败 ({row['trigger_id']}): {e}")
        return count

    # ── 运行时触发器增删（世界级） ──

    async def delete_triggers_by_world(self, world_id: str, trigger_ids: list[str] | None = None) -> int:
        """删除指定世界中的触发器

        trigger_ids 为 None 时删除该世界全部触发器，否则只删除指定的。
        """
        conn = await self._get_conn()
        if trigger_ids:
            result = await conn.execute(
                "DELETE FROM static_triggers WHERE world_id=$1 AND trigger_id = ANY($2)",
                world_id, trigger_ids,
            )
        else:
            result = await conn.execute(
                "DELETE FROM static_triggers WHERE world_id=$1",
                world_id,
            )
        # 解析 PostgreSQL 的 DELETE count 返回值
        count = int(result.split()[-1]) if result else 0
        return count

    # ── 全量静态蓝图复制（种子 → 目标世界） ──

    async def copy_static_data_to_world(self, source_world_id: str, target_world_id: str) -> dict[str, int]:
        """将种子工作区的全部静态蓝图数据复制到目标世界

        处理 locations/interactables/entities/clue_discoveries/knowledge_registry 间的外键关系：
          先复制 knowledge_registry 和 locations（无外键依赖），
          再复制 interactables 和 entities（依赖 location_id），
          最后复制 clue_discoveries（依赖 interactable_id 和 knowledge_id）。
        """
        conn = await self._get_conn()
        counts: dict[str, int] = {"knowledge": 0, "locations": 0, "interactables": 0,
                                  "entities": 0, "clues": 0, "triggers": 0}

        # 1. 复制 knowledge_registry（保持 ID 不变供 clue FK 引用，已有相同 id 时跳过）
        kn_rows = await conn.fetch(
            "SELECT * FROM knowledge_registry WHERE world_id=$1", source_world_id,
        )
        for row in kn_rows:
            try:
                await conn.execute("""
                    INSERT INTO knowledge_registry (id, knowledge_id, rag_key, description, tags_granted, world_id)
                    VALUES ($1,$2,$3,$4,$5::text[],$6)
                    ON CONFLICT (id) DO NOTHING
                """, row["id"], row["knowledge_id"], row.get("rag_key", ""),
                    row.get("description", ""), row.get("tags_granted", []), target_world_id)
                counts["knowledge"] += 1
            except Exception as e:
                logger.warning(f"复制知识失败 ({row.get('knowledge_id')}): {e}")

        # 2. 复制 locations，重写 id 为新 UUID，构建 old_id → new_id 映射
        loc_rows = await conn.fetch(
            "SELECT id, key, name, base_desc, tags, exits_json FROM locations WHERE world_id=$1",
            source_world_id,
        )
        loc_id_map: dict[str, str] = {}
        for row in loc_rows:
            try:
                new_id = str(uuid.uuid4())
                loc_id_map[str(row["id"])] = new_id
                raw_exits = row.get("exits_json", {})
                if isinstance(raw_exits, str):
                    raw_exits = json.loads(raw_exits) if raw_exits else {}
                exits_json_str = json.dumps(raw_exits, ensure_ascii=False)
                await conn.execute("""
                    INSERT INTO locations (id, key, name, base_desc, tags, exits_json, world_id)
                    VALUES ($1,$2,$3,$4,$5::text[],$6::jsonb,$7)
                    ON CONFLICT (key, world_id) DO NOTHING
                """, new_id, row["key"], row["name"], row.get("base_desc", ""),
                    row.get("tags", []), exits_json_str, target_world_id)
                counts["locations"] += 1
            except Exception as e:
                logger.warning(f"复制场景失败 ({row.get('key')}): {e}")

        # 3. 复制 interactables，重写 id 和 location_id
        item_rows = await conn.fetch(
            "SELECT * FROM interactables WHERE world_id=$1", source_world_id,
        )
        item_id_map: dict[str, str] = {}
        for row in item_rows:
            try:
                new_id = str(uuid.uuid4())
                item_id_map[str(row["id"])] = new_id
                new_loc_id = loc_id_map.get(str(row["location_id"]), "")
                await conn.execute("""
                    INSERT INTO interactables (id, key, name, location_id, tags, state, world_id)
                    VALUES ($1,$2,$3,$4,$5::text[],$6,$7)
                    ON CONFLICT (key, world_id) DO NOTHING
                """, new_id, row["key"], row["name"], new_loc_id,
                    row.get("tags", []), row.get("state", ""), target_world_id)
                counts["interactables"] += 1
            except Exception as e:
                logger.warning(f"复制物品失败 ({row.get('key')}): {e}")

        # 4. 复制 entities，重写 id 和 location_id
        ent_rows = await conn.fetch(
            "SELECT * FROM entities WHERE world_id=$1", source_world_id,
        )
        for row in ent_rows:
            try:
                new_id = str(uuid.uuid4())
                new_loc_id = loc_id_map.get(str(row["location_id"]), "")
                raw_stats = row.get("stats_json", {})
                if isinstance(raw_stats, str):
                    raw_stats = json.loads(raw_stats) if raw_stats else {}
                stats_str = json.dumps(raw_stats, ensure_ascii=False)
                await conn.execute("""
                    INSERT INTO entities (id, key, name, location_id, tags, stats_json, world_id)
                    VALUES ($1,$2,$3,$4,$5::text[],$6::jsonb,$7)
                    ON CONFLICT (key, world_id) DO NOTHING
                """, new_id, row["key"], row["name"], new_loc_id,
                    row.get("tags", []), stats_str, target_world_id)
                counts["entities"] += 1
            except Exception as e:
                logger.warning(f"复制实体失败 ({row.get('key')}): {e}")

        # 5. 复制 clue_discoveries，重写 interactable_id 和 id
        clue_rows = await conn.fetch(
            "SELECT * FROM clue_discoveries WHERE world_id=$1", source_world_id,
        )
        for row in clue_rows:
            try:
                new_id = str(uuid.uuid4())
                new_item_id = item_id_map.get(str(row["interactable_id"]), "")
                # knowledge_id 直接复用（knowledge_registry 保持 ID 不变）
                kid = row.get("knowledge_id")
                raw_req = row.get("required_check", {})
                if isinstance(raw_req, str):
                    raw_req = json.loads(raw_req) if raw_req else {}
                req_str = json.dumps(raw_req, ensure_ascii=False)
                raw_det = row.get("deterministic_changes", {})
                if isinstance(raw_det, str):
                    raw_det = json.loads(raw_det) if raw_det else {}
                det_str = json.dumps(raw_det, ensure_ascii=False)
                await conn.execute("""
                    INSERT INTO clue_discoveries
                        (id, interactable_id, entity_key, knowledge_id,
                         required_check, flavor_text, loot_items,
                         required_item, deterministic_changes, world_id)
                    VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7::text[],$8,$9::jsonb,$10)
                    ON CONFLICT (id, world_id) DO NOTHING
                """, new_id, new_item_id or None, row.get("entity_key"),
                    kid, req_str, row.get("flavor_text", ""),
                    row.get("loot_items", []),
                    row.get("required_item", ""), det_str, target_world_id)
                counts["clues"] += 1
            except Exception as e:
                logger.warning(f"复制线索失败: {e}")

        # 6. 复制 static_triggers
        trig_count = await self.copy_triggers_to_world(source_world_id, target_world_id)
        counts["triggers"] = trig_count

        logger.info(
            f"copy_static_data_to_world: {source_world_id} → {target_world_id} "
            f"(locations={counts['locations']}, items={counts['interactables']}, "
            f"entities={counts['entities']}, clues={counts['clues']}, "
            f"knowledge={counts['knowledge']}, triggers={counts['triggers']})"
        )
        return counts
