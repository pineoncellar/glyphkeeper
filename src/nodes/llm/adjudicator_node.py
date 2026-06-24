"""
即兴裁决节点 (替代旧 Adjudicator Agent)

职责:
  - 处理无硬编码规则对应的玩家即兴行为
  - 将玩家的创意行动转化为规则参数（难度等级、效果）
  - 查询 RAG 规则库辅助裁决
  - 使用 standard/smart 级别 LLM

输入: PlayerIntent + GameContext + RuleReference
输出: AdjudicationResult (难度/效果参数)

原则:
  - 只输出规则参数，不直接修改状态
  - 结果由后续 Rule Node 执行
"""
