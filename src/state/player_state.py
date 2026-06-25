"""
@File     :   player_state.py
@Desc     :   玩家/调查员状态管理 — 从 GameState 到持久化的读写接口
@Note     :   适配旧 entity_repo + investigator_profile_repo 到新的 state 架构

职责:
  - 管理单个调查员的完整状态（属性、技能、物品、位置）
  - 提供状态查询接口（供 Nodes 读取）
  - 提供 JSON 文件持久化（无需数据库即可使用）

使用方式:
    loader = PlayerLoader()
    char = loader.load_character("session-001")
    loader.save_character("session-001", character)
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from src.state.game_state import GameState
from src.state.event_log import EventLog
from src.domain.character import Character, Stats
from src.memory.event_store import EventStore
from src.tools import PROJECT_ROOT


# ── 玩家数据键名常量 ──

PLAYER_DATA_KEY = "player_data"
"""GameState 中存储玩家数据的字段前缀"""

# ── 角色 JSON 持久化路径 ──
_CHARACTER_DIR = PROJECT_ROOT / "data" / "characters"


def _char_dir() -> Path:
    _CHARACTER_DIR.mkdir(parents=True, exist_ok=True)
    return _CHARACTER_DIR


class CharacterStore:
    """PG 角色存储 — 替代 JSON 文件持久化

    适合大量角色的场景，利用 PG 的 JSONB 存储 Character 数据。

    使用方式:
        store = CharacterStore()
        await store.save("session-1", character)
        char = await store.load("session-1")
    """

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
                raise RuntimeError("PG 不可用")

        import asyncpg
        self._conn = await asyncpg.connect(uri)
        await self._init_db()
        return self._conn

    async def _init_db(self):
        conn = await self._get_conn()
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS characters (
                session_id TEXT PRIMARY KEY,
                character_data JSONB NOT NULL,
                saved_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        logger.info("CharacterStore: 表已就绪")

    async def close(self):
        if self._conn and not self._conn.is_closed():
            await self._conn.close()
            self._conn = None

    # ── 核心操作 ──

    async def save(self, session_id: str, character: Character):
        """保存角色到 PG"""
        conn = await self._get_conn()
        char_dict = _character_to_dict(character)
        await conn.execute(
            """INSERT INTO characters (session_id, character_data, saved_at)
               VALUES ($1, $2::jsonb, $3::timestamptz)
               ON CONFLICT (session_id)
               DO UPDATE SET character_data = $2::jsonb, saved_at = $3::timestamptz""",
            session_id,
            json.dumps(char_dict, ensure_ascii=False),
            datetime.now(timezone.utc),
        )

    async def load(self, session_id: str) -> Optional[Character]:
        """从 PG 加载角色"""
        conn = await self._get_conn()
        row = await conn.fetchrow(
            "SELECT character_data FROM characters WHERE session_id = $1",
            session_id,
        )
        if row is None:
            return None
        char_data = row["character_data"]
        if isinstance(char_data, str):
            char_data = json.loads(char_data)
        return _dict_to_character(char_data)

    async def exists(self, session_id: str) -> bool:
        """检查角色是否存在"""
        conn = await self._get_conn()
        val = await conn.fetchval(
            "SELECT 1 FROM characters WHERE session_id = $1", session_id,
        )
        return val is not None

    async def delete(self, session_id: str):
        """删除角色"""
        conn = await self._get_conn()
        await conn.execute(
            "DELETE FROM characters WHERE session_id = $1", session_id,
        )

    async def list_all(self) -> list[dict]:
        """列出所有角色（仅元数据）"""
        conn = await self._get_conn()
        rows = await conn.fetch(
            "SELECT session_id, saved_at FROM characters ORDER BY saved_at DESC"
        )
        return [dict(row) for row in rows]


# ====================================================================
# 辅助函数（Character ↔ dict 转换）
# ====================================================================


def _character_to_dict(char: Character) -> dict:
    """将 Character 对象转为可 JSON 序列化的 dict"""
    return {
        "id": char.id,
        "name": char.name,
        "occupation": char.occupation,
        "stats": char.stats.to_dict() if hasattr(char.stats, "to_dict") else vars(char.stats),
        "skills": char.skills,
        "sanity": char.sanity,
        "max_sanity": char.max_sanity,
        "hit_points": char.hit_points,
        "max_hit_points": char.max_hit_points,
        "magic_points": char.magic_points,
        "max_magic_points": char.max_magic_points,
        "damage_bonus": char.damage_bonus,
        "build": char.build,
        "move": char.move,
        "armor": char.armor,
    }


def _dict_to_character(data: dict) -> Character:
    """从 dict 还原 Character 对象"""
    stats_data = data.get("stats", {})
    stats = Stats.from_dict(stats_data) if isinstance(stats_data, dict) else Stats()
    return Character(
        id=data.get("id", ""),
        name=data.get("name", ""),
        occupation=data.get("occupation", ""),
        stats=stats,
        skills=data.get("skills", {}),
        sanity=data.get("sanity", 0),
        max_sanity=data.get("max_sanity", 0),
        hit_points=data.get("hit_points", 0),
        max_hit_points=data.get("max_hit_points", 0),
        magic_points=data.get("magic_points", 0),
        max_magic_points=data.get("max_magic_points", 0),
        damage_bonus=data.get("damage_bonus", "0"),
        build=data.get("build", 0),
        move=data.get("move", 8),
        armor=data.get("armor", 0),
    )
