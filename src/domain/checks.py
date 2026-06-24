"""
检定逻辑内核

职责:
  - 技能检定核心逻辑
  - 属性检定（STR CON 等）
  - 对抗检定（opposed roll）
  - 孤注一掷（pushing the roll）规则

函数:
  - skill_check(skill_value, difficulty, bonus_dice) -> CheckResult
  - stat_check(stat_value, difficulty) -> CheckResult
  - opposed_check(active_value, passive_value) -> OpposedResult
  - push_roll(original_result, new_roll) -> PushResult

类:
  - CheckResult: 检定结果（成功等级、掷骰值、描述）
  - OpposedResult: 对抗结果（胜者、 margin）

原则: 100% 确定性，无 LLM 调用
"""

from dataclasses import dataclass
from typing import Optional
from src.domain.coc_rules import SuccessLevel, Difficulty, get_difficulty_threshold, determine_success_level
from src.tools.dice import roll_d100, roll_bonus_dice, roll_penalty_dice


@dataclass
class CheckResult:
    """检定结果"""
    success_level: SuccessLevel
    roll_value: int
    skill_value: int
    is_push: bool = False

    @property
    def is_success(self) -> bool:
        """是否成功（非 FAILURE/FUMBLE）"""
        return self.success_level in (
            SuccessLevel.REGULAR,
            SuccessLevel.HARD,
            SuccessLevel.EXTREME,
            SuccessLevel.CRITICAL,
        )

    @property
    def is_failure(self) -> bool:
        """是否失败"""
        return self.success_level in (SuccessLevel.FAILURE, SuccessLevel.FUMBLE)


@dataclass
class OpposedResult:
    """对抗检定结果"""
    winner: str            # "active" 或 "passive"
    active_level: SuccessLevel
    passive_level: SuccessLevel
    margin: int            # 成功等级差（正数表示主动方优势）

    @property
    def is_active_win(self) -> bool:
        return self.winner == "active"

    @property
    def is_passive_win(self) -> bool:
        return self.winner == "passive"

    @property
    def is_tie(self) -> bool:
        return self.winner == "tie"


# 成功等级的数值权重（用于对抗检定比较）
_SUCCESS_LEVEL_ORDER = {
    SuccessLevel.CRITICAL: 5,
    SuccessLevel.EXTREME: 4,
    SuccessLevel.HARD: 3,
    SuccessLevel.REGULAR: 2,
    SuccessLevel.FAILURE: 1,
    SuccessLevel.FUMBLE: 0,
}


def _get_level_order(level: SuccessLevel) -> int:
    """获取成功等级的数值权重"""
    return _SUCCESS_LEVEL_ORDER.get(level, 0)


def skill_check(
    skill_value: int,
    difficulty: Difficulty = Difficulty.REGULAR,
    bonus_dice: int = 0,
    penalty_dice: int = 0,
) -> CheckResult:
    """
    技能检定核心逻辑。

    1. 掷 D100（含奖励/惩罚骰）
    2. 根据 difficulty 计算阈值
    3. 调用 determine_success_level 判定
    4. 返回 CheckResult

    参数:
      skill_value: 技能值（0-99）
      difficulty: 难度等级
      bonus_dice: 奖励骰数量
      penalty_dice: 惩罚骰数量

    注意: bonus_dice 和 penalty_dice 不能同时大于 0
    """
    # 应用奖励/惩罚骰
    if bonus_dice > 0 and penalty_dice > 0:
        bonus_dice = 0
        penalty_dice = 0

    if bonus_dice > 0:
        roll_value, _ = roll_bonus_dice(bonus_dice)
    elif penalty_dice > 0:
        roll_value, _ = roll_penalty_dice(penalty_dice)
    else:
        _, _, roll_value = roll_d100()

    # 根据难度调整有效技能值
    effective_skill = get_difficulty_threshold(skill_value, difficulty)

    # 用原始 skill_value 做成功等级判定（规则书要求）
    # 但难度调整影响阈值比较
    success_level = determine_success_level(effective_skill, roll_value)

    return CheckResult(
        success_level=success_level,
        roll_value=roll_value,
        skill_value=skill_value,
    )


def stat_check(stat_value: int, difficulty: Difficulty = Difficulty.REGULAR) -> CheckResult:
    """
    属性检定（STR/CON/DEX 等），逻辑同 skill_check。
    
    属性检定的目标是属性值 × 5（CoC 7版规则）。
    """
    skill_value = stat_value * 5
    return skill_check(skill_value, difficulty)


def opposed_check(
    active_value: int,
    passive_value: int,
    active_bonus: int = 0,
    passive_bonus: int = 0,
) -> OpposedResult:
    """
    对抗检定（CoC 7版规则）。

    双方各掷 D100，比较成功等级：
    - 一方成功一方失败 → 成功方胜
    - 双方都成功 → 成功等级高者胜
    - 双方都失败 → 平局

    参数:
      active_value: 主动方技能值
      passive_value: 被动方技能值
      active_bonus: 主动方奖励骰
      passive_bonus: 被动方奖励骰

    返回: OpposedResult
    """
    active_result = skill_check(active_value, bonus_dice=active_bonus)
    passive_result = skill_check(passive_value, bonus_dice=passive_bonus)

    active_order = _get_level_order(active_result.success_level)
    passive_order = _get_level_order(passive_result.success_level)

    if active_order > passive_order:
        winner = "active"
    elif passive_order > active_order:
        winner = "passive"
    else:
        # 相同成功等级，主动方胜（CoC 7版 house rule）
        winner = "active" if active_result.is_success else "tie"

    margin = active_order - passive_order

    return OpposedResult(
        winner=winner,
        active_level=active_result.success_level,
        passive_level=passive_result.success_level,
        margin=margin,
    )


def push_roll(original_check: CheckResult, new_roll: int) -> CheckResult:
    """
    孤注一掷（Pushing the Roll）。

    重投检定，但失败后果更严重：
    - 新掷骰的结果覆盖原结果
    - 如果新结果也是失败，触发大失败效果

    参数:
      original_check: 原检定结果
      new_roll: 新掷骰值

    返回: 新的检定结果（标记 is_push=True）
    """
    skill_value = original_check.skill_value
    success_level = determine_success_level(skill_value, new_roll)

    # 孤注一掷：失败视为大失败
    if success_level in (SuccessLevel.FAILURE,):
        success_level = SuccessLevel.FUMBLE

    return CheckResult(
        success_level=success_level,
        roll_value=new_roll,
        skill_value=skill_value,
        is_push=True,
    )
