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
  - SKILL_LIST: 标准技能列表

函数:
  - determine_success_level(skill_value, roll_value) -> SuccessLevel
  - get_difficulty_threshold(skill_value, difficulty) -> int
  - is_extreme_success(roll_value) -> bool
  - is_fumble(skill_value, roll_value) -> bool
"""
