import asyncio
import sys
import os
import shutil
import argparse
from sqlalchemy import text

# 将项目根目录添加到 python 路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.memory.database import DatabaseManager, Base
from src.core.config import get_settings, PROJECT_ROOT
from src.core.logger import get_logger

logger = get_logger("db_cleanup")

async def cleanup_database():
    parser = argparse.ArgumentParser(description="GlyphKeeper 数据库清理工具")
    parser.add_argument("--force", action="store_true", help="跳过确认提示直接执行")
    parser.add_argument("--target", choices=["all", "world", "rules"], default="all", help="清理目标: all(全部), world(当前世界), rules(规则库)")
    args = parser.parse_args()

    print("=== GlyphKeeper 数据库清理工具 ===")
    print(f"目标: {args.target}")
    print("警告: 此操作将不可逆地删除数据！")
    
    if not args.force:
        confirm = input("请输入 'DELETE' 确认执行清理: ")
        if confirm != "DELETE":
            print("操作已取消。")
            return
    else:
        print("已启用强制模式，跳过确认。")

    print("\n[1] 正在连接数据库...")
    db_manager = DatabaseManager()
    settings = get_settings()
    from src.tools.world_manager import list_worlds
    all_worlds = list_worlds()
    print(f"    发现 {len(all_worlds)} 个世界目录: {', '.join(all_worlds) if all_worlds else '(无)'}")

    try:
        async with db_manager.engine.begin() as conn:

            # === 清理世界数据 ===
            if args.target in ["all", "world"]:
                print(f"\n[2] 正在清理所有世界数据和目录...")
                for wid in all_worlds:
                    schema_name = f"world_{wid}"
                    try:
                        await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"))
                        print(f"    -> Schema {schema_name} 已删除")
                        await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}"))
                        await conn.execute(text(f"SET search_path TO {schema_name}, public"))
                        await conn.run_sync(Base.metadata.create_all)
                    except Exception as e:
                        print(f"    -> Schema {schema_name} 处理失败: {e}")

                    world_dir = PROJECT_ROOT / "data" / "worlds" / wid
                    if world_dir.exists():
                        shutil.rmtree(world_dir)
                    world_dir.mkdir(parents=True, exist_ok=True)
                    print(f"    -> 世界 '{wid}' 目录已重置")
                print("    世界数据清理完成。")

            # === 清理规则库数据 ===
            if args.target in ["all", "rules"]:
                print(f"\n[3] 正在清理规则库数据 (Schema: rag_rules)...")
                
                # 1. 删除数据库 Schema
                print(f"    -> 删除 Schema rag_rules...")
                await conn.execute(text("DROP SCHEMA IF EXISTS rag_rules CASCADE"))
                # 重建空 Schema，等待 LightRAG 初始化时自动填充
                await conn.execute(text("CREATE SCHEMA IF NOT EXISTS rag_rules"))
                
                # 2. 清理本地文件
                print(f"    -> 清理本地文件...")
                rules_dir = PROJECT_ROOT / "data" / "rules"
                if rules_dir.exists():
                    shutil.rmtree(rules_dir)
                rules_dir.mkdir(parents=True, exist_ok=True)
                print("    规则库清理完成。")

    except Exception as e:
        print(f"\n[!] 清理过程中出错: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await db_manager.engine.dispose()
        print("\n=== 清理工作全部完成 ===")

if __name__ == "__main__":
    asyncio.run(cleanup_database())
