"""
向量/图存储（LightRAG 封装）

职责:
  - 封装 LightRAG 的初始化与管理
  - 提供语义检索接口（local/global/hybrid/naive）
  - 管理多 workspace 隔离（world / rules）
  - 文本嵌入生成与存储

方法:
  - query(question, mode, top_k) -> str
  - insert(text, source_type) -> bool
  - delete(document_id) -> bool

技术栈:
  - 向量: PGVector (PostgreSQL)
  - 图: NetworkX
  - KV: PostgreSQL
"""
