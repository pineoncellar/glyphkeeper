"""
数据库管理模块
负责数据库连接、会话管理及初始化
"""
import logging
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker, AsyncEngine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy import text
from ..core import get_settings

# 设置 SQLAlchemy 日志级别为 WARNING，减少看不懂的无关日志输出
logging.getLogger('sqlalchemy.engine').setLevel(logging.WARNING)

def get_db_url() -> str:
    """构建数据库连接 URL"""
    settings = get_settings()
    
    if settings.database.host:
        # 构建 PostgreSQL URL
        # 格式: postgresql+asyncpg://user:password@host:port/dbname
        user = settings.database.username
        password = settings.database.password or ""
        host = settings.database.host
        port = settings.database.port or "5432"
        dbname = settings.database.project_name
        return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{dbname}"

class DatabaseManager:
    """数据库管理器"""
    def __init__(self):
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker | None = None

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            settings = get_settings()
            active_world = settings.project.active_world
            # 构造 schema 名称，例如 world_the_haunting
            world_schema = f"world_{active_world}"
            
            self._engine = create_async_engine(
                get_db_url(), 
                echo=settings.project.debug,
                # 连接池配置，避免长时间操作时连接超时
                pool_size=10,  # 连接池大小
                max_overflow=20,  # 最大溢出连接数
                pool_pre_ping=True,  # 检查连接是否有效
                pool_recycle=3600,  # 连接回收时间（秒）
                connect_args={
                    "server_settings": {
                        "search_path": f"{world_schema},public"
                    },
                    "timeout": 30,  # 连接超时
                    "command_timeout": 60,  # 命令超时
                }
            )
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker:
        if self._session_factory is None:
            self._session_factory = async_sessionmaker(
                bind=self.engine,
                class_=AsyncSession,
                expire_on_commit=False
            )
        return self._session_factory

# 全局单例
db_manager = DatabaseManager()


class RulesDatabaseManager:
    """
    规则数据库管理器（独立 schema：coc7th_rules）
    用于管理跨世界的共享规则数据，与世界特定数据隔离
    """
    def __init__(self):
        self._engine: AsyncEngine | None = None
        self._session_factory: async_sessionmaker | None = None

    @property
    def engine(self) -> AsyncEngine:
        if self._engine is None:
            settings = get_settings()
            # 固定使用 coc7th_rules schema，不受 active_world 影响
            self._engine = create_async_engine(
                get_db_url(), 
                echo=settings.project.debug,
                connect_args={
                    "server_settings": {
                        "search_path": "coc7th_rules,public"
                    }
                }
            )
        return self._engine

    @property
    def session_factory(self) -> async_sessionmaker:
        if self._session_factory is None:
            self._session_factory = async_sessionmaker(
                bind=self.engine,
                class_=AsyncSession,
                expire_on_commit=False
            )
        return self._session_factory


# 全局单例
rules_db_manager = RulesDatabaseManager()


class Base(DeclarativeBase):
    pass

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """获取异步数据库会话的依赖项"""
    async with db_manager.session_factory() as session:
        yield session

async def init_db():
    """初始化数据库表"""
    # 导入所有模型以确保它们被注册到 Base.metadata
    from .models import (
        Location, Interactable, Entity, InvestigatorProfile,
        Knowledge, ClueDiscovery, GameSession, Event, DialogueRecord, MemoryTrace
    )
    
    settings = get_settings()
    active_world = settings.project.active_world
    world_schema = f"world_{active_world}"

    async with db_manager.engine.begin() as conn:
        # 确保 Schema 存在
        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {world_schema}"))
        await conn.run_sync(Base.metadata.create_all)

