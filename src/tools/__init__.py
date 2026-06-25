"""
tools - 外部工具层 + 配置管理 + 模组摄入

职责:
  - 提供无副作用的纯工具函数
  - 骰子引擎、随机数、向量搜索、时间工具
  - 全局配置管理（config.yaml / providers.ini）
  - 模组数据摄入（intermediate JSON → VectorStore + EventStore）
  - 分级 Logger 工厂
"""

from src.tools.config import (
    # 常量
    PROJECT_ROOT,
    LOG_DIR,
    # 配置模型
    Settings,
    ProjectConfig,
    DatabaseConfig,
    ProviderConfig,
    ModelConfig,
    VectorStoreConfig,
    # 配置函数
    get_settings,
    reload_config,
    # 日志函数
    get_logger,
    setup_logger,
    _early_log,
    _settings_instance,
    _DEBUG_MODE,
)

from src.tools.ingestion import (
    ModuleIngestor,
    ingest_by_name,
    ingest_by_path,
    list_available_modules,
    find_module_files,
)

from src.tools.llm_client import (
    call_llm,
    call_llm_stream,
    ask_llm,
)

__all__ = [
    # config
    "PROJECT_ROOT",
    "LOG_DIR",
    "Settings",
    "ProjectConfig",
    "DatabaseConfig",
    "ProviderConfig",
    "ModelConfig",
    "VectorStoreConfig",
    "get_settings",
    "reload_config",
    "get_logger",
    "setup_logger",
    "_early_log",
    "_settings_instance",
    "_DEBUG_MODE",
    # ingestion
    "ModuleIngestor",
    "ingest_by_name",
    "ingest_by_path",
    "list_available_modules",
    "find_module_files",
    # llm_client
    "call_llm",
    "call_llm_stream",
    "ask_llm",
]
