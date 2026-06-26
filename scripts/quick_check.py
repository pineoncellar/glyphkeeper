#!/usr/bin/env python3
"""快速检查各表记录数"""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

async def main():
    from src.memory.event_store import EventStore
    es = EventStore()
    conn = await es._get_conn()

    for tbl in ['knowledge_registry', 'locations', 'interactables', 'clue_discoveries']:
        cnt = await conn.fetchval(f"SELECT count(*) FROM {tbl}")
        print(f"{tbl}: {cnt} 条记录")

    ecnt = await conn.fetchval(
        "SELECT count(*) FROM events WHERE session_id='00000000-0000-0000-0000-000000000000'"
    )
    print(f"events (template): {ecnt} 条")

    await conn.close()

asyncio.run(main())
