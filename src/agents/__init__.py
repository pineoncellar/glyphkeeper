"""
Agents 模块
封装业务逻辑层，包括搜索代理和游戏代理
"""
from .search import (
    SearchAgent,
    SearchResult,
    search,
    game_search,
)

__all__ = [
    "SearchAgent",
    "SearchResult",
    "search",
    "game_search",
]
