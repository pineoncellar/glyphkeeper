"""
配置读取模块
"""

import yaml
import configparser
from pathlib import Path
from typing import Dict, Optional, List, Any
from pydantic import BaseModel, Field, model_validator
from .logger import get_logger

# 初始化日志记录器
logger = get_logger(__name__)

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

class ProjectConfig(BaseModel):
    """项目基础配置"""
    name: str = Field("GlyphKeeper", description="项目名称")
    debug: bool = Field(False, description="调试模式")
    model_cost_tracking: bool = Field(False, description="是否开启模型成本追踪")


class DatabaseConfig(BaseModel):
    """数据库配置"""
    host: Optional[str] = Field(None, description="数据库主机")
    port: Optional[str] = Field(None, description="数据库端口")
    username: Optional[str] = Field(None, description="数据库用户名")
    password: Optional[str] = Field(None, description="数据库密码")
    project_name: Optional[str] = Field(None, description="项目名称，与项目基础配置中的名称保持一致")


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


# ============================================
# 主配置类
# ============================================

class Settings(BaseModel):
    """
    应用总配置
    """
    
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    database: DatabaseConfig = Field(default_factory=lambda: DatabaseConfig(url="sqlite:///./data/game.db"))
    providers: Dict[str, ProviderConfig] = Field(default_factory=dict)
    model_tiers: Dict[str, ModelConfig] = Field(default_factory=dict)
    vector_store: VectorStoreConfig = Field(default_factory=VectorStoreConfig)
    
    @model_validator(mode='after')
    def sync_database_project_name(self):
        """使数据库配置中的项目名称、用户名称与全局项目名称一致"""
        if self.database.project_name is None:
            self.database.project_name = self.project.name

        if self.database.username is None:
            self.database.username = self.project.name
            
        return self

    @property
    def PROJECT_NAME(self) -> str:
        """项目名称"""
        return self.project.name
    
    @property
    def DEBUG(self) -> bool:
        """调试模式"""
        return self.project.debug
    
    @property
    def MODEL_COST_TRACKING(self) -> bool:
        """模型成本追踪开关"""
        return self.project.model_cost_tracking
    
    @property
    def DATABASE_URL(self) -> str:
        """数据库 URL"""
        return self.database.url
    
    @classmethod
    def load_config(cls) -> "Settings":
        """
        1. 读取 providers.ini (提供方配置、数据库 - 敏感信息)
        2. 读取 config.yaml (业务配置、项目基础配置)
        3. 合并并实例化 Settings 对象
        """
        ini_config = cls._load_providers_ini()
        
        yaml_path = PROJECT_ROOT / "config.yaml"
        yaml_config = {}
        
        if yaml_path.exists():
            try:
                with open(yaml_path, "r", encoding="utf-8") as f:
                    yaml_config = yaml.safe_load(f) or {}
            except Exception as e:
                logger.warning(f"无法读取 config.yaml: {e}，将使用默认配置")
        else:
            logger.warning(f"未找到 {yaml_path}，将使用默认配置")

        yaml_config.update(ini_config)
        
        instance = cls(**yaml_config)
        instance._ensure_directories()
        return instance
    
    @staticmethod
    def _load_providers_ini() -> Dict[str, Any]:
        """
        从 providers.ini 加载配置
        """
        ini_path = PROJECT_ROOT / "providers.ini"
        result = {
            'providers': {},
            'database': None
        }
        
        if not ini_path.exists():
            logger.warning(f"未找到 {ini_path}，请从 template/providers.ini.template 复制并配置")
            return result
        
        try:
            config = configparser.ConfigParser()
            config.read(ini_path, encoding='utf-8')
            
            for section in config.sections():
                try:
                    section_lower = section.lower()
                    
                    # 数据库
                    if section_lower == 'database':
                        # 读取基本配置
                        host = config.get(section, 'host', fallback=None)
                        port = config.get(section, 'port', fallback=None)
                        username = config.get(section, 'username', fallback=None)
                        password = config.get(section, 'password', fallback=None)
                        
                        result['database'] = DatabaseConfig(
                            host=host,
                            port=port,
                            username=username,
                            password=password
                        )
                    
                    # AI提供方配置
                    else:
                        provider_config = ProviderConfig(
                            base_url=config.get(section, 'base_url'),
                            api_key=config.get(section, 'api_key')
                        )
                        result['providers'][section_lower] = provider_config
                
                except Exception as e:
                    logger.warning(f"无法加载配置节 [{section}]: {e}")
            
            provider_count = len(result['providers'])
            logger.info(f"成功加载 {provider_count} 个 AI 提供方配置")
            
            if result['database']:
                logger.info("成功加载数据库配置")
            
        except Exception as e:
            logger.warning(f"无法读取 providers.ini: {e}")
        
        return result
    
    def _ensure_directories(self):
        """确保必要的目录存在"""
        for name in ("logs", "data", "data/modules", "data/raw_sources", "data/intermediate"):
            (PROJECT_ROOT / name).mkdir(parents=True, exist_ok=True)
    
    def get_model_config(self, tier: str) -> ModelConfig:
        """
        获取指定层级的模型配置
        """
        if tier not in self.model_tiers:
            available = ", ".join(self.model_tiers.keys())
            raise KeyError(
                f"未知的模型层级 '{tier}'. "
                f"可用层级: {available}"
            )
        return self.model_tiers[tier]
    
    def get_provider_config(self, provider: str) -> Optional[ProviderConfig]:
        """
        根据提供商名称获取对应的提供方配置
        """
        return self.providers.get(provider.lower())
    
    def get_full_model_config(self, tier: str) -> tuple[ModelConfig, Optional[ProviderConfig]]:
        """
        获取完整的模型配置
        """
        model_config = self.get_model_config(tier)
        provider_config = self.get_provider_config(model_config.provider)
        
        if provider_config is None:
            raise ValueError(
                f"未找到提供方 '{model_config.provider}' 的配置. "
                f"请检查 providers.ini 文件是否包含 [{model_config.provider.upper()}] 配置节"
            )
        
        return model_config, provider_config
    
    def get_database_config(self) -> DatabaseConfig:
        """
        获取数据库配置
        """
        return self.database
    
    def get_api_key(self, provider: str) -> Optional[str]:
        """
        根据提供商名称获取对应的APIKey
        """
        provider_config = self.get_provider_config(provider)
        return provider_config.api_key if provider_config else None
    
    def get_absolute_path(self, relative_path: str) -> Path:
        """
        将相对路径转换为绝对路径
        """
        return PROJECT_ROOT / relative_path

# 实例化配置 (应用启动时自动加载)
settings = Settings.load_config()


# ============================================
# 便捷函数
# ============================================

def get_settings() -> Settings:
    """
    获取全局配置实例
    """
    return settings


def reload_config() -> Settings:
    """
    重新加载配置
    """
    global settings
    settings = Settings.load_config()
    return settings
