# -*- coding: utf-8 -*-
"""
@File     :   db_lookup_node.py
@Desc     :   DB Lookup Node - 从 PostgreSQL 读模型表查询物理现实上下文
@Note     :   纯 Python + SQL，零 LLM 零向量库，返回 <physical_reality> XML 片段
              同时产出 scene_npcs 供 disambiguation_node 做 NPC 消歧使用
"""

from __future__ import annotations

from typing import Optional
from src.state.game_state import GameState, get_current_player
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
    全量空间拓扑拉取 — 查询当前场景及所有邻接场景的静态元数据。
    不触碰 clue_discoveries 表，确保左脑零推导、零剧透。

    查询链路：
      查 locations 表获取当前场景信息 + exits_json
      按 exits_json 批量拉取所有邻接场景的元数据
      批量拉取所有相关场景的 entities（只取 tags/stats，不碰线索）
      透传 time_slot 和 game_phase 到 XML
      产出 scene_npcs / entity_name_map 供 disambiguation_node 消歧
    """
    current_loc = get_current_player(state).get("current_location", "")
    time_slot = state.get("time_slot", "AFTERNOON")
    game_phase = state.get("game_phase", "exploration")
    EMPTY = {"physical_reality": "", "world_context": "", "scene_npcs": [], "entity_name_map": {}}

    if not current_loc:
        logger.debug("db_lookup_node: 无 current_location")
        return EMPTY

    try:
        store = await _get_store()
        conn = await store._get_conn()

        # 查当前场景
        loc_row = await conn.fetchrow(
            "SELECT id, key, name, base_desc, tags, exits_json FROM locations WHERE key = $1",
            current_loc,
        )
        if not loc_row:
            logger.debug(f"db_lookup_node: 未找到场景 '{current_loc}'")
            return EMPTY

        # asyncpg 在测试环境下 JSONB 列可能返回 str 而非 dict，一律 json.loads 保底
        raw_exits = loc_row["exits_json"]
        if isinstance(raw_exits, str):
            exits_json = json.loads(raw_exits) if raw_exits else {}
        else:
            exits_json = raw_exits or {}

        # 收集所有相关场景 key：当前场景 + 所有邻接场景
        adjacent_keys = list(exits_json.values())
        all_loc_keys = [current_loc] + adjacent_keys

        # 批量查询所有相关场景的基础数据
        loc_rows = await conn.fetch(
            "SELECT id, key, name, base_desc, tags, exits_json FROM locations WHERE key = ANY($1)",
            all_loc_keys,
        )
        loc_map = {r["key"]: r for r in loc_rows}  # key -> row

        # 收集所有相关场景的 id，批量查 entities
        all_loc_ids = [r["id"] for r in loc_rows]
        entity_rows = await conn.fetch(
            """SELECT e.key, e.name, e.location_id, e.tags, e.stats_json FROM entities e
               WHERE e.location_id = ANY($1)""",
            all_loc_ids,
        )

        # 按 location_id 分组 entities
        entities_by_loc: dict[str, list[dict]] = {}
        for er in entity_rows:
            lid = str(er["location_id"])
            entities_by_loc.setdefault(lid, []).append({
                "key": er["key"],
                "name": er["name"],
                "tags": er["tags"] or [],
                "stats": er["stats_json"] or {},
            })

        # 批量查当前场景物品（interactables 表）— 供 disambiguation 消歧
        interactable_rows = await conn.fetch(
            """SELECT i.key, i.name, i.tags, i.state FROM interactables i
               JOIN locations l ON i.location_id = l.id
               WHERE l.key = $1""",
            current_loc,
        )
        scene_items = [
            {"key": r["key"], "name": r["name"], "tags": r["tags"] or [], "state": r["state"] or ""}
            for r in interactable_rows
        ]

        # 构建 scene_npcs 和 entity_name_map（供 disambiguation_node 消歧）
        # 取当前场景 + 所有邻接场景的实体，确保玩家站在大街也能提到屋内 NPC 的名字
        scene_npcs: list[str] = []
        entity_name_map: dict[str, str] = {}
        for loc_id, entities in entities_by_loc.items():
            for ent in entities:
                entity_name_map[ent["key"]] = ent["name"]
                scene_npcs.append(ent["key"])
        # 物品也加入 entity_name_map，便于后续消歧使用
        for item in scene_items:
            entity_name_map[item["key"]] = item["name"]

        # 附加 WorldManager 运行时实体（兜底）
        try:
            session_id = state.get("session_id", "")
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
            logger.debug(f"db_lookup_node: WorldManager 查询失败: {e}")

        # 构建当前场景 XML
        cur = loc_map.get(current_loc, loc_row)
        cur_tags = cur["tags"] or []

        xml_parts = ["<physical_reality>"]

        # --- current_location ---
        cur_loc_id = str(loc_row["id"])
        cur_entities = entities_by_loc.get(cur_loc_id, [])

        xml_parts.append(f'  <current_location id="{current_loc}">')
        xml_parts.append(f'    <name>{cur["name"]}</name>')
        xml_parts.append(f'    <base_desc>{cur["base_desc"]}</base_desc>')
        if cur_tags:
            xml_parts.append(f'    <tags>{json.dumps(cur_tags, ensure_ascii=False)}</tags>')
        if cur_entities:
            xml_parts.append("    <present_entities>")
            for ent in cur_entities:
                xml_parts.append(f'      <entity id="{ent["key"]}">')
                xml_parts.append(f'        <name>{ent["name"]}</name>')
                xml_parts.append(f'        <tags>{json.dumps(ent["tags"], ensure_ascii=False)}</tags>')
                xml_parts.append("      </entity>")
            xml_parts.append("    </present_entities>")
        xml_parts.append("  </current_location>")

        # --- items (当前场景物品) ---
        if scene_items:
            xml_parts.append("  <items>")
            for item in scene_items:
                attrs = f'id="{item["key"]}"'
                if item.get("state"):
                    attrs += f' state="{item["state"]}"'
                if item.get("tags"):
                    attrs += f' tags={json.dumps(item["tags"], ensure_ascii=False)}'
                xml_parts.append(f'    <item {attrs}>{item["name"]}</item>')
            xml_parts.append("  </items>")

        # --- adjacent_locations ---
        xml_parts.append("  <adjacent_locations>")
        for adj_key in adjacent_keys:
            adj = loc_map.get(adj_key)
            if not adj:
                continue
            # 反向映射获取方向名
            direction = next((k for k, v in exits_json.items() if v == adj_key), "Unknown")
            adj_id = str(adj["id"])
            adj_tags = adj["tags"] or []

            xml_parts.append(f'    <location id="{adj_key}">')
            xml_parts.append(f'      <name>{adj["name"]}</name>')
            xml_parts.append(f'      <direction>{direction}</direction>')
            if adj_tags:
                xml_parts.append(f'      <tags>{json.dumps(adj_tags, ensure_ascii=False)}</tags>')

            adj_entities = entities_by_loc.get(adj_id, [])
            if adj_entities:
                xml_parts.append("      <present_entities>")
                for ent in adj_entities:
                    xml_parts.append(f'        <entity id="{ent["key"]}">')
                    xml_parts.append(f'          <name>{ent["name"]}</name>')
                    xml_parts.append(f'          <tags>{json.dumps(ent["tags"], ensure_ascii=False)}</tags>')
                    xml_parts.append("        </entity>")
                xml_parts.append("      </present_entities>")

            xml_parts.append(f'    </location>')
        xml_parts.append("  </adjacent_locations>")

        # --- session_state (透传动态参数，不做逻辑计算) ---
        xml_parts.append("  <session_state>")
        xml_parts.append(f'    <current_time>{time_slot}</current_time>')
        xml_parts.append(f'    <game_phase>{game_phase}</game_phase>')
        xml_parts.append("  </session_state>")

        xml_parts.append("</physical_reality>")

        xml_str = "\n".join(xml_parts)
        logger.debug(
            f"db_lookup_node: {cur['name']} -> "
            f"{len(adjacent_keys)} adjacent, "
            f"{len(entity_rows)} entities total"
        )

        return {
            "physical_reality": xml_str,
            "world_context": xml_str,
            "scene_npcs": scene_npcs,
            "entity_name_map": entity_name_map,
        }

    except Exception as e:
        logger.error(f"db_lookup_node: 查询失败: {e}")
        return {"physical_reality": "", "world_context": "", "scene_npcs": [], "entity_name_map": {}}
