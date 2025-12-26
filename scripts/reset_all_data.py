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
    args = parser.parse_args()

    print("=== GlyphKeeper 数据库清理工具 ===")
    print("警告: 此操作将清空所有项目数据！")
    
    if not args.force:
        confirm = input("请输入 'DELETE' 确认执行清理: ")
        if confirm != "DELETE":
            print("操作已取消。")
            return
    else:
        print("已启用强制模式，跳过确认。")

    print("\n[1] 正在连接数据库...")
    db_manager = DatabaseManager()
    
    try:
        async with db_manager.engine.begin() as conn:
            # 1. 清空所有业务表
            print("[2] 正在清空业务数据表...")
            
            # 方案 A: 删除并重建所有表 (最彻底)
            print("    -> 删除所有 SQLAlchemy 定义的表...")
            await conn.run_sync(Base.metadata.drop_all)
            
            # 方案 B: 显式删除 LightRAG 表
            # 注意：PostgreSQL 中未引用的标识符通常是小写的。
            # 我们使用 CASCADE 来处理依赖关系。
            lightrag_tables = [
                "lightrag_doc_chunks", 
                "lightrag_doc_status", 
                "lightrag_doc_full",
                "lightrag_entity_chunks",
                "lightrag_full_entities",
                "lightrag_full_relations",
                "lightrag_llm_cache",
                "lightrag_relation_chunks",
                "lightrag_vdb_chunks", 
                "lightrag_vdb_entity", 
                "lightrag_vdb_relation",
                "entities",
                "events",
                "game_session",
                "interactables",
                "knowledge_registry",
                "lightrag_doc_full",
                "locations"
            ]
            
            for table in lightrag_tables:
                try:
                    # 尝试删除 (不带引号，让 PG 处理大小写)
                    print(f"    -> 尝试删除 LightRAG 表: {table}")
                    await conn.execute(text(f"DROP TABLE IF EXISTS {table} CASCADE"))
                except Exception as e:
                    print(f"       (忽略) 删除 {table} 失败: {e}")

            print("    -> 重新创建业务表结构...")
            await conn.run_sync(Base.metadata.create_all)
            
        print("    数据库清理完成。")

        # 2. 清理本地文件存储 (LightRAG 的 GraphML 等)
        print("\n[3] 正在清理本地数据文件...")
        data_dir = PROJECT_ROOT / "data"
        modules_dir = data_dir / "modules"
        
        if modules_dir.exists():
            print(f"    -> 删除目录: {modules_dir}")
            try:
                shutil.rmtree(modules_dir)
                # 重新创建空目录
                modules_dir.mkdir(parents=True, exist_ok=True)
                print("    本地文件清理完成。")
            except Exception as e:
                print(f"    清理本地文件失败: {e}")
        else:
            print("    目录不存在，无需清理。")

    except Exception as e:
        print(f"\n[!] 清理过程中出错: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await db_manager.engine.dispose()
        print("\n=== 清理工作全部完成 ===")

if __name__ == "__main__":
    asyncio.run(cleanup_database())
