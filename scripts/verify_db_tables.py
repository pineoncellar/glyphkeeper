#!/usr/bin/env python3
"""验证 CQRS 读模型表"""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main():
    from src.memory.event_store import EventStore
    es = EventStore()
    conn = await es._get_conn()

    tables = await conn.fetch(
        """SELECT table_name FROM information_schema.tables 
           WHERE table_schema='public' AND table_type='BASE TABLE'"""
    )
    print("=== 数据库表 ===")
    tnames = [r["table_name"] for r in tables]
    for t in tnames:
        print(f"  {t}")

    # knowledge_registry
    if "knowledge_registry" in tnames:
        rows = await conn.fetch("SELECT * FROM knowledge_registry ORDER BY knowledge_id")
        print(f"\n=== knowledge_registry ({len(rows)} 条) ===")
        for r in rows:
            print(f"  {r['knowledge_id']}: tags={r['tags_granted']}")

    # locations
    if "locations" in tnames:
        rows = await conn.fetch("SELECT * FROM locations ORDER BY key")
        print(f"\n=== locations ({len(rows)} 条) ===")
        for r in rows:
            print(f"  {r['key']}: {r['name']}, exits_json={r.get('exits_json', 'N/A')}")

    # interactables
    if "interactables" in tnames:
        rows = await conn.fetch("SELECT * FROM interactables ORDER BY key")
        print(f"\n=== interactables ({len(rows)} 条) ===")
        for r in rows:
            print(f"  {r['key']}: {r['name']} @ {r['location_id']}")

    # clue_discoveries
    if "clue_discoveries" in tnames:
        rows = await conn.fetch("SELECT * FROM clue_discoveries ORDER BY id")
        print(f"\n=== clue_discoveries ({len(rows)} 条) ===")
        for r in rows:
            kid = r.get("knowledge_id") or "null"
            print(f"  id={r['id']}, interactable={r['interactable_id']}, trigger={r['trigger']}, knowledge={kid}")

    # session_knowledge_state
    if "session_knowledge_state" in tnames:
        rows = await conn.fetch("SELECT * FROM session_knowledge_state")
        print(f"\n=== session_knowledge_state ({len(rows)} 条) ===")
        for r in rows:
            print(f"  session={r['session_id']}, knowledge_ids={r['knowledge_ids']}")
    else:
        print("\n=== session_knowledge_state: 表不存在 ===")

    await conn.close()


asyncio.run(main())
