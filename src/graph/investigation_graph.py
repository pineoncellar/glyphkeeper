"""
调查/探索子 Graph

职责:
  - 定义探索与调查流程的执行拓扑
  - 节点序列: search → discovery → knowledge_grant → narrate
  - 管理线索发现的多对多映射逻辑
  - 处理技能检定流程（申请 → 掷骰 → 判定 → 结果）

流程示意:
    [InvestigateIntent] → [SkillCheckNode]
        ↓ success                  ↓ fail
    [ClueDiscoveryNode] → [KnowledgeGrant] → [Narrate]
                                  [PartialInfo] → [Narrate]
"""
