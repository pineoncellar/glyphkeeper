# -*- coding: utf-8 -*-
"""
@File     :   db_lookup_node.py
@Desc     :   DB Lookup Node — 从 PostgreSQL 读模型表查询物理现实上下文
@Note     :   纯 Python + SQL，零 LLM 零向量库，返回 <physical_reality> XML 片段

Node 签名:
    async def db_lookup_node(state: GameState) -> dict:
        查当前场景 locations/interactables/clues/entities → 拼 XML
        返回: {"physical_reality": xml_str, "world_context": xml_str}
"""

from __future__ import annotations

from typing import Optional
from src.state.game_state import GameState
from src.tools import get_logger

import json

logger = get_logger(__name__)


# ── 全局读模型实例（懒加载） ──

_store: Optional["StaticReadStore"] = None  # noqa: F821


async def _get_store():
    global _store
    if _store is None:
        from src.state.read_models import StaticReadStore
        _store = StaticReadStore()
    return _store


async def db_lookup_node(state: GameState) -> dict:
    """从 PG 读模型表查询当前场景的物理事实，拼为 XML

    查询链路:
      1. 从 state.current_location 取场景 key
      2. 查 locations 表获取名称/出口/描述
      3. 查 interactables 表获取该场景的物品列表
      4. 查 clue_discoveries 表关联线索
      5. 拼为 <physical_reality> XML
    """
    current_loc = state.get("current_location", "")
    if not current_loc:
        logger.debug("db_lookup_node: 无 current_location")
        return {"physical_reality": "", "world_context": ""}

    try:
        store = await _get_store()
        conn = await store._get_conn()

        # 查场景
        loc_row = await conn.fetchrow(
            "SELECT id, name, base_desc, tags, exits_json FROM locations WHERE key = $1",
            current_loc,
        )
        if not loc_row:
            logger.debug(f"db_lookup_node: 未找到场景 '{current_loc}'")
            return {"physical_reality": "", "world_context": ""}

        loc_name = loc_row["name"]
        loc_desc = loc_row["base_desc"]
        loc_tags = loc_row["tags"] or []
        loc_id = loc_row["id"]

        # asyncpg 在测试环境下（Windows/Python3.12）JSONB 列返回 Python str 而非 dict。
        # 无论数据从何而来，一律 json.loads 保底。
        raw_exits = loc_row["exits_json"]
        if isinstance(raw_exits, str):
            exits_json = json.loads(raw_exits) if raw_exits else {}
        else:
            exits_json = raw_exits or {}

        # 查物品
        item_rows = await conn.fetch(
            "SELECT id, key, name, tags FROM interactables WHERE location_id = $1",
            loc_id,
        )
        items = []
        for r in item_rows:
            tags = r["tags"] or []
            clue_rows = await conn.fetch(
                """SELECT flavor_text FROM clue_discoveries
                   WHERE interactable_id = $1""",
                r["id"],
            )
            clue_hint = ""
            if clue_rows:
                clue_hint = " (含线索)"
            items.append(f"{r['name']}{clue_hint}")

        # 出口格式化
        exit_desc = ", ".join(
            f"{k}({v})" for k, v in exits_json.items()
        ) if exits_json else "无"

        # 环境标签
        tag_desc = ", ".join(loc_tags) if loc_tags else ""

        # 构建 XML
        parts = ["<physical_reality>"]
        parts.append(f"  <location>{loc_name}</location>")
        parts.append(f"  <description>{loc_desc}</description>")
        parts.append(f"  <exits>{exit_desc}</exits>")
        if items:
            parts.append(f"  <items>{'; '.join(items)}</items>")
        if tag_desc:
            parts.append(f"  <environment_tags>{tag_desc}</environment_tags>")
        parts.append("</physical_reality>")

        xml_str = "\n".join(parts)
        logger.debug(f"db_lookup_node: {loc_name} → {len(items)} 物品")

        return {"physical_reality": xml_str, "world_context": xml_str}

    except Exception as e:
        logger.error(f"db_lookup_node: 查询失败: {e}")
        return {"physical_reality": "", "world_context": ""}
