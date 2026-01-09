"""
世界管理和数据库初始化工具

提供以下服务:
初始化数据库（创建以项目名为名的数据库用户与数据库）
初始化世界（创建schema和对应的表）
备份、清空与恢复世界


数据分三类：
- LightRAG的本地图谱数据（data/worlds/<world_name>）
- 数据库中的世界schemas数据（world_<world_name> schema）
- LightRAG的public schemas数据（按workspace分类）
"""

import os
import sys
import shutil
import asyncio
import tarfile
import json
import csv
import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Tuple
from sqlalchemy import text, inspect
from sqlalchemy.ext.asyncio import AsyncSession

from ..core import get_logger, get_settings, PROJECT_ROOT
from ..memory.database import DatabaseManager, Base

logger = get_logger(__name__)


class DatabaseInitializer:
    """数据库初始化工具"""

    def __init__(self):
        self.settings = get_settings()
        self.config_path = PROJECT_ROOT / "providers.ini"
        self.db_config = self._load_db_config()

    def _load_db_config(self) -> Dict:
        """从 providers.ini 读取数据库配置"""
        import configparser

        config = configparser.ConfigParser()
        config.read(self.config_path, encoding="utf-8")

        if "DATABASE" not in config:
            raise ValueError(f"配置文件 {self.config_path} 中缺少 [DATABASE] 部分")

        db_cfg = config["DATABASE"]
        return {
            "host": db_cfg.get("host", "localhost"),
            "port": db_cfg.get("port", "5432"),
            "admin_user": db_cfg.get("admin_user", "postgres"),
            "admin_password": db_cfg.get("admin_password"),
            "app_password": db_cfg.get("password"),
        }

    def init_database(self) -> bool:
        """
        初始化数据库
        1. 创建以项目名为名的数据库用户
        2. 创建以项目名为名的数据库
        3. 安装必要的扩展（pgvector等）
        4. 授予相应权限
        """
        logger.info(f"开始数据库初始化... (项目名: {self.settings.PROJECT_NAME})")

        # 管理员连接配置
        admin_config = {
            "host": self.db_config["host"],
            "port": self.db_config["port"],
            "user": self.db_config["admin_user"],
            "password": self.db_config["admin_password"],
            "dbname": "postgres",
        }

        # 应用连接配置
        app_config = {
            "db_name": self.settings.PROJECT_NAME,
            "user": self.settings.PROJECT_NAME,
            "password": self.db_config["app_password"],
        }

        try:
            # 连接到管理库
            logger.info("连接到 PostgreSQL 管理库...")
            conn = psycopg2.connect(**admin_config)
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = conn.cursor()

            # 创建用户
            try:
                cur.execute(
                    sql.SQL("CREATE USER {} WITH PASSWORD %s").format(
                        sql.Identifier(app_config["user"])
                    ),
                    [app_config["password"]],
                )
                logger.info(f"用户 '{app_config['user']}' 创建成功")
            except psycopg2.errors.DuplicateObject:
                logger.info(f"用户 '{app_config['user']}' 已存在 (跳过)")

            # 创建数据库
            try:
                cur.execute(
                    sql.SQL("CREATE DATABASE {} OWNER {}").format(
                        sql.Identifier(app_config["db_name"]),
                        sql.Identifier(app_config["user"]),
                    )
                )
                logger.info(f"数据库 '{app_config['db_name']}' 创建成功")
            except psycopg2.errors.DuplicateDatabase:
                logger.info(f"数据库 '{app_config['db_name']}' 已存在 (跳过)")

            cur.close()
            conn.close()

            # 连接到新数据库安装扩展
            logger.info("连接到应用数据库...")
            app_conn_config = {
                "host": self.db_config["host"],
                "port": self.db_config["port"],
                "user": self.db_config["admin_user"],
                "password": self.db_config["admin_password"],
                "dbname": app_config["db_name"],
            }

            conn = psycopg2.connect(**app_conn_config)
            conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
            cur = conn.cursor()

            # 安装 pgvector
            try:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                logger.info("pgvector 扩展安装成功")
            except Exception as e:
                logger.warning(f"pgvector 扩展安装失败 (可能未安装): {e}")

            # 授权
            cur.execute(
                sql.SQL("GRANT ALL PRIVILEGES ON DATABASE {} TO {}").format(
                    sql.Identifier(app_config["db_name"]),
                    sql.Identifier(app_config["user"]),
                )
            )
            cur.execute(
                sql.SQL("GRANT ALL ON SCHEMA public TO {}").format(
                    sql.Identifier(app_config["user"])
                )
            )
            logger.info(f"授予用户 '{app_config['user']}' 权限")

            cur.close()
            conn.close()

            logger.info("数据库初始化完成！")
            return True

        except Exception as e:
            logger.error(f"数据库初始化失败: {e}")
            return False


