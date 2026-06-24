"""
多玩家输入调度器

职责:
  - 管理多个并发游戏会话的输入队列
  - 按会话 ID 分发输入到对应的 Graph 实例
  - 处理并发冲突与优先级排序
  - 提供公平调度保证

使用方式:
    scheduler = InputScheduler()
    await scheduler.submit(session_id, player_input)
"""
