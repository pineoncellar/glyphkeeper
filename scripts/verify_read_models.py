#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验证 CQRS 读模型表（1.4 测试）
"""
import asyncio, sys, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def main():
    from src.tools.pg_manager import PgManager
    mgr = await PgManager.get_instance()
    if not mgr.available:
        await mgr.start()
    import asyncpg
    conn = await asyncpg.connect(mgr.uri)

    # 获取所有表
    tables = await conn.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_type='BASE TABLE'"
    )
    print("=== 数据库表 ===")
    for r in tables:
        print(f"  {r['table_name']}")

    # knowledge_registry
    print("\n=== knowledge_registry ===")
    rows = await conn.fetch("SELECT * FROM knowledge_registry ORDER BY knowledge_id")
    print(f"记录数: {len(rows)}")
    for r in rows:
        print(f"  id={r['id']}, knowledge_id={r['knowledge_id']}, tags_granted={r['tags_granted']}, description={str(r.get('description',''))[:60]}")

    # locations
    print("\n=== locations ===")
    rows = await conn.fetch("SELECT * FROM locations ORDER BY key")
    print(f"记录数: {len(rows)}")
    for r in rows:
        exits = json.dumps(r.get('exits', {}), ensure_ascii=False)
        print(f"  key={r['key']}, name={r['name']}, exits={exits}")

    # interactables
    print("\n=== interactables ===")
    rows = await conn.fetch("SELECT * FROM interactables ORDER BY key")
    print(f"记录数: {len(rows)}")
    for r in rows:
        print(f"  key={r['key']}, name={r['name']}, location_id={r.get('location_id','')}")

    # clue_discoveries
    print("\n=== clue_discoveries ===")
    rows = await conn.fetch("SELECT * FROM clue_discoveries ORDER BY id")
    print(f"记录数: {len(rows)}")
    for r in rows:
        req = r.get('required_check')
        req_str = json.dumps(req, ensure_ascii=False) if req else 'null'
        print(f"  id={r['id']}, interactable_id={r['interactable_id']}, trigger={r['trigger']}, "
              f"knowledge_id={r.get('knowledge_id','null')}, required_check={req_str}")

    # session_knowledge_state
    print("\n=== session_knowledge_state ===")
    try:
        rows = await conn.fetch("SELECT * FROM session_knowledge_state")
        print(f"记录数: {len(rows)}")
        for r in rows:
            print(f"  session={r['session_id']}, knowledge_ids={r['knowledge_ids']}")
    except Exception as e:
        print(f"  (表不存在或查询失败: {e})")

    await conn.close()

asyncio.run(main())
