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
from src.tools import PROJECT_ROOT, get_logger

logger = get_logger(__name__)


# ── 玩家数据键名常量 ──

PLAYER_DATA_KEY = "player_data"
"""GameState 中存储玩家数据的字段前缀"""

# ── 种子卡前缀：角色卡导入后先存为种子，/start 时才拷贝到会话 ──
CARD_PREFIX = "__card__"

# ── 角色 JSON 持久化路径 ──
_CHARACTER_DIR = PROJECT_ROOT / "data" / "characters"


def _char_dir() -> Path:
    _CHARACTER_DIR.mkdir(parents=True, exist_ok=True)
    return _CHARACTER_DIR


class CharacterStore:
    """角色存储 — 优先使用 PG，不可用时自动降级到 JSON 文件持久化

    使用方式:
        store = CharacterStore()
        await store.save("session-1", character)
        char = await store.load("session-1")
    """

    def __init__(self, pg_uri: Optional[str] = None):
        self._uri = pg_uri or ""
        self._conn = None
        self._file_fallback = False

    async def _ensure_pg(self) -> bool:
        """检测 PG 是否可用，返回 True 表示 PG 可用"""
        if self._conn is not None and not self._conn.is_closed():
            return True

        uri = self._uri
        if not uri:
            try:
                from src.tools.pg_manager import PgManager
                mgr = await PgManager.get_instance()
                if mgr.available:
                    await mgr.start()
                    uri = mgr.uri
            except Exception:
                return False

        if not uri:
            return False

        try:
            import asyncpg
            self._conn = await asyncpg.connect(uri)
            await self._init_db()
            return True
        except Exception as e:
            logger.warning(f"CharacterStore: PG 不可用，降级到 JSON 文件存储: {e}")
            return False

    async def _get_conn(self):
        """获取 PG 连接，如果不可用则触发降级到文件存储"""
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
        await self._conn.execute("""
            CREATE TABLE IF NOT EXISTS characters (
                session_id TEXT PRIMARY KEY,
                character_data JSONB NOT NULL,
                saved_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)
        logger.info("CharacterStore: PG 表已就绪")

    # ── 文件存储辅助 ──

    def _file_path(self, session_id: str) -> Path:
        return _char_dir() / f"{session_id}.json"

    async def _save_file(self, session_id: str, char_dict: dict):
        path = self._file_path(session_id)
        path.write_text(
            json.dumps(char_dict, ensure_ascii=False, default=str),
            encoding="utf-8",
        )
        logger.debug(f"CharacterStore: 角色已保存至 {path}")

    async def _load_file(self, session_id: str) -> Optional[dict]:
        path = self._file_path(session_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"CharacterStore: 读取文件失败 {path}: {e}")
            return None

    async def _exists_file(self, session_id: str) -> bool:
        return self._file_path(session_id).exists()

    async def _delete_file(self, session_id: str):
        path = self._file_path(session_id)
        if path.exists():
            path.unlink()

    async def _list_all_files(self) -> list[dict]:
        files = sorted(_char_dir().glob("*.json"))
        result = []
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                saved_at = data.get("_saved_at", "")
                result.append({
                    "session_id": f.stem,
                    "saved_at": saved_at,
                    "character_name": data.get("name", ""),
                })
            except (json.JSONDecodeError, OSError):
                continue
        return result

    async def close(self):
        if self._conn and not self._conn.is_closed():
            await self._conn.close()
            self._conn = None

    # ── 核心操作（带降级） ──

    async def save(self, session_id: str, character: Character):
        """保存角色 — 优先 PG，降级到 JSON 文件"""
        char_dict = _character_to_dict(character)
        char_dict["_saved_at"] = datetime.now(timezone.utc).isoformat()

        try:
            conn = await self._get_conn()
            await conn.execute(
                """INSERT INTO characters (session_id, character_data, saved_at)
                   VALUES ($1, $2::jsonb, $3::timestamptz)
                   ON CONFLICT (session_id)
                   DO UPDATE SET character_data = $2::jsonb, saved_at = $3::timestamptz""",
                session_id,
                json.dumps(char_dict, ensure_ascii=False),
                datetime.now(timezone.utc),
            )
        except RuntimeError:
            await self._save_file(session_id, char_dict)

    async def load(self, session_id: str) -> Optional[Character]:
        """加载角色 — 优先 PG，降级到 JSON 文件"""
        try:
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
        except RuntimeError:
            data = await self._load_file(session_id)
            return _dict_to_character(data) if data else None

    async def exists(self, session_id: str) -> bool:
        """检查角色是否存在 — 优先 PG，降级到 JSON 文件"""
        try:
            conn = await self._get_conn()
            val = await conn.fetchval(
                "SELECT 1 FROM characters WHERE session_id = $1", session_id,
            )
            return val is not None
        except RuntimeError:
            return await self._exists_file(session_id)

    async def delete(self, session_id: str):
        """删除角色 — 优先 PG，降级到 JSON 文件"""
        try:
            conn = await self._get_conn()
            await conn.execute(
                "DELETE FROM characters WHERE session_id = $1", session_id,
            )
        except RuntimeError:
            await self._delete_file(session_id)

    async def list_all(self) -> list[dict]:
        """列出所有角色 — 优先 PG，降级到 JSON 文件"""
        try:
            conn = await self._get_conn()
            rows = await conn.fetch(
                "SELECT session_id, saved_at FROM characters ORDER BY saved_at DESC"
            )
            return [dict(row) for row in rows]
        except RuntimeError:
            return await self._list_all_files()

    # ------- 种子卡库（/import → 种子 → /start 拷贝到会话） -------

    async def save_card(self, card_name: str, character: Character):
        """将角色保存为种子卡（独立于会话，可被多个世界复用）"""
        seed_id = f"{CARD_PREFIX}{card_name}"
        await self.save(seed_id, character)

    async def load_card(self, card_name: str) -> Optional[Character]:
        """加载指定名称的种子卡"""
        return await self.load(f"{CARD_PREFIX}{card_name}")

    async def card_exists(self, card_name: str) -> bool:
        """检查种子卡是否存在"""
        return await self.exists(f"{CARD_PREFIX}{card_name}")

    async def delete_card(self, card_name: str):
        """删除指定种子卡"""
        await self.delete(f"{CARD_PREFIX}{card_name}")

    async def list_cards(self) -> list[dict]:
        """列出所有种子卡（过滤 __card__ 前缀）"""
        try:
            conn = await self._get_conn()
            rows = await conn.fetch(
                "SELECT session_id, saved_at,"
                " character_data->>'name' AS character_name,"
                " character_data->>'occupation' AS occupation"
                " FROM characters"
                " WHERE session_id LIKE $1 ORDER BY saved_at DESC",
                f"{CARD_PREFIX}%",
            )
            result = []
            for row in rows:
                d = dict(row)
                sid = d.get("session_id", "")
                d["card_name"] = sid[len(CARD_PREFIX):] if sid.startswith(CARD_PREFIX) else sid
                result.append(d)
            return result
        except RuntimeError:
            return await self._list_card_files()

    async def _list_card_files(self) -> list[dict]:
        """文件降级：列出所有 __card__ 前缀的 JSON 文件"""
        prefix = f"{CARD_PREFIX}"
        result: list[dict] = []
        for f in sorted(_char_dir().glob(f"{prefix}*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                saved_at = data.get("_saved_at", "")
                # 从文件名剥离前缀得到卡片名
                card_name = f.stem[len(prefix):] if f.stem.startswith(prefix) else f.stem
                result.append({
                    "session_id": f.stem,
                    "card_name": card_name,
                    "saved_at": saved_at,
                    "character_name": data.get("name", ""),
                    "occupation": data.get("occupation", ""),
                })
            except (json.JSONDecodeError, OSError):
                continue
        return result


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
