"""
@File     :   keeper_graph.py
@Desc     :   守密人主 Graph — 系统的核心执行拓扑
@Note     :   编译后通过 runtime/engine.py 的 GraphEngine 驱动

主流程:
    START → intent → router → [combat_subgraph]  ──→ narrate → END
                               [investigate_subgraph] ─┘
                               [narrate (直接)] ──────┘

节点说明:
  - intent:       IntentNode（LLM 意图分析 + 规则兜底）
  - combat:       CombatSubgraph（战斗流程：掷骰→裁决→循环）
  - investigate:  InvestigationSubgraph（调查流程：技能检定）
  - narrate:      NarratorNode（LLM 叙事生成 + 模板兜底）

路由规则:
  - COMBAT_ACTION     → combat subgraph
  - MOVE / PHYSICAL   → investigate subgraph
  - SOCIAL / META/其他 → 直接 narrate
"""

from __future__ import annotations

from langgraph.graph import StateGraph, START, END
from src.state.game_state import GameState
from src.nodes.llm.intent_node import intent_node
from src.nodes.llm.narrator_node import narrate_node
from src.graph.router_graph import route_by_intent
from src.graph.combat_graph import combat_subgraph
from src.graph.investigation_graph import investigation_subgraph
from src.config import get_logger

logger = get_logger(__name__)


def build_keeper_graph() -> StateGraph:
    """构建并返回守密人主 StateGraph

    组装所有节点和子图，定义边与条件路由。

    Returns:
        编译好的 CompiledStateGraph
    """
    builder = StateGraph(GameState)

    # ── 注册节点 ──
    builder.add_node("intent", intent_node)
    builder.add_node("narrate", narrate_node)
    builder.add_node("combat", combat_subgraph)          # 战斗子图
    builder.add_node("investigate", investigation_subgraph)  # 调查子图

    # ── 定义边 ──
    # START → 意图分析
    builder.add_edge(START, "intent")

    # intent → 条件路由
    builder.add_conditional_edges(
        "intent",
        route_by_intent,
        {
            "combat": "combat",
            "investigate": "investigate",
            "narrate": "narrate",
        },
    )

    # 子图执行完后 → 叙事
    builder.add_edge("combat", "narrate")
    builder.add_edge("investigate", "narrate")

    # 叙事 → 结束
    builder.add_edge("narrate", END)

    compiled = builder.compile()
    logger.info("keeper_graph: 守密人主图编译完成")
    return compiled


# ── 导出编译好的主图实例 ──
keeper_graph = build_keeper_graph()
