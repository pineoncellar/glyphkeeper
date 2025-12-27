import asyncio
import sys
import os
import shutil
import argparse
import yaml
from pathlib import Path
from sqlalchemy import text

# 将项目根目录添加到 python 路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.memory.database import DatabaseManager, Base
from src.core.config import get_settings, PROJECT_ROOT
from src.core.logger import get_logger

logger = get_logger("world_manager")

def get_config_path():
    return PROJECT_ROOT / "config.yaml"

def load_yaml_config():
    path = get_config_path()
    if not path.exists():
        print(f"错误: 找不到配置文件 {path}")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def save_yaml_config(config):
    path = get_config_path()
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, allow_unicode=True, sort_keys=False)

def list_worlds():
    worlds_dir = PROJECT_ROOT / "data" / "worlds"
    if not worlds_dir.exists():
        print("没有找到任何世界存档。")
        return []
    
    worlds = [d.name for d in worlds_dir.iterdir() if d.is_dir()]
    
    config = load_yaml_config()
    active_world = config.get("project", {}).get("active_world", "")
    
    print("\n=== 可用的世界 ===")
    for w in worlds:
        status = "*" if w == active_world else " "
        print(f"[{status}] {w}")
    print("==================\n")
    return worlds

async def create_world(world_name: str):
    if not world_name.isidentifier():
        print(f"错误: 世界名称 '{world_name}' 不合法，必须是有效的 Python 标识符 (字母、数字、下划线)。")
        return

    worlds_dir = PROJECT_ROOT / "data" / "worlds"
    world_path = worlds_dir / world_name
    
    if world_path.exists():
        print(f"错误: 世界 '{world_name}' 已存在。")
        return

    print(f"正在创建世界 '{world_name}'...")
    
    # 1. 创建目录
    world_path.mkdir(parents=True, exist_ok=True)
    print(f"  -> 创建目录: {world_path}")
    
    # 2. 创建数据库 Schema
    schema_name = f"world_{world_name}"
    db_manager = DatabaseManager()
    try:
        async with db_manager.engine.begin() as conn:
            await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}"))
            print(f"  -> 创建数据库 Schema: {schema_name}")
            
            # 3. 初始化表结构 (在新的 Schema 中)
            # 我们需要临时设置 search_path 来创建表
            await conn.execute(text(f"SET search_path TO {schema_name}, public"))
            await conn.run_sync(Base.metadata.create_all)
            print(f"  -> 初始化表结构完成")
            
    except Exception as e:
        print(f"创建数据库失败: {e}")
        # 回滚目录创建
        shutil.rmtree(world_path)
        return

    print(f"世界 '{world_name}' 创建成功！")
    
    # 询问是否切换
    if input(f"是否立即切换到 '{world_name}'? (y/n): ").lower() == 'y':
        switch_world(world_name)

def switch_world(world_name: str):
    worlds_dir = PROJECT_ROOT / "data" / "worlds"
    if not (worlds_dir / world_name).exists():
        print(f"错误: 世界 '{world_name}' 不存在。")
        return

    config = load_yaml_config()
    config["project"]["active_world"] = world_name
    save_yaml_config(config)
    print(f"已切换当前世界为: {world_name}")

async def delete_world(world_name: str):
    confirm = input(f"警告: 此操作将永久删除世界 '{world_name}' 的所有存档和数据！\n请输入 '{world_name}' 确认删除: ")
    if confirm != world_name:
        print("操作已取消。")
        return

    print(f"正在删除世界 '{world_name}'...")
    
    # 1. 删除数据库 Schema
    schema_name = f"world_{world_name}"
    db_manager = DatabaseManager()
    try:
        async with db_manager.engine.begin() as conn:
            await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE"))
            print(f"  -> 删除数据库 Schema: {schema_name}")
    except Exception as e:
        print(f"删除数据库 Schema 失败: {e}")

    # 2. 删除目录
    world_path = PROJECT_ROOT / "data" / "worlds" / world_name
    if world_path.exists():
        shutil.rmtree(world_path)
        print(f"  -> 删除目录: {world_path}")
    
    print(f"世界 '{world_name}' 已删除。")

async def main():
    parser = argparse.ArgumentParser(description="GlyphKeeper 世界管理器")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")
    
    # list
    subparsers.add_parser("list", help="列出所有世界")
    
    # create
    create_parser = subparsers.add_parser("create", help="创建新世界")
    create_parser.add_argument("name", help="世界名称 (英文标识符)")
    
    # switch
    switch_parser = subparsers.add_parser("switch", help="切换当前世界")
    switch_parser.add_argument("name", help="世界名称")
    
    # delete
    delete_parser = subparsers.add_parser("delete", help="删除世界")
    delete_parser.add_argument("name", help="世界名称")
    
    args = parser.parse_args()
    
    if args.command == "list":
        list_worlds()
    elif args.command == "create":
        await create_world(args.name)
    elif args.command == "switch":
        switch_world(args.name)
    elif args.command == "delete":
        await delete_world(args.name)
    else:
        parser.print_help()

if __name__ == "__main__":
    asyncio.run(main())
