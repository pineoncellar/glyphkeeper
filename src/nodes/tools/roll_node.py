"""
自动化检定节点

职责:
  - 组合 DiceNode + SkillNode 完成完整检定流程
  - 自动选择正确的检定规则（属性/技能/对抗）
  - 处理检定结果的后续路由（成功走 A，失败走 B）
  - 封装完整的检定上下文

输入: RollRequest (skill_name, character, difficulty)
输出: RollResult (dice_value, success_level, description)

流程:
  收到请求 → 查询角色技能值 → 掷骰 → 比较判定 → 返回结果
"""
