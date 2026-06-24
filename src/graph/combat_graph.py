"""
战斗子 Graph

职责:
  - 定义战斗流程的独立执行拓扑
  - 轮次管理: init → action_select → resolve → damage → check_end
  - 支持多角色并发行动解析
  - 通过 RouterGraph 从主 Graph 路由进入

流程示意:
    [CombatStart] → [InitiativeOrder]
        ↓
    [PlayerAction] → [CombatRuleNode] → [DamageCalc]
        ↓                               ↓
    [NPCReaction] ← [StateUpdate]  ← [HealthCheck]
        ↓
    [CombatEnd?] → Yes → [ReturnToExplore]
        ↓ No
    [NextTurn]
"""
