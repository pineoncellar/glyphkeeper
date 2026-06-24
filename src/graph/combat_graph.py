"""
@File     :   combat_graph.py
@Desc     :   战斗子 Graph — 定义战斗回合的独立执行拓扑
@Note     :   编译为 CompiledStateGraph，作为节点嵌入 keeper_graph

流程:
    START → dice_roll → resolve_combat
        ↑                    ↓ (combat_active & enemies_alive)
        └────── continue ────┘
                              ↓ (combat_end)
                             END

节点说明:
  - dice_roll:      调用 dice_node，处理 pending_dice 中的掷骰请求
  - resolve_combat: 调用 combat_node，执行战斗轮裁决
  - 条件边:         combat_active=True 且存在存活敌人时继续，否则结束
"""

from __future__ import annotations

from langgraph.graph import StateGraph, START, END
from src.state.game_state import GameState, create_initial_state
from src.nodes.tools.dice_node import dice_node as dice_node_fn
from src.nodes.rules.combat_node import combat_node as combat_node_fn
from src.config import get_logger

logger = get_logger(__name__)


def _check_combat_end(state: GameState) -> str:
    """判断战斗是否结束

    检查 state 中的 combat_active 标志、combatants 存活状态和最大轮次:
      - combat_active=False            → end
      - 无存活敌人                     → end
      - combat_round >= MAX_ROUNDS     → end（循环保护）
      - 仍有存活敌人                   → continue
    """
    MAX_ROUNDS = 10  # 安全上限，防止无限循环

    if not state.get("combat_active"):
        logger.debug("combat_graph: combat_active=False → end")
        return "end"

    combat_round = state.get("combat_round", 0)
    if combat_round >= MAX_ROUNDS:
        logger.warning(f"combat_graph: 达到最大轮次 {MAX_ROUNDS} → end")
        return "end"

    combatants = state.get("combatants", [])
    # 检查是否有存活敌人（名字不同于当前角色的 combatant 且 HP > 0）
    character = state.get("character") or {}
    pc_name = character.get("name", "")
    enemies_alive = any(
        c.get("hit_points", 0) > 0
        for c in combatants
        if c.get("name", "") != pc_name
    )

    if not enemies_alive:
        logger.debug("combat_graph: 无存活敌人 → end")
        return "end"

    logger.debug(f"combat_graph: 战斗继续 (round {combat_round + 1}) → continue")
    return "continue"


def build_combat_subgraph() -> StateGraph:
    """构建并返回战斗子 StateGraph"""
    builder = StateGraph(GameState)

    builder.add_node("dice_roll", dice_node_fn)
    builder.add_node("resolve_combat", combat_node_fn)

    builder.add_edge(START, "dice_roll")
    builder.add_edge("dice_roll", "resolve_combat")
    builder.add_conditional_edges(
        "resolve_combat",
        _check_combat_end,
        {"continue": "dice_roll", "end": END},
    )

    compiled = builder.compile()
    logger.info("combat_graph: 战斗子图编译完成")
    return compiled


# ── 导出编译好的子图实例 ──
combat_subgraph = build_combat_subgraph()
