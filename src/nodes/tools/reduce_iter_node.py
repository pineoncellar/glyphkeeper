"""
@File     :   reduce_iter_node.py
@Desc     :   循环内即时 Reducer — 将当前步的 deterministic_changes 写回 GameState
@Note     :   读取 executed_actions[-1].deterministic_changes，
              直接作为 state_patch 返回，LangGraph 的自动合并机制负责写入。
"""

from __future__ import annotations

from src.state.game_state import GameState
from src.tools import get_logger

logger = get_logger(__name__)


async def reduce_iter_node(state: GameState) -> dict:
    """循环内即时 Reducer 节点

    读取 executed_actions[-1].deterministic_changes 返回为 state_patch。
    确保循环中下一轮迭代能看到上一轮产生的状态变更
    （如 MOVE 后的 current_location 更新）。
    """
    actions = state.get("executed_actions", [])
    if not actions:
        return {}

    last = actions[-1]
    changes = last.get("deterministic_changes", {})

    if changes:
        logger.debug(f"reduce_iter: 即时回写 {list(changes.keys())}")

    return changes
