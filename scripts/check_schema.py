import asyncio
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from src.memory.database import db_manager
from sqlalchemy import text

async def check_schemas():
    async with db_manager.engine.connect() as conn:
        # 检查所有 world schemas
        result = await conn.execute(text(
            "SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE 'world_%'"
        ))
        schemas = [row[0] for row in result]
        
        print("现有的 world schemas:")
        if schemas:
            for schema in schemas:
                print(f"  - {schema}")
        else:
            print("  (无)")
        
        # 检查 world_book schema 中的表
        if "world_book" in schemas:
            result = await conn.execute(text(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'world_book'"
            ))
            tables = [row[0] for row in result]
            print("\nworld_book schema 中的表:")
            if tables:
                for table in tables:
                    print(f"  - {table}")
            else:
                print("  (无)")

if __name__ == "__main__":
    asyncio.run(check_schemas())
