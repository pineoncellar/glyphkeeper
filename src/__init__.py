"""
GlyphKeeper - 基于 Graph Runtime 架构的 CoC 7版 AI 守密人系统

架构层级:
  runtime/  - Graph 执行引擎（核心调度器）
  state/    - 世界唯一真相（事件溯源）
  graph/    - Agent Graph 定义（节点 + 边）
  nodes/    - 可执行能力节点（LLM / 规则 / 工具）
  tools/    - 外部工具（骰子 / 向量检索）
  domain/   - CoC 规则域模型（纯确定性逻辑）
  memory/   - 长期记忆（RAG + Event Store）
  workers/  - 后台任务（记忆固化 / 摘要）
  api/      - 接口层（WebSocket / HTTP）
  config/   - 配置管理
"""
