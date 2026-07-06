# -*- coding: utf-8 -*-
"""
@File     :   tools/config.py
@Desc     :   GlyphKeeper 配置管理模块 — 整合入 tools 层
@Note     :   原 src/config/__init__.py 迁移至此

职责:
  - 从 config.yaml + providers.ini 加载全局配置
  - 提供 Settings 懒加载单例（get_settings()）
  - 提供分级 Logger 工厂（get_logger()）
  - LLM 提供商与模型分级配置
  - 世界/模组切换配置
"""

import sys
import yaml
import configparser
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, List, Any
from pydantic import BaseModel, Field, model_validator

# ── 项目根目录 ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# ── 日志目录 ──
LOG_DIR = PROJECT_ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)


# ====================================================================
# 配置数据模型
# ====================================================================


class ProjectConfig(BaseModel):
    """项目基础配置"""
    name: str = Field("GlyphKeeper", description="项目名称")
    debug: bool = Field(False, description="调试模式")
    model_cost_tracking: bool = Field(False, description="是否开启模型成本追踪")
    model_usage_logging: bool = Field(True, description="是否将每次模型调用的用量/额度追加写入日志文件")
    model_usage_log_path: str = Field("logs/llm_usage.jsonl", description="模型用量日志文件路径（相对项目根目录）")


class ProviderConfig(BaseModel):
    """AI 提供方配置"""
    base_url: str = Field(description="API 基础 URL")
    api_key: str = Field(description="API 密钥")


class ModelConfig(BaseModel):
    """单个模型的配置"""
    provider: str = Field(description="模型提供商名称")
    model_name: str = Field(description="模型名称")
    temperature: float = Field(0.7, description="生成温度")
    max_tokens: int = Field(1000, description="最大 token 数")
    input_cost: Optional[float] = Field(None, description="输入价格（人民币/M Tokens）")
    output_cost: Optional[float] = Field(None, description="输出价格（人民币/M Tokens）")


class VectorStoreConfig(BaseModel):
    """向量数据库配置"""
    provider: str = Field(default="openai", description="向量嵌入模型提供商名称")
    embedding_model_name: str = "text-embedding-3-small"
    chunk_size: int = 500
    chunk_overlap: int = 50
    embedding_dim: int = 1024
    collection_name: str = "game_knowledge"
    input_cost: Optional[float] = Field(None, description="输入价格（人民币/M Tokens）")
    output_cost: Optional[float] = Field(None, description="输出价格（人民币/M Tokens）")


# ====================================================================
# LangSmith 配置
# ====================================================================


class LangSmithConfig(BaseModel):
    """LangSmith 调试与可观测性配置"""
    tracing_enabled: bool = Field(False, description="是否启用 LangSmith 追踪")
    api_key: str = Field("", description="LangSmith API Key")
    project: str = Field("glyphkeeper", description="LangSmith 项目名")
    endpoint: str = Field("https://api.smith.langchain.com", description="LangSmith API 端点")

    def apply_env(self):
        """将配置写入环境变量（langsmith SDK 自动读取）"""
        if not self.tracing_enabled:
            return
        import os
        os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
        os.environ.setdefault("LANGCHAIN_PROJECT", self.project)
        os.environ.setdefault("LANGCHAIN_ENDPOINT", self.endpoint)
        if self.api_key:
            os.environ.setdefault("LANGCHAIN_API_KEY", self.api_key)


# ====================================================================
# 主配置类
# ====================================================================


