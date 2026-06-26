"""
@File     :   supplement_module.py
@Desc     :   模组增补工具 — 无需重摄入即可向已摄入模组追加数据
@Note     :   通过向模板会话 (TEMPLATE_SESSION_ID) 追加事件实现
              所有增补对后续新建的游戏会话生效。

使用方式:
    # 查看已摄入模组
    uv run python scripts/supplement_module.py list

    # 查看模组现有数据
    uv run python scripts/supplement_module.py show book

    # 新增场景
    uv run python scripts/supplement_module.py add-location book loc_basement ^
        --name "地下室" --desc "阴暗潮湿的地下室，墙角堆着旧木箱..." ^
        --exits "up:loc_kimball_house_study" --tags "indoor,dark"

    # 新增 NPC 到指定场景
    uv run python scripts/supplement_module.py add-npc book loc_neighborhood ^
        --key "npc_mysterious_stranger" --name "神秘陌生人" ^
        --tags "mysterious" --stats "APP:55"

    # 新增物品到指定场景
    uv run python scripts/supplement_module.py add-item book loc_neighborhood ^
        --key "item_old_letter" --name "旧信" --state "intact" ^
        --tags "paper" --clue-text "信封上写着: '致道格拉斯, 关于地下的秘密...'"

    # 新增全局知识
    uv run python scripts/supplement_module.py add-knowledge book ^
        --key "fact_cemetery_tunnel" ^
        --content "公墓下方有一条废弃的隧道网络，连接着几个秘密藏身处。" ^
        --tags "unlock_tunnel_entry,lore_underground"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ── 路径引导 ──
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TEMPLATE_SESSION_ID = "00000000-0000-0000-0000-000000000000"


# ====================================================================
# 辅助：获取 EventStore
# ====================================================================

async def _get_store():
    from src.memory.event_store import EventStore
    return EventStore()


async def _get_events(module_name: str) -> list[dict]:
    """获取模板会话的所有事件"""
    store = await _get_store()
    events = await store.get_events(TEMPLATE_SESSION_ID, since_version=0)
    return [
        e for e in events
        if e.get("data", {}).get("module_name") == module_name
    ]


def _find_data(events: list[dict], event_type: str) -> Optional[dict]:
    """从事件列表中查找指定类型的事件数据"""
    for e in events:
        if e.get("type") == event_type:
            return e.get("data", {})
    return None


# ====================================================================
# 命令实现
# ====================================================================

async def cmd_list(args):
    """列出已摄入的模组"""
    store = await _get_store()
    events = await store.get_events(TEMPLATE_SESSION_ID, since_version=0)

    modules: dict[str, dict] = {}
    for e in events:
        data = e.get("data", {})
        etype = e.get("type", "")
        name = data.get("module_name", "")
        if not name:
            continue

        if name not in modules:
            modules[name] = {"name": name, "locations": 0, "knowledge": 0}

        if etype == "WorldInitialized":
            modules[name]["locations"] = len(data.get("locations", {}))
        elif etype == "KnowledgeAdded":
            modules[name]["knowledge"] = modules[name].get("knowledge", 0) + 1

    if not modules:
        print("[INFO] 尚未摄入任何模组")
        return

    for m in modules.values():
        print(f"  {m['name']}  ({m['locations']} 场景, {m['knowledge']} 条知识)")


async def cmd_show(args):
    """查看模组的详细数据"""
    events = await _get_events(args.module)
    if not events:
        print(f"[WARN] 模组 '{args.module}' 未找到")
        return

    world = _find_data(events, "WorldInitialized")
    if not world:
        print("[WARN] 无 WorldInitialized 数据")
        return

    locations = world.get("locations", {})
    raw_locations = world.get("raw_locations", [])

    print(f"模组: {args.module}")
    print(f"场景数: {len(locations)}")

    for loc_key, loc_data in locations.items():
        name = loc_data.get("name", loc_key)
        exits = loc_data.get("exits", {})
        entities = loc_data.get("entities", [])
        interactables = loc_data.get("interactables", [])
        print(f"\n  [场景] {name} ({loc_key})")
        print(f"    出口: {', '.join(f'{k}→{v}' for k, v in exits.items()) or '无'}")
        print(f"    NPC: {', '.join(entities) or '无'}")
        print(f"    物品: {', '.join(interactables) or '无'}")

    if raw_locations:
        print(f"\n  --- 原始数据 ---")
        for loc in raw_locations:
            print(f"  {loc.get('name')} ({loc.get('key')}): "
                  f"{len(loc.get('interactables', []))} 物品 (含线索)")


async def cmd_add_location(args):
    """新增场景到模组"""
    store = await _get_store()
    events = await _get_events(args.module)
    world = _find_data(events, "WorldInitialized")

    if not world:
        print(f"[FAIL] 模组 '{args.module}' 未找到，请先摄入")
        return

    locations = world.get("locations", {})

    if args.key in locations:
        print(f"[WARN] 场景 '{args.key}' 已存在，将追加/覆盖")

    # 解析 exits: "方向1:目标key,方向2:目标key"
    exits = {}
    if args.exits:
        for part in args.exits.split(","):
            part = part.strip()
            if ":" in part:
                k, v = part.split(":", 1)
                exits[k.strip()] = v.strip()

    tags = [t.strip() for t in args.tags.split(",")] if args.tags else []
    new_location = {
        "key": args.key,
        "name": args.name,
        "base_desc": args.desc,
        "exits": exits,
        "tags": tags,
        "entities": [],
        "interactables": [],
    }

    # 追加 LocationAdded 事件
    await store.append(
        session_id=TEMPLATE_SESSION_ID,
        event_type="LocationAdded",
        data={
            "module_name": args.module,
            "location_key": args.key,
            "location": new_location,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        source_node="supplement_module",
    )

    print(f"[OK] 场景 '{args.name}' ({args.key}) 已追加")
    print(f"     出口: {exits}")
    print(f"     标签: {tags}")
    print(f"     注意: 需更新 ModuleLoader 或 WorldManager 以读取 LocationAdded 事件")


async def cmd_add_npc(args):
    """新增 NPC 到指定场景"""
    store = await _get_store()
    events = await _get_events(args.module)
    world = _find_data(events, "WorldInitialized")

    if not world:
        print(f"[FAIL] 模组 '{args.module}' 未找到")
        return

    locations = world.get("locations", {})
    if args.location not in locations:
        print(f"[WARN] 场景 '{args.location}' 不在模组已有场景中")
        print(f"      可用场景: {', '.join(locations.keys())}")

    tags = [t.strip() for t in args.tags.split(",")] if args.tags else []
    stats = {}
    if args.stats:
        for part in args.stats.split(","):
            part = part.strip()
            if ":" in part:
                k, v = part.split(":", 1)
                stats[k.strip()] = int(v.strip()) if v.strip().isdigit() else v.strip()

    npc = {
        "key": args.key,
        "name": args.name,
        "tags": tags,
        "stats": stats,
        "dialogue_clues": [],
    }

    await store.append(
        session_id=TEMPLATE_SESSION_ID,
        event_type="EntityAdded",
        data={
            "module_name": args.module,
            "location_key": args.location,
            "entity": npc,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        source_node="supplement_module",
    )

    print(f"[OK] NPC '{args.name}' ({args.key}) 已追加到场景 '{args.location}'")
    print(f"     标签: {tags}")
    print(f"     注意: 需更新模块以处理 EntityAdded 事件")


async def cmd_add_item(args):
    """新增物品到指定场景（可附带线索）"""
    store = await _get_store()
    events = await _get_events(args.module)
    world = _find_data(events, "WorldInitialized")

    if not world:
        print(f"[FAIL] 模组 '{args.module}' 未找到")
        return

    tags = [t.strip() for t in args.tags.split(",")] if args.tags else []
    clues = []

    if args.clue_text:
        clue = {
            "trigger": args.clue_trigger or "search",
            "flavor_text": args.clue_text,
            "target_knowledge": args.clue_knowledge or None,
            "required_check": None,
        }
        if args.clue_skill:
            clue["required_check"] = {
                "skill": args.clue_skill,
                "difficulty": args.clue_difficulty or "Regular",
            }
        clues.append(clue)

    item = {
        "key": args.key,
        "name": args.name,
        "state": args.state or "pristine",
        "tags": tags,
        "clues": clues,
    }

    await store.append(
        session_id=TEMPLATE_SESSION_ID,
        event_type="ItemAdded",
        data={
            "module_name": args.module,
            "location_key": args.location,
            "interactable": item,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        source_node="supplement_module",
    )

    print(f"[OK] 物品 '{args.name}' ({args.key}) 已追加到场景 '{args.location}'")
    if clues:
        print(f"     线索: {clues[0]['flavor_text'][:60]}...")
    if args.clue_skill:
        print(f"     检定: {args.clue_skill}({args.clue_difficulty})")


async def cmd_add_knowledge(args):
    """新增全局知识"""
    store = await _get_store()
    events = await _get_events(args.module)
    world = _find_data(events, "WorldInitialized")

    if not world:
        print(f"[FAIL] 模组 '{args.module}' 未找到")
        return

    tags = [t.strip() for t in args.tags.split(",")] if args.tags else []
    knowledge = {
        "key": args.key,
        "rag_content": args.content,
        "tags_granted": tags,
    }

    await store.append(
        session_id=TEMPLATE_SESSION_ID,
        event_type="KnowledgeAdded",
        data={
            "module_name": args.module,
            "knowledge": knowledge,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        source_node="supplement_module",
    )

    print(f"[OK] 知识 '{args.key}' 已追加")
    print(f"     内容: {args.content[:80]}...")
    print(f"     解锁标签: {tags}")
    print(f"     注意: 需更新 ModuleLoader 以读取 KnowledgeAdded 事件")


# ====================================================================
# CLI
# ====================================================================

def main():
    parser = argparse.ArgumentParser(
        description="GlyphKeeper 模组增补工具 — 无需重摄入即可追加数据",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── list ──
    sub.add_parser("list", help="列出已摄入模组")

    # ── show ──
    p_show = sub.add_parser("show", help="查看模组详情")
    p_show.add_argument("module", help="模组名")

    # ── add-location ──
    p_loc = sub.add_parser("add-location", help="新增场景")
    p_loc.add_argument("module", help="模组名")
    p_loc.add_argument("key", help="场景 key (如 loc_basement)")
    p_loc.add_argument("--name", required=True, help="场景名")
    p_loc.add_argument("--desc", required=True, help="场景描述")
    p_loc.add_argument("--exits", default="", help="出口, 格式: 方向1:目标key,方向2:目标key")
    p_loc.add_argument("--tags", default="", help="标签, 逗号分隔")

    # ── add-npc ──
    p_npc = sub.add_parser("add-npc", help="新增 NPC")
    p_npc.add_argument("module", help="模组名")
    p_npc.add_argument("location", help="所在场景 key")
    p_npc.add_argument("--key", required=True, help="NPC key (如 npc_stranger)")
    p_npc.add_argument("--name", required=True, help="NPC 名")
    p_npc.add_argument("--tags", default="", help="标签, 逗号分隔")
    p_npc.add_argument("--stats", default="", help="属性, 格式: APP:50,POW:40")

    # ── add-item ──
    p_item = sub.add_parser("add-item", help="新增物品")
    p_item.add_argument("module", help="模组名")
    p_item.add_argument("location", help="所在场景 key")
    p_item.add_argument("--key", required=True, help="物品 key")
    p_item.add_argument("--name", required=True, help="物品名")
    p_item.add_argument("--state", default="pristine", help="物品状态")
    p_item.add_argument("--tags", default="", help="标签, 逗号分隔")
    p_item.add_argument("--clue-text", default="", help="线索文本")
    p_item.add_argument("--clue-trigger", default="search", help="线索触发方式")
    p_item.add_argument("--clue-skill", default="", help="线索检定技能名")
    p_item.add_argument("--clue-difficulty", default="Regular",
                        choices=["Regular", "Hard", "Extreme"], help="检定难度")
    p_item.add_argument("--clue-knowledge", default="", help="关联知识 key")

    # ── add-knowledge ──
    p_know = sub.add_parser("add-knowledge", help="新增全局知识")
    p_know.add_argument("module", help="模组名")
    p_know.add_argument("--key", required=True, help="知识 key")
    p_know.add_argument("--content", required=True, help="知识内容")
    p_know.add_argument("--tags", default="", help="解锁标签, 逗号分隔")

    args = parser.parse_args()
    asyncio.run(_dispatch(args))


async def _dispatch(args):
    cmd_map = {
        "list": cmd_list,
        "show": cmd_show,
        "add-location": cmd_add_location,
        "add-npc": cmd_add_npc,
        "add-item": cmd_add_item,
        "add-knowledge": cmd_add_knowledge,
    }
    fn = cmd_map.get(args.command)
    if fn:
        await fn(args)
    else:
        print(f"[FAIL] 未知命令: {args.command}")


if __name__ == "__main__":
    main()
