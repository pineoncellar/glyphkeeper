"""
@File     :   reducer.py
@Desc     :   State Reducer — 唯一允许修改 State 的地方
@Note     :   Node 返回 state_patch → Engine 调用 Reducer → Reducer 生成新 State

核心原则:
  ❗ LLM Node 禁止直接修改 state
  ❗ Rule Node 禁止直接修改 state
  ❗ Tool Node 禁止直接修改 state
  ✅ Node 只能通过返回 state_patch 请求修改
  ✅ Engine 调用 Reducer 合并 state_patch 到 state
  ✅ 每次修改都生成一条不可变 Event
"""

from __future__ import annotations

import copy
from typing import Any
from src.state.game_state import GameState


# ── 合并策略常量 ──

APPEND_FIELDS = {"active_tags", "errors", "node_trace", "combatants"}
"""需要追加而非替换的 list 字段"""

DEEP_MERGE_FIELDS = {"intent", "resolution", "pending_dice"}
"""需要深度合并的 dict 字段"""

COUNTER_FIELDS = {"beat_counter", "combat_round"}
"""数值计数器字段（支持增量）"""


# ── 工具函数 ──

def _deep_merge(base: dict, override: dict) -> dict:
    """
    递归深度合并两个 dict。
    override 中的值覆盖 base 中同名字段。
    嵌套 dict 递归合并，非 dict 字段直接替换。
    """
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def reduce_state(current: GameState, patch: dict) -> GameState:
    """
    合并 state_patch 到当前 state — 唯一允许修改 State 的地方。

    规则:
      1. patch 中 value=None 的字段 → 从 state 中删除
      2. list 字段（在 APPEND_FIELDS 中）→ 追加而非替换
      3. dict 字段（在 DEEP_MERGE_FIELDS 中）→ 递归深度合并
      4. 数值计数器字段（在 COUNTER_FIELDS 中）→ 增量操作
      5. 其他字段 → 直接替换

    参数:
      current: 当前完整 state
      patch:  Node 返回的增量更新 dict

    返回:
      合并后的新 state（浅拷贝 + 按规则合并）
    """
    new_state: GameState = dict(current)  # 浅拷贝

    for key, value in patch.items():
        # 规则 1: None 值表示删除字段
        if value is None:
            new_state.pop(key, None)
            continue

        # 规则 2: list 字段追加
        if key in APPEND_FIELDS:
            existing = new_state.get(key)
            if isinstance(existing, list):
                new_state[key] = existing + value
            else:
                new_state[key] = value if isinstance(value, list) else [value]
            continue

        # 规则 3: dict 字段递归深度合并
        if key in DEEP_MERGE_FIELDS:
            existing = new_state.get(key)
            if isinstance(existing, dict) and isinstance(value, dict):
                new_state[key] = _deep_merge(existing, value)
            else:
                new_state[key] = value
            continue

        # 规则 4: 计数器字段支持增量（value 以 "+N" 或 "-N" 字符串形式）
        if key in COUNTER_FIELDS and isinstance(value, str) and (value.startswith("+") or value.startswith("-")):
            try:
                delta = int(value)
                new_state[key] = new_state.get(key, 0) + delta
            except (ValueError, TypeError):
                new_state[key] = value
            continue

        # 规则 5: 直接替换
        new_state[key] = value

    return new_state


def apply_events_to_state(state: GameState, events: list[dict]) -> GameState:
    """
    将一系列事件回放到 state 上，重建状态。

    每个 event 的 data 字段应包含 {"patch": {...}}，
    reducer 将按事件顺序逐一合并。

    参数:
      state: 起始状态（可以是空状态或快照）
      events: 按版本升序排列的事件列表

    返回:
      回放所有事件后的新 state
    """
    new_state = dict(state)  # 浅拷贝
    for event in events:
        patch = event.get("data", {}).get("patch", {})
        if patch:
            new_state = reduce_state(new_state, patch)
    return new_state


def merge_patches(patch_a: dict, patch_b: dict) -> dict:
    """
    合并两个 state_patch（用于批量处理）。
    patch_b 中的字段优先级高于 patch_a。

    规则:
      - list 字段: 先追加 a 再追加 b
      - dict 字段: 深度合并（b 覆盖 a）
      - 其他字段: b 覆盖 a
    """
    merged = copy.deepcopy(patch_a)

    for key, value in patch_b.items():
        if value is None:
            merged.pop(key, None)
        elif key in APPEND_FIELDS:
            existing = merged.get(key)
            if isinstance(existing, list):
                merged[key] = existing + (value if isinstance(value, list) else [value])
            else:
                merged[key] = value if isinstance(value, list) else [value]
        elif key in DEEP_MERGE_FIELDS:
            existing = merged.get(key)
            if isinstance(existing, dict) and isinstance(value, dict):
                merged[key] = _deep_merge(existing, value)
            else:
                merged[key] = value
        else:
            merged[key] = value

    return merged
