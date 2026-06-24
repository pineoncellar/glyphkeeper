"""
向量搜索工具

职责:
  - 封装对 LightRAG / PGVector 的语义搜索调用
  - 提供统一的搜索接口（local/global/hybrid/naive 模式）
  - 支持多 workspace 隔离（world / rules）
  - 结果格式化与相关性评分

函数:
  - semantic_search(query, mode, top_k, domain) -> List[Result]
  - hybrid_search(query, top_k, domain) -> List[Result]
"""
