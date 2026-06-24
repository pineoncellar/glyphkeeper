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