class WorldManager:
    """世界管理工具"""

    def __init__(self):
        self.db_manager = DatabaseManager()
        self.settings = get_settings()

    @staticmethod
    def _validate_world_name(world_name: str) -> bool:
        """验证世界名称是否合法（必须是有效的 Python 标识符）"""
        return world_name.isidentifier()

    async def create_world(self, world_name: str) -> bool:
        """
        创建新世界
        1. 验证世界名称
        2. 创建本地目录 (data/worlds/<world_name>)
        3. 创建数据库 Schema (world_<world_name>)
        4. 初始化表结构
        lightrag数据会自己存入public，以workspace分类，不需要在这里创建
        """
        if not self._validate_world_name(world_name):
            logger.error(
                f"世界名称 '{world_name}' 不合法，必须是有效的 Python 标识符"
            )
            return False

        worlds_dir = PROJECT_ROOT / "data" / "worlds"
        world_path = worlds_dir / world_name
        schema_name = f"world_{world_name}"

        if world_path.exists():
            logger.warning(f"世界 '{world_name}' 已存在")
            return False

        try:
            logger.info(f"正在创建世界 '{world_name}'...")

            # 创建目录
            world_path.mkdir(parents=True, exist_ok=True)
            logger.info(f"创建目录: {world_path}")

            # 创建 Schema 和表
            async with self.db_manager.engine.begin() as conn:
                await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}"))
                logger.info(f"创建 Schema: {schema_name}")

                # 设置 search_path 并创建表
                await conn.execute(text(f"SET search_path TO {schema_name}, public"))
                await conn.run_sync(Base.metadata.create_all)
                logger.info(f"初始化表结构")

            logger.info(f"世界 '{world_name}' 创建成功！")
            return True

        except Exception as e:
            logger.error(f"创建世界失败: {e}")
            # 清理已创建的目录
            if world_path.exists():
                shutil.rmtree(world_path)
            return False

    async def delete_world(self, world_name: str, force: bool = False) -> bool:
        """
        删除世界
        1. 删除数据库 Schema (world_<world_name>)
        2. 删除本地目录 (data/worlds/<world_name>)
        3. 删除相关备份数据 (可选)
        """
        schema_name = f"world_{world_name}"
        world_path = PROJECT_ROOT / "data" / "worlds" / world_name

        if not world_path.exists():
            logger.warning(f"世界 '{world_name}' 不存在")
            return False

        try:
            logger.info(f"正在删除世界 '{world_name}'...")

            # 删除 Schema
            async with self.db_manager.engine.begin() as conn:
                await conn.execute(
                    text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")
                )
                logger.info(f"删除 Schema: {schema_name}")

            # 删除目录
            shutil.rmtree(world_path)
            logger.info(f"删除目录: {world_path}")
            # 清空该世界的 public schema 数据 (LightRAG)
            await self._clear_workspace_data(world_name)

            logger.info(f"世界 '{world_name}' 已删除")
            return True

        except Exception as e:
            logger.error(f"删除世界失败: {e}")
            return False

    async def _clear_workspace_data(self, workspace: str) -> None:
        """清空指定 workspace 的 public schema 数据"""
        try:
            async with self.db_manager.engine.begin() as conn:
                # 获取所有包含 workspace 列的表
                query = text("""
                    SELECT table_name 
                    FROM information_schema.columns 
                    WHERE table_schema = 'public' AND column_name = 'workspace'
                """)
                result = await conn.execute(query)
                tables = [row[0] for row in result.fetchall()]

                for table in tables:
                    await conn.execute(
                        text(f'DELETE FROM public."{table}" WHERE workspace = :w'),
                        {"w": workspace},
                    )
                    logger.info(f"清空 public.{table} (workspace={workspace})")

        except Exception as e:
            logger.warning(f"清空 workspace 数据失败: {e}")


