"""
RESTful HTTP 路由

职责:
  - 提供管理用 HTTP API
  - 游戏会话管理（创建/查询/列表）
  - 世界/模组管理（导入/导出）
  - 系统管理（配置/日志/备份）

路由:
  POST   /api/session              - 创建新会话
  GET    /api/session/{id}         - 查询会话状态
  GET    /api/world/{name}         - 获取世界状态
  POST   /api/world/ingest         - 导入模组数据
  GET    /api/health               - 系统健康检查
"""
