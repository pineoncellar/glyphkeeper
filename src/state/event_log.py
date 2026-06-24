"""
事件溯源日志

职责:
  - 记录所有游戏状态变更的不可变事件流
  - 支持事件回放与状态重建
  - 提供事件订阅机制（用于 Workers 后台处理）
  - 所有 state 修改必须通过 Event → Reducer 模式

事件格式:
  {
    "type": "EntityMoved",
    "entity_id": "...",
    "from": "location_a",
    "to": "location_b",
    "timestamp": "..."
  }
"""
