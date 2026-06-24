"""
@File     :   router_graph.py
@Desc     :   意图路由函数 — 根据 IntentType 分发到对应子 Graph
@Note     :   供 keeper_graph.py 的条件边使用

路由规则:
  - COMBAT_ACTION       → combat
  - MOVE                → investigate
  - PHYSICAL_INTERACT   → investigate
  - SOCIAL_INTERACT     → narrate
  - META                → narrate
  - 未知类型            → narrate（兜底）

使用方式:
    builder.add_conditional_edges("intent", route_by_intent, {
        "combat": "combat",
        "investigate": "investigate",
        "narrate": "narrate",
    })
"""

from __future__ import annotations

from src.state.game_state import GameState
from src.config import get_logger

logger = get_logger(__name__)

# ── 路由表 ──
_ROUTING_TABLE: dict[str, str] = {
    "COMBAT_ACTION": "combat",
    "MOVE": "investigate",
    "PHYSICAL_INTERACT": "investigate",
    "SOCIAL_INTERACT": "narrate",
    "META": "narrate",
}


def route_by_intent(state: GameState) -> str:
    """
    根据意图类型路由到对应的子 Graph。

    Args:
        state: 当前 GameState（需要包含 intent 字段）

    Returns:
        下一个节点的名称:
        - "combat"      → CombatGraph
        - "investigate" → InvestigationGraph
        - "narrate"     → 直接叙事（兜底）
    """
    intent = state.get("intent")
    if not intent:
        logger.debug("route_by_intent: 无 intent，路由到 narrate")
        return "narrate"

    intent_type = intent.get("type", "")
    target = _ROUTING_TABLE.get(intent_type, "narrate")

    logger.debug(f"route_by_intent: {intent_type} → {target}")
    return target