class WorldBackupRestore:
    """世界备份与恢复工具"""

    def __init__(self):
        self.db_manager = DatabaseManager()

    async def backup_world(
        self,
        world_name: str,
        output_file: Optional[str] = None,
        remark: Optional[str] = None,
    ) -> Optional[Path]:
        """
        备份世界
        打包三类数据：
        1. 本地图谱数据 (data/worlds/<world_name>)
        2. 数据库 Schema 数据 (world_<world_name>)
        3. LightRAG 公共数据 (public schema, workspace=world_name)
        """
        logger.info(f"开始备份世界 '{world_name}'...")

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        schema_name = f"world_{world_name}"

        # 创建临时目录
        temp_dir = PROJECT_ROOT / "tmp" / f"backup_{world_name}_{timestamp}"
        if temp_dir.exists():
            shutil.rmtree(temp_dir)
        temp_dir.mkdir(parents=True)
        
        struct_dir = temp_dir / "structured"
        struct_dir.mkdir()
        
        unstruct_dir = temp_dir / "unstructured"
        unstruct_dir.mkdir()
        
        graph_dir = temp_dir / "graph"
        graph_dir.mkdir()

        try:
            backup_summary = {}
            
            async with self.db_manager.engine.begin() as conn:
                # 备份 world schema 数据
                logger.info(f"备份 {schema_name} 数据...")
                world_summary = await self._backup_schema(conn, schema_name, struct_dir)
                backup_summary.update(world_summary)

                # 备份 public schema 数据
                logger.info(f"备份 public schema 数据 (workspace={world_name})...")
                public_summary = await self._backup_workspace_data(conn, world_name, unstruct_dir)
                backup_summary.update(public_summary)

            # 备份本地文件
            world_path = PROJECT_ROOT / "data" / "worlds" / world_name
            if world_path.exists():
                logger.info(f"备份图谱文件...")
                for file in world_path.glob("*"):
                    if file.is_file():
                        shutil.copy2(file, graph_dir)

            # 创建元数据
            meta = {
                "world": world_name,
                "timestamp": timestamp,
                "remark": remark or "",
            }
            with open(temp_dir / "metadata.json", "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

            # 创建存档文件
            if not output_file:
                backup_dir = PROJECT_ROOT / "data" / "backups"
                backup_dir.mkdir(parents=True, exist_ok=True)
                output_file = backup_dir / f"{world_name}_{timestamp}.tar.gz"
            else:
                output_file = Path(output_file)
                output_file.parent.mkdir(parents=True, exist_ok=True)

            logger.info(f"创建存档: {output_file}")
            with tarfile.open(output_file, "w:gz") as tar:
                tar.add(temp_dir, arcname=world_name)

            logger.info(f"备份完成: {output_file}")
            
            # 输出备份统计
            logger.info("\n=== 备份统计 ===")
            total_rows = 0
            for table, count in sorted(backup_summary.items()):
                logger.info(f"{table}: {count} 行")
                total_rows += count
            logger.info(f"总计: {total_rows} 行")
            logger.info("==================\n")
            
            return output_file

        except Exception as e:
            logger.error(f"备份失败: {e}")
            return None
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

    async def restore_world(
        self, world_name: str, backup_file: str, overwrite: bool = False
    ) -> bool:
        """
        恢复世界
        从存档恢复三类数据
        """
        logger.info(f"开始恢复世界 '{world_name}' 从 {backup_file}...")

        backup_path = Path(backup_file)
        if not backup_path.exists():
            logger.error(f"备份文件不存在: {backup_path}")
            return False

        schema_name = f"world_{world_name}"
        world_path = PROJECT_ROOT / "data" / "worlds" / world_name

        # 创建临时目录解压
        temp_dir = (
            PROJECT_ROOT
            / "tmp"
            / f"restore_{world_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        )
        if temp_dir.exists():
            shutil.rmtree(temp_dir)

        try:
            # 解压
            logger.info("解压存档文件...")
            with tarfile.open(backup_path, "r:gz") as tar:
                tar.extractall(temp_dir)

            # 找到实际的内容目录
            extracted_root = next(temp_dir.iterdir())
            if not (extracted_root / "structured").exists():
                extracted_root = temp_dir

            struct_dir = extracted_root / "structured"
            unstruct_dir = extracted_root / "unstructured"
            graph_dir = extracted_root / "graph"

            # 恢复本地文件
            if graph_dir.exists() and graph_dir.is_dir():
                logger.info("恢复图谱文件...")
                world_path.mkdir(parents=True, exist_ok=True)
                for file in graph_dir.glob("*"):
                    if file.is_file():
                        shutil.copy2(file, world_path)

            # 恢复数据库数据
            async with self.db_manager.engine.begin() as conn:
                # 检查并清理现有 schema
                if overwrite:
                    await conn.execute(
                        text(f"DROP SCHEMA IF EXISTS {schema_name} CASCADE")
                    )
                    logger.info(f"清空现有 Schema: {schema_name}")

                # 创建 Schema 和表
                await conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema_name}"))
                await conn.execute(text(f"SET search_path TO {schema_name}, public"))
                await conn.run_sync(Base.metadata.create_all)
                logger.info(f"✓ 创建 Schema: {schema_name}")

                # 恢复 world schema 数据
                restore_summary = {}
                if struct_dir.exists():
                    logger.info("恢复 world schema 数据...")
                    world_summary = await self._restore_schema(conn, schema_name, struct_dir)
                    restore_summary.update(world_summary)

                # 恢复 public schema 数据
                if unstruct_dir.exists():
                    logger.info("恢复 public schema 数据...")
                    public_summary = await self._restore_workspace_data(
                        conn, world_name, unstruct_dir, overwrite
                    )
                    restore_summary.update(public_summary)

            logger.info(f"世界 '{world_name}' 恢复成功！")
            
            # 输出恢复统计
            logger.info("\n=== 恢复统计 ===")
            total_rows = 0
            for table, count in sorted(restore_summary.items()):
                if isinstance(count, int):
                    logger.info(f"{table}: {count} 行")
                    total_rows += count
                else:
                    logger.info(f"{table}: {count}")
            logger.info(f"总计: {total_rows} 行")
            logger.info("==================\n")
            
            return True

        except Exception as e:
            logger.error(f"恢复失败: {e}")
            return False
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

    async def _backup_schema(self, conn, schema: str, output_dir: Path) -> Dict[str, int]:
        """备份指定 schema 的所有表到 JSONL 格式"""
        # 获取所有表
        query = text(
            f"SELECT table_name FROM information_schema.tables WHERE table_schema = '{schema}'"
        )
        result = await conn.execute(query)
        tables = [row[0] for row in result.fetchall()]

        summary = {}
        for table in tables:
            logger.info(f"  导出 {schema}.{table}...")
            row_count = await self._backup_table_to_jsonl(conn, schema, table, None, output_dir / f"{table}.jsonl")
            summary[f"{schema}.{table}"] = row_count
        
        return summary

    async def _backup_workspace_data(
        self, conn, workspace: str, output_dir: Path
    ) -> Dict[str, int]:
        """备份指定 workspace 的 public schema 数据"""
        # 获取所有包含 workspace 列的表
        query = text("""
            SELECT table_name 
            FROM information_schema.columns 
            WHERE table_schema = 'public' AND column_name = 'workspace'
        """)
        result = await conn.execute(query)
        tables = [row[0] for row in result.fetchall()]

        summary = {}
        for table in tables:
            logger.info(f"  导出 public.{table} (workspace={workspace})...")
            row_count = await self._backup_table_to_jsonl(
                conn, "public", table, workspace, output_dir / f"{table}.jsonl"
            )
            summary[f"public.{table}"] = row_count
        
        return summary

    async def _backup_table_to_jsonl(
        self,
        conn,
        schema: str,
        table: str,
        workspace_filter: Optional[str],
        output_path: Path,
    ) -> int:
        """备份单个表到 JSONL 文件，返回行数"""
        if workspace_filter:
            query = text(
                f'SELECT * FROM "{schema}"."{table}" WHERE workspace = :workspace'
            )
            result = await conn.execute(query, {"workspace": workspace_filter})
        else:
            query = text(f'SELECT * FROM "{schema}"."{table}"')
            result = await conn.execute(query)

        keys = list(result.keys())
        row_count = 0

        with open(output_path, "w", encoding="utf-8") as f:
            # 写入列信息头
            f.write(json.dumps({"columns": keys}) + "\n")

            # 写入数据行
            for row in result:
                row_dict = {}
                for idx, val in enumerate(row):
                    if isinstance(val, datetime):
                        val = val.isoformat()
                    row_dict[keys[idx]] = val
                f.write(json.dumps(row_dict, default=str, ensure_ascii=False) + "\n")
                row_count += 1
        
        return row_count

    async def _restore_schema(
        self, conn, schema: str, input_dir: Path
    ) -> Dict[str, int]:
        """恢复 schema 数据，按依赖顺序导入"""
        summary = {}
        json_files = list(input_dir.glob("*.jsonl"))
        
        # 定义表的依赖顺序（基于外键关系）
        # 越基础的表越先导入
        table_order = [
            "locations",              # 无依赖
            "knowledge_registry",     # 无依赖
            "game_session",          # 无依赖
            "entities",              # 依赖 locations
            "interactables",         # 依赖 locations, entities
            "clue_discoveries",      # 依赖 interactables, entities, knowledge_registry
            "investigator_profiles", # 依赖 entities
        ]
        
        # 按顺序处理表
        processed = set()
        
        # 先按预定义顺序处理
        for table_name in table_order:
            file_path = input_dir / f"{table_name}.jsonl"
            if file_path.exists():
                logger.info(f"  导入 {schema}.{table_name}...")
                row_count = await self._restore_table_from_jsonl(
                    conn, schema, table_name, file_path
                )
                summary[f"{schema}.{table_name}"] = row_count
                processed.add(table_name)
        
        # 处理剩余的未知表
        for file_path in json_files:
            table_name = file_path.stem
            if table_name not in processed:
                logger.info(f"  导入 {schema}.{table_name}...")
                row_count = await self._restore_table_from_jsonl(
                    conn, schema, table_name, file_path
                )
                summary[f"{schema}.{table_name}"] = row_count

        return summary

    async def _restore_workspace_data(
        self,
        conn,
        workspace: str,
        input_dir: Path,
        overwrite: bool = False,
    ) -> Dict[str, int]:
        """恢复 public schema 数据"""
        summary = {}

        # 获取有效的表
        query = text("""
            SELECT table_name 
            FROM information_schema.columns 
            WHERE table_schema = 'public' AND column_name = 'workspace'
        """)
        result = await conn.execute(query)
        valid_tables = {row[0] for row in result.fetchall()}

        for file_path in input_dir.glob("*.jsonl"):
            table_name = file_path.stem

            if table_name not in valid_tables:
                logger.warning(f"  表 public.{table_name} 不存在，跳过")
                summary[f"public.{table_name}"] = "Skipped"
                continue

            if overwrite:
                await conn.execute(
                    text(f'DELETE FROM public."{table_name}" WHERE workspace = :w'),
                    {"w": workspace},
                )

            logger.info(f"  导入 public.{table_name} (workspace={workspace})...")
            row_count = await self._restore_table_from_jsonl(
                conn, "public", table_name, file_path
            )
            summary[f"public.{table_name}"] = row_count

        return summary

    async def _restore_table_from_jsonl(
        self, conn, schema: str, table: str, json_path: Path
    ) -> int:
        """从 JSONL 恢复单个表，处理列名不匹配和 NOT NULL 约束"""
        if not json_path.exists():
            return 0

        # 获取目标表的列信息（用于处理缺失列和 NOT NULL 约束）
        def get_col_types(sync_conn):
            inspector = inspect(sync_conn)
            return inspector.get_columns(table, schema=schema)

        try:
            columns_info = await conn.run_sync(get_col_types)
        except Exception as e:
            logger.warning(f"  无法检查表 {schema}.{table} 的列信息: {e}")
            columns_info = []

        # 构建列信息映射：列名 -> (类型, 是否可为空)
        column_metadata = {}
        for col in columns_info:
            col_name = col["name"]
            col_type = str(col["type"]).upper()
            is_nullable = col.get("nullable", True)
            
            # 检测列的数据类型
            col_info = {
                "type": col_type,
                "nullable": is_nullable,
                "is_json": "JSON" in col_type or "JSONB" in col_type,
                "is_array": "ARRAY" in col_type or "[]" in col_type,
            }
            column_metadata[col_name] = col_info

        total_rows = 0

        with open(json_path, "r", encoding="utf-8") as f:
            # 读取列信息头
            first_line = f.readline()
            if not first_line:
                return 0

            header = json.loads(first_line)
            backup_columns = header["columns"]
            
            # 获取当前表的所有列名
            current_columns = set(column_metadata.keys())
            backup_columns_set = set(backup_columns)
            
            # 找出缺失的列（新增的 NOT NULL 列）
            missing_columns = current_columns - backup_columns_set
            
            if missing_columns:
                logger.info(f"    检测到新增列: {missing_columns}")

            # 构建 INSERT 语句（使用备份中存在的列 + 缺失的必需列）
            insert_columns = backup_columns.copy()
            default_values = {}
            
            # 为缺失的 NOT NULL 列添加默认值
            for col_name in missing_columns:
                col_info = column_metadata.get(col_name)
                if col_info and not col_info["nullable"]:
                    # 根据类型决定默认值
                    if col_info["is_json"]:
                        default_values[col_name] = "{}"
                        logger.info(f"    为 NOT NULL JSON 列 '{col_name}' 设置默认值: {{}}")
                    elif col_info["is_array"]:
                        default_values[col_name] = "[]"
                        logger.info(f"    为 NOT NULL ARRAY 列 '{col_name}' 设置默认值: []")
                    else:
                        # 其他类型暂时跳过，让数据库报错
                        logger.warning(f"    列 '{col_name}' 为 NOT NULL 但无法自动填充默认值（类型: {col_info['type']}）")
                        continue
                    
                    insert_columns.append(col_name)

            cols_str = ", ".join([f'"{c}"' for c in insert_columns])
            vals_str = ", ".join([f":{c}" for c in insert_columns])
            stmt = text(
                f'INSERT INTO "{schema}"."{table}" ({cols_str}) VALUES ({vals_str})'
            )

            chunk = []
            BATCH_SIZE = 1000

            for line in f:
                line = line.strip()
                if not line:
                    continue

                data = json.loads(line)
                
                # 添加缺失列的默认值
                for col_name, default_val in default_values.items():
                    data[col_name] = default_val
                
                # 转换 JSON/JSONB 和 ARRAY 类型的值
                # asyncpg 不会自动将 Python dict/list 转换为 JSONB/ARRAY
                for col_name in insert_columns:
                    if col_name in data and data[col_name] is not None:
                        col_info = column_metadata.get(col_name)
                        if col_info:
                            col_type = col_info["type"]
                            
                            # 对于 JSON/JSONB 类型，如果值是 dict/list，转换为 JSON 字符串
                            if col_info["is_json"] and isinstance(data[col_name], (dict, list)):
                                data[col_name] = json.dumps(data[col_name], ensure_ascii=False)
                            
                            # 对于 TIMESTAMP/DATETIME 类型，如果值是字符串，转换为 datetime
                            elif "TIMESTAMP" in col_type or "DATETIME" in col_type:
                                if isinstance(data[col_name], str):
                                    from datetime import datetime
                                    # 尝试解析 ISO 格式的日期时间字符串
                                    try:
                                        data[col_name] = datetime.fromisoformat(data[col_name])
                                    except ValueError:
                                        # 如果解析失败，保持原值
                                        pass
                
                chunk.append(data)

                if len(chunk) >= BATCH_SIZE:
                    await conn.execute(stmt, chunk)
                    total_rows += len(chunk)
                    chunk = []

            if chunk:
                await conn.execute(stmt, chunk)
                total_rows += len(chunk)

        return total_rows

    async def list_backups(self, world_filter: Optional[str] = None) -> List[Dict]:
        """列出所有备份"""
        backup_dir = PROJECT_ROOT / "data" / "backups"
        if not backup_dir.exists():
            logger.info(f"备份目录不存在: {backup_dir}")
            return []

        backups = []
        files = list(backup_dir.glob("*.tar.gz")) + list(backup_dir.glob("*.zip"))

        for file_path in files:
            try:
                info = {
                    "file": file_path.name,
                    "created": datetime.fromtimestamp(file_path.stat().st_mtime),
                    "size": file_path.stat().st_size,
                    "world": "Unknown",
                    "remark": "",
                    "timestamp": "",
                }

                # 尝试读取元数据
                try:
                    with tarfile.open(file_path, "r:gz") as tar:
                        for member in tar.getmembers():
                            if member.name.endswith("metadata.json"):
                                f = tar.extractfile(member)
                                if f:
                                    meta = json.load(f)
                                    info.update(meta)
                                break
                except Exception as e:
                    # 后备解析：从文件名解析
                    parts = file_path.stem.split("_")
                    if len(parts) >= 2:
                        info["world"] = parts[0]

                if world_filter and info.get("world") != world_filter:
                    continue

                backups.append(info)

            except Exception as e:
                logger.warning(f"检查备份文件失败 {file_path.name}: {e}")

        # 按时间排序
        backups.sort(
            key=lambda x: datetime.fromisoformat(x.get("timestamp", ""))
            if x.get("timestamp")
            else x["created"],
            reverse=True,
        )

        return backups
