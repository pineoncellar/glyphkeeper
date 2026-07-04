"""
@File     :   skill_node.py
@Desc     :   技能检定节点 — 执行技能检定并返回结构化结果
@Note     :   100% 确定性逻辑，调用 domain/checks.py

Node 签名:
    async def skill_node(state: GameState) -> dict:
        从 intent 提取 skill_name → 查角色技能值 → 执行 skill_check → 返回 resolution
"""

from __future__ import annotations

from typing import Any
from src.state.game_state import GameState, get_current_player
from src.domain.checks import skill_check, CheckResult
from src.domain.coc_rules import Difficulty, SuccessLevel
from src.domain.character import Character
from src.tools import get_logger

logger = get_logger(__name__)


def _parse_difficulty(diff_str: Any) -> Difficulty:
    """安全解析难度枚举"""
    if isinstance(diff_str, Difficulty):
        return diff_str
    if isinstance(diff_str, str):
        try:
            return Difficulty(diff_str.upper())
        except ValueError:
            pass
    return Difficulty.REGULAR


def _success_label(level: SuccessLevel) -> str:
    return {
        SuccessLevel.CRITICAL: "大成功",
        SuccessLevel.EXTREME: "极难成功",
        SuccessLevel.HARD: "困难成功",
        SuccessLevel.REGULAR: "常规成功",
        SuccessLevel.FAILURE: "失败",
        SuccessLevel.FUMBLE: "大失败",
    }.get(level, "未知")


async def skill_node(state: GameState) -> dict:
    """
    技能检定节点。

    从 intent_queue 读取当前意图的检定参数，执行技能检定。
    结果以 ActionExecutionResult 形式追加到 executed_actions。
    """
    idx = state.get("current_intent_idx", 0)
    queue = state.get("intent_queue", [])
    current_intent = queue[idx] if idx < len(queue) else {}
    intent_data = current_intent.get("data", {})

    skill_name = intent_data.get("skill_name", "")
    difficulty = _parse_difficulty(intent_data.get("difficulty", "REGULAR"))
    bonus_dice = intent_data.get("bonus_dice", 0)
    penalty_dice = intent_data.get("penalty_dice", 0)

    if not skill_name:
        # check_type=none 表示无需检定（如敲门、打招呼等日常动作），直接返回空成功
        check_type = intent_data.get("check_type", "none")
        if check_type == "none":
            logger.debug(f"skill_node: 无需检定 (intent_{idx}, {current_intent.get('core_action', '')})")
            return {
                "executed_actions": [{
                    "intent_id": f"intent_{idx}",
                    "intent_type": current_intent.get("type", "PHYSICAL_INTERACT"),
                    "rule_context": {"success": True, "check_type": "none", "description": "日常动作，无需检定"},
                    "deterministic_changes": {},
                    "raw_fixed_text": "",
                    "flavor_context": current_intent.get("flavor_context", ""),
                }],
            }
        logger.warning("skill_node: 缺少 skill_name")
        return {
            "executed_actions": [{
                "intent_id": f"intent_{idx}",
                "intent_type": current_intent.get("type", "PHYSICAL_INTERACT"),
                "rule_context": {"success": False, "error": "缺少技能名称", "skill_name": ""},
                "deterministic_changes": {},
                "raw_fixed_text": "",
                "flavor_context": current_intent.get("flavor_context", ""),
            }],
        }

    # ── 获取技能值 ──
    skill_value = intent_data.get("skill_value")
    if skill_value is None:
        character_data = get_current_player(state).get("character")
        if character_data:
            skills = character_data.get("skills") or {}
            skill_value = skills.get(skill_name)
            if skill_value is None:
                for k, v in skills.items():
                    if skill_name in k or k in skill_name:
                        skill_value = v
                        break

    if skill_value is None:
        skill_value = intent_data.get("skill_value", 50)
        logger.debug(f"skill_node: 未找到技能 '{skill_name}' 的值，使用默认值 {skill_value}")

    # ── 执行检定 ──
    try:
        result: CheckResult = skill_check(skill_value, difficulty, bonus_dice, penalty_dice)
    except Exception as e:
        logger.error(f"skill_node: 检定失败: {e}")
        return {
            "executed_actions": [{
                "intent_id": f"intent_{idx}",
                "intent_type": "PHYSICAL_INTERACT",
                "rule_context": {"success": False, "error": str(e), "skill_name": skill_name},
                "deterministic_changes": {},
                "raw_fixed_text": "",
                "flavor_context": current_intent.get("flavor_context", ""),
            }],
        }

    # ── 构建结果 ──
    action_result = {
        "intent_id": f"intent_{idx}",
        "intent_type": current_intent.get("type", "PHYSICAL_INTERACT"),
        "rule_context": {
            "success": True,
            "node_type": "skill_check",
            "skill_name": skill_name,
            "skill_value": skill_value,
            "roll_value": result.roll_value,
            "success_level": result.success_level.value,
            "success_label": _success_label(result.success_level),
            "is_success": result.is_success,
            "is_failure": result.is_failure,
            "difficulty": difficulty.value,
            "bonus_dice": bonus_dice,
            "penalty_dice": penalty_dice,
            "is_push": result.is_push,
        },
        "deterministic_changes": {},
        "raw_fixed_text": "",
        "flavor_context": current_intent.get("flavor_context", ""),
    }

    logger.info(
        f"skill_node: {skill_name}({skill_value}) "
        f"→ roll={result.roll_value} {action_result['rule_context']['success_label']}"
    )

    return {"executed_actions": [action_result]}


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
