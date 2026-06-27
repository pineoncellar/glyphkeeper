"""
@File     :   investigation_graph.py
@Desc     :   调查/探索子 Graph — 技能检定 + 线索查询拓扑
@Note     :   DB 查询已上提至 keeper_graph 顶层统一执行

流程:
    START → [有技能名] → resolve_skill → archivist → END
            [无技能名] → END

节点说明:
  - resolve_skill: skill_node，执行技能检定（纯确定性逻辑）
  - archivist:     archivist_node，检定成功后解析目标并查线索
"""

from __future__ import annotations

from langgraph.graph import StateGraph, START, END
from src.state.game_state import GameState
from src.nodes.rules.skill_node import skill_node as skill_node_fn
from src.nodes.tools.archivist_node import archivist_node as archivist_node_fn
from src.tools import get_logger

logger = get_logger(__name__)


def _has_skill_name(state: GameState) -> str:
    """判断 intent 中是否包含技能名称，用于条件路由"""
    intent = state.get("intent") or {}
    data = intent.get("data") or {}
    skill_name = data.get("skill_name", "")
    if skill_name:
        logger.debug(f"investigation_graph: 技能检定 '{skill_name}' → resolve_skill")
        return "resolve_skill"
    logger.debug("investigation_graph: 无技能名 → end")
    return "end"


def build_investigation_subgraph() -> StateGraph:
    """构建并返回调查/探索子 StateGraph"""
    builder = StateGraph(GameState)

    builder.add_node("resolve_skill", skill_node_fn)
    builder.add_node("archivist", archivist_node_fn)

    builder.add_conditional_edges(
        START,
        _has_skill_name,
        {"resolve_skill": "resolve_skill", "end": END},
    )
    builder.add_edge("resolve_skill", "archivist")
    builder.add_edge("archivist", END)

    compiled = builder.compile()
    logger.info("investigation_graph: 调查子图（含线索查询）编译完成")
    return compiled


# ── 导出编译好的子图实例 ──
investigation_subgraph = build_investigation_subgraph()
