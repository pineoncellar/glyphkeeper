"""
通用工具函数

职责:
  - 提供跨模块使用的辅助函数
  - 字符串处理、数据格式化
  - 错误类型定义
  - 通用数据校验

函数:
  - normalize_name(name: str) -> str  # 统一名称格式
  - validate_uuid(value: str) -> bool
  - safe_get(d: dict, *keys, default=None) -> Any
"""
