"""
世界数据备份与恢复工具
支持备份/恢复：
1. PostgreSQL 数据库 Schema（世界数据）
2. LightRAG 图谱文件（NetworkX .graphml 文件）
"""

import asyncio
import sys
import os
import shutil
import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

# 将项目根目录添加到 python 路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text, inspect
from sqlalchemy.dialects.postgresql import UUID, ARRAY, JSONB
from src.memory.database import DatabaseManager
from src.core.config import get_settings, PROJECT_ROOT
from src.core.logger import get_logger

logger = get_logger("backup_restore")

# 备份根目录
BACKUP_ROOT = PROJECT_ROOT / "data" / "backups"


def get_active_world() -> str:
    """获取当前激活的世界名称"""
    settings = get_settings()
    return settings.project.active_world


def get_backup_path(world_name: str, backup_name: Optional[str] = None) -> Path:
    """获取备份路径"""
    if backup_name is None:
        # 使用时间戳作为备份名
        backup_name = datetime.now().strftime("%Y%m%d_%H%M%S")
    return BACKUP_ROOT / world_name / backup_name


def list_backups(world_name: Optional[str] = None):
    """列出所有备份"""
    if world_name:
        world_dirs = [BACKUP_ROOT / world_name] if (BACKUP_ROOT / world_name).exists() else []
    else:
        world_dirs = [d for d in BACKUP_ROOT.iterdir() if d.is_dir()] if BACKUP_ROOT.exists() else []
    
    if not world_dirs:
        print("没有找到任何备份。")
        return
    
    print("\n=== 可用备份 ===")
    for world_dir in sorted(world_dirs):
        world = world_dir.name
        backups = sorted([b.name for b in world_dir.iterdir() if b.is_dir()], reverse=True)
        if backups:
            print(f"\n世界 [{world}]:")
            for backup in backups:
                backup_path = world_dir / backup
                # 检查备份是否完整
                has_db = (backup_path / "database").exists()
                has_graph = (backup_path / "lightrag").exists()
                status = "✓" if (has_db and has_graph) else "⚠"
                
                # 读取备份元信息
                meta_file = backup_path / "meta.json"
                meta_info = ""
                if meta_file.exists():
                    try:
                        with open(meta_file, "r", encoding="utf-8") as f:
                            meta = json.load(f)
                            meta_info = f" - {meta.get('description', '')}"
                    except:
                        pass
                
                print(f"  [{status}] {backup}{meta_info}")
    print("\n================")


async def backup_database(world_name: str, backup_path: Path) -> bool:
    """备份数据库 Schema 数据"""
    schema_name = f"world_{world_name}"
    db_backup_path = backup_path / "database"
    db_backup_path.mkdir(parents=True, exist_ok=True)
    
    print(f"  -> 备份数据库 Schema: {schema_name}")
    
    db_manager = DatabaseManager()
    
    try:
        async with db_manager.engine.connect() as conn:
            # 获取 Schema 中的所有表
            result = await conn.execute(text(f"""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = :schema
            """), {"schema": schema_name})
            tables = [row[0] for row in result.fetchall()]
            
            if not tables:
                print(f"  -> 警告: Schema {schema_name} 中没有表")
                return True
            
            print(f"  -> 发现 {len(tables)} 个表: {', '.join(tables)}")
            
            # 备份每个表的数据
            backup_data = {}
            for table in tables:
                result = await conn.execute(text(f'SELECT * FROM {schema_name}."{table}"'))
                
                # 获取列信息
                columns = result.keys()
                rows = result.fetchall()
                
                # 将数据转换为可序列化格式
                table_data = []
                for row in rows:
                    row_dict = {}
                    for col, val in zip(columns, row):
                        # 处理特殊类型
                        if isinstance(val, (bytes, memoryview)):
                            row_dict[col] = val.hex() if val else None
                        elif hasattr(val, 'isoformat'):  # datetime
                            row_dict[col] = val.isoformat()
                        elif hasattr(val, '__str__') and not isinstance(val, (str, int, float, bool, list, dict, type(None))):
                            row_dict[col] = str(val)
                        else:
                            row_dict[col] = val
                    table_data.append(row_dict)
                
                backup_data[table] = {
                    "columns": list(columns),
                    "rows": table_data
                }
                print(f"     - {table}: {len(rows)} 行")
            
            # 保存到 JSON 文件
            with open(db_backup_path / "tables.json", "w", encoding="utf-8") as f:
                json.dump(backup_data, f, ensure_ascii=False, indent=2, default=str)
            
            print(f"  -> 数据库备份完成")
            return True
            
    except Exception as e:
        logger.error(f"备份数据库失败: {e}")
        print(f"  -> 错误: {e}")
        return False


