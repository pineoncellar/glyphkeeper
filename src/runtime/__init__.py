"""
runtime - Graph 执行引擎（系统 CPU）

职责:
  - 执行 Graph 拓扑（节点遍历与路由）
  - 调度多玩家输入
  - 分发 Node 执行
  - 管理执行上下文（suspend / resume）
"""
