# -*- coding: utf-8 -*-
"""
@File     :   navigation_node.py
@Desc     :   导航节点 — 处理 MOVE 意图: 将玩家送往目标场景
@Note     :   纯确定性逻辑 + LLM 兜底。coc哲学: 命名目的地直接去（非阻拦即成功），
              方向移动检查出口（"北边"→exits），LLM 解析模糊指代（"现场"→书房）。
              事件系统负责途中事件，导航只负责「去哪」。

Node 签名:
    async def navigation_node(state: GameState) -> dict:
        读 intent.data.target → 查当前场景 exits → 四级匹配 →
        成功则返回 current_location state_patch，否则在 resolution.error 描述原因
"""

from __future__ import annotations

import json
from typing import Optional

from src.state.game_state import GameState
from src.tools import get_logger
from src.domain.navigation import find_path, build_graph, is_blocked, BLOCKED_TAGS

logger = get_logger(__name__)


# ── 全局读模型实例（懒加载，与 db_lookup_node 共用模式） ──

_store: Optional["StaticReadStore"] = None  # noqa: F821


async def _get_store():
    global _store
    if _store is None:
        from src.state.read_models import StaticReadStore
        _store = StaticReadStore()
    return _store


async def _load_location_exits(conn, location_key: str) -> Optional[dict]:
    """从 locations 表加载指定场景的 exits_json

    返回:
        exits 字典 {"方向": "目标key", ...}，或 None 表示场景不存在
    """
    row = await conn.fetchrow(
        "SELECT exits_json FROM locations WHERE key = $1",
        location_key,
    )
    if not row:
        return None
    raw = row["exits_json"]
    if isinstance(raw, str):
        return json.loads(raw) if raw else {}
    return raw or {}


def _match_exit_direction(target: str, exits: dict) -> Optional[str]:
    """出口方向匹配 — 仅用于方向性移动（"北边"/"north"/"出去"）

    不区分大小写，匹配 exits 的方向 key。
    """
    target_lower = target.strip().lower()
    for direction, loc_key in exits.items():
        if direction.strip().lower() == target_lower:
            return loc_key
    return None


def _match_any_location(target: str, location_keys: list[str], location_names: list[str]) -> Optional[str]:
    """匹配任意已知场景 — Key → 精确名 → 子串模糊

    coc跑团理念：只要玩家说出了模组中存在的场景名，就直接让他过去，
    不需要出口验证。「能否到达」是事件系统的责任，不是导航的。
    """
    target_stripped = target.strip()
    if not target_stripped:
        return None

    # 第一级: location key 精确匹配
    if target_stripped in location_keys:
        return target_stripped

    # 第二级: 场景名称精确匹配
    for idx, name in enumerate(location_names):
        if name == target_stripped:
            return location_keys[idx]

    # 第三级: 子串模糊匹配（排除单字）
    if len(target_stripped) < 2:
        return None
    candidates = []
    for idx, name in enumerate(location_names):
        if not name or len(name) < 2:
            continue
        shared = False
        for start in range(len(target_stripped) - 1):
            if target_stripped[start:start + 2] in name:
                shared = True
                break
        if not shared:
            for start in range(len(name) - 1):
                if name[start:start + 2] in target_stripped:
                    shared = True
                    break
        if shared:
            candidates.append((len(name), idx, location_keys[idx]))
    if candidates:
        candidates.sort(key=lambda x: x[0])
        return candidates[0][2]

    return None


