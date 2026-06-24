"""
Runtime 集成测试

职责:
  - 测试 runtime/ 层的 Graph 执行引擎
  - 覆盖 engine, scheduler, dispatcher

测试范围:
  - Graph 拓扑执行正确性
  - 节点路由与状态传递
  - Suspend/Resume 生命周期
  - 多会话调度并发安全
"""
