"""
CoC 7版 核心规则

职责:
  - 定义核心规则常量与枚举
  - 难度等级计算（常规/困难/极难）
  - 成功等级判定（成功/困难成功/极难成功/大成功/大失败）
  - 属性/技能值校验逻辑

常量:
  - DIFFICULTY: 难度等级枚举
  - SUCCESS_LEVEL: 成功等级枚举
  - STAT_NAMES: 八项属性名称列表

函数:
  - determine_success_level(skill_value, roll_value) -> SuccessLevel
  - get_difficulty_threshold(skill_value, difficulty) -> int
  - is_fumble(skill_value, roll_value) -> bool
  - is_critical(roll_value) -> bool
"""

from enum import Enum, auto


class SuccessLevel(Enum):
    """成功等级"""
    FUMBLE = "FUMBLE"           # 大失败（骰出 96-100 且 > 技能值）
    FAILURE = "FAILURE"         # 失败
    REGULAR = "REGULAR"         # 常规成功
    HARD = "HARD"               # 困难成功（≤ 技能值/2）
    EXTREME = "EXTREME"        # 极难成功（≤ 技能值/5）
    CRITICAL = "CRITICAL"      # 大成功（骰出 01）


class Difficulty(Enum):
    """难度等级"""
    REGULAR = "REGULAR"         # 常规难度
    HARD = "HARD"               # 困难难度（技能值/2）
    EXTREME = "EXTREME"        # 极难难度（技能值/5）


# 八项属性名称
STAT_NAMES = ["STR", "CON", "SIZ", "DEX", "APP", "INT", "POW", "EDU"]

# 标准技能列表（CoC 7版）
# TODO: 规范化
SKILL_LIST = [
    "会计", "人类学", "考古学", "艺术与手艺", "魅惑", "攀爬",
    "计算机使用", "信用评级", "克苏鲁神话", "汽车驾驶", "乔装",
    "闪避", "汽车驾驶", "电气维修", "电子学", "话术", "斗殴",
    "格斗(刀)", "手枪", "射击(猎枪/步枪)", "射击(冲锋枪)",
    "射击(机枪)", "急救", "历史", "恐吓", "跳跃", "语言(母语)",
    "图书馆利用", "聆听", "锁匠", "机械维修", "医学", "博物学",
    "导航", "神秘学", "操作重型机械", "说服", "精神分析", "心理学",
    "骑术", "科学(化学)", "科学(物理学)", "科学(生物学)",
    "科学(天文学)", "科学(地质学)", "科学(药学)", "侦查",
    "潜行", "生存", "游泳", "投掷", "追踪",
]


def determine_success_level(skill_value: int, roll_value: int) -> SuccessLevel:
    """
    根据技能值和掷骰值判定成功等级。

    CoC 7版规则：
    - 骰出 01 → CRITICAL（大成功）
    - 骰出 96-100 且 > 技能值 → FUMBLE（大失败）
    - roll_value ≤ skill_value / 5 → EXTREME（极难成功）
    - roll_value ≤ skill_value / 2 → HARD（困难成功）
    - roll_value ≤ skill_value → REGULAR（常规成功）
    - 其他 → FAILURE（失败）
    """
    # 大成功
    if roll_value == 1:
        return SuccessLevel.CRITICAL

    # 大失败：骰出 96-100 且 roll_value > skill_value
    if is_fumble(skill_value, roll_value):
        return SuccessLevel.FUMBLE

    # 极难成功
    if roll_value <= max(1, skill_value // 5):
        return SuccessLevel.EXTREME

    # 困难成功
    if roll_value <= max(1, skill_value // 2):
        return SuccessLevel.HARD

    # 常规成功
    if roll_value <= skill_value:
        return SuccessLevel.REGULAR

    # 失败
    return SuccessLevel.FAILURE


def get_difficulty_threshold(skill_value: int, difficulty: Difficulty) -> int:
    """
    根据难度等级返回实际阈值。

    参数:
      skill_value: 技能值（1-99）
      difficulty: 难度等级

    返回:
      通过该难度所需的最大掷骰值
    """
    if difficulty == Difficulty.REGULAR:
        return skill_value
    elif difficulty == Difficulty.HARD:
        return max(1, skill_value // 2)
    elif difficulty == Difficulty.EXTREME:
        return max(1, skill_value // 5)
    return skill_value


def is_fumble(skill_value: int, roll_value: int) -> bool:
    """
    判断是否为大失败。
    
    规则：骰出 96-100 且 roll_value > skill_value
    
    特殊情况：
    - skill_value < 50 时，96-100 总是大失败（因为 96 > 50 > skill_value）
    - skill_value ≥ 50 时，只有 100 可能是大失败
    - skill_value = 0 时，任何掷骰都是大失败
    """
    if roll_value < 96:
        return False
    return roll_value > skill_value


def is_critical(roll_value: int) -> bool:
    """判断是否为大成功（骰出 01，即 roll_value == 1）"""
    return roll_value == 1
