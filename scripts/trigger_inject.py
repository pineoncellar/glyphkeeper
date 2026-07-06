# -*- coding: utf-8 -*-
"""
@File     :   trigger_inject.py
@Desc     :   运行时注入触发器到指定世界 — 游戏进行中动态添加/删除
@Note     :   写入 static_triggers 表时使用目标 world_id 做世界隔离。
              读档到注入前的时间点不会撤销此操作（PG 读模型不在快照范围内），
              但触发器状态（是否已触发）由 session_trigger_state 表管理，
              快照恢复时会重置之。

使用方式:
    # 从 JSON 文件注入触发器到指定世界
    uv run python scripts/trigger_inject.py inject <world_id> <triggers.json>

    # 删除指定世界中的某个触发器
    uv run python scripts/trigger_inject.py remove <world_id> <trigger_id>

    # 列出指定世界的所有触发器
    uv run python scripts/trigger_inject.py list <world_id>
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# 将项目根目录加入 Python 路径，确保 from src 导入可用
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


_USAGE = """\
用法:
  inject   <world_id> <json_file>   从 JSON 文件注入触发器到指定世界
  remove   <world_id> <trigger_id>  删除指定世界中的触发器
  list     <world_id>               列出指定世界的所有触发器
"""


async def cmd_inject(world_id: str, json_path: str):
    with open(json_path, encoding="utf-8") as f:
        raw = json.load(f)

    triggers = raw if isinstance(raw, list) else raw.get("static_triggers", [raw])
    if not triggers:
        print("[FAIL] JSON 中未找到触发器数据")
        return

    from src.state.read_models import StaticReadStore
    store = StaticReadStore()
    _ = await store.connect_script()

    count = await store.bulk_insert_triggers(triggers, world_id=world_id)
    print(f"[OK] 注入 {count}/{len(triggers)} 条触发器到世界 '{world_id}'")
    await store.close()


async def cmd_remove(world_id: str, trigger_id: str):
    from src.state.read_models import StaticReadStore
    store = StaticReadStore()
    _ = await store.connect_script()
    count = await store.delete_triggers_by_world(world_id, [trigger_id])
    if count:
        print(f"[OK] 已从世界 '{world_id}' 删除触发器 '{trigger_id}'")
    else:
        print(f"[WARN] 世界 '{world_id}' 中未找到触发器 '{trigger_id}'")
    await store.close()


async def cmd_list(world_id: str):
    from src.state.read_models import StaticReadStore
    store = StaticReadStore()
    _ = await store.connect_script()
    conn = await store._get_conn()
    rows = await conn.fetch(
        "SELECT trigger_id, module_name, description, priority, is_one_off, world_id"
        " FROM static_triggers WHERE world_id=$1 ORDER BY priority DESC",
        world_id,
    )
    if not rows:
        print(f"世界 '{world_id}' 中无触发器")
    else:
        print(f"\n世界 '{world_id}' 的触发器列表:")
        for r in rows:
            one_off = "一次性" if r["is_one_off"] else "可重复"
            print(f"  {r['trigger_id']:40s} | {one_off} | pri={r['priority']} | {r['description']}")
    await store.close()


async def main():
    if len(sys.argv) < 3:
        print(_USAGE)
        return

    action = sys.argv[1]
    world_id = sys.argv[2]

    if action == "inject":
        if len(sys.argv) < 4:
            print("缺少 JSON 文件路径")
            return
        await cmd_inject(world_id, sys.argv[3])
    elif action == "remove":
        if len(sys.argv) < 4:
            print("缺少 trigger_id")
            return
        await cmd_remove(world_id, sys.argv[3])
    elif action == "list":
        await cmd_list(world_id)
    else:
        print(f"未知操作: {action}")
        print(_USAGE)


if __name__ == "__main__":
    asyncio.run(main())
