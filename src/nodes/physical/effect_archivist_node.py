"""
@File     :   effect_archivist_node.py
@Desc     :   结算与线索颁发节点 — 合并检定+仲裁结果，输出最终 ActionExecutionResult
@Note     :   从旧 archivist_node 搬迁重构，去掉了独立的目标解析（复用 resolved_targets）。
              普通物品: physical_executed=true + execution_phase=NORMAL 时查线索。
              即兴物品: execution_phase=IMPROMPTU 时跳过线索查询，产出落包单。
              追加一条完整的 ActionExecutionResult 到 executed_actions，并清理临时字段。
"""

from __future__ import annotations

from typing import Optional
from src.state.game_state import GameState, get_current_player
from src.tools import get_logger

logger = get_logger(__name__)


# ====================================================================
# 线索查询
# ====================================================================


async def _query_clues(
    session_id: str,
    target_key: str,
    skill_name: str,
    skill_value: int,
    roll_value: int,
    character_name: str,
) -> list[dict]:
    """在物理执行确认后查询线索

    只有首次成功搜索（execution_phase=NORMAL）才会真正查询。
    复用 Archivist 数据访问层，但不再自己做 target 解析。
    """
    if not target_key:
        return []

    try:
        from src.tools.archivist import Archivist
        archivist = Archivist()
        clue_result = await archivist.inspect_target(
            session_id=session_id,
            target_key=target_key,
            skill_name=skill_name,
            skill_value=skill_value,
            roll_value=roll_value,
            character_name=character_name,
        )
        if clue_result:
            logger.info(
                f"effect_archivist: 线索发现! "
                f"knowledge={clue_result.get('knowledge_id')}"
            )
            return [clue_result]
    except Exception as e:
        logger.debug(f"effect_archivist: 线索查询异常（非阻塞）: {e}")

    return []


# ====================================================================
# 状态变更补丁
# ====================================================================


def _build_state_changes(
    spatial: dict,
    target_key: str,
    target_name: str,
) -> dict:
    """基于执行结果生成状态变更补丁

    NORMAL 执行后标记物品为已搜索（通过 _mark_searched 通知外部处理器）。
    IMPROMPTU 即兴路径跳过线索查询，直接产出落包通知和防复刷锁。
    其他状态不产生确定性变更。
    """
    if not spatial.get("physical_executed"):
        return {}

    phase = spatial.get("execution_phase", "")
    if phase == "IMPROMPTU":
        # 从 impromptu_xxx 格式的 key 中提取物品名
        item_name = target_name or target_key
        if target_key and target_key.startswith("impromptu_"):
            item_name = target_key[len("impromptu_"):]
        return {
            "_mark_searched": f"impromptu_{item_name}",
            "_inventory_append": item_name,
        }
    if phase == "NORMAL":
        return {"_mark_searched": target_key}
    return {}


# ====================================================================
# Node 主函数
# ====================================================================


async def effect_archivist_node(state: GameState) -> dict:
    """结算与线索颁发节点

    合并 _skill_check_result 和 _spatial_result，输出完整的 ActionExecutionResult。
    只有满足以下全部条件时才会查询线索:
      - skill_check 检定成功（或 bypassed）
      - spatial_physics 判定 physical_executed=True
      - execution_phase == "NORMAL"（首次搜索）

    返回的 state_patch 包含:
      - executed_actions: 一条完整的 ActionExecutionResult
      - _skill_check_result: None（清理临时字段）
      - _spatial_result: None（清理临时字段）
    """
    check = state.get("_skill_check_result") or {}
    spatial = state.get("_spatial_result") or {}
    idx = state.get("current_intent_idx", 0)
    queue = state.get("intent_queue", [])
    current_intent = queue[idx] if idx < len(queue) else {}

    # 读取基础字段
    skill_name = check.get("skill_name", "")
    roll_value = check.get("roll_value", 0)
    is_success = check.get("is_success", True)
    bypassed = check.get("bypassed", False)

    target_key = spatial.get("_target_key", "")
    physical_executed = spatial.get("physical_executed", False)
    execution_phase = spatial.get("execution_phase", "")
    is_locked = spatial.get("is_locked", False)
    is_searched = spatial.get("is_searched", False)

    # 读取意图目标名（即兴落包时用于生成物品名）
    intent_data = current_intent.get("data", {})
    target_name = intent_data.get("target", "")

    # 决定是否查线索：
    # NORMAL 路径 — 检定成功 + 物理执行 + 首次搜索，走 PG 线索表
    # IMPROMPTU 路径 — 即兴物品，直接跳过线索查询防幻觉
    clues_discovered: list[dict] = []
    raw_text = ""
    if execution_phase == "IMPROMPTU":
        # 即兴物品不查线索，不产生幻觉文本
        pass
    elif is_success and physical_executed and execution_phase == "NORMAL":
        session_id = state.get("session_id", "")
        character_data = get_current_player(state).get("character") or {}
        character_name = character_data.get("name", "")
        skill_value = check.get("skill_value", 50)
        clues_discovered = await _query_clues(
            session_id=session_id,
            target_key=target_key,
            skill_name=skill_name,
            skill_value=skill_value,
            roll_value=roll_value,
            character_name=character_name,
        )
        raw_text = clues_discovered[0].get("flavor_text", "") if clues_discovered else ""

    # 从线索中提取掉落物品列表（loot_items），追加到 deterministic_changes
    loot_items: list[str] = []
    if clues_discovered:
        loot = clues_discovered[0].get("loot_items", [])
        if loot and isinstance(loot, list):
            loot_items = loot

    # 构建 ActionExecutionResult
    action_result = {
        "intent_id": f"intent_{idx}",
        "intent_type": current_intent.get("type", "PHYSICAL_INTERACT"),
        "rule_context": {
            # 技能检定结果
            "success": is_success,
            "bypassed": bypassed,
            "node_type": "physical_interact",
            "skill_name": skill_name,
            "skill_value": check.get("skill_value", 0),
            "roll_value": roll_value,
            "success_level": check.get("success_level", ""),
            "success_label": check.get("success_label", ""),
            "difficulty": check.get("difficulty", "REGULAR"),
            "bonus_dice": check.get("bonus_dice", 0),
            "penalty_dice": check.get("penalty_dice", 0),
            "is_push": check.get("is_push", False),
            # 物理裁决结果
            "physical_executed": physical_executed,
            "execution_phase": execution_phase,
            "spatial_reason": spatial.get("spatial_reason", ""),
            "is_locked": is_locked,
            "is_searched": is_searched,
            "has_key": spatial.get("has_key", False),
            # 即兴物品名（叙事节点可据此生成描述）
            "impromptu_item": target_name if execution_phase == "IMPROMPTU" else "",
            # 线索（物理未执行时强行锁空，即兴不查线索）
            "clues_discovered": clues_discovered if physical_executed else [],
        },
        "deterministic_changes": {
            **_build_state_changes(spatial, target_key, target_name),
            **({"_inventory_append": loot_items} if loot_items else {}),
        },
        "raw_fixed_text": raw_text,
        "flavor_context": current_intent.get("flavor_context", ""),
    }

    logger.info(
        f"effect_archivist: phase={execution_phase} "
        f"skill={skill_name}({check.get('skill_value', '?')}) "
        f"→ roll={roll_value} "
        f"clues={len(clues_discovered)}"
    )

    return {
        "executed_actions": [action_result],
        "_skill_check_result": None,
        "_spatial_result": None,
    }
