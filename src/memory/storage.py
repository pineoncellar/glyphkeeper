"""
LightRAG 存储层配置
管理 KV（Redis/PG）、Vector（Milvus/PGVector）、Graph（Neo4j）的连接配置
"""
import os
from pathlib import Path
from typing import Dict, Any, Optional, Literal
from pydantic import BaseModel, Field

from ..core import get_logger, get_settings, PROJECT_ROOT

logger = get_logger(__name__)


class StorageConfig(BaseModel):
    """存储配置模型"""
    working_dir: str = Field(description="LightRAG 工作目录")
    
    # 图存储配置
    graph_storage: Literal[
        "NetworkXStorage",  # 开发环境
        "Neo4JStorage",     # 生产环境推荐
        "ArangoDBStorage",
        "AGEStorage"
    ] = Field(default="NetworkXStorage", description="图数据库类型")
    
    # 键值存储配置
    kv_storage: Literal[
        "JsonKVStorage",       # 开发环境
        "OracleKVStorage",
        "MongoKVStorage",
        "RedisKVStorage",
        "PGKVStorage",         # PostgreSQL
        "TiDBKVStorage"
    ] = Field(default="JsonKVStorage", description="KV 存储类型")
    
    # 向量存储配置
    vector_storage: Literal[
        "NanoVectorDBStorage",  # 开发环境 (默认, 轻量级)
        "MilvusVectorDBStorage",
        "ChromaVectorDBStorage",
        "PGVectorStorage",      # PostgreSQL pgvector
        "FaissVectorDBStorage",
        "QdrantVectorDBStorage",
        "TiDBVectorDBStorage"
    ] = Field(default="NanoVectorDBStorage", description="向量数据库类型")
    
    # 文档存储配置
    doc_storage: Literal[
        "JsonDocStorage",      # 开发环境 (默认)
        "PGDocStorage",        # PostgreSQL
        "MongoDocStorage"
    ] = Field(default="JsonDocStorage", description="文档存储类型")


def get_storage_config(working_dir: Optional[str] = None) -> Dict[str, Any]:
    """获取存储配置字典，供 Core 层初始化 LightRAG 使用"""
    if working_dir is None:
        working_dir = str(PROJECT_ROOT / "data")
    
    # 确保工作目录存在
    Path(working_dir).mkdir(parents=True, exist_ok=True)
    
    # 使用混合存储策略: PG + NetworkX
    db_url = get_postgres_url()
    settings = get_settings()
    
    config = {
        "working_dir": working_dir,
        "graph_storage": "NetworkXStorage",
        "vector_storage": "PGVectorStorage",
        "vector_db_storage_cls_kwargs": {
            "cosine_better_than_threshold": 0.2,
            "embedding_dim": settings.vector_store.embedding_dim
        },
        "kv_storage": "PGKVStorage",
        "doc_status_storage": "PGDocStatusStorage",
        "addon_params": {
            "db_url": db_url,
            "use_jsonb": True
        }
    }

    return config


def get_postgres_url() -> str:
    """获取 PostgreSQL 连接 URL"""
    settings = get_settings()
    database_config = settings.get_database_config()

    url = database_config.url
    db = database_config.project_name
    user = database_config.username
    password = database_config.password
    
    return f"postgresql://{user}:{password}@{url}/{db}"


# ============================================
# 存储健康检查
# ============================================

async def check_storage_health(rag_instance) -> Dict[str, bool]:
    """
    检查各存储组件的健康状态
    
    Args:
        rag_instance: LightRAG 实例
        
    Returns:
        各组件健康状态字典
    """
    health = {
        "graph_storage": False,
        "kv_storage": False,
        "vector_storage": False,
        "doc_storage": False,
    }
    
    try:
        # 简单的健康检查逻辑
        if hasattr(rag_instance, 'chunk_entity_relation_graph'):
            health["graph_storage"] = True
        if hasattr(rag_instance, 'key_string_value_json_storage_cls'):
            health["kv_storage"] = True
        if hasattr(rag_instance, 'embedding_func'):
            health["vector_storage"] = True
        health["doc_storage"] = True  # 默认认为文档存储可用
        
    except Exception as e:
        logger.error(f"存储健康检查失败: {e}")
    
    return health
