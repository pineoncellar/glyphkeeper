# -*- coding: utf-8 -*-
"""
@File     :   navigation.py
@Desc     :   导航域模型 — BFS 寻路算法与阻挡标签检查
@Note     :   纯函数，零外部依赖，可独立 pytest。

数据结构:
    NavNode = {"key": str, "exits": {方向: 目标key}, "tags": [str]}
    NavGraph = {node_key: NavNode}

函数:
    build_graph(locations)       — 从位置列表构建导航图
    find_path(graph, start, end) — BFS 搜索最短路径，返回 [{dir, key}, ...]
    check_blocked(node, tags)    — 检查节点是否被阻挡标签封锁
"""

from __future__ import annotations

from collections import deque
from typing import Any

# ── 阻挡标签集 — 硬编码的不可通行标签列表 ──
# 状态：当场景 tag 包含以下任意值时，视为不可通行
BLOCKED_TAGS = {"blocked", "sealed", "locked", "police_line"}


def build_graph(locations: list[dict]) -> dict[str, dict]:
    """从位置列表构建导航图

    locations 格式（与 book.json 中的 locations 一致）:
        [{"key": "...", "exits": {"方向": "目标key", ...}, "tags": [...], ...}, ...]

    返回:
        {node_key: {"key": ..., "exits": {...}, "tags": [...]}, ...}
    """
    graph: dict[str, dict] = {}
    for loc in locations:
        key = loc.get("key", "")
        if not key:
            continue
        graph[key] = {
            "key": key,
            "exits": loc.get("exits", {}) or {},
            "tags": loc.get("tags", []) or [],
        }
    return graph


def find_path(
    graph: dict[str, dict],
    start_key: str,
    end_key: str,
    blocked_tags: set[str] | None = None,
) -> list[dict] | None:
    """BFS 求最短路径

    先检查起点到终点是否可达，沿途跳过 blocked_tags 标记的节点。
    返回路径列表，每步格式: {"direction": "方向", "key": "目标key"}
    不可达或起点==终点时返回 None。

    参数:
        graph:        NavGraph
        start_key:    起点场景 key
        end_key:      目标场景 key
        blocked_tags: 视为阻挡的标签集合，默认 BLOCKED_TAGS
    """
    if start_key == end_key:
        return None
    if start_key not in graph or end_key not in graph:
        return None

    blocked = BLOCKED_TAGS if blocked_tags is None else blocked_tags

    # BFS: queue 中存 (当前key, 路径列表)
    queue: deque[tuple[str, list[dict]]] = deque()
    queue.append((start_key, []))
    visited = {start_key}

    while queue:
        curr_key, path = queue.popleft()

        if curr_key == end_key:
            return path

        curr_node = graph.get(curr_key)
        if not curr_node:
            continue

        for direction, neighbor_key in curr_node["exits"].items():
            if neighbor_key not in graph:
                continue  # 出口指向不存在的场景，跳过
            if neighbor_key in visited:
                continue

            neighbor_node = graph[neighbor_key]
            if is_blocked(neighbor_node, blocked):
                continue  # 被阻挡，不可通行

            visited.add(neighbor_key)
            new_path = list(path)
            new_path.append({"direction": direction, "key": neighbor_key})
            queue.append((neighbor_key, new_path))

    return None  # 无路径


def is_blocked(node: dict, blocked_tags: set[str] | None = None) -> bool:
    """检查节点是否被阻挡标签封锁

    blocked_tags=None 时使用默认 BLOCKED_TAGS；
    传入空 set() 表示不阻挡任何标签。
    """
    if not node:
        return True
    tags = node.get("tags", []) or []
    blocked = BLOCKED_TAGS if blocked_tags is None else blocked_tags
    return any(t in blocked for t in tags)
