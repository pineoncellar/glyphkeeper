"""
@File     :   physical_interact_graph.py
@Desc     :   物理交互子图 — 三阶段链路（检定→仲裁→结算）的 LangGraph 编译与包裹函数
@Note     :   与主图共享同一个 GameState TypedDict。
              包裹函数 run_physical_interact_subgraph 对 dispatch_node 完全透明，
              调用方感知不到内部是子图还是单函数。
"""

from __future__ import annotations

from langgraph.graph import StateGraph, START, END
from src.state.game_state import GameState
from src.nodes.physical.skill_check_node import skill_check_node
from src.nodes.physical.spatial_physics_node import spatial_physics_node
from src.nodes.physical.effect_archivist_node import effect_archivist_node
from src.tools import get_logger

logger = get_logger(__name__)


def build_physical_interact_graph():
    """构建物理交互子图 — 三阶段串行链路

    子图拓扑:
      START → skill_check_node → spatial_physics_node → effect_archivist_node → END

    注意: 子图节点通过 _skill_check_result / _spatial_result 临时字段传递中间结果，
          只有最后一个节点 effect_archivist_node 追加 executed_actions。
          因此即使子图内部有多个节点，最终 executed_actions 中也只有一条记录。
    """
    builder = StateGraph(GameState)

    builder.add_node("skill_check", skill_check_node)
    builder.add_node("spatial_physics", spatial_physics_node)
    builder.add_node("effect_archivist", effect_archivist_node)

    builder.add_edge(START, "skill_check")
    builder.add_edge("skill_check", "spatial_physics")
    builder.add_edge("spatial_physics", "effect_archivist")
    builder.add_edge("effect_archivist", END)

    compiled = builder.compile()
    logger.info("physical_interact_graph: 子图编译完成")
    return compiled


# ── 编译好的子图实例（模块级单例） ──
_physical_interact_graph = build_physical_interact_graph()


async def run_physical_interact_subgraph(state: GameState) -> dict:
    """物理交互子图的包裹函数 — 对 dispatch_node 完全透明

    调用方（dispatch_node）感知不到内部是子图还是单函数。
    返回值格式与旧的 skill_node 完全一致:
      {
          "executed_actions": [...],       # 一条 ActionExecutionResult
          "current_intent_idx": int,        # 由 dispatch_node 后续递增
      }
    """
    result_state = await _physical_interact_graph.ainvoke(state)

    # 从子图结果中提取增量
    patch = {
        "executed_actions": result_state.get("executed_actions", []),
        "current_intent_idx": result_state.get("current_intent_idx", 0),
    }

    # 清理临时字段（子图出口已由 effect_archivist_node 设为 None，此处兜底）
    if "_skill_check_result" in result_state:
        patch["_skill_check_result"] = None
    if "_spatial_result" in result_state:
        patch["_spatial_result"] = None

    return patch
