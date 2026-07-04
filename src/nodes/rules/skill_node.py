"""
@File     :   skill_node.py
@Desc     :   技能检定节点 — 转发至物理交互子图（向后兼容层）
@Note     :   PHYSICAL_INTERACT 现已由 physical_interact_subgraph 接管，
              此文件保留 skill_node / batch_skill_check 导出供旧版调用者使用。
              skill_node 转发到 run_physical_interact_subgraph。
"""

from __future__ import annotations

from src.state.game_state import GameState
from src.nodes.physical.physical_interact_graph import run_physical_interact_subgraph
from src.tools import get_logger

logger = get_logger(__name__)


async def skill_node(state: GameState) -> dict:
    """技能检定节点 — 转发至物理交互子图（向后兼容）

    PHYSICAL_INTERACT 现已由 physical_interact_subgraph 接管，
    此处转发确保旧版 graph 或直接调用仍可工作。
    """
    return await run_physical_interact_subgraph(state)


async def batch_skill_check(state: GameState) -> dict:
    """
    批量技能检定节点 — 一次检定多项技能。

    intent.data.skills 格式:
        [{"name": "侦查", "difficulty": "HARD"}, {"name": "聆听", "bonus_dice": 1}]
    """
    intent = state.get("intent") or {}
    intent_data = intent.get("data") or {}
    skills_config = intent_data.get("skills", [])

    if not skills_config:
        return await skill_node(state)

    results = []
    character_data = get_current_player(state).get("character")

    for sc in skills_config:
        sname = sc.get("name", "")
        sdiff = _parse_difficulty(sc.get("difficulty", "REGULAR"))
        sbonus = sc.get("bonus_dice", 0)
        spenalty = sc.get("penalty_dice", 0)

        sval = sc.get("skill_value")
        if sval is None and character_data:
            skills = character_data.get("skills") or {}
            sval = skills.get(sname)

        if sval is None:
            sval = 50

        try:
            r = skill_check(sval, sdiff, sbonus, spenalty)
            results.append({
                "skill_name": sname,
                "skill_value": sval,
                "roll_value": r.roll_value,
                "success_level": r.success_level.value,
                "success_label": _success_label(r.success_level),
                "is_success": r.is_success,
                "difficulty": sdiff.value,
            })
        except Exception as e:
            results.append({
                "skill_name": sname,
                "error": str(e),
            })

    all_success = all(r.get("is_success", False) for r in results)

    return {
        "resolution": {
            "success": True,
            "node_type": "batch_skill_check",
            "results": results,
            "all_success": all_success,
            "success_count": sum(1 for r in results if r.get("is_success")),
            "total_count": len(results),
        },
    }