async def _build_location_physical_reality(
    conn, location_key: str, state: GameState,
) -> str:
    """为目标地点构建 <physical_reality> XML，语义等价于 db_lookup_node

    导航移动成功后调用，用新位置的完整数据覆盖 state 中的旧物理现实。
    查询链路：目标地点信息 + 邻接场景 + 实体 + 物品，格式与 db_lookup_node 一致。
    """
    import json

    time_slot = state.get("time_slot", "AFTERNOON")
    game_phase = state.get("game_phase", "exploration")

    # 查目标地点
    loc_row = await conn.fetchrow(
        "SELECT id, key, name, base_desc, tags, exits_json FROM locations WHERE key = $1",
        location_key,
    )
    if not loc_row:
        return ""

    raw_exits = loc_row["exits_json"]
    exits_json = json.loads(raw_exits) if isinstance(raw_exits, str) else (raw_exits or {})
    adjacent_keys = list(exits_json.values())

    # 批量查邻接场景
    all_loc_keys = [location_key] + adjacent_keys
    loc_rows = await conn.fetch(
        "SELECT id, key, name, base_desc, tags, exits_json FROM locations WHERE key = ANY($1)",
        all_loc_keys,
    )
    loc_map = {r["key"]: r for r in loc_rows}

    # 批量查实体
    all_loc_ids = [r["id"] for r in loc_rows]
    entity_rows = await conn.fetch(
        """SELECT e.key, e.name, e.location_id, e.tags FROM entities e
           WHERE e.location_id = ANY($1)""",
        all_loc_ids,
    )
    entities_by_loc: dict[str, list[dict]] = {}
    for er in entity_rows:
        lid = str(er["location_id"])
        entities_by_loc.setdefault(lid, []).append({
            "key": er["key"], "name": er["name"], "tags": er["tags"] or [],
        })

    # 查目标地点的物品
    interactable_rows = await conn.fetch(
        """SELECT i.key, i.name, i.tags FROM interactables i
           JOIN locations l ON i.location_id = l.id
           WHERE l.key = $1""",
        location_key,
    )

    # ── 组装 XML ──
    cur = loc_map.get(location_key, loc_row)
    cur_tags = cur["tags"] or []

    # 当前地点的实体
    cur_loc_id = str(loc_row["id"])
    cur_entities = entities_by_loc.get(cur_loc_id, [])

    xml_parts = ["<physical_reality>"]
    xml_parts.append(f'  <current_location id="{location_key}">')
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

    # 物品
    if interactable_rows:
        item_names = ";".join(r["name"] for r in interactable_rows)
        xml_parts.append(f'  <items>{item_names}</items>')

    # 邻接场景
    xml_parts.append("  <adjacent_locations>")
    for adj_key in adjacent_keys:
        adj = loc_map.get(adj_key)
        if not adj:
            continue
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

    # session_state
    xml_parts.append("  <session_state>")
    xml_parts.append(f'    <current_time>{time_slot}</current_time>')
    xml_parts.append(f'    <game_phase>{game_phase}</game_phase>')
    xml_parts.append("  </session_state>")
    xml_parts.append("</physical_reality>")

    return "\n".join(xml_parts)


async def _llm_resolve_target(target: str, location_names: list[str], location_keys: list[str]) -> Optional[str]:
    """LLM 辅助解析 — 当 rule 匹配不到时，让 LLM 猜玩家想去哪

    返回匹配到的 location_key，或 None。
    """
    try:
        from src.tools.llm_client import call_llm

        names_enum = "\n".join(f"- {n} ({k})" for n, k in zip(location_names, location_keys))
        prompt = (
            f"玩家说「{target}」。已知模组中的场景列表如下：\n"
            f"{names_enum}\n\n"
            f"请判断玩家最可能想去哪一个场景。只输出场景 key，不要其他文字。"
            f"如果无法确定，输出 UNKNOWN。"
        )
        result = await call_llm("fast", [
            {"role": "user", "content": prompt},
        ], max_tokens=32, temperature=0.1)
        if result.is_ok and result.text:
            text = result.text.strip().strip('"').strip("'")
            if text in location_keys:
                logger.info(f"_llm_resolve_target: '{target}' → {text}")
                return text
            # 有时 LLM 会输出带描述的 key
            for key in location_keys:
                if key in text:
                    logger.info(f"_llm_resolve_target: '{target}' → {key}")
                    return key
        logger.debug(f"_llm_resolve_target: '{target}' 无法解析")
    except Exception as e:
        logger.debug(f"_llm_resolve_target: 调用失败: {e}")
    return None


