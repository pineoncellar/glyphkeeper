"""
@File     :   event_log.py
@Desc     :   事件溯源日志 — Event → Reducer 模式的核心编排层
@Note     :   将 State 的每次变更记录为 Event，提供订阅与回放能力

职责:
  - 将 State 的每次变更记录为不可变 Event
  - 通过 EventStore 持久化事件流
  - 提供事件订阅机制（给 Workers 用）
  - 支持基于事件的调试和回放
  - 所有 state 修改必须通过 Event → Reducer 模式
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Optional
from src.state.game_state import GameState, create_initial_state
from src.state.reducer import reduce_state, apply_events_to_state
from src.memory.event_store import EventStore


# 回调类型: (event: dict) -> Coroutine
EventCallback = Callable[[dict], Coroutine[Any, Any, None]]


# ── 内建事件类型常量 ──

EVENT_PLAYER_INPUT = "PlayerInput"
EVENT_COMBAT_ROUND = "CombatRound"
EVENT_SKILL_CHECK = "SkillCheck"
EVENT_NARRATIVE_OUTPUT = "NarrativeOutput"
EVENT_CLUE_DISCOVERED = "ClueDiscovered"
EVENT_WORLD_INITIALIZED = "WorldInitialized"

# ── Tier 1 追赶事件类型 ──

EVENT_TIER1_ITEM_STATE = "Tier1ItemStateChange"
EVENT_TIER1_NPC_STATE = "Tier1NpcStateChange"
EVENT_TIER1_LOCATION_TAG = "Tier1LocationTagChange"
EVENT_TIER1_SCENE_TRANSITION = "Tier1SceneTransitionImplied"


class EventLog:
    """
    事件溯源日志 — 高层事件管理接口。

    职责:
      - 记录 State 变更事件
      - 管理事件订阅
      - 支持状态回放重建

    使用方式:
        log = EventLog(event_store)
        new_state = await log.record_and_apply(
            current_state,
            patch,
            event_type="SkillCheck",
            source_node="skill_node",
        )
    """

    def __init__(self, event_store: EventStore):
        self._store = event_store
        self._subscriptions: dict[str, list[EventCallback]] = {}

    # ── 核心操作 ──

    async def record_and_apply(
        self,
        current: GameState,
        patch: dict,
        event_type: str,
        source_node: str = "",
        parent_event_id: Optional[str] = None,
        extra_data: Optional[dict] = None,
    ) -> tuple[GameState, dict]:
        """
        记录事件 → 写入 EventStore → 应用 Reducer → 通知订阅者。

        参数:
          current: 当前 state
          patch: Node 返回的 state_patch
          event_type: 事件类型（如 "SkillCheck", "SanityLost"）
          source_node: 产生此事件的 Node 名
          parent_event_id: 父事件 ID（因果链）
          extra_data: 事件中 patch 之外的额外数据

        返回:
          (new_state, event_record)
        """
        world_id = current.get("world_id", "")
        if not world_id:
            raise ValueError("world_id 不能为空，事件溯源需 world_id 标识事件流")

        new_state = reduce_state(current, patch)

        event_data: dict = {"patch": patch}
        if extra_data:
            event_data.update(extra_data)

        event_record = await self._store.append(
            world_id=world_id,
            event_type=event_type,
            data=event_data,
            source_node=source_node,
            parent_event_id=parent_event_id,
        )

        # 通知订阅者
        await self._notify(event_record)

        return new_state, event_record

    # ── 查询与回放 ──

    async def get_events(
        self, world_id: str, since_version: int = 0
    ) -> list[dict]:
        """获取指定世界的事件流"""
        return await self._store.get_events(world_id, since_version)

    async def replay_to_state(
        self,
        world_id: str,
        base_state: Optional[GameState] = None,
    ) -> GameState:
        """回放指定世界的全部事件重建状态"""
        if base_state is None:
            base_state = create_initial_state(world_id=world_id)
        events = await self._store.get_events(world_id, since_version=0)
        return apply_events_to_state(base_state, events)

    async def get_latest_version(self, world_id: str) -> int:
        """获取指定世界的最新版本号"""
        return await self._store.get_latest_version(world_id)

    async def get_event_count(self, world_id: str) -> int:
        """获取指定世界的事件总数"""
        return await self._store.get_event_count(world_id)

    async def rollback_to_version(
        self,
        world_id: str,
        target_version: int,
        base_state: Optional[GameState] = None,
    ) -> Optional[GameState]:
        """回滚到指定事件版本，返回重建后的 GameState"""
        latest = await self._store.get_latest_version(world_id)
        if target_version > latest or target_version < 0:
            return None

        events = await self._store.get_events(world_id, since_version=0)
        events = [e for e in events if e.get("version", 0) <= target_version]

        for evt in reversed(events):
            data = evt.get("data", {})
            snapshot = data.get("state_snapshot") if isinstance(data, dict) else None
            if snapshot:
                snapshot["player_input"] = ""
                snapshot["intent"] = None
                snapshot["resolution"] = None
                snapshot["intent_queue"] = []
                snapshot["current_intent_idx"] = 0
                snapshot["executed_actions"] = []
                return snapshot

        if base_state is None:
            base_state = create_initial_state(world_id=world_id)
        return apply_events_to_state(base_state, events)

    # ── 订阅机制 ──

    def subscribe(self, event_type: str, callback: EventCallback):
        """
        订阅特定类型的事件。

        参数:
          event_type: 事件类型（空字符串 "" 表示订阅所有事件）
          callback: 异步回调函数 async def cb(event: dict) -> None
        """
        if event_type not in self._subscriptions:
            self._subscriptions[event_type] = []
        self._subscriptions[event_type].append(callback)

    def unsubscribe(self, event_type: str, callback: EventCallback):
        """取消订阅"""
        if event_type in self._subscriptions:
            self._subscriptions[event_type] = [
                cb for cb in self._subscriptions[event_type] if cb is not callback
            ]

    async def _notify(self, event: dict):
        """通知所有匹配的订阅者"""
        event_type = event.get("type", "")

        # 通知特定类型订阅者
        for cb in self._subscriptions.get(event_type, []):
            try:
                await cb(event)
            except Exception:
                pass  # 订阅者异常不影响主流程

        # 通知全通配订阅者
        for cb in self._subscriptions.get("", []):
            try:
                await cb(event)
            except Exception:
                pass

    # ── 工具方法 ──

    async def clear_world(self, world_id: str):
        """清空指定世界的事件（仅测试用）"""
        await self._store.clear_world(world_id)

    async def close(self):
        """关闭底层存储连接"""
        await self._store.close()
