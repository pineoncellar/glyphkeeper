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

import re
import uuid
from typing import Any


def normalize_name(name: str) -> str:
    """
    统一角色/物品名称格式：去除首尾空格，统一全半角。
    
    - 去除首尾空白
    - 全角英文字母 → 半角
    - 全角数字 → 半角
    - 合并连续空格
    """
    if not name:
        return ""
    
    # 去除首尾空白
    name = name.strip()
    
    # 全角英文字母 → 半角
    result = []
    for ch in name:
        code = ord(ch)
        if 0xFF21 <= code <= 0xFF3A:  # 全角 A-Z
            result.append(chr(code - 0xFEE0))
        elif 0xFF41 <= code <= 0xFF5A:  # 全角 a-z
            result.append(chr(code - 0xFEE0))
        elif 0xFF10 <= code <= 0xFF19:  # 全角 0-9
            result.append(chr(code - 0xFEE0))
        else:
            result.append(ch)
    
    name = "".join(result)
    
    # 合并连续空格
    name = re.sub(r'\s+', ' ', name)
    
    return name


def validate_uuid(value: str) -> bool:
    """验证 UUID 字符串是否合法"""
    if not value:
        return False
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError):
        return False


def safe_get(d: dict, *keys, default: Any = None) -> Any:
    """
    安全的嵌套字典取值：d["a"]["b"]["c"] 而不抛 KeyError。
    
    用法:
      value = safe_get(data, "a", "b", "c", default="fallback")
    """
    current = d
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current
