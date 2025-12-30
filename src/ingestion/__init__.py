"""
Ingestion 模块
数据摄入和处理
"""
from .loader import (
    ModuleLoader,
    load_module_from_json
)

__all__ = [
    "ModuleLoader",
    "load_module_from_json"
]
