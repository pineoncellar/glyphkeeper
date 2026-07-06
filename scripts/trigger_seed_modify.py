# -*- coding: utf-8 -*-
"""
@File     :   trigger_seed_modify.py
@Desc     :   修改种子工作区中的模组触发器 — 影响后续所有 /start 新世界
@Note     :   写入世界 ID 为 __seed__{module_name}，不影响已在运行的世界。

使用方式:
    # 从 JSON 文件添加触发器到种子
    uv run python scripts/trigger_seed_modify.py add <module_name> <triggers.json>

    # 从种子删除指定触发器
    uv run python scripts/trigger_seed_modify.py remove <module_name> <trigger_id>

    # 列出种子中的所有触发器
    uv run python scripts/trigger_seed_modify.py list <module_name>
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
  add    <module_name> <json_file>   从 JSON 添加触发器到种子工作区
  remove <module_name> <trigger_id>  从种子删除指定触发器
  list   <module_name>               列出种子中的所有触发器
"""


def _seed_ws(module_name: str) -> str:
    return f"__seed__{module_name}"


async def cmd_add(module_name: str, json_path: str):
    from src.memory.vector_store import VectorStore

    seed_ws = VectorStore.seed_workspace_name(module_name)

    with open(json_path, encoding="utf-8") as f:
        raw = json.load(f)

    triggers = raw if isinstance(raw, list) else raw.get("static_triggers", [raw])
    if not triggers:
        print("[FAIL] JSON 中未找到触发器数据")
        return

    # 为每项补上 module_name（如果缺失）
    for t in triggers:
        t.setdefault("module_name", module_name)

    from src.state.read_models import StaticReadStore
    store = StaticReadStore()
    _ = await store.connect_script()

    count = await store.bulk_insert_triggers(triggers, world_id=seed_ws)
    print(f"[OK] 已添加 {count}/{len(triggers)} 条触发器到种子 '{seed_ws}'")
    print(f"下次 /start {module_name} 时，新世界将自动包含这些触发器。")
    await store.close()


async def cmd_remove(module_name: str, trigger_id: str):
    from src.memory.vector_store import VectorStore
    from src.state.read_models import StaticReadStore

    seed_ws = VectorStore.seed_workspace_name(module_name)
    store = StaticReadStore()
    _ = await store.connect_script()
    count = await store.delete_triggers_by_world(seed_ws, [trigger_id])
    if count:
        print(f"[OK] 已从种子 '{seed_ws}' 删除触发器 '{trigger_id}'")
    else:
        print(f"[WARN] 种子 '{seed_ws}' 中未找到触发器 '{trigger_id}'")
    await store.close()


async def cmd_list(module_name: str):
    from src.memory.vector_store import VectorStore
    from src.state.read_models import StaticReadStore

    seed_ws = VectorStore.seed_workspace_name(module_name)
    store = StaticReadStore()
    _ = await store.connect_script()
    conn = await store._get_conn()
    rows = await conn.fetch(
        "SELECT trigger_id, description, priority, is_one_off"
        " FROM static_triggers WHERE world_id=$1 ORDER BY priority DESC",
        seed_ws,
    )
    if not rows:
        print(f"种子 '{seed_ws}' 中无触发器")
    else:
        print(f"\n种子 '{seed_ws}' 的触发器列表 (影响后续 /start):")
        for r in rows:
            one_off = "一次性" if r["is_one_off"] else "可重复"
            print(f"  {r['trigger_id']:40s} | {one_off} | pri={r['priority']} | {r['description']}")
    await store.close()


async def main():
    if len(sys.argv) < 3:
        print(_USAGE)
        return

    action = sys.argv[1]
    module_name = sys.argv[2]

    if action == "add":
        if len(sys.argv) < 4:
            print("缺少 JSON 文件路径")
            return
        await cmd_add(module_name, sys.argv[3])
    elif action == "remove":
        if len(sys.argv) < 4:
            print("缺少 trigger_id")
            return
        await cmd_remove(module_name, sys.argv[3])
    elif action == "list":
        await cmd_list(module_name)
    else:
        print(f"未知操作: {action}")
        print(_USAGE)


if __name__ == "__main__":
    asyncio.run(main())
