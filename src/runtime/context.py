"""
当前执行上下文

职责:
  - 封装单次 Graph 执行的全部上下文数据
  - 包含当前会话 ID、玩家输入、历史节点输出
  - 提供上下文传递与状态快照
  - 支持序列化/反序列化（用于暂停恢复）

使用方式:
    ctx = ExecutionContext(session_id, player_input)
    ctx.set("intent", intent_obj)
    ctx.get("resolution_result")
"""