async def navigation_node(state: GameState) -> dict:
    """导航节点 — 将玩家送往目标场景

    coc跑团哲学:
      - 方向性移动（"北边"/"north"）→ 检查出口，必须直连
      - 命名目的地（"金博尔宅的书房"/"公墓"）→ 只要模组中有这个场景，直接去
      - LLM 兜底 — 规则匹配不到时让 LLM 猜玩家想去哪
      - 「能否到达」是事件系统/守密人的责任，导航只负责「去哪」

    返回值中的字段:
        current_location: str    — 新位置 key（成功时）
        resolution: dict         — 执行结果，含 success/error/from_location/to_location
    """
    intent = state.get("intent") or {}
    intent_data = intent.get("data") or {}
    target = intent_data.get("target", "")
    current_loc = state.get("current_location", "")

    if not target:
        return {"resolution": {"success": False, "error": "你要去哪里？", "action": "move", "from_location": current_loc}}
    if not current_loc:
        return {"resolution": {"success": False, "error": "你还不确定自己在哪里。", "action": "move", "from_location": ""}}

    try:
        store = await _get_store()
        conn = await store._get_conn()

        # 读当前场景出口 + 所有场景的 key/name
        exits = await _load_location_exits(conn, current_loc)
        all_rows = await conn.fetch("SELECT key, name, exits_json, tags FROM locations")
        all_keys = [r["key"] for r in all_rows]
        all_names = [r["name"] for r in all_rows]

        resolved_key: Optional[str] = None
        bfs_path = None

        # 第一步: 方向性移动 → 只检查出口
        resolved_key = _match_exit_direction(target, exits or {})

        # 第二步: 命名目的地 → 匹配任意已知场景，直接去
        if resolved_key is None:
            resolved_key = _match_any_location(target, all_keys, all_names)

        # 第三步: 直连+BFS 寻路 → direction->key 的图路径搜索（非出口方向时不限制）
        if resolved_key is None:
            # 尝试从 target 解析出目标 key
            target_key = _match_any_location(target, all_keys, all_names)
            if target_key and target_key != current_loc:
                raw_locations = []
                for r in all_rows:
                    raw = r["exits_json"]
                    raw_locations.append({
                        "key": r["key"],
                        "name": r["name"],
                        "exits": raw if isinstance(raw, dict) else {},
                        "tags": r["tags"] or [],
                    })
                nav_graph = build_graph(raw_locations)
                bfs_path = find_path(nav_graph, current_loc, target_key)
                if bfs_path is not None:
                    resolved_key = bfs_path[-1]["key"]
                    path_desc = " → ".join(f"{s['direction']}({s['key']})" for s in bfs_path)
                    logger.info(f"navigation_node: BFS {current_loc} → {resolved_key} [{path_desc}]")

        # 第四步: LLM 兜底 — 规则匹配不到时让 LLM 猜
        if resolved_key is None:
            logger.debug(f"navigation_node: 规则匹配不到 '{target}'，尝试 LLM 解析")
            resolved_key = await _llm_resolve_target(target, all_names, all_keys)

        # 全失败 → 返回当前可用的出口方向
        if resolved_key is None:
            exit_dirs = list(exits.keys()) if exits else []
            exit_desc = "、".join(exit_dirs) if exit_dirs else "没有出口"
            logger.debug(f"navigation_node: '{target}' 不可达, 当前出口: {exit_desc}")
            return {
                "current_location": current_loc,
                "resolution": {
                    "success": False,
                    "error": f"从这里无法前往「{target}」。当前可走的方向: {exit_desc}",
                    "action": "move", "from_location": current_loc, "available_exits": exit_dirs,
                },
            }

        # 原地踏步检查
        if resolved_key == current_loc:
            return {
                "current_location": current_loc,
                "resolution": {"success": False, "error": "你已经在这里了。", "action": "move", "from_location": current_loc},
            }

        # 移动成功后，为目标地点构建物理现实 XML，供 narrate_node 直接使用
        physical_xml = await _build_location_physical_reality(conn, resolved_key, state)

        logger.info(f"navigation_node: {current_loc} → {resolved_key} (target='{target}')")
        result = {
            "current_location": resolved_key,
            "physical_reality": physical_xml,
            "world_context": physical_xml,
            "resolution": {
                "success": True, "error": "", "action": "move",
                "from_location": current_loc, "to_location": resolved_key, "target_label": target,
            },
        }
        if bfs_path is not None:
            result["resolution"]["path"] = [{"direction": s["direction"], "key": s["key"]} for s in bfs_path]
        return result

    except Exception as e:
        logger.error(f"navigation_node: 查询失败: {e}")
        return {
            "current_location": current_loc,
            "resolution": {"success": False, "error": f"导航查询异常: {e}", "action": "move", "from_location": current_loc},
        }
