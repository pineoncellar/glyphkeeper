"""
@File     :   dispatch_node.py
@Desc     :   意图分发节点 — 根据当前意图类型路由到对应规则节点执行
@Note     :   从 intent_queue[current_intent_idx] 读取当前意图，
              路由到对应节点函数，递增指针。同时注入 flavor_context。
"""

from __future__ import annotations

from src.state.game_state import GameState
from src.nodes.rules.combat_node import combat_node
from src.nodes.rules.skill_node import skill_node
from src.nodes.rules.navigation_node import navigation_node
from src.nodes.llm.npc_dialogue_node import npc_dialogue_node
from src.tools import get_logger

logger = get_logger(__name__)


# 意图类型 → 节点函数的路由表
_DISPATCH_TABLE: dict[str, callable] = {
    "COMBAT_ACTION": combat_node,
    "PHYSICAL_INTERACT": skill_node,
    "SOCIAL_INTERACT": npc_dialogue_node,
    "MOVE": navigation_node,
}


async def dispatch_node(state: GameState) -> dict:
    """根据当前意图类型分发到对应规则节点执行

    从 intent_queue[current_intent_idx] 读取当前意图，
    路由到对应的规则节点，递增指针。
    未知或 META 意图直接跳过。
    """
    idx = state.get("current_intent_idx", 0)
    queue = state.get("intent_queue", [])

    if idx >= len(queue):
        return {"current_intent_idx": idx + 1}

    current = queue[idx]
    intent_type = current.get("type", "META")

    node_fn = _DISPATCH_TABLE.get(intent_type)
    if node_fn is None:
        logger.debug(f"dispatch: 跳过 {intent_type} (intent_{idx})")
        return {"current_intent_idx": idx + 1}

    result = await node_fn(state)

    # 注入 flavor_context 和 core_action（规则节点不感知意图层字段，dispatch 负责注入）
    if result.get("executed_actions"):
        for action in result["executed_actions"]:
            if not action.get("flavor_context"):
                action["flavor_context"] = current.get("flavor_context", "")
            if not action.get("core_action"):
                action["core_action"] = current.get("core_action", "")
            if not action.get("detail"):
                action["detail"] = current.get("data", {}).get("detail", "")

    # 递增指针
    result["current_intent_idx"] = idx + 1

    return result
