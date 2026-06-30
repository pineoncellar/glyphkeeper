# -*- coding: utf-8 -*-
"""
@File     :   db_lookup_node.py
@Desc     :   DB Lookup Node - 从 PostgreSQL 读模型表查询物理现实上下文
@Note     :   纯 Python + SQL，零 LLM 零向量库，返回 <physical_reality> XML 片段
              同时产出 scene_npcs 供 disambiguation_node 做 NPC 消歧使用
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


# ── 全局世界管理器实例（懒加载） ──

_world_mgr: Optional["WorldManager"] = None  # noqa: F821


async def _get_world_manager(session_id: str):
    """懒加载 WorldManager 实例"""
    global _world_mgr
    if _world_mgr is None:
        from src.memory.event_store import create_event_store
        from src.state.event_log import EventLog
        from src.state.world_state import WorldManager
        es = await create_event_store()
        _world_mgr = WorldManager(event_store=es)
    return _world_mgr


async def db_lookup_node(state: GameState) -> dict:
    """
    从 PG 读模型表查询当前场景的物理事实，拼为 XML。
    同时查询 WorldManager 获取当前场景中的 NPC 列表。

    查询链路：
      查 locations 表获取名称/出口/描述
      查 interactables 表获取该场景的物品列表
      查 clue_discoveries 表关联线索
      通过 WorldManager 获取场景 NPC 实体
      拼为 <physical_reality> XML + scene_npcs 列表
    """
    current_loc = state.get("current_location", "")
    EMPTY = {"physical_reality": "", "world_context": "", "scene_npcs": [], "entity_name_map": {}}

    if not current_loc:
        logger.debug("db_lookup_node: 无 current_location")
        return EMPTY

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
            return EMPTY

        loc_name = loc_row["name"]
        loc_desc = loc_row["base_desc"]
        loc_tags = loc_row["tags"] or []
        loc_id = loc_row["id"]

        # asyncpg 在测试环境下 JSONB 列可能返回 str 而非 dict，一律 json.loads 保底
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
            clue_rows = await conn.fetch(
                """SELECT flavor_text FROM clue_discoveries
                   WHERE interactable_id = $1""",
                r["id"],
            )
            clue_hint = ""
            if clue_rows:
                clue_hint = " (含线索)"
            items.append(f"{r['name']}{clue_hint}")

        # 从 entities 表获取 NPC 显示名 + 从 WorldManager 获取场景 NPC key 列表
        scene_npcs: list[str] = []
        entity_name_map: dict[str, str] = {}
        try:
            session_id = state.get("session_id", "")
            # 从读模型 entities 表查询显示名（精确、高效）
            entity_rows = await conn.fetch(
                """SELECT e.key, e.name FROM entities e
                   JOIN locations l ON e.location_id = l.id
                   WHERE l.key = $1""",
                current_loc,
            )
            for r in entity_rows:
                entity_name_map[r["key"]] = r["name"]
                scene_npcs.append(r["key"])

            # 兜底：WorldManager 可能还有额外实体（如通过运行时事件添加的）
            world_mgr = await _get_world_manager(session_id)
            location_data = await world_mgr.load_location(session_id, current_loc)
            if location_data:
                raw_entities = location_data.get("entities") or []
                for e in raw_entities:
                    ek = e if isinstance(e, str) else e.get("key", e.get("name", ""))
                    if ek not in entity_name_map:
                        en = e if isinstance(e, str) else e.get("name", ek)
                        entity_name_map[ek] = en
                        scene_npcs.append(ek)
        except Exception as e:
            logger.debug(f"db_lookup_node: 查询 NPC 失败: {e}")

        # 出口格式化 — 从 locations 表查目标场景中文名
        exit_parts = []
        for direction, target_key in exits_json.items():
            # 查询目标场景的显示名（如 loc_kimball_house_study → "金博尔宅-书房"）
            target_name = target_key
            try:
                target_row = await conn.fetchrow(
                    "SELECT name FROM locations WHERE key = $1", target_key
                )
                if target_row:
                    target_name = target_row["name"]
            except Exception:
                pass
            # 用「;」分隔多条目避免中文名内逗号冲突
            exit_parts.append(f"{direction}|{target_name}|{target_key}")
        exit_desc = "; ".join(exit_parts) if exit_parts else "无"

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
        logger.debug(f"db_lookup_node: {loc_name} → {len(items)} 物品, {len(scene_npcs)} NPCs")

        return {
            "physical_reality": xml_str,
            "world_context": xml_str,
            "scene_npcs": scene_npcs,
            "entity_name_map": entity_name_map,
        }

    except Exception as e:
        logger.error(f"db_lookup_node: 查询失败: {e}")
        return {"physical_reality": "", "world_context": "", "scene_npcs": [], "entity_name_map": {}}
