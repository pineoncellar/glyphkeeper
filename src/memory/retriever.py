"""
记忆检索器（Graph Node 的输入源）

职责:
  - 为 Graph Node 提供统一的记忆检索接口
  - 组合向量检索 + 结构化查询 + 对话历史
  - 构建 Node 执行所需的上下文
  - 支持多源结果融合与重排序

方法:
  - retrieve_context(session_id, query, top_k) -> Context
  - retrieve_relevant_rules(query) -> List[Rule]
  - retrieve_episodic_memory(session_id, keywords) -> List[Event]

使用方式:
    context = await retriever.retrieve_context(
        session_id=session_id,
        query=player_intent.description,
        top_k=30
    )
"""