async def backup_lightrag(world_name: str, backup_path: Path) -> bool:
    """备份 LightRAG 图谱文件"""
    world_data_path = PROJECT_ROOT / "data" / "worlds" / world_name
    rag_backup_path = backup_path / "lightrag"
    rag_backup_path.mkdir(parents=True, exist_ok=True)
    
    print(f"  -> 备份 LightRAG 数据: {world_data_path}")
    
    try:
        # 备份 graphml 文件
        graphml_files = list(world_data_path.glob("*.graphml"))
        if graphml_files:
            for graphml_file in graphml_files:
                shutil.copy2(graphml_file, rag_backup_path / graphml_file.name)
                print(f"     - {graphml_file.name}")
        else:
            print(f"  -> 警告: 没有找到 .graphml 文件")
        
        # 备份其他可能的 LightRAG 数据文件
        for pattern in ["*.json", "*.pkl", "*.db"]:
            for f in world_data_path.glob(pattern):
                shutil.copy2(f, rag_backup_path / f.name)
                print(f"     - {f.name}")
        
        print(f"  -> LightRAG 备份完成")
        return True
        
    except Exception as e:
        logger.error(f"备份 LightRAG 失败: {e}")
        print(f"  -> 错误: {e}")
        return False


async def create_backup(
    world_name: Optional[str] = None,
    backup_name: Optional[str] = None,
    description: str = ""
):
    """创建完整备份"""
    if world_name is None:
        world_name = get_active_world()
    
    print(f"\n=== 创建备份: 世界 [{world_name}] ===")
    
    # 检查世界是否存在
    world_path = PROJECT_ROOT / "data" / "worlds" / world_name
    if not world_path.exists():
        print(f"错误: 世界 '{world_name}' 不存在")
        return False
    
    # 创建备份目录
    backup_path = get_backup_path(world_name, backup_name)
    if backup_path.exists():
        print(f"错误: 备份 '{backup_path.name}' 已存在")
        return False
    
    backup_path.mkdir(parents=True, exist_ok=True)
    print(f"备份路径: {backup_path}")
    
    # 1. 备份数据库
    db_success = await backup_database(world_name, backup_path)
    
    # 2. 备份 LightRAG
    rag_success = await backup_lightrag(world_name, backup_path)
    
    # 3. 保存元信息
    meta = {
        "world_name": world_name,
        "backup_time": datetime.now().isoformat(),
        "description": description,
        "database_backup": db_success,
        "lightrag_backup": rag_success
    }
    with open(backup_path / "meta.json", "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    
    if db_success and rag_success:
        print(f"\n✓ 备份创建成功: {backup_path.name}")
    else:
        print(f"\n⚠ 备份部分完成，请检查错误信息")
    
    return db_success and rag_success


async def restore_database(world_name: str, backup_path: Path) -> bool:
    """恢复数据库 Schema 数据"""
    schema_name = f"world_{world_name}"
    db_backup_path = backup_path / "database"
    tables_file = db_backup_path / "tables.json"
    
    if not tables_file.exists():
        print(f"  -> 警告: 没有找到数据库备份文件")
        return True
    
    print(f"  -> 恢复数据库 Schema: {schema_name}")
    
    # 加载备份数据
    with open(tables_file, "r", encoding="utf-8") as f:
        backup_data = json.load(f)
    
    db_manager = DatabaseManager()
    
    try:
        async with db_manager.engine.begin() as conn:
            # 设置 search_path
            await conn.execute(text(f"SET search_path TO {schema_name}, public"))
            
            # 按照依赖顺序处理表（先清空子表，再清空父表）
            # 定义表的删除顺序（子表在前）
            delete_order = [
                "dialogue_records",
                "clue_discoveries", 
                "investigator_profiles",
                "interactables",
                "entities",
                "knowledge_registry",
                "events",
                "game_session",
                "locations"
            ]
            
            # 清空现有数据（按顺序）
            for table in delete_order:
                if table in backup_data:
                    try:
                        await conn.execute(text(f'DELETE FROM {schema_name}."{table}"'))
                        print(f"     - 清空表: {table}")
                    except Exception as e:
                        logger.warning(f"清空表 {table} 失败: {e}")
            
            # 定义表的插入顺序（父表在前）
            insert_order = [
                "locations",
                "game_session",
                "events",
                "knowledge_registry",
                "entities",
                "interactables",
                "investigator_profiles",
                "clue_discoveries",
                "dialogue_records"
            ]
            
            # 插入数据（按顺序）
            for table in insert_order:
                if table not in backup_data:
                    continue
                    
                table_info = backup_data[table]
                rows = table_info["rows"]
                
                if not rows:
                    print(f"     - {table}: 0 行 (跳过)")
                    continue
                
                columns = table_info["columns"]
                
                # 构建 INSERT 语句
                col_names = ', '.join([f'"{c}"' for c in columns])
                placeholders = ', '.join([f':{c}' for c in columns])
                
                insert_sql = f'INSERT INTO {schema_name}."{table}" ({col_names}) VALUES ({placeholders})'
                
                # 批量插入
                for row in rows:
                    try:
                        await conn.execute(text(insert_sql), row)
                    except Exception as e:
                        logger.warning(f"插入行失败 ({table}): {e}")
                
                print(f"     - {table}: {len(rows)} 行")
            
            print(f"  -> 数据库恢复完成")
            return True
            
    except Exception as e:
        logger.error(f"恢复数据库失败: {e}")
        print(f"  -> 错误: {e}")
        return False


async def restore_lightrag(world_name: str, backup_path: Path) -> bool:
    """恢复 LightRAG 图谱文件"""
    world_data_path = PROJECT_ROOT / "data" / "worlds" / world_name
    rag_backup_path = backup_path / "lightrag"
    
    if not rag_backup_path.exists():
        print(f"  -> 警告: 没有找到 LightRAG 备份")
        return True
    
    print(f"  -> 恢复 LightRAG 数据到: {world_data_path}")
    
    try:
        # 确保目标目录存在
        world_data_path.mkdir(parents=True, exist_ok=True)
        
        # 先删除现有的图谱文件
        for pattern in ["*.graphml", "*.json", "*.pkl", "*.db"]:
            for f in world_data_path.glob(pattern):
                f.unlink()
                print(f"     - 删除: {f.name}")
        
        # 复制备份文件
        for f in rag_backup_path.iterdir():
            if f.is_file():
                shutil.copy2(f, world_data_path / f.name)
                print(f"     - 恢复: {f.name}")
        
        print(f"  -> LightRAG 恢复完成")
        return True
        
    except Exception as e:
        logger.error(f"恢复 LightRAG 失败: {e}")
        print(f"  -> 错误: {e}")
        return False


async def restore_backup(
    backup_name: str,
    world_name: Optional[str] = None,
    force: bool = False
):
    """从备份恢复数据"""
    if world_name is None:
        world_name = get_active_world()
    
    backup_path = BACKUP_ROOT / world_name / backup_name
    
    if not backup_path.exists():
        print(f"错误: 备份 '{backup_name}' 不存在于世界 '{world_name}'")
        return False
    
    print(f"\n=== 恢复备份: 世界 [{world_name}] <- {backup_name} ===")
    
    # 读取元信息
    meta_file = backup_path / "meta.json"
    if meta_file.exists():
        with open(meta_file, "r", encoding="utf-8") as f:
            meta = json.load(f)
        print(f"备份时间: {meta.get('backup_time', '未知')}")
        print(f"备份描述: {meta.get('description', '无')}")
    
    # 确认操作
    if not force:
        confirm = input(f"\n⚠ 警告: 此操作将覆盖世界 '{world_name}' 的所有现有数据！\n输入 'yes' 确认恢复: ")
        if confirm.lower() != 'yes':
            print("操作已取消。")
            return False
    
    print()
    
    # 1. 恢复数据库
    db_success = await restore_database(world_name, backup_path)
    
    # 2. 恢复 LightRAG
    rag_success = await restore_lightrag(world_name, backup_path)
    
    if db_success and rag_success:
        print(f"\n✓ 恢复成功！")
        print(f"  注意: 如果程序正在运行，请重启以加载新数据")
    else:
        print(f"\n⚠ 恢复部分完成，请检查错误信息")
    
    return db_success and rag_success


async def delete_backup(backup_name: str, world_name: Optional[str] = None):
    """删除备份"""
    if world_name is None:
        world_name = get_active_world()
    
    backup_path = BACKUP_ROOT / world_name / backup_name
    
    if not backup_path.exists():
        print(f"错误: 备份 '{backup_name}' 不存在")
        return False
    
    confirm = input(f"确认删除备份 '{backup_name}'? (y/n): ")
    if confirm.lower() != 'y':
        print("操作已取消。")
        return False
    
    shutil.rmtree(backup_path)
    print(f"✓ 已删除备份: {backup_name}")
    return True


async def main():
    parser = argparse.ArgumentParser(
        description="GlyphKeeper 世界数据备份与恢复工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 备份当前激活的世界
  python backup_restore.py backup
  
  # 备份指定世界，并添加描述
  python backup_restore.py backup -w test -n before_test -d "测试前的备份"
  
  # 列出所有备份
  python backup_restore.py list
  
  # 恢复指定备份
  python backup_restore.py restore 20250106_120000
  
  # 强制恢复（跳过确认）
  python backup_restore.py restore 20250106_120000 -f
"""
    )
    
    subparsers = parser.add_subparsers(dest="command", help="可用命令")
    
    # backup 命令
    backup_parser = subparsers.add_parser("backup", help="创建备份")
    backup_parser.add_argument("-w", "--world", help="世界名称 (默认: 当前激活世界)")
    backup_parser.add_argument("-n", "--name", help="备份名称 (默认: 时间戳)")
    backup_parser.add_argument("-d", "--description", default="", help="备份描述")
    
    # restore 命令
    restore_parser = subparsers.add_parser("restore", help="恢复备份")
    restore_parser.add_argument("backup_name", help="备份名称")
    restore_parser.add_argument("-w", "--world", help="世界名称 (默认: 当前激活世界)")
    restore_parser.add_argument("-f", "--force", action="store_true", help="跳过确认提示")
    
    # list 命令
    list_parser = subparsers.add_parser("list", help="列出备份")
    list_parser.add_argument("-w", "--world", help="仅列出指定世界的备份")
    
    # delete 命令
    delete_parser = subparsers.add_parser("delete", help="删除备份")
    delete_parser.add_argument("backup_name", help="备份名称")
    delete_parser.add_argument("-w", "--world", help="世界名称 (默认: 当前激活世界)")
    
    args = parser.parse_args()
    
    if args.command == "backup":
        await create_backup(
            world_name=args.world,
            backup_name=args.name,
            description=args.description
        )
    elif args.command == "restore":
        await restore_backup(
            backup_name=args.backup_name,
            world_name=args.world,
            force=args.force
        )
    elif args.command == "list":
        list_backups(args.world)
    elif args.command == "delete":
        await delete_backup(args.backup_name, args.world)
    else:
        parser.print_help()


if __name__ == "__main__":
    asyncio.run(main())
