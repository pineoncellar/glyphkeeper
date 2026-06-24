"""
Graph 执行引擎（系统最核心模块）

职责:
  - 接收玩家输入，遍历 Graph 拓扑执行节点
  - 管理 Step Loop：意图 → 路由 → 裁决 → 叙事 → 等待
  - 处理节点的 suspend/resume 生命周期
  - 维护当前执行栈与上下文传递

使用方式:
    engine = GraphEngine()
    result = await engine.run(player_input, session_id)
"""