class Settings(BaseModel):
    """应用总配置 — 聚合所有子配置"""
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    providers: Dict[str, ProviderConfig] = Field(default_factory=dict)
    model_tiers: Dict[str, ModelConfig] = Field(default_factory=dict)
    vector_store: VectorStoreConfig = Field(default_factory=VectorStoreConfig)
    langsmith: LangSmithConfig = Field(default_factory=LangSmithConfig)

    @property
    def PROJECT_NAME(self) -> str:
        return self.project.name

    @property
    def DEBUG(self) -> bool:
        return self.project.debug

    @property
    def MODEL_COST_TRACKING(self) -> bool:
        return self.project.model_cost_tracking

    @classmethod
    def load_config(cls) -> "Settings":
        """
        加载配置流程：
        - 读取 providers.ini（敏感信息：提供方密钥、数据库密码）
        - 读取 config.yaml（业务配置、模型分级）
        - 合并并实例化 Settings
        """
        ini_config = cls._load_providers_ini()
        yaml_config = cls._load_config_yaml()

        # 合并时过滤掉 None 值，避免覆盖默认值
        for key, value in ini_config.items():
            if value is not None:
                yaml_config[key] = value

        instance = cls(**yaml_config)
        instance._ensure_directories()
        instance.langsmith.apply_env()
        return instance

    @classmethod
    def _load_config_yaml(cls) -> Dict[str, Any]:
        """读取 config.yaml"""
        yaml_path = PROJECT_ROOT / "config.yaml"
        if not yaml_path.exists():
            _early_log("WARNING", f"未找到 {yaml_path}，将使用默认配置")
            return {}
        try:
            with open(yaml_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            _early_log("WARNING", f"无法读取 config.yaml: {e}")
            return {}

    @staticmethod
    def _load_providers_ini() -> Dict[str, Any]:
        """从 providers.ini 加载提供方和数据库配置"""
        ini_path = PROJECT_ROOT / "providers.ini"
        result: Dict[str, Any] = {}

        if not ini_path.exists():
            _early_log("WARNING", f"未找到 {ini_path}，请从 template/providers.ini.template 复制并配置")
            return result

        try:
            config = configparser.ConfigParser()
            config.read(ini_path, encoding='utf-8')

            providers: Dict[str, ProviderConfig] = {}
            for section in config.sections():
                section_lower = section.lower()
                try:
                    if section_lower == 'database':
                        continue  # 数据库配置已由 pgembed 管理
                    providers[section_lower] = ProviderConfig(
                        base_url=config.get(section, 'base_url'),
                        api_key=config.get(section, 'api_key'),
                    )
                except Exception as e:
                    _early_log("WARNING", f"无法加载配置节 [{section}]: {e}")

            if providers:
                result['providers'] = providers
            _early_log("INFO", f"成功加载 {len(providers)} 个 AI 提供方配置")
        except Exception as e:
            _early_log("WARNING", f"无法读取 providers.ini: {e}")

        return result

    def _ensure_directories(self):
        """确保必要的目录存在"""
        for name in ("logs", "data", "data/modules", "data/raw_sources", "data/intermediate",
                     "data/worlds", "data/rules"):
            (PROJECT_ROOT / name).mkdir(parents=True, exist_ok=True)

    def get_model_config(self, tier: str) -> ModelConfig:
        """获取指定层级的模型配置"""
        if tier not in self.model_tiers:
            raise KeyError(f"未知的模型层级 '{tier}'。可用层级: {list(self.model_tiers.keys())}")
        return self.model_tiers[tier]

    def get_provider_config(self, provider: str) -> Optional[ProviderConfig]:
        """根据提供商名称获取对应的提供方配置"""
        return self.providers.get(provider.lower())

    def get_full_model_config(self, tier: str) -> tuple[ModelConfig, Optional[ProviderConfig]]:
        """获取完整的模型配置（含提供商信息）"""
        model_config = self.get_model_config(tier)
        provider_config = self.get_provider_config(model_config.provider)
        if provider_config is None:
            raise ValueError(
                f"未找到提供方 '{model_config.provider}' 的配置。"
                f"请检查 providers.ini 文件是否包含 [{model_config.provider.upper()}] 配置节"
            )
        return model_config, provider_config

    def get_api_key(self, provider: str) -> Optional[str]:
        provider_config = self.get_provider_config(provider)
        return provider_config.api_key if provider_config else None

    def get_absolute_path(self, relative_path: str) -> Path:
        return PROJECT_ROOT / relative_path


# ── 全局单例（应用启动时自动加载） ──
_settings_instance: Optional[Settings] = None


def get_settings() -> Settings:
    """获取全局配置实例（懒加载单例）"""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings.load_config()
    return _settings_instance


def reload_config() -> Settings:
    """重新加载配置"""
    global _settings_instance
    _settings_instance = Settings.load_config()
    return _settings_instance


# ====================================================================
# 日志系统
# ====================================================================


def _early_log(level: str, msg: str):
    """在日志系统初始化前的早期日志输出（直接打印到 stderr）"""
    sys.stderr.write(f"[Config.{level}] {msg}\n")


def _load_debug_mode() -> bool:
    """从 config.yaml 读取 debug 配置（日志系统初始化前使用）"""
    try:
        config_path = PROJECT_ROOT / "config.yaml"
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
                if config and "project" in config:
                    return config["project"].get("debug", False)
    except Exception as e:
        sys.stderr.write(f"Warning: Failed to load debug config: {e}\n")
    return False


_DEBUG_MODE = _load_debug_mode()


def setup_logger(name: str = "GlyphKeeper", log_level: int = logging.INFO) -> logging.Logger:
    """配置并获取日志记录器"""
    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    if logger.handlers:
        return logger

    class ConditionalFormatter(logging.Formatter):
        """WARNING+ 级别显示行号"""
        def format(self, record):
            if record.levelno >= logging.WARNING:
                self._style._fmt = "[%(asctime)s] [%(levelname)s] [%(name)s] [%(module)s:%(lineno)d] - %(message)s"
            else:
                self._style._fmt = "[%(asctime)s] [%(levelname)s] [%(name)s] - %(message)s"
            return super().format(record)

    formatter = ConditionalFormatter(datefmt="%Y-%m-%d %H:%M:%S")

    # 控制台输出
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # 文件输出（按日期轮转）
    today = datetime.now().strftime("%Y-%m-%d")
    file_handler = RotatingFileHandler(
        LOG_DIR / f"{today}.log",
        maxBytes=10 * 1024 * 1024,  # 10MB
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger


# ── 全局 Logger 缓存 ──
_loggers: dict[str, logging.Logger] = {}


def get_logger(name: str = "GlyphKeeper") -> logging.Logger:
    """
    获取分级 Logger。

    Args:
        name: Logger 名称（默认 GlyphKeeper）

    Returns:
        配置好的 Logger 实例
    """
    if name not in _loggers:
        # debug=true 时自动使用 DEBUG 级别
        level = logging.DEBUG if _DEBUG_MODE else logging.INFO
        _loggers[name] = setup_logger(name, log_level=level)
    return _loggers[name]
