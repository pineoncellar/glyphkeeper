"""
知识检索节点

职责:
  - 从 RAG 知识库检索相关信息
  - 支持结构化（PostgreSQL）和语义（LightRAG）检索
  - 封装检索结果为统一格式供其他 Node 消费
  - 支持规则查询和模组知识查询

输入: LookupRequest (query, domain, mode)
输出: LookupResult (text_snippets, references)
"""
