"""清理 LightRAG 的 PG 表"""
import asyncio, sys

async def main():
    from src.tools.pg_manager import PgManager
    mgr = await PgManager.get_instance()
    await mgr.start()
    import asyncpg
    conn = await asyncpg.connect(mgr.uri)
    rows = await conn.fetch(
        "SELECT tablename FROM pg_tables WHERE tablename LIKE 'lightrag_%'"
    )
    for r in rows:
        await conn.execute(f'DROP TABLE IF EXISTS "{r["tablename"]}" CASCADE')
    await conn.close()
    print(f"已删除 {len(rows)} 个 LightRAG 表")

asyncio.run(main())
