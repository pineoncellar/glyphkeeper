"""
@File     :   skill_check_node.py
@Desc     :   纯数值检定节点 — 从 intent 提取参数，掷骰判定成功等级
@Note     :   100% 确定性逻辑，调用 domain/checks.py，不涉及任何外部查询或物理仲裁。
              输出写入 _skill_check_result 临时字段，不追加 executed_actions。
"""

from __future__ import annotations

from typing import Any
from src.state.game_state import GameState, get_current_player
from src.domain.checks import skill_check, CheckResult
from src.domain.coc_rules import Difficulty, SuccessLevel
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


def _resolve_skill_value(state: GameState, skill_name: str, intent_data: dict) -> int:
    """从角色卡或意图数据中获取技能值，兜底默认 50"""
    skill_value = intent_data.get("skill_value")
    if skill_value is not None:
        return skill_value

    character_data = get_current_player(state).get("character")
    if character_data:
        skills = character_data.get("skills") or {}
        skill_value = skills.get(skill_name)
        if skill_value is None:
            # 子串模糊匹配，先精确再模糊
            for k, v in skills.items():
                if skill_name in k or k in skill_name:
                    skill_value = v
                    break

    if skill_value is None:
        skill_value = 50
        logger.debug(f"skill_check_node: 未找到技能 '{skill_name}' 的值，使用默认值 {skill_value}")

    return skill_value


async def skill_check_node(state: GameState) -> dict:
    """纯数值检定节点

    从 intent_queue 读取当前意图的检定参数，执行 skill_check。
    结果以 _skill_check_result 形式暂存，供后续 spatial_physics_node 和
    effect_archivist_node 消费。不直接追加 executed_actions。
    """
    idx = state.get("current_intent_idx", 0)
    queue = state.get("intent_queue", [])
    current_intent = queue[idx] if idx < len(queue) else {}
    intent_data = current_intent.get("data", {})

    skill_name = intent_data.get("skill_name", "")
    difficulty = _parse_difficulty(intent_data.get("difficulty", "REGULAR"))
    bonus_dice = intent_data.get("bonus_dice", 0)
    penalty_dice = intent_data.get("penalty_dice", 0)

    # check_type=none 表示无需检定（如敲门、打招呼等日常动作），直接绕过
    check_type = intent_data.get("check_type", "none")
    if check_type == "none" or not skill_name:
        logger.debug(
            f"skill_check_node: 无需检定 (intent_{idx}, "
            f"{current_intent.get('core_action', '')})"
        )
        return {
            "_skill_check_result": {
                "bypassed": True,
                "is_success": True,
                "success_level": "BYPASSED",
                "skill_name": skill_name,
                "roll_value": 0,
                "skill_value": 0,
                "difficulty": "REGULAR",
                "node_type": "skill_check",
            }
        }

    # 获取技能值
    skill_value = _resolve_skill_value(state, skill_name, intent_data)

    # 执行检定
    try:
        result: CheckResult = skill_check(skill_value, difficulty, bonus_dice, penalty_dice)
    except Exception as e:
        logger.error(f"skill_check_node: 检定失败: {e}")
        return {
            "_skill_check_result": {
                "bypassed": False,
                "is_success": False,
                "success_level": "FAILURE",
                "error": str(e),
                "skill_name": skill_name,
                "skill_value": skill_value,
                "roll_value": 0,
                "difficulty": difficulty.value,
                "node_type": "skill_check",
            }
        }

    logger.info(
        f"skill_check_node: {skill_name}({skill_value}) "
        f"→ roll={result.roll_value} {_success_label(result.success_level)}"
    )

    return {
        "_skill_check_result": {
            "bypassed": False,
            "is_success": result.is_success,
            "is_failure": result.is_failure,
            "success_level": result.success_level.value,
            "success_label": _success_label(result.success_level),
            "skill_name": skill_name,
            "skill_value": skill_value,
            "roll_value": result.roll_value,
            "difficulty": difficulty.value,
            "bonus_dice": bonus_dice,
            "penalty_dice": penalty_dice,
            "is_push": result.is_push,
            "node_type": "skill_check",
        }
    }
