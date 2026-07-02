"""
@File     :   loop_guard_node.py
@Desc     :   循环守卫 — 检查意图队列指针，决定继续循环或跳转到叙事
@Note     :   不修改任何 state 字段，纯读操作。路由由 route_loop 条件边控制。
"""

from __future__ import annotations

from src.state.game_state import GameState
from src.tools import get_logger

logger = get_logger(__name__)


async def loop_guard_node(state: GameState) -> dict:
    """循环守卫 — 检查是否还有未处理的意图

    返回空 dict 不修改 state，条件边 route_loop 根据
    current_intent_idx 和 intent_queue 长度决定下一跳。
    """
    idx = state.get("current_intent_idx", 0)
    queue = state.get("intent_queue", [])
    is_continue = idx < len(queue)
    logger.debug(f"loop_guard: idx={idx}/{len(queue)} → {'continue' if is_continue else 'narrate'}")
    return {}
