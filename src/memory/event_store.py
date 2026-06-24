"""
事件溯源存储

职责:
  - 存储不可变的事件流（Event Sourcing 模式）
  - 提供事件追加与范围查询接口
  - 支持事件回放（replay）重建状态
  - 事件序列化/反序列化

事件结构:
  {
    "id": UUID,
    "type": str,          # 事件类型
    "session_id": UUID,
    "timestamp": datetime,
    "data": dict,         # 事件负载
    "version": int        # 乐观锁版本号
  }

方法:
  - append_event(event) -> Event
  - get_events(session_id, since_version) -> List[Event]
  - replay(session_id) -> AsyncGenerator[Event]
"""
