"""
向量存储操作模块

功能：
- 生成文本的向量嵌入（Embeddings）
- 执行相似度搜索（余弦相似度）
- 批量添加/删除向量
- 管理向量索引

注意：
- 当前通过 LightRAG 的 PGVectorStorage 实现
- 此文件可用于自定义向量操作或直接访问 pgvector

用法示例：
    embeddings = await create_embedding("查询文本")
    results = await similarity_search(embeddings, top_k=10)
"""
