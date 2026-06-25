"""
@File     :   roll_node.py
@Desc     :   自动化检定节点 — 组合掷骰 + 技能判定完成完整检定流程
@Note     :   从 intent 或 state 读取参数 → 查角色技能 → 掷骰 → 判定 → 返回

Node 签名:
    async def roll_node(state: GameState) -> dict:
        返回: {"pending_dice": None, "resolution": roll_result_dict}
"""

from __future__ import annotations

from typing import Optional, Any
from src.state.game_state import GameState
from src.domain.checks import skill_check, stat_check, opposed_check, push_roll, CheckResult
from src.domain.coc_rules import Difficulty, SuccessLevel
from src.domain.character import Character
from src.tools.dice import roll_d100
from src.tools import get_logger

logger = get_logger(__name__)


def _parse_difficulty(diff_str: str) -> Difficulty:
    """解析难度字符串为枚举"""
    try:
        return Difficulty(diff_str.upper())
    except (ValueError, AttributeError):
        return Difficulty.REGULAR


def _skill_level_label(level: SuccessLevel) -> str:
    """成功等级的中文标签"""
    labels = {
        SuccessLevel.CRITICAL: "大成功",
        SuccessLevel.EXTREME: "极难成功",
        SuccessLevel.HARD: "困难成功",
        SuccessLevel.REGULAR: "常规成功",
        SuccessLevel.FAILURE: "失败",
        SuccessLevel.FUMBLE: "大失败",
    }
    return labels.get(level, "未知")


def _get_skill_value(character: dict | None, skill_name: str) -> int | None:
    """从角色数据中获取技能值"""
    if not character:
        return None
    skills = character.get("skills") or {}
    return skills.get(skill_name)


async def roll_node(state: GameState) -> dict:
    """
    自动化检定节点。

    从 state["intent"] 或 state["pending_dice"] 读取检定参数，
    执行完整检定流程（查技能 → 掷骰 → 判定）。

    支持的 intent.data 格式:
        {
            "check_type": "skill" | "stat" | "opposed" | "push",
            "skill_name": "侦查",
            "stat_name": "STR",              # 属性检定用
            "difficulty": "REGULAR",
            "bonus_dice": 0,
            "penalty_dice": 0,
            "target_skill": "潜行",           # 对抗检定用
            "target_value": 50,               # 对抗检定用
        }
    """
    intent = state.get("intent") or {}
    intent_data = intent.get("data") or {}
    pending = state.get("pending_dice")

    # ── 确定检定参数 ──
    check_type = intent_data.get("check_type", "skill")
    if pending:
        check_type = "skill"
        skill_name = pending.get("skill_name", "")
        difficulty = _parse_difficulty(pending.get("difficulty", "REGULAR"))
        bonus_dice = pending.get("bonus_dice", 0)
        penalty_dice = pending.get("penalty_dice", 0)
        skill_value = pending.get("skill_value")
    else:
        skill_name = intent_data.get("skill_name", "")
        stat_name = intent_data.get("stat_name", "")
        difficulty = _parse_difficulty(intent_data.get("difficulty", "REGULAR"))
        bonus_dice = intent_data.get("bonus_dice", 0)
        penalty_dice = intent_data.get("penalty_dice", 0)

        # 从角色数据获取技能值
        character_data = state.get("character")
        skill_value = _get_skill_value(character_data, skill_name)
        if skill_value is None:
            skill_value = intent_data.get("skill_value")

    # ── 执行检定 ──
    result: Optional[CheckResult] = None
    opposed_result = None
    error = None

    try:
        if check_type == "stat" and stat_name:
            # 属性检定: 先查角色属性
            stat_value = None
            if character_data:
                stats = character_data.get("stats") or {}
                stat_map = {
                    "STR": "strength", "CON": "constitution", "SIZ": "size",
                    "DEX": "dexterity", "APP": "appearance", "INT": "intelligence",
                    "POW": "power", "EDU": "education",
                }
                attr = stat_map.get(stat_name.upper())
                if attr:
                    stat_value = stats.get(attr)
            if stat_value is None:
                stat_value = intent_data.get("stat_value", 50)
            result = stat_check(stat_value, difficulty)

        elif check_type == "opposed":
            # 对抗检定
            target_skill_name = intent_data.get("target_skill", "")
            target_value = intent_data.get("target_value", 50)
            if character_data and target_skill_name:
                target_value = _get_skill_value(character_data, target_skill_name) or target_value
            if skill_value is None:
                skill_value = 50
            opposed_result = opposed_check(
                skill_value, target_value,
                active_bonus=bonus_dice,
                passive_bonus=intent_data.get("target_bonus", 0),
            )

        elif check_type == "push":
            # 孤注一掷 — 需要原结果
            original = state.get("resolution")
            if original and "roll_value" in original:
                new_roll, _, _ = roll_d100()
                result = push_roll(
                    CheckResult(
                        success_level=SuccessLevel(original.get("success_level", "FAILURE")),
                        roll_value=original.get("roll_value", 50),
                        skill_value=original.get("skill_value", 50),
                    ),
                    new_roll,
                )
            else:
                error = "孤注一掷需要原始的检定结果"
        else:
            # 默认: 技能检定
            if skill_value is None:
                skill_value = 50
            result = skill_check(skill_value, difficulty, bonus_dice, penalty_dice)

    except Exception as e:
        error = str(e)
        logger.error(f"roll_node: 检定失败: {e}")

    # ── 构建返回结果 ──
    if error:
        return {
            "pending_dice": None,
            "resolution": {
                "success": False,
                "error": error,
                "check_type": check_type,
            },
        }

    if opposed_result:
        resolution = {
            "success": True,
            "check_type": "opposed",
            "winner": opposed_result.winner,
            "active_level": opposed_result.active_level.value,
            "passive_level": opposed_result.passive_level.value,
            "margin": opposed_result.margin,
            "is_active_win": opposed_result.is_active_win,
            "skill_name": skill_name,
            "difficulty": difficulty.value,
        }
    elif result:
        resolution = {
            "success": True,
            "check_type": check_type,
            "success_level": result.success_level.value,
            "success_label": _skill_level_label(result.success_level),
            "roll_value": result.roll_value,
            "skill_value": result.skill_value,
            "is_success": result.is_success,
            "is_push": result.is_push,
            "skill_name": skill_name,
            "difficulty": difficulty.value,
        }
    else:
        resolution = {
            "success": False,
            "error": "未知检定类型",
            "check_type": check_type,
        }

    logger.info(
        f"roll_node: {check_type} skill={skill_name}"
        + (f" → {resolution.get('success_label', '')}" if resolution.get("success_label") else "")
    )

    result_dict: dict = {
        "pending_dice": None,
        "resolution": resolution,
    }

    # 如果检定失败且是技能检定，记录错误到 errors
    if result and not result.is_success and not resolution.get("is_push"):
        if skill_name:
            result_dict.setdefault("errors", []).append(
                f"{skill_name}检定失败（骰出{result.roll_value}，技能值{result.skill_value}）"
            )

    return result_dict


async def quick_skill_check(
    skill_value: int,
    difficulty: str = "REGULAR",
    bonus_dice: int = 0,
    penalty_dice: int = 0,
) -> dict:
    """
    快捷技能检定 — 不依赖 GameState。
    """
    diff = _parse_difficulty(difficulty)
    result = skill_check(skill_value, diff, bonus_dice, penalty_dice)
    return {
        "success": True,
        "success_level": result.success_level.value,
        "success_label": _skill_level_label(result.success_level),
        "roll_value": result.roll_value,
        "skill_value": result.skill_value,
        "is_success": result.is_success,
    }
