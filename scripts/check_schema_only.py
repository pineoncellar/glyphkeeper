#!/usr/bin/env python3
"""检查数据库表结构"""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

async def main():
    from src.memory.event_store import EventStore
    es = EventStore()
    conn = await es._get_conn()

    for tbl in ['locations', 'interactables', 'clue_discoveries', 'knowledge_registry', 'session_knowledge_state']:
        rows = await conn.fetch(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name=$1 ORDER BY ordinal_position", tbl
        )
        print(f"{tbl}:")
        for r in rows:
            print(f"  {r['column_name']} ({r['data_type']})")
        print()

    # Now query actual data
    print("\n=== locations ===")
    rows = await conn.fetch("SELECT * FROM locations ORDER BY key")
    print(f"Count: {len(rows)}")
    for r in rows:
        print(f"  {dict(r)}")

    print("\n=== interactables ===")
    rows = await conn.fetch("SELECT * FROM interactables ORDER BY key")
    print(f"Count: {len(rows)}")
    for r in rows:
        print(f"  {dict(r)}")

    print("\n=== clue_discoveries ===")
    rows = await conn.fetch("SELECT * FROM clue_discoveries ORDER BY id")
    print(f"Count: {len(rows)}")
    for r in rows:
        print(f"  {dict(r)}")

    print("\n=== knowledge_registry ===")
    rows = await conn.fetch("SELECT * FROM knowledge_registry ORDER BY knowledge_id")
    print(f"Count: {len(rows)}")
    for r in rows:
        print(f"  {dict(r)}")

    await conn.close()

asyncio.run(main())
