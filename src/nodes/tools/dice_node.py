"""
@File     :   dice_node.py
@Desc     :   掷骰执行节点 — 封装一次掷骰请求的执行流程
@Note     :   从 state["pending_dice"] 读取参数 → 调用 tools/dice → 返回结果

Node 签名:
    async def dice_node(state: GameState) -> dict:
        返回: {"pending_dice": None, "resolution": dice_result_dict}
"""

from __future__ import annotations

from typing import Any
from src.state.game_state import GameState, get_current_player
from src.tools.dice import roll_d100, roll_bonus_dice, roll_penalty_dice, roll_dice as roll_expression
from src.domain.coc_rules import determine_success_level, SuccessLevel
from src.tools import get_logger

logger = get_logger(__name__)


async def dice_node(state: GameState) -> dict:
    """
    掷骰执行节点。

    从 state["pending_dice"] 读取骰子参数，执行掷骰并返回结果。

    支持的 pending_dice 格式:
        {
            "reason": "侦查检定",           # 掷骰原因
            "skill_name": "侦查",            # 关联技能名（可选）
            "skill_value": 50,               # 技能值（可选，用于判定成功等级）
            "difficulty": "REGULAR",         # 难度等级（可选，默认 REGULAR）
            "bonus_dice": 0,                # 奖励骰数量
            "penalty_dice": 0,              # 惩罚骰数量
            "expression": "1D6",            # 自定义骰子表达式（可选，非 D100 时使用）
        }
    """
    pending = get_current_player(state).get("pending_dice")
    if not pending:
        # 正常路径：combat_graph 无条件执行 dice_node 作为图的第一步，
        # 此时可能没有待处理的掷骰请求，属于正常"无事可做"
        logger.debug("dice_node: 被调用但无 pending_dice（正常路径，来自 combat_graph 的 dice_roll）")
        return {
            "pending_dice": None,
            "resolution": {
                "success": False,
                "error": "无待处理的掷骰请求",
            },
        }

    reason = pending.get("reason", "掷骰")
    expression = pending.get("expression")
    skill_value = pending.get("skill_value")
    bonus_dice = pending.get("bonus_dice", 0)
    penalty_dice = pending.get("penalty_dice", 0)

    # 支持外部注入的 roll_value（来自 CLI 输入）
    roll_value_override = pending.get("roll_value")

    # ── 执行掷骰 ──
    if roll_value_override is not None:
        # 使用玩家输入的掷骰值（用于 CLI 交互式掷骰）
        roll_total = int(roll_value_override)
        tens, ones = roll_total // 10, roll_total % 10
        if tens == 10:  # 100 → 十位骰 0, 个位骰 0
            tens, ones = 0, 0
        all_rolls = [roll_total]
        logger.info(f"dice_node: 使用外部注入掷骰值 {roll_total}")
    elif expression:
        # 自定义骰子表达式（如 "1D6", "2D6+2"）
        roll_total = roll_expression(expression)
        tens, ones = 0, 0
        all_rolls = [roll_total]
    elif bonus_dice > 0:
        roll_total, all_rolls = roll_bonus_dice(bonus_dice)
        tens, ones = 0, 0
    elif penalty_dice > 0:
        roll_total, all_rolls = roll_penalty_dice(penalty_dice)
        tens, ones = 0, 0
    else:
        tens, ones, roll_total = roll_d100()
        all_rolls = [roll_total]

    # ── 判定成功等级（如果有技能值） ──
    success_level = None
    if skill_value is not None:
        from src.domain.coc_rules import Difficulty
        difficulty_name = pending.get("difficulty", "REGULAR")
        try:
            difficulty = Difficulty(difficulty_name)
        except ValueError:
            difficulty = Difficulty.REGULAR
        effective_skill = skill_value // {
            "REGULAR": 1,
            "HARD": 2,
            "EXTREME": 5,
        }.get(difficulty_name, 1)
        success_level = determine_success_level(effective_skill, roll_total).value

    # ── 构建结果 ──
    result = {
        "success": True,
        "reason": reason,
        "roll_value": roll_total,
        "tens": tens,
        "ones": ones,
        "all_rolls": all_rolls,
        "skill_name": pending.get("skill_name"),
        "skill_value": skill_value,
        "difficulty": pending.get("difficulty", "REGULAR"),
        "success_level": success_level,
        "expression": expression,
        "bonus_dice": bonus_dice,
        "penalty_dice": penalty_dice,
    }

    logger.info(
        f"dice_node: {reason} → roll={roll_total}"
        + (f" level={success_level}" if success_level else "")
    )

    return {
        "pending_dice": None,
        "executed_actions": [{
            "intent_id": "dice_auto",
            "intent_type": "",
            "rule_context": result,
            "deterministic_changes": {},
            "raw_fixed_text": "",
            "flavor_context": "",
        }],
    }


async def simple_dice_roll(
    expression: str = "1D100",
    reason: str = "掷骰",
    skill_value: int | None = None,
    difficulty: str = "REGULAR",
) -> dict:
    """
    简化掷骰辅助函数 — 不依赖 GameState，直接返回结果。

    用于在非 Graph 环境中快速掷骰。

    示例:
        result = await simple_dice_roll("1D6", "伤害掷骰")
        result = await simple_dice_roll(skill_value=50, difficulty="HARD")
    """
    from src.domain.coc_rules import Difficulty as DiffEnum

    if skill_value is not None:
        # D100 技能检定
        _, _, roll_total = roll_d100()
        try:
            diff = DiffEnum(difficulty)
        except ValueError:
            diff = DiffEnum.REGULAR
        effective_skill = skill_value // {
            "REGULAR": 1, "HARD": 2, "EXTREME": 5
        }.get(difficulty, 1)
        level = determine_success_level(effective_skill, roll_total)
        return {
            "roll_value": roll_total,
            "success_level": level.value,
            "skill_value": skill_value,
            "difficulty": difficulty,
            "reason": reason,
        }
    else:
        # 普通掷骰
        total = roll_expression(expression)
        return {"roll_value": total, "expression": expression, "reason": reason}
