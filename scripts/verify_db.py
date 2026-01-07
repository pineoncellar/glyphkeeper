import asyncio
import sys
import os
from sqlalchemy import text

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.memory.database import DatabaseManager
from src.core.config import get_settings

async def verify():
    settings = get_settings()
    active_world = settings.project.active_world
    schema = f"world_{active_world}"
    
    db = DatabaseManager()
    print(f"Connecting to DB: {db.engine.url}")
    print(f"Target Schema: {schema}")
    
    async with db.engine.connect() as conn:
        # 1. 检查 Schema 表
        print(f"\n--- Tables in {schema} ---")
        try:
            # 获取 schema 下的所有表名
            tables_query = text(f"SELECT table_name FROM information_schema.tables WHERE table_schema = '{schema}'")
            result = await conn.execute(tables_query)
            tables = [r[0] for r in result.fetchall()]
            
            if not tables:
                print(f"No tables found in schema {schema}")
            else:
                for table in tables:
                    try:
                        count_query = text(f'SELECT count(*) FROM "{schema}"."{table}"')
                        result = await conn.execute(count_query)
                        count = result.scalar()
                        print(f"{table}: {count} rows")
                    except Exception as e:
                        print(f"{table}: Error counting ({e})")
                
        except Exception as e:
            print(f"Error checking schema {schema}: {e}")

        # 2. 检查 Public 表 (LightRAG)
        print("\n--- Tables in public ---")
        try:
            tables_query = text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
            result = await conn.execute(tables_query)
            tables = [r[0] for r in result.fetchall()]
            
            for table in tables:
                try:
                    # 检查是否存在 workspace 列
                    col_check = await conn.execute(text(f"SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='{table}' AND column_name='workspace'"))
                    has_workspace = col_check.scalar() is not None
                    
                    count_query = text(f'SELECT count(*) FROM "public"."{table}"')
                    total = (await conn.execute(count_query)).scalar()
                    
                    msg = f"{table}: {total} rows"
                    
                    if has_workspace:
                        ws_query = text(f'SELECT count(*) FROM "public"."{table}" WHERE workspace = :w')
                        ws_count = (await conn.execute(ws_query, {"w": active_world})).scalar()
                        msg += f" (workspace='{active_world}': {ws_count})"
                    
                    print(msg)
                except Exception as e:
                    print(f"{table}: Error checking ({e})")
                    
        except Exception as e:
            print(f"Error checking public: {e}")

if __name__ == "__main__":
    asyncio.run(verify())