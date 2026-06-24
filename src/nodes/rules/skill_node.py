"""
技能检定节点

职责:
  - 执行技能检定（常规/困难/极难难度）
  - 孤注一掷判定逻辑
  - 成长标记处理
  - 调用 domain/checks.py 的确定性逻辑

输入: SkillCheckRequest + CharacterSkills
输出: SkillCheckResult（成功等级、效果描述）

难度等级:
  - 常规: ≤技能值
  - 困难: ≤技能值/2
  - 极难: ≤技能值/5
"""
