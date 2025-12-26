"""
数据库管理模块
负责数据库连接、会话管理及初始化
"""
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker, AsyncEngine
from sqlalchemy.orm import DeclarativeBase
from src.core.config import get_settings

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
            self._engine = create_async_engine(
                get_db_url(), 
                echo=get_settings().project.debug
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

class Base(DeclarativeBase):
    pass

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """获取异步数据库会话的依赖项"""
    async with db_manager.session_factory() as session:
        yield session

async def init_db():
    """初始化数据库表"""
    async with db_manager.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

