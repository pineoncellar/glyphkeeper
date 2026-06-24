"""
游戏全局状态

职责:
  - 维护当前游戏会话的全局属性（状态、时间、节拍）
  - 管理活跃 Tag 系统（条件性内容解锁）
  - 存储当前模组/剧本元数据
  - 通过 event → reducer 模式更新，禁止直接修改

数据模型（PostgreSQL）:
  - GameSession: id, status, scenario_name, time_slot, beat_counter
  - active_global_tags: List[str]  # 当前激活的全局标签
"""
