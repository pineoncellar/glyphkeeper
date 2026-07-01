"""
@File     :   keeper_graph.py
@Desc     :   守密人主 Graph — 双脑路由拓扑
@Note     :   db_lookup 始终执行（毫秒级 PG 查物理现实）
              navigation 移动成功时自身输出新位置的 physical_reality
              rag_lookup 条件执行（按 intent.needs_rag 决定是否查 LightRAG）

主流程:
    START → intent → db_lookup → disambiguation → router
        ├── combat  → combat_subgraph         → rag_lookup → narrate
        ├── investigate → investigate_subgraph → rag_lookup → narrate
        ├── navigation → navigation_node      → rag_lookup → narrate
        │   └─ 成功时自负 physical_reality 刷新
        ├── npc_dialogue → npc_dialogue_node   → rag_lookup → narrate
        └── narrate (直接)                          → rag_lookup → narrate
                                                                    ↓
                                                             state_extractor → END

节点说明:
  - intent:           IntentNode（LLM 意图分析 + needs_rag 标记）
  - db_lookup:        DB Lookup Node（查 PG 读模型表 → <physical_reality> XML）
  - disambiguation:   DisambiguationNode（按意图策略路由，三级降级匹配实体 ID）
  - rag_lookup:       RAG Lookup Node（按需查 LightRAG → <semantic_knowledge>）
  - navigation:       NavigationNode（纯逻辑验证出口并更新位置）
  - state_extractor:  StateExtractorNode（用 fast LLM 按三级系统提取状态变更）
  - 其余节点保持不变
"""

from __future__ import annotations

from langgraph.graph import StateGraph, START, END
from src.state.game_state import GameState
from src.nodes.llm.intent_node import intent_node
from src.nodes.llm.narrator_node import narrate_node
from src.nodes.llm.npc_dialogue_node import npc_dialogue_node
from src.nodes.tools.db_lookup_node import db_lookup_node
from src.nodes.tools.rag_lookup_node import rag_lookup_node
from src.nodes.tools.disambiguation_node import disambiguation_node
from src.nodes.tools.state_extractor_node import state_extractor_node
from src.nodes.rules.navigation_node import navigation_node
from src.graph.router_graph import route_by_intent
from src.graph.combat_graph import combat_subgraph
from src.graph.investigation_graph import investigation_subgraph
from src.tools import get_logger

logger = get_logger(__name__)


def build_keeper_graph() -> StateGraph:
    """构建并返回守密人主 StateGraph（双脑路由拓扑）

    Assembles all nodes, subgraphs, and conditional edges.

    Returns:
        CompiledStateGraph ready for GraphEngine
    """
    builder = StateGraph(GameState)

    # ── 注册节点 ──
    builder.add_node("intent", intent_node)
    builder.add_node("db_lookup", db_lookup_node)
    builder.add_node("disambiguation", disambiguation_node)
    builder.add_node("rag_lookup", rag_lookup_node)
    builder.add_node("narrate", narrate_node)
    builder.add_node("combat", combat_subgraph)
    builder.add_node("investigate", investigation_subgraph)
    builder.add_node("npc_dialogue", npc_dialogue_node)
    builder.add_node("navigation", navigation_node)
    builder.add_node("state_extractor", state_extractor_node)

    # ── 定义边 ──

    # START → 意图分析 → 查 PG 物理现实（始终执行）→ 实体对齐
    builder.add_edge(START, "intent")
    builder.add_edge("intent", "db_lookup")
    builder.add_edge("db_lookup", "disambiguation")

    # disambiguation → 条件路由到子图或直接叙事
    builder.add_conditional_edges(
        "disambiguation",
        route_by_intent,
        {
            "combat": "combat",
            "investigate": "investigate",
            "navigation": "navigation",
            "npc_dialogue": "npc_dialogue",
            "narrate": "rag_lookup",  # 直接叙事也走 rag_lookup（内部判断是否需查 RAG）
        },
    )

    # 子图/NPC 对话/导航执行完后 → rag_lookup（条件查 LightRAG）
    # 移动成功后 navigation_node 自身已刷新 physical_reality，无需二次 db_lookup
    builder.add_edge("combat", "rag_lookup")
    builder.add_edge("investigate", "rag_lookup")
    builder.add_edge("navigation", "rag_lookup")
    builder.add_edge("npc_dialogue", "rag_lookup")

    # rag_lookup → 叙事 → 状态提取 → 结束
    # state_extractor 用 fast LLM 从叙事文中提取 Tier 1/Tier 2 信息，
    # 由 Engine 后台异步追赶固化到 PG 和 LightRAG
    builder.add_edge("rag_lookup", "narrate")
    builder.add_edge("narrate", "state_extractor")
    builder.add_edge("state_extractor", END)

    compiled = builder.compile()
    logger.info("keeper_graph: 双脑路由主图编译完成")
    return compiled


# ── 导出编译好的主图实例 ──
keeper_graph = build_keeper_graph()
