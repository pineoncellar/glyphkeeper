"""
@File     :   world_state.py
@Desc     :   世界状态管理器 — 场景、NPC、物品的状态查询与变更
@Note     :   适配旧 location_repo + interactable_repo 到新的 state 架构

职责:
  - 管理游戏世界状态（场景、NPC、物品、线索）
  - 提供世界数据的统一查询入口
  - 状态变更通过 Event → Reducer 模式进行
  - 世界隔离（world schema）

使用方式:
    mgr = WorldManager(event_store, event_log)
    loc = await mgr.load_location(session_id, "location_key")
    view = await mgr.get_location_view(session_id, "entity_key")
    await mgr.move_entity(session_id, "entity_key", "target_location")
"""

from __future__ import annotations

from typing import Any, Optional
from src.state.game_state import GameState
from src.state.event_log import EventLog
from src.memory.event_store import EventStore


# ── 世界数据键名常量 ──

WORLD_DATA_KEY = "world_data"
LOCATIONS_KEY = f"{WORLD_DATA_KEY}.locations"
ENTITIES_KEY = f"{WORLD_DATA_KEY}.entities"


class WorldManager:
    """
    世界状态管理器。

    通过事件溯源管理场景、NPC、物品的状态变更。
    所有修改通过 EventLog 记录，确保可回放。
    """

    def __init__(
        self,
        event_store: EventStore,
        event_log: Optional[EventLog] = None,
    ):
        self._event_store = event_store
        self._event_log = event_log

    # ── 场景查询 ──

    async def load_location(self, session_id: str, location_key: str) -> Optional[dict]:
        """
        从事件流重建指定场景的最新状态。

        场景数据结构:
        {
            "key": "old_library",
            "name": "旧图书馆",
            "base_desc": "积满灰尘的阅览室...",
            "exits": {"north": "main_hall", "east": "reading_room"},
            "tags": ["indoor", "dark"],
            "entities": ["npc_librarian"],
            "interactables": ["rusty_key"],
        }
        """
        events = await self._event_store.get_events(session_id, since_version=0)
        location: Optional[dict] = None

        def _loc_list_to_dict(locs):
            """将 locations 列表按 key 转为字典（Events 中 locations 存为 list）"""
            if isinstance(locs, dict):
                return locs
            return {loc.get("key", ""): loc for loc in locs if isinstance(loc, dict)}

        for event in events:
            data = event.get("data", {})
            event_type = event.get("type", "")
            patch = data.get("patch", {})

            if event_type in ("LocationCreated", "WorldInitialized"):
                loc_dict = _loc_list_to_dict(
                    patch.get(LOCATIONS_KEY, data.get("locations", {}))
                )
                loc_data = loc_dict.get(location_key)
                if loc_data:
                    location = loc_data

            elif event_type == "LocationUpdated" and location:
                loc_patch = patch.get(f"{LOCATIONS_KEY}.{location_key}")
                if loc_patch:
                    location.update(loc_patch)

            elif event_type in ("EntityMoved", "ItemMoved") and location:
                target = data.get("to_location", "")
                if target == location_key:
                    # 实体/物品进入此场景
                    entity_key = data.get("entity_key", data.get("item_key", ""))
                    if entity_key and entity_key not in location.setdefault("entities", []):
                        location["entities"].append(entity_key)
                source = data.get("from_location", "")
                if source == location_key:
                    entity_key = data.get("entity_key", data.get("item_key", ""))
                    if entity_key and entity_key in location.get("entities", []):
                        location["entities"].remove(entity_key)

        return location

    async def load_all_locations(self, session_id: str) -> dict[str, dict]:
        """加载会话中所有场景"""
        def _loc_list_to_dict(locs):
            if isinstance(locs, dict):
                return locs
            return {loc.get("key", ""): loc for loc in locs if isinstance(loc, dict)}

        events = await self._event_store.get_events(session_id, since_version=0)
        locations: dict[str, dict] = {}

        for event in events:
            data = event.get("data", {})
            event_type = event.get("type", "")
            patch = data.get("patch", {})

            if event_type in ("LocationCreated", "WorldInitialized"):
                locs = _loc_list_to_dict(
                    patch.get(LOCATIONS_KEY, data.get("locations", {}))
                )
                if locs:
                    locations.update(locs)

            elif event_type == "LocationUpdated":
                loc_patches = {k: v for k, v in patch.items()
                               if k.startswith(f"{LOCATIONS_KEY}.")}
                for key, value in loc_patches.items():
                    loc_key = key.split(".", 2)[2]  # "world_data.locations.xxx" → "xxx"
                    if loc_key in locations:
                        locations[loc_key].update(value)

            elif event_type in ("EntityMoved", "ItemMoved"):
                entity_key = data.get("entity_key", data.get("item_key", ""))
                from_loc = data.get("from_location", "")
                to_loc = data.get("to_location", "")
                if from_loc in locations and entity_key:
                    loc_entities = locations[from_loc].setdefault("entities", [])
                    if entity_key in loc_entities:
                        loc_entities.remove(entity_key)
                if to_loc in locations and entity_key:
                    locations[to_loc].setdefault("entities", []).append(entity_key)

        return locations

    async def get_location_view(
        self, session_id: str, location_key: str
    ) -> str:
        """
        生成场景的自然语言描述（供 LLM Node 使用）。

        格式:
        [场景名]
        [基础描述]
        出口: [出口列表]
        可见: [NPC/物品列表]
        """
        location = await self.load_location(session_id, location_key)
        if not location:
            return f"（未找到场景: {location_key}）"

        lines = [f"【{location.get('name', location_key)}】"]
        lines.append(location.get("base_desc", ""))

        exits = location.get("exits", {})
        if exits:
            exit_desc = ", ".join(f"{k}: {v}" for k, v in exits.items())
            lines.append(f"出口: {exit_desc}")

        entities = location.get("entities", [])
        if entities:
            lines.append(f"可见: {', '.join(entities)}")

        return "\n".join(lines)

    # ── 实体移动 ──

    async def move_entity(
        self,
        session_id: str,
        entity_key: str,
        target_location_key: str,
        source_node: str = "world_state",
    ) -> Optional[dict]:
        """
        移动实体到目标场景。

        通过事件记录移动，可以从事件流重建位置变更历史。
        """
        # 先查当前在哪个场景
        current_location = await self._find_entity_location(session_id, entity_key)

        patch = {}
        if current_location:
            patch[f"{LOCATIONS_KEY}.{current_location}.entities"] = [entity_key]
            # 注意: reducer 的 APPEND 行为不适合删除
            # 删除通过完整的事件 data 记录

        extra = {
            "entity_key": entity_key,
            "from_location": current_location or "",
            "to_location": target_location_key,
        }

        if self._event_log:
            _, event = await self._event_log.record_and_apply(
                current={"session_id": session_id},
                patch=patch,
                event_type="EntityMoved",
                source_node=source_node,
                extra_data=extra,
            )
            return event
        else:
            return await self._event_store.append(
                session_id=session_id,
                event_type="EntityMoved",
                data={**extra, "patch": patch},
                source_node=source_node,
            )

    async def _find_entity_location(
        self, session_id: str, entity_key: str
    ) -> Optional[str]:
        """查找实体当前所在的场景 key"""
        locations = await self.load_all_locations(session_id)
        for loc_key, loc_data in locations.items():
            if entity_key in loc_data.get("entities", []):
                return loc_key
        return None

    # ── 场景创建与更新 ──

    async def create_location(
        self,
        session_id: str,
        location_key: str,
        name: str,
        base_desc: str,
        exits: Optional[dict] = None,
        tags: Optional[list[str]] = None,
        source_node: str = "world_state",
    ) -> Optional[dict]:
        """创建或更新场景"""
        location_data = {
            "key": location_key,
            "name": name,
            "base_desc": base_desc,
            "exits": exits or {},
            "tags": tags or [],
            "entities": [],
            "interactables": [],
        }

        patch = {f"{LOCATIONS_KEY}.{location_key}": location_data}

        if self._event_log:
            _, event = await self._event_log.record_and_apply(
                current={"session_id": session_id},
                patch=patch,
                event_type="LocationCreated",
                source_node=source_node,
                extra_data={"location_key": location_key, "name": name},
            )
            return event
        else:
            return await self._event_store.append(
                session_id=session_id,
                event_type="LocationCreated",
                data={"patch": patch, "location_key": location_key, "name": name},
                source_node=source_node,
            )

    async def update_location_desc(
        self,
        session_id: str,
        location_key: str,
        new_desc: str,
        source_node: str = "world_state",
    ) -> Optional[dict]:
        """更新场景描述"""
        patch = {f"{LOCATIONS_KEY}.{location_key}.base_desc": new_desc}

        if self._event_log:
            _, event = await self._event_log.record_and_apply(
                current={"session_id": session_id},
                patch=patch,
                event_type="LocationUpdated",
                source_node=source_node,
                extra_data={"location_key": location_key},
            )
            return event
        else:
            return await self._event_store.append(
                session_id=session_id,
                event_type="LocationUpdated",
                data={"patch": patch, "location_key": location_key},
                source_node=source_node,
            )

    # ── 世界初始化 ──

    async def initialize_world(
        self,
        session_id: str,
        locations: dict[str, dict],
        source_node: str = "world_state",
    ) -> Optional[dict]:
        """
        批量初始化世界场景数据。

        locations 格式:
        {
            "old_library": {"name": "旧图书馆", "base_desc": "...", "exits": {...}},
            "main_hall": {"name": "大厅", ...},
        }
        """
        patch = {f"{LOCATIONS_KEY}": locations}

        if self._event_log:
            _, event = await self._event_log.record_and_apply(
                current={"session_id": session_id},
                patch=patch,
                event_type="WorldInitialized",
                source_node=source_node,
                extra_data={"location_count": len(locations)},
            )
            return event
        else:
            return await self._event_store.append(
                session_id=session_id,
                event_type="WorldInitialized",
                data={"patch": patch, "locations": locations,
                      "location_count": len(locations)},
                source_node=source_node,
            )
