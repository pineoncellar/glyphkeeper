"""
state - 世界唯一真相（Event Sourcing）

职责:
  - 所有游戏状态的唯一权威来源
  - LLM 不直接修改 state，仅通过 event → reducer 变更
  - 支持快照与时间线回溯
"""
