"""
Orchestrator Agent - 路由协调代理

职责：
- 接收用户输入，分析意图
- 路由到合适的子代理 (Narrator/Archivist/Search)
- 协调多个代理的协作流程
- 管理对话上下文和状态

路由策略：
- 游戏叙事请求 → Narrator (故事推进、场景描述)
- 规则查询请求 → Archivist (查找规则、数据检索)
- 知识问答请求 → Search Agent (RAG 检索)
- 混合请求 → 多代理协作

用法示例：
    orchestrator = Orchestrator()
    response = await orchestrator.route(user_input)
"""
