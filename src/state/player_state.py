"""
@File     :   player_state.py
@Desc     :   玩家/调查员状态管理 — 从 GameState 到持久化的读写接口
@Note     :   characters 表改用 (scope, key) 复合主键替代 session_id。
              scope='template' 为种子卡（可被多世界复用），scope='world' 为游戏实例。
              CARD_PREFIX 已移除，改用显式 save_template/load_template API。
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
from src.tools import PROJECT_ROOT, get_logger

logger = get_logger(__name__)


PLAYER_DATA_KEY = "player_data"

_CHARACTER_DIR = PROJECT_ROOT / "data" / "characters"


def _char_dir() -> Path:
    _CHARACTER_DIR.mkdir(parents=True, exist_ok=True)
    return _CHARACTER_DIR


class CharacterStore:
    """角色存储（PgManager 连接池版）— scope='template' 为种子卡，scope='world' 为游戏实例

    characters 表结构: (scope, key) 复合主键。
    scope='template' 的角色卡可被多个世界复用（/card import 后 /start 时拷贝）。
    scope='world' 的角色卡与特定游戏世界绑定。
    """

    def __init__(self):
        self._conn = None
        self._file_fallback = False
        self._inited = False

    async def _ensure_pg(self) -> bool:
        if self._conn is not None and not self._conn.is_closed():
            return True
        try:
            from src.tools.pg_manager import PgManager
            mgr = await PgManager.get_instance()
            if not mgr.available:
                return False
            await mgr.start()
            self._conn = await mgr.get_conn()
            if not self._inited:
                await self._init_db()
                self._inited = True
            return True
        except Exception as e:
            logger.warning(f"CharacterStore: PG 不可用，降级到 JSON 文件存储: {e}")
            return False

    async def _get_conn(self):
        if self._file_fallback:
            raise RuntimeError("PG 不可用")
        ok = await self._ensure_pg()
        if not ok:
            self._file_fallback = True
            raise RuntimeError("PG 不可用")
        return self._conn

    async def _init_db(self):
        if not self._conn or self._conn.is_closed():
            return
        # scope='template'/scope='world' 隔离存储，不再依赖 session_id 前缀 hack
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS characters (
                scope TEXT NOT NULL,
                key TEXT NOT NULL,
                character_data JSONB NOT NULL,
                saved_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (scope, key)
            )
        """)
        logger.info("CharacterStore: PG 表已就绪 (scope, key)")

    # ── 文件存储辅助 ──

    def _file_path(self, scope: str, key: str) -> Path:
        return _char_dir() / f"{scope}__{key}.json"

    async def _save_file(self, scope: str, key: str, char_dict: dict):
        path = self._file_path(scope, key)
        path.write_text(json.dumps(char_dict, ensure_ascii=False, default=str), encoding="utf-8")
        logger.debug(f"CharacterStore: 角色已保存至 {path}")

    async def _load_file(self, scope: str, key: str) -> Optional[dict]:
        path = self._file_path(scope, key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"CharacterStore: 读取文件失败 {path}: {e}")
            return None

    async def _exists_file(self, scope: str, key: str) -> bool:
        return self._file_path(scope, key).exists()

    async def _delete_file(self, scope: str, key: str):
        path = self._file_path(scope, key)
        if path.exists():
            path.unlink()

    async def _list_files(self, scope: str = "") -> list[dict]:
        pattern = f"{scope}__*.json" if scope else "*.json"
        files = sorted(_char_dir().glob(pattern))
        result = []
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                saved_at = data.get("_saved_at", "")
                stem_scope, stem_key = f.stem.split("__", 1) if "__" in f.stem else ("", f.stem)
                result.append({
                    "scope": stem_scope,
                    "key": stem_key,
                    "saved_at": saved_at,
                    "character_name": data.get("name", ""),
                })
            except (json.JSONDecodeError, OSError):
                continue
        return result

    async def close(self):
        if self._conn and not self._conn.is_closed():
            from src.tools.pg_manager import PgManager
            mgr = await PgManager.get_instance()
            await mgr.release_conn(self._conn)
            self._conn = None

    # ── 核心操作（scope + key 复合主键） ──

    async def save(self, scope: str, key: str, character: Character):
        """保存角色到指定 scope"""
        char_dict = _character_to_dict(character)
        char_dict["_saved_at"] = datetime.now(timezone.utc).isoformat()
        try:
            conn = await self._get_conn()
            await conn.execute(
                """INSERT INTO characters (scope, key, character_data, saved_at)
                   VALUES ($1, $2, $3::jsonb, $4::timestamptz)
                   ON CONFLICT (scope, key)
                   DO UPDATE SET character_data = $3::jsonb, saved_at = $4::timestamptz""",
                scope, key,
                json.dumps(char_dict, ensure_ascii=False),
                datetime.now(timezone.utc),
            )
        except RuntimeError:
            await self._save_file(scope, key, char_dict)

    async def load(self, scope: str, key: str) -> Optional[Character]:
        """加载指定 scope+key 的角色"""
        try:
            conn = await self._get_conn()
            row = await conn.fetchrow(
                "SELECT character_data FROM characters WHERE scope = $1 AND key = $2",
                scope, key,
            )
            if row is None:
                return None
            char_data = row["character_data"]
            if isinstance(char_data, str):
                char_data = json.loads(char_data)
            return _dict_to_character(char_data)
        except RuntimeError:
            data = await self._load_file(scope, key)
            return _dict_to_character(data) if data else None

    async def exists(self, scope: str, key: str) -> bool:
        """检查角色是否存在"""
        try:
            conn = await self._get_conn()
            val = await conn.fetchval(
                "SELECT 1 FROM characters WHERE scope = $1 AND key = $2", scope, key,
            )
            return val is not None
        except RuntimeError:
            return await self._exists_file(scope, key)

    async def delete(self, scope: str, key: str):
        """删除角色"""
        try:
            conn = await self._get_conn()
            await conn.execute(
                "DELETE FROM characters WHERE scope = $1 AND key = $2", scope, key,
            )
        except RuntimeError:
            await self._delete_file(scope, key)

    async def list_scope(self, scope: str) -> list[dict]:
        """列出指定 scope 的所有角色"""
        try:
            conn = await self._get_conn()
            rows = await conn.fetch(
                "SELECT scope, key, saved_at,"
                " character_data->>'name' AS character_name,"
                " character_data->>'occupation' AS occupation"
                " FROM characters WHERE scope = $1 ORDER BY saved_at DESC",
                scope,
            )
            return [dict(row) for row in rows]
        except RuntimeError:
            return await self._list_files(scope=scope)

    # ------- 种子卡 template API（scope='template'） -------

    async def save_template(self, name: str, character: Character):
        """将角色保存为种子卡（scope='template'，可被多个世界复用）"""
        await self.save("template", name, character)

    async def load_template(self, name: str) -> Optional[Character]:
        """加载指定名称的种子卡"""
        return await self.load("template", name)

    async def template_exists(self, name: str) -> bool:
        """检查种子卡是否存在"""
        return await self.exists("template", name)

    async def delete_template(self, name: str):
        """删除指定种子卡"""
        await self.delete("template", name)

    async def list_templates(self) -> list[dict]:
        """列出所有种子卡"""
        return await self.list_scope("template")


