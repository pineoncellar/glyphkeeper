#!/usr/bin/env python3
"""
验证模组摄入结果 — 检查 EventStore 和 LightRAG 中的记录
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.memory.event_store import EventStore

TEMPLATE_SESSION = "00000000-0000-0000-0000-000000000000"


async def main():
    es = EventStore()
    events = await es.get_events(TEMPLATE_SESSION, since_version=0)
    print(f"[CHECK] EventStore 事件总数: {len(events)}")

    if not events:
        print("[FAIL] 未找到任何事件，摄入可能未成功")
        await es.close()
        return

    world_init_count = 0
    opening_count = 0

    for ev in events:
        evt_type = ev.get("type", "?")
        data = ev.get("data", {})
        if evt_type == "WorldInitialized":
            world_init_count += 1
            locs = data.get("locations", {})
            module = data.get("module_name", "?")
            print(f"  [OK] WorldInitialized: 模组={module}, 场景数={len(locs)}")
            for lk, lv in locs.items():
                print(f"       - {lk}: {lv.get('name', '?')}")
        elif evt_type == "OpeningTemplateSet":
            opening_count += 1
            module = data.get("module_name", "?")
            start_loc = data.get("opening", {}).get("start_location_key", "?")
            print(f"  [OK] OpeningTemplateSet: 模组={module}, 起点={start_loc}")

    print()
    if world_init_count > 0 and opening_count > 0:
        print(f"[OK] 摄入验证通过: {world_init_count} 个 WorldInitialized, "
              f"{opening_count} 个 OpeningTemplateSet")
    else:
        print(f"[FAIL] 缺少关键事件: WorldInitialized={world_init_count}, "
              f"OpeningTemplateSet={opening_count}")

    await es.close()


if __name__ == "__main__":
    asyncio.run(main())
