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


class PlayerLoader:
    """
    玩家/调查员状态加载器。

    职责:
      - 通过 EventStore 或 JSON 文件读写调查员数据
      - 将领域对象 (Character) 转换为 dict 存储
      - 提供零依赖的 JSON 文件持久化
    """

    def __init__(self, event_store: Optional[EventStore] = None, event_log: Optional[EventLog] = None):
        self._event_store = event_store
        self._event_log = event_log

    # ── JSON 文件持久化 ──

    def character_exists(self, session_id: str) -> bool:
        """检查指定会话是否有已保存的角色"""
        path = _char_dir() / f"{session_id}.json"
        return path.exists()

    def save_character(self, session_id: str, character: Character):
        """将角色保存为 JSON 文件"""
        data = {
            "session_id": session_id,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "character": _character_to_dict(character),
        }
        path = _char_dir() / f"{session_id}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load_character(self, session_id: str) -> Optional[Character]:
        """从 JSON 文件加载角色"""
        path = _char_dir() / f"{session_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            char_data = data.get("character", {})
            return _dict_to_character(char_data)
        except Exception:
            return None

    def delete_character(self, session_id: str):
        """删除已保存的角色文件"""
        path = _char_dir() / f"{session_id}.json"
        if path.exists():
            path.unlink()

    # ── EventStore 持久化（旧接口） ──

    # ── 查询接口 ──

    async def load_player(self, session_id: str, character_id: str) -> Optional[dict]:
        """
        从事件流重建指定调查员的最新状态。

        策略:
          1. 尝试从最近的 CharacterCreated/CreateInvestigator 事件读取
          2. 回放后续 SkillUpdated/SanityChanged 等增量事件
          3. 组装为完整的 player_data dict
        """
        events = await self._event_store.get_events(session_id, since_version=0)
        player_data: Optional[dict] = None

        for event in events:
            data = event.get("data", {})
            event_type = event.get("type", "")

            # 角色创建事件
            if event_type in ("CharacterCreated", "CreateInvestigator"):
                char_data = data.get("character", data.get("patch", {}).get(PLAYER_DATA_KEY))
                if char_data and char_data.get("id") == character_id:
                    player_data = char_data

            # 属性/技能更新事件
            elif event_type in ("SkillUpdated", "StatUpdated", "SanityChanged",
                                "HitPointsChanged", "MagicPointsChanged") and player_data:
                patch = data.get("patch", {})
                player_key = f"{PLAYER_DATA_KEY}.{character_id}"
                for key, value in patch.items():
                    if key == player_key and isinstance(value, dict):
                        player_data.update(value)
                    elif key.startswith(f"{PLAYER_DATA_KEY}.{character_id}."):
                        # 点号路径: "player_data.char-123.skills.侦查" → skills["侦查"]
                        parts = key.split(".")
                        if len(parts) == 4 and parts[0] == PLAYER_DATA_KEY:
                            field = parts[2]
                            if field == "skills" and isinstance(player_data.get("skills"), dict):
                                player_data["skills"][parts[3]] = value
                            else:
                                player_data[field] = value

        return player_data

    async def load_players(self, session_id: str) -> list[dict]:
        """加载会话中所有调查员"""
        events = await self._event_store.get_events(session_id, since_version=0)
        players: dict[str, dict] = {}

        for event in events:
            data = event.get("data", {})
            event_type = event.get("type", "")
            patch = data.get("patch", {})

            if event_type == "CreateInvestigator":
                char_data = patch.get(PLAYER_DATA_KEY, data.get("character", {}))
                char_id = char_data.get("id", "")
                if char_id:
                    players[char_id] = char_data

            elif event_type == "CharacterCreated":
                char_data = data.get("character", {})
                char_id = char_data.get("id", "")
                if char_id:
                    players[char_id] = char_data

            # 增量更新
            player_key_patch = {k: v for k, v in patch.items()
                                if k.startswith(f"{PLAYER_DATA_KEY}.")}
            for key, value in player_key_patch.items():
                parts = key.split(".")
                if len(parts) >= 3:
                    char_id = parts[1]
                    if char_id in players:
                        field = parts[2]
                        if field == "skills" and len(parts) == 4:
                            players[char_id].setdefault("skills", {})[parts[3]] = value
                        elif len(parts) == 3:
                            players[char_id][field] = value

        return list(players.values())

    async def get_player_from_state(self, state: GameState, character_id: str) -> Optional[dict]:
        """从 GameState 直接读取调查员数据（最快路径，无需查库）"""
        player_key = f"{PLAYER_DATA_KEY}.{character_id}"
        return state.get(player_key) or state.get(PLAYER_DATA_KEY, {}).get(character_id)

    # ── 写入接口 ──

    async def save_player(
        self,
        session_id: str,
        character: Character,
        source_node: str = "player_state",
    ) -> dict:
        """
        保存完整的调查员数据（创建或覆盖）。

        通过 EventLog 记录 CreateInvestigator 事件。
        """
        char_data = self._character_to_dict(character)
        patch = {f"{PLAYER_DATA_KEY}.{character.id}": char_data}

        if self._event_log:
            _, event = await self._event_log.record_and_apply(
                current={"session_id": session_id},
                patch=patch,
                event_type="CreateInvestigator",
                source_node=source_node,
            )
            return event
        else:
            return await self._event_store.append(
                session_id=session_id,
                event_type="CreateInvestigator",
                data={"character": char_data, "patch": patch},
                source_node=source_node,
            )

    async def update_skill(
        self,
        session_id: str,
        character_id: str,
        skill_name: str,
        new_value: int,
        source_node: str = "player_state",
    ) -> Optional[dict]:
        """更新调查员的单个技能值"""
        if not (1 <= new_value <= 100):
            raise ValueError(f"技能值必须在 1-100 之间: {new_value}")

        patch = {f"{PLAYER_DATA_KEY}.{character_id}.skills.{skill_name}": new_value}

        if self._event_log:
            _, event = await self._event_log.record_and_apply(
                current={"session_id": session_id},
                patch=patch,
                event_type="SkillUpdated",
                source_node=source_node,
                extra_data={"character_id": character_id, "skill": skill_name, "new_value": new_value},
            )
            return event
        else:
            return await self._event_store.append(
                session_id=session_id,
                event_type="SkillUpdated",
                data={"patch": patch, "character_id": character_id,
                      "skill": skill_name, "new_value": new_value},
                source_node=source_node,
            )

    async def update_sanity(
        self,
        session_id: str,
        character_id: str,
        new_sanity: int,
        source_node: str = "player_state",
    ) -> Optional[dict]:
        """更新调查员的理智值"""
        patch = {f"{PLAYER_DATA_KEY}.{character_id}.sanity": new_sanity}

        if self._event_log:
            _, event = await self._event_log.record_and_apply(
                current={"session_id": session_id},
                patch=patch,
                event_type="SanityChanged",
                source_node=source_node,
                extra_data={"character_id": character_id, "new_sanity": new_sanity},
            )
            return event
        else:
            return await self._event_store.append(
                session_id=session_id,
                event_type="SanityChanged",
                data={"patch": patch, "character_id": character_id, "new_sanity": new_sanity},
                source_node=source_node,
            )

    async def update_hit_points(
        self,
        session_id: str,
        character_id: str,
        new_hp: int,
        source_node: str = "player_state",
    ) -> Optional[dict]:
        """更新调查员的 HP"""
        patch = {f"{PLAYER_DATA_KEY}.{character_id}.hit_points": new_hp}

        if self._event_log:
            _, event = await self._event_log.record_and_apply(
                current={"session_id": session_id},
                patch=patch,
                event_type="HitPointsChanged",
                source_node=source_node,
                extra_data={"character_id": character_id, "new_hp": new_hp},
            )
            return event
        else:
            return await self._event_store.append(
                session_id=session_id,
                event_type="HitPointsChanged",
                data={"patch": patch, "character_id": character_id, "new_hp": new_hp},
                source_node=source_node,
            )

    async def update_magic_points(
        self,
        session_id: str,
        character_id: str,
        new_mp: int,
        source_node: str = "player_state",
    ) -> Optional[dict]:
        """更新调查员的 MP"""
        patch = {f"{PLAYER_DATA_KEY}.{character_id}.magic_points": new_mp}

        if self._event_log:
            _, event = await self._event_log.record_and_apply(
                current={"session_id": session_id},
                patch=patch,
                event_type="MagicPointsChanged",
                source_node=source_node,
                extra_data={"character_id": character_id, "new_mp": new_mp},
            )
            return event
        else:
            return await self._event_store.append(
                session_id=session_id,
                event_type="MagicPointsChanged",
                data={"patch": patch, "character_id": character_id, "new_mp": new_mp},
                source_node=source_node,
            )

    async def update_location(
        self,
        session_id: str,
        character_id: str,
        location_key: str,
        source_node: str = "player_state",
    ) -> Optional[dict]:
        """更新调查员位置"""
        patch = {f"{PLAYER_DATA_KEY}.{character_id}.location": location_key}

        if self._event_log:
            _, event = await self._event_log.record_and_apply(
                current={"session_id": session_id},
                patch=patch,
                event_type="PlayerMoved",
                source_node=source_node,
                extra_data={"character_id": character_id, "location": location_key},
            )
            return event
        else:
            return await self._event_store.append(
                session_id=session_id,
                event_type="PlayerMoved",
                data={"patch": patch, "character_id": character_id, "location": location_key},
                source_node=source_node,
            )

    # ── 辅助方法 ──

    def _character_to_dict(self, char: Character) -> dict:
        """将 Character 对象转为可 JSON 序列化的 dict"""
        return _character_to_dict(char)


def _character_to_dict(char: Character) -> dict:
    """将 Character 对象转为可 JSON 序列化的 dict（模块级函数）"""
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
