"""
数据传输对象（DTO）

职责:
  - 定义 API 请求/响应的 Pydantic 模型
  - 输入校验与格式化
  - 文档生成（OpenAPI Schema）

类:
  - CreateSessionRequest: 创建会话请求
  - SessionResponse: 会话信息响应
  - PlayerInputRequest: 玩家输入请求
  - NarrativeResponse: 叙事文本响应
  - DiceRequest: 掷骰请求
  - DiceResponse: 掷骰结果响应
  - IngestRequest: 模组导入请求
  - HealthResponse: 健康检查响应
"""
