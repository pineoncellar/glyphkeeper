"""
Node 路由执行器

职责:
  - 根据当前 Graph 状态决定下一个执行的 Node
  - 调用 Node.execute(context) 并获取结果
  - 处理 Node 返回的 suspend 信号，挂起等待外部输入（如掷骰）
  - 管理 Node 执行超时与错误恢复

使用方式:
    dispatcher = NodeDispatcher(graph)
    next_node = dispatcher.route(context)
    result = await dispatcher.dispatch(next_node, context)
"""
