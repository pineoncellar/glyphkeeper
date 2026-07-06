"""
@File     :   keeper_graph.py
@Desc     :   守密人主 Graph — 串行循环管线拓扑
@Note     :   四阶段流程：
              意图裂变 → 串行隔离裁决循环 → 叙事总装 → 状态固化
              loop_guard 条件边驱动循环；dispatch 按意图类型分流；
              reduce_iter 在循环内即时回写状态变更。
"""

from __future__ import annotations

from langgraph.graph import StateGraph, START, END
from src.state.game_state import GameState
from src.nodes.llm.intent_node import intent_node
from src.nodes.llm.narrator_node import narrate_node
from src.nodes.tools.db_lookup_node import db_lookup_node
from src.nodes.tools.rag_lookup_node import rag_lookup_node
from src.nodes.tools.disambiguation_node import disambiguation_node
from src.nodes.tools.state_extractor_node import state_extractor_node
from src.nodes.tools.loop_guard_node import loop_guard_node
from src.nodes.tools.dispatch_node import dispatch_node
from src.nodes.tools.reduce_iter_node import reduce_iter_node
from src.tools import get_logger

logger = get_logger(__name__)


def route_loop(state: GameState) -> str:
    """循环条件路由 — 由 loop_guard 调用

    检查 current_intent_idx 是否超出 intent_queue 长度。
    未超则继续循环（continue），已超则进入叙事总装（narrate）。
    """
    idx = state.get("current_intent_idx", 0)
    queue = state.get("intent_queue", [])
    return "continue" if idx < len(queue) else "narrate"


def build_keeper_graph() -> StateGraph:
    """构建并返回守密人主 StateGraph（串行循环管线）

    拓扑结构:
      START → intent (意图裂变)
           → loop_guard (条件边)
               ├── continue → db_lookup → disambiguation → rag_lookup → dispatch → reduce_iter → loop_guard
               └── narrate  → narrate → state_extractor → END

    Returns:
        CompiledStateGraph ready for GraphEngine
    """
    builder = StateGraph(GameState)

    # ── 意图裂变 ──
    builder.add_node("intent", intent_node)

    # ── 循环守卫 ──
    builder.add_node("loop_guard", loop_guard_node)

    # ── 隔离裁决循环体 ──
    builder.add_node("db_lookup", db_lookup_node)
    builder.add_node("disambiguation", disambiguation_node)
    builder.add_node("rag_lookup", rag_lookup_node)
    builder.add_node("dispatch", dispatch_node)
    builder.add_node("reduce_iter", reduce_iter_node)

    # ── 叙事总装 ──
    builder.add_node("narrate", narrate_node)

    # ── 状态固化 ──
    builder.add_node("state_extractor", state_extractor_node)

    # ── 定义边 ──
    builder.add_edge(START, "intent")
    builder.add_edge("intent", "loop_guard")

    # loop_guard → 条件路由：还有意图循环，否则叙事
    builder.add_conditional_edges(
        "loop_guard",
        route_loop,
        {"continue": "db_lookup", "narrate": "narrate"},
    )

    # 循环体：db_lookup → disambiguation → rag_lookup → dispatch → reduce_iter → 回到 loop_guard
    builder.add_edge("db_lookup", "disambiguation")
    builder.add_edge("disambiguation", "rag_lookup")
    builder.add_edge("rag_lookup", "dispatch")
    builder.add_edge("dispatch", "reduce_iter")
    builder.add_edge("reduce_iter", "loop_guard")

    # 叙事总装 → 状态固化 → END
    builder.add_edge("narrate", "state_extractor")
    builder.add_edge("state_extractor", END)

    compiled = builder.compile()
    logger.info("keeper_graph: 串行循环管线主图编译完成")
    return compiled


# ── 导出编译好的主图实例 ──
keeper_graph = build_keeper_graph()
