"""
随机数工具

职责:
  - 提供安全的随机数生成
  - 随机选择（抽签、随机目标）
  - 洗牌算法
  - 基于 Python secrets 模块的密码学安全随机

函数:
  - secure_choice(items) -> Any
  - secure_shuffle(items) -> List
  - weighted_choice(items, weights) -> Any
"""

import secrets
from typing import Any, Sequence, List


def secure_choice(seq: Sequence) -> Any:
    """密码学安全的随机选择"""
    if not seq:
        raise ValueError("不能从空序列中选择")
    return secrets.choice(list(seq))


def secure_shuffle(seq: List) -> List:
    """
    密码学安全的洗牌，返回新列表。
    不修改原列表。
    """
    result = list(seq)
    for i in range(len(result) - 1, 0, -1):
        j = secrets.randbelow(i + 1)
        result[i], result[j] = result[j], result[i]
    return result


def weighted_choice(items: List, weights: List[float]) -> Any:
    """
    加权随机选择。

    参数:
      items: 选项列表
      weights: 权重列表（长度必须与 items 相同）

    返回: 被选中的选项
    """
    if not items or not weights:
        raise ValueError("items 和 weights 不能为空")
    if len(items) != len(weights):
        raise ValueError(f"items 长度 ({len(items)}) 与 weights 长度 ({len(weights)}) 不匹配")

    total = sum(weights)
    if total <= 0:
        raise ValueError("权重总和必须大于 0")

    r = secrets.randbelow(10000) / 10000.0 * total
    cumulative = 0.0
    for item, weight in zip(items, weights):
        cumulative += weight
        if r <= cumulative:
            return item

    return items[-1]
