"""
战斗规则内核

职责:
  - 伤害计算（含伤害加值 DB）
  - 命中判定与部位判定
  - 护甲减伤
  - 武器属性定义与管理

函数:
  - calculate_damage(weapon, db, success_level) -> int
  - determine_hit_location(roll_value) -> str
  - apply_armor(damage, armor_value) -> int
  - resolve_combat_round(actor, target, weapon, db, target_armor) -> CombatRoundResult

数据:
  - WEAPONS: Dict[str, WeaponStats]  武器数据表

原则: 100% 确定性，无 LLM 调用
"""

from dataclasses import dataclass
from typing import Dict, Optional
from src.domain.coc_rules import SuccessLevel
from src.domain.checks import opposed_check
from src.tools.dice import roll_d100, roll_dice


@dataclass
class WeaponStats:
    """武器属性"""
    name: str
    base_range: str            # "接触" / "10m" 等
    damage: str                # "1D8" / "2D6+2" 等
    attacks_per_round: int
    ammunition: int
    malfunction: int           # 故障值


@dataclass
class CombatRoundResult:
    """战斗回合结果"""
    actor_name: str
    target_name: str
    attack_roll: int
    hit: bool
    damage: int
    hit_location: str
    armor_reduced: int
    net_damage: int


# 标准武器表
# TODO: 完善
WEAPONS: Dict[str, WeaponStats] = {
    "拳头": WeaponStats("拳头", "接触", "1D3+DB", 1, 999, 0),
    "踢": WeaponStats("踢", "接触", "1D6+DB", 1, 999, 0),
    "左轮手枪": WeaponStats("左轮手枪", "15m", "1D10", 1, 6, 100),
    "猎枪": WeaponStats("猎枪", "50m", "4D6/2D6/1D6", 1, 2, 100),
    "小刀": WeaponStats("小刀", "接触", "1D4+DB", 1, 999, 0),
    "棒球棒": WeaponStats("棒球棒", "接触", "1D8+DB", 1, 999, 0),
    "步枪": WeaponStats("步枪", "100m", "2D6", 1, 8, 100),
    "冲锋枪": WeaponStats("冲锋枪", "30m", "1D10", 2, 30, 95),
    " shotgun": WeaponStats("shotgun", "50m", "4D6/2D6/1D6", 1, 2, 100),
    "小手刀": WeaponStats("小手刀", "接触", "1D4+DB", 1, 999, 0),
    "撬棍": WeaponStats("撬棍", "接触", "1D8+DB", 1, 999, 0),
    "火把": WeaponStats("火把", "接触", "1D6+DB", 1, 999, 0),
}


def calculate_damage(weapon: WeaponStats, db: str, success_level: SuccessLevel) -> int:
    """
    计算伤害值。
    
    规则：
    - 常规成功：正常掷骰
    - 困难成功（HARD）：取最大值
    - 极难成功（EXTREME）：取最大值
    - 大成功（CRITICAL）：取最大值
    - 大失败/失败：伤害为 0

    参数:
      weapon: 武器属性
      db: 伤害加值表达式（如 "+1D4", "+1D6", "-2", "0"）
      success_level: 成功等级

    返回: 伤害值
    """
    if success_level in (SuccessLevel.FAILURE, SuccessLevel.FUMBLE):
        return 0

    # 解析武器伤害表达式，对于猎枪等有多个伤害值的武器，取第一个
    # TODO: 支持根据距离选择不同伤害值
    damage_expr = weapon.damage.split("/")[0]

    # 将 DB 占位符替换为实际 db 值
    # 如 "1D3+DB" + db="+1D4" → "1D3+1D4"
    db_clean = parse_damage_bonus(db) if db else "0"
    final_expr = damage_expr.upper().replace("DB", db_clean)

    # 计算伤害
    if success_level in (SuccessLevel.HARD, SuccessLevel.EXTREME, SuccessLevel.CRITICAL):
        damage = _max_damage(final_expr)
    else:
        damage = roll_dice(final_expr)

    return max(0, damage)


