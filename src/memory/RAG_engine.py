"""
LightRAG 核心引擎
实现单例模式，确保整个应用只初始化一个 RAG 对象
避免重复加载模型和连接数据库
"""
import asyncio
import logging
from pathlib import Path
from typing import Optional, Literal
from lightrag import LightRAG, QueryParam
from sqlalchemy import text

from ..core.config import get_settings, PROJECT_ROOT
from ..core.logger import get_logger
from ..llm import create_llm_model_func, create_embedding_func
from .storage import get_storage_config
from .database import db_manager

logger = get_logger(__name__)

# 统一配置 LightRAG 的日志格式
# 获取 lightrag 的 logger
lightrag_logger = logging.getLogger("lightrag")
# 移除可能存在的默认 handler (如 StreamHandler)，防止格式不统一
lightrag_logger.handlers.clear()
# 使用项目的 logger 配置重新初始化，这样就会应用统一的 Formatter
# 注意：get_logger 内部会检查 handlers 是否为空，所以上面必须先 clear
get_logger("lightrag", log_level="WARNING")
# 禁止向上传播，防止根 logger 重复打印或使用默认格式
lightrag_logger.propagate = False


class RAGEngine:
    """RAG 引擎单例类"""
    _instances: dict[str, "RAGEngine"] = {}
    _lock = asyncio.Lock()
    
    def __init__(self, domain: str):
        self.rag: Optional[LightRAG] = None
        self._initialized = False
        self.domain = domain
    
    @classmethod
    async def get_instance(
        cls,
        domain: str = "world",
        llm_tier: str = "standard",
        force_reinit: bool = False # 是否强制初始化
    ) -> "RAGEngine":
        """获取 RAG 引擎实例"""
        async with cls._lock:
            if domain not in cls._instances or force_reinit:
                cls._instances[domain] = cls(domain)
                await cls._instances[domain]._initialize(llm_tier)
            return cls._instances[domain]
    
    async def _initialize(
        self,
        llm_tier: str
    ):
        """初始化 LightRAG 实例"""
        if self._initialized:
            logger.warning(f"RAG 引擎 ({self.domain}) 已初始化，跳过重复初始化")
            return
        
        logger.info(f"正在初始化 RAG 引擎 ({self.domain}): llm_tier={llm_tier}")
        
        settings = get_settings()
        data_dir = PROJECT_ROOT / "data"
        
        # 确定工作目录和数据库 Schema
        if self.domain == "rules":
            working_dir = data_dir / "rules"
            schema = "rag_rules"
        else:
            # world domain
            active_world = settings.project.active_world
            working_dir = data_dir / "worlds" / active_world
            schema = f"world_{active_world}"

        # 确保目录存在
        working_dir.mkdir(parents=True, exist_ok=True)
        (data_dir / "raw_sources").mkdir(parents=True, exist_ok=True)
        (data_dir / "intermediate").mkdir(parents=True, exist_ok=True)
        
        # 确保 Schema 存在
        try:
            async with db_manager.engine.begin() as conn:
                await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        except Exception as e:
            logger.warning(f"尝试创建 Schema {schema} 失败 (可能已存在或权限不足): {e}")

        # 获取存储配置
        storage_config = get_storage_config(
            working_dir=str(working_dir),
            schema=schema
        )
        
        # 设置环境变量,满足 LightRAG 对 PGKVStorage 的要求
        # LightRAG 内部会检查这些环境变量
        import os
        settings = get_settings()
        db_config = settings.database
        os.environ["POSTGRES_USER"] = db_config.username or "postgres"
        os.environ["POSTGRES_PASSWORD"] = db_config.password or ""
        os.environ["POSTGRES_DATABASE"] = db_config.project_name or "postgres"
        os.environ["POSTGRES_HOST"] = db_config.host
        os.environ["POSTGRES_PORT"] = db_config.port or "5432"
        
        # 创建 LLM 和 Embedding 函数
        llm_func = create_llm_model_func(tier=llm_tier)
        
        vector_config = settings.vector_store
        logger.debug(f"使用的向量存储配置: provider={vector_config.provider}, model={vector_config.embedding_model_name}, dim={vector_config.embedding_dim}")
        embedding_func = create_embedding_func(
            model_name=vector_config.embedding_model_name,
            embedding_dim=vector_config.embedding_dim,
            max_token_size=8192,
            provider=vector_config.provider
        )
        
        try:
            # 初始化 LightRAG
            self.rag = LightRAG(
                llm_model_func=llm_func,
                embedding_func=embedding_func,
                **storage_config
            )
            
            # 初始化存储连接 (异步)
            await self.rag.initialize_storages()
            
            self._initialized = True
            logger.debug(f"RAG 引擎 ({self.domain}) 初始化完成")
            
        except Exception as e:
            logger.error(f"RAG 引擎 ({self.domain}) 初始化失败: {e}")
            raise
    
    async def insert(self, content: str, **kwargs) -> bool:
        """插入文本内容到知识库"""
        if not self._initialized or self.rag is None:
            raise RuntimeError("RAG 引擎未初始化")
        
        try:
            # 目前 LightRAG 的 ainsert 方法不支持 metadata 等额外参数
            # 为了避免 TypeError 和不必要的 try-catch 开销，这里直接忽略 kwargs
            # 如果未来 LightRAG 更新支持了 metadata，可以重新加上
            if kwargs:
                logger.debug(f"忽略不支持的插入参数: {list(kwargs.keys())}")
            
            await self.rag.ainsert(content)
            logger.debug(f"成功插入文本 (长度: {len(content)})")
            return True
        except Exception as e:
            logger.error(f"插入文本失败: {e}")
            return False
    
    async def insert_batch(self, contents: list[str]) -> int:
        """批量插入文本内容"""
        if not self._initialized or self.rag is None:
            raise RuntimeError("RAG 引擎未初始化")
        
        success_count = 0
        for content in contents:
            try:
                await self.rag.ainsert(content)
                success_count += 1
            except Exception as e:
                logger.error(f"批量插入中某项失败: {e}")
        
        logger.info(f"批量插入完成: {success_count}/{len(contents)}")
        return success_count
    
    async def query(
        self,
        question: str,
        mode: Literal["local", "global", "hybrid", "mix", "naive"] = "hybrid",
        top_k: int = 60,
        user_prompt: Optional[str] = None,
        **kwargs
    ) -> str:
        """查询知识库

        查询模式
        local: 局部搜索，侧重实体关系
        global: 全局搜索，侧重主题概念
        hybrid: 混合模式 (推荐)
        mix: 组合多种结果
        naive: 朴素搜索
        """
        if not self._initialized or self.rag is None:
            raise RuntimeError("RAG 引擎未初始化")
        
        # 构建查询参数
        param = QueryParam(
            mode=mode,
            top_k=top_k,
        )
        
        # 如果有自定义提示词，设置到参数中
        if user_prompt:
            param.user_prompt = user_prompt
        
        logger.debug(f"RAG 查询: question={question[:50]}..., mode={mode}")
        
        try:
            # 目前 LightRAG 的 aquery 方法不支持 filters 等额外参数
            # 为了避免 TypeError 和不必要的 try-catch 开销，这里直接忽略 kwargs
            if kwargs:
                logger.debug(f"忽略不支持的查询参数: {list(kwargs.keys())}")

            result = await self.rag.aquery(question, param=param)
            return result
        except Exception as e:
            logger.error(f"RAG 查询失败: {e}")
            raise
    
    async def close(self):
        """关闭RAG引擎，释放资源"""
        if self.rag is not None:
            try:
                await self.rag.finalize_storages()
                logger.info("RAG 引擎已关闭")
            except Exception as e:
                logger.error(f"关闭 RAG 引擎失败: {e}")
        
        self._initialized = False
        self.rag = None
    
    @property
    def is_initialized(self) -> bool:
        """检查引擎是否已初始化"""
        return self._initialized


##########################################################################
#便捷测试函数

async def get_rag_engine(
    llm_tier: str = "standard"
) -> RAGEngine:
    """获取RAG引擎实例的便捷函数"""
    return await RAGEngine.get_instance(llm_tier)


async def quick_query(question: str, mode: str = "hybrid") -> str:
    """快速查询的便捷函数"""
    engine = await get_rag_engine()
    return await engine.query(question, mode=mode)
