"""
意图路由子 Graph

职责:
  - 根据 Intent 类型将执行流路由到对应的子 Graph
  - 路由表: IntentType → SubGraph
  - 支持条件路由（基于游戏状态和 Tag 系统）
  - 未知意图的兜底处理

路由规则:
  - PHYSICAL_INTERACT → InvestigationGraph / CombatGraph
  - SOCIAL_INTERACT  → DialogueGraph（未来扩展）
  - COMBAT_ACTION    → CombatGraph
  - MOVE             → InvestigationGraph（导航部分）
  - META             → 直接处理（系统命令）
"""