# ====================================================================
# 辅助函数（Character ↔ dict 转换）
# ====================================================================


def _character_to_dict(char: Character) -> dict:
    """将 Character 对象转为可 JSON 序列化的 dict"""
    return {
        "id": char.id,
        "name": char.name,
        "gender": char.gender,
        "age": char.age,
        "birthplace": char.birthplace,
        "occupation": char.occupation,
        "stats": char.stats.to_dict() if hasattr(char.stats, "to_dict") else vars(char.stats),
        "skills": char.skills,
        "sanity": char.sanity,
        "max_sanity": char.max_sanity,
        "initial_sanity": char.initial_sanity,
        "sanity_loss_today": char.sanity_loss_today,
        "temp_insanity": char.temp_insanity,
        "indefinite_insanity": char.indefinite_insanity,
        "hit_points": char.hit_points,
        "max_hit_points": char.max_hit_points,
        "major_wound": char.major_wound,
        "unconscious": char.unconscious,
        "dying": char.dying,
        "magic_points": char.magic_points,
        "max_magic_points": char.max_magic_points,
        "luck": char.luck,
        "damage_bonus": char.damage_bonus,
        "build": char.build,
        "move": char.move,
        "armor": char.armor,
        "inventory": char.inventory,
        "appearance_desc": char.appearance_desc,
        "belief": char.belief,
        "significant_person": char.significant_person,
        "significant_place": char.significant_place,
        "cherished_possession": char.cherished_possession,
        "trait": char.trait,
        "injury_scar": char.injury_scar,
        "spells": char.spells,
        "full_backstory": char.full_backstory,
        "phobias_manias": char.phobias_manias,
    }


def _dict_to_character(data: dict) -> Character:
    """从 dict 还原 Character 对象"""
    stats_data = data.get("stats", {})
    stats = Stats.from_dict(stats_data) if isinstance(stats_data, dict) else Stats()
    return Character(
        id=data.get("id", ""),
        name=data.get("name", ""),
        gender=data.get("gender", ""),
        age=data.get("age", 0),
        birthplace=data.get("birthplace", ""),
        occupation=data.get("occupation", ""),
        stats=stats,
        skills=data.get("skills", {}),
        sanity=data.get("sanity", 0),
        max_sanity=data.get("max_sanity", 0),
        initial_sanity=data.get("initial_sanity", 0),
        sanity_loss_today=data.get("sanity_loss_today", 0),
        temp_insanity=data.get("temp_insanity", False),
        indefinite_insanity=data.get("indefinite_insanity", False),
        hit_points=data.get("hit_points", 0),
        max_hit_points=data.get("max_hit_points", 0),
        major_wound=data.get("major_wound", False),
        unconscious=data.get("unconscious", False),
        dying=data.get("dying", False),
        magic_points=data.get("magic_points", 0),
        max_magic_points=data.get("max_magic_points", 0),
        luck=data.get("luck", 0),
        damage_bonus=data.get("damage_bonus", "0"),
        build=data.get("build", 0),
        move=data.get("move", 8),
        armor=data.get("armor", 0),
        inventory=data.get("inventory", []),
        appearance_desc=data.get("appearance_desc", ""),
        belief=data.get("belief", ""),
        significant_person=data.get("significant_person", ""),
        significant_place=data.get("significant_place", ""),
        cherished_possession=data.get("cherished_possession", ""),
        trait=data.get("trait", ""),
        injury_scar=data.get("injury_scar", ""),
        spells=data.get("spells", []),
        full_backstory=data.get("full_backstory", ""),
        phobias_manias=data.get("phobias_manias", ""),
    )
