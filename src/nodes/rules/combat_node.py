"""
战斗规则节点

职责:
  - 执行战斗行动裁决（攻击、闪避、战技、法术）
  - 伤害计算、部位判定
  - 管理战斗轮次与行动顺序
  - 调用 domain/combat_rules.py 的确定性逻辑

输入: CombatActionIntent + CharacterStats
输出: CombatResolutionResult

注意: 本节点不包含 LLM 调用，全确定性逻辑
"""