def _max_damage(damage_expr: str) -> int:
    """计算骰子表达式的最大值"""
    import re
    expr = damage_expr.strip().upper()

    # 纯数字
    try:
        return int(expr)
    except ValueError:
        pass

    total = 0
    current_sign = 1
    parts = re.split(r'([+-])', expr)

    for part in parts:
        if part == '+':
            current_sign = 1
        elif part == '-':
            current_sign = -1
        else:
            part = part.strip()
            if not part:
                continue
            match = re.match(r'^(\d+)[D](\d+)$', part)
            if match:
                n = int(match.group(1))
                sides = int(match.group(2))
                total += current_sign * (n * sides)
            else:
                try:
                    total += current_sign * int(part)
                except ValueError:
                    return 0

    return total


def determine_hit_location(roll_value: int) -> str:
    """
    根据 D20 掷骰值确定命中部位（可选规则）。

    命中部位表：
    1-3   → 右腿
    4-6   → 左腿
    7-10  → 腹部
    11-15 → 胸部
    16-17 → 右臂
    18-19 → 左臂
    20    → 头部
    """
    if roll_value >= 20:
        return "头部"
    elif roll_value >= 18:
        return "左臂"
    elif roll_value >= 16:
        return "右臂"
    elif roll_value >= 11:
        return "胸部"
    elif roll_value >= 7:
        return "腹部"
    elif roll_value >= 4:
        return "左腿"
    else:
        return "右腿"


def apply_armor(damage: int, armor_value: int) -> int:
    """护甲减伤：damage - armor_value，最小为 0"""
    return max(0, damage - armor_value)


def parse_damage_bonus(damage_bonus: str) -> str:
    """
    解析伤害加值字符串。
    
    输入: "-2" / "+1D4" / "+2D6" / "0"
    输出: 标准化的 dice 表达式
    """
    db = damage_bonus.strip()
    if db == "0" or not db:
        return "0"
    if db.startswith("+"):
        return db[1:]
    return db


def resolve_combat_round(
    actor_skill: int,
    target_skill: int,
    weapon: WeaponStats,
    db: str,
    target_armor: int,
    actor_bonus: int = 0,
    target_bonus: int = 0,
) -> CombatRoundResult:
    """
    完整的一轮战斗裁决。

    1. 双方掷 D100（含奖励/惩罚骰）
    2. 比较成功等级（同 opposed_check）
    3. 如果攻击方成功 → 计算伤害
    4. 如果防御方成功且等级更高 → 闪避成功，伤害为 0

    参数:
      actor_skill: 攻击方技能值
      target_skill: 防御方技能值（闪避或格斗）
      weapon: 攻击方使用的武器
      db: 攻击方伤害加值
      target_armor: 防御方护甲值
      actor_bonus: 攻击方奖励骰
      target_bonus: 防御方奖励骰

    返回: CombatRoundResult
    """
    # 双方对抗检定
    opposed = opposed_check(
        actor_skill,
        target_skill,
        active_bonus=actor_bonus,
        passive_bonus=target_bonus,
    )

    # 提取攻击掷骰值
    _, _, attack_roll = roll_d100()

    hit = opposed.is_active_win
    damage = 0

    if hit:
        # 攻击方命中
        damage = calculate_damage(weapon, db, opposed.active_level)
        armor_reduced = min(damage, target_armor)
        net_damage = apply_armor(damage, target_armor)
    else:
        armor_reduced = 0
        net_damage = 0

    # 命中部位
    hit_location = determine_hit_location(attack_roll) if hit else ""

    return CombatRoundResult(
        actor_name="actor",
        target_name="target",
        attack_roll=attack_roll,
        hit=hit,
        damage=damage,
        hit_location=hit_location,
        armor_reduced=armor_reduced,
        net_damage=net_damage,
    )
