"""
tools - 外部工具层 + 配置管理

职责:
  - 提供无副作用的纯工具函数
  - 骰子引擎、随机数、向量搜索、时间工具
  - 全局配置管理（config.yaml / providers.ini）
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
]
