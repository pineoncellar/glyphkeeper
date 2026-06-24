"""
理智规则内核

职责:
  - 理智损失计算（基于 SAN 值和损失表）
  - 临时疯狂判定与效果生成
  - indefinite insanity 判定
  - 疯狂症状表（可扩展）

函数:
  - calculate_sanity_loss(max_san_loss, current_san, is_mythos) -> SanityLoss
  - check_temporary_insanity(sanity_loss, current_san) -> bool
  - check_indefinite_insanity(sanity_loss, current_san) -> bool
  - roll_insanity_symptom() -> str
  - get_sanity_loss_bounds(source_type: str) -> tuple[int, int]

原则: 100% 确定性，无 LLM 调用
# TODO: 实现总结疯狂判定
"""

import random
from dataclasses import dataclass
from typing import Tuple


@dataclass
class SanityLoss:
    """理智损失计算结果"""
    actual_loss: int
    is_temporary_insanity: bool
    is_indefinite_insanity: bool
    max_possible_loss: int


@dataclass
class InsanityResult:
    """疯狂判定结果"""
    insanity_type: str         # "temporary" / "indefinite" / "none"
    duration_hours: int
    symptom: str               # 疯狂症状描述


# 疯狂症状表（CoC 7版 简化版）
# TODO: 完善
INSANITY_SYMPTOMS = [
    "失忆 - 玩家突然无法记起最近发生的事",
    "假性残疾 - 玩家出现某种身体机能障碍（如失明、失语）",
    "暴力倾向 - 玩家表现出不受控制的暴力行为",
    "偏执 - 玩家对周围的一切产生强烈的不信任感",
    "严重恐惧 - 玩家对某个特定事物产生极度恐惧",
    "狂躁 - 玩家陷入不可抑制的兴奋或焦虑状态",
    "生理反应 - 玩家出现持续的生理不适（如呕吐、颤抖）",
    "幻觉 - 玩家看到或听到不存在的事物",
]


def calculate_sanity_loss(
    current_san: int,
    max_san: int,
    loss_range: Tuple[int, int],
    is_mythos: bool = False,
) -> SanityLoss:
    """
    计算理智损失。

    参数:
      current_san: 当前 SAN 值
      max_san: 最大 SAN 值
      loss_range: (最小损失, 最大损失)
      is_mythos: 是否神话相关（可能触发更大损失）

    返回: SanityLoss

    规则:
    - 实际损失 = random(loss_range[0], loss_range[1])
    - 但不超过 current_san（不会降到 0 以下）
    - 如果损失 ≥ current_san/5 → 标记临时疯狂
    - 如果损失 ≥ max_san/5 → 标记 indefinite insanity
    """
    min_loss, max_loss = loss_range

    # 确保范围有效
    if min_loss < 0:
        min_loss = 0
    if max_loss < min_loss:
        max_loss = min_loss

    # 随机损失值
    if min_loss == max_loss:
        actual_loss = min_loss
    else:
        actual_loss = random.randint(min_loss, max_loss)

    # 不超过当前 SAN（不会降到 0 以下）
    actual_loss = min(actual_loss, current_san)

    # 疯狂判定
    is_temporary = check_temporary_insanity(actual_loss, current_san)
    is_indefinite = check_indefinite_insanity(actual_loss, max_san)

    return SanityLoss(
        actual_loss=actual_loss,
        is_temporary_insanity=is_temporary,
        is_indefinite_insanity=is_indefinite,
        max_possible_loss=max_loss,
    )


def check_temporary_insanity(sanity_loss: int, current_san: int) -> bool:
    """
    判断是否触发临时疯狂。
    
    规则：loss ≥ current_san / 5
    """
    if current_san <= 0:
        return True
    return sanity_loss >= current_san / 5


def check_indefinite_insanity(sanity_loss: int, max_san: int) -> bool:
    """
    判断是否触发 indefinite insanity。
    
    规则：loss ≥ max_san / 5
    """
    if max_san <= 0:
        return True
    return sanity_loss >= max_san / 5


def roll_insanity_symptom() -> str:
    """随机产生一个临时疯狂症状"""
    return random.choice(INSANITY_SYMPTOMS)


def get_sanity_loss_bounds(source_type: str) -> Tuple[int, int]:
    """
    根据来源类型返回理智损失范围。

    参数:
      source_type: 损失来源类型

    返回: (最小损失, 最大损失)
    """
    loss_table = {
        "seeing_dead_body": (0, 1),
        "seeing_violent_death": (1, 3),
        "seeing_mythos_creature": (1, 6),
        "reading_tome": (1, 10),
        "casting_spell": (1, 10),
        "nightmare": (0, 3),
        "torture": (2, 8),
        "betrayal": (1, 4),
        "loved_one_dies": (1, 6),
        "seeing_ally_die_violently": (1, 8),
        "mythos_revelation": (2, 10),
        "elder_god_encounter": (5, 20),
    }

    # 默认值
    return loss_table.get(source_type, (0, 1))


def roll_full_insanity(
    current_san: int,
    max_san: int,
    loss_range: Tuple[int, int],
    source_type: str = "",
) -> InsanityResult:
    """
    完整的理智检定（损失计算 + 疯狂判定 + 症状生成）。

    参数:
      current_san: 当前 SAN 值
      max_san: 最大 SAN 值
      loss_range: (最小损失, 最大损失)
      source_type: 损失来源类型（用于描述）

    返回: InsanityResult
    """
    # 如果没有指定 loss_range，从来源类型推导
    if not loss_range or loss_range == (0, 0):
        loss_range = get_sanity_loss_bounds(source_type)

    result = calculate_sanity_loss(current_san, max_san, loss_range)

    if result.is_indefinite_insanity:
        insanity_type = "indefinite"
        # Indefinite insanity: 持续 d10 小时
        duration_hours = random.randint(1, 10)
    elif result.is_temporary_insanity:
        insanity_type = "temporary"
        # 临时疯狂: 持续 1d10 轮（约 30 秒）
        duration_hours = 0  # 战斗轮中，用轮次表示
    else:
        insanity_type = "none"
        duration_hours = 0

    symptom = roll_insanity_symptom() if insanity_type != "none" else ""

    return InsanityResult(
        insanity_type=insanity_type,
        duration_hours=duration_hours,
        symptom=symptom,
    )
