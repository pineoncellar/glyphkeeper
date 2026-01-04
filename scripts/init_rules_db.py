"""
初始化规则数据库 Schema
为 COC7th 规则数据创建独立的 schema：coc7th_rules
"""
import psycopg2
from psycopg2 import sql
import os
import configparser
import yaml

# 读取配置文件
config = configparser.ConfigParser()
config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'providers.ini')
config.read(config_path, encoding='utf-8')

if 'DATABASE' not in config:
    raise ValueError(f"配置文件 {config_path} 中缺少 [DATABASE] 部分")

db_config = config['DATABASE']

# 读取 config.yaml 获取项目名称
yaml_config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.yaml')
with open(yaml_config_path, 'r', encoding='utf-8') as f:
    yaml_config = yaml.safe_load(f)
project_name = yaml_config.get('project', {}).get('name', 'GlyphKeeper')

# 数据库连接配置
DB_CONFIG = {
    "host": db_config.get("host", "localhost"),
    "port": db_config.get("port", "5432"),
    "user": project_name,  # 使用项目用户
    "password": db_config.get("password"),
    "dbname": project_name
}

# 规则 Schema 名称
RULES_SCHEMA = "coc7th_rules"


def init_rules_schema():
    """初始化规则数据专用 Schema"""
    print(f"正在连接到数据库 {DB_CONFIG['dbname']}...")
    
    try:
        conn = psycopg2.connect(**DB_CONFIG)
        conn.autocommit = True
        cur = conn.cursor()
        
        print(f"\n开始初始化规则数据 Schema: {RULES_SCHEMA}")
        
        # 1. 创建 Schema
        try:
            cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(
                sql.Identifier(RULES_SCHEMA)
            ))
            print(f"✓ Schema '{RULES_SCHEMA}' 创建成功")
        except Exception as e:
            print(f"✗ 创建 Schema 失败: {e}")
            return
        
        # 2. 授予用户权限
        try:
            cur.execute(sql.SQL("GRANT ALL ON SCHEMA {} TO {}").format(
                sql.Identifier(RULES_SCHEMA),
                sql.Identifier(DB_CONFIG["user"])
            ))
            print(f"✓ 已授予用户 '{DB_CONFIG['user']}' 对 Schema '{RULES_SCHEMA}' 的权限")
        except Exception as e:
            print(f"✗ 授予权限失败: {e}")
        
        # 3. 设置默认权限（未来在此 Schema 中创建的表都会授权）
        try:
            cur.execute(sql.SQL(
                "ALTER DEFAULT PRIVILEGES IN SCHEMA {} GRANT ALL ON TABLES TO {}"
            ).format(
                sql.Identifier(RULES_SCHEMA),
                sql.Identifier(DB_CONFIG["user"])
            ))
            print(f"✓ 已设置默认表权限")
        except Exception as e:
            print(f"✗ 设置默认权限失败: {e}")
        
        # 4. 确保 vector 扩展可用（如果使用 PGVector）
        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            print(f"✓ vector 扩展已启用")
        except Exception as e:
            print(f"⚠ vector 扩展启用失败（如果未使用 PGVector 可忽略）: {e}")
        
        print(f"\n✅ 规则数据 Schema '{RULES_SCHEMA}' 初始化完成！")
        print(f"\n接下来你可以：")
        print(f"  1. 运行 scripts/ingest_module.py 导入规则数据")
        print(f"  2. 使用 get_rules_storage_config() 初始化 LightRAG")
        print(f"  3. 通过 rules_db_manager.session_factory() 访问规则数据库")
        
        cur.close()
        conn.close()
        
    except psycopg2.OperationalError as e:
        print(f"✗ 数据库连接失败: {e}")
        print(f"  请检查 providers.ini 中的数据库配置是否正确")
    except Exception as e:
        print(f"✗ 初始化失败: {e}")


if __name__ == "__main__":
    init_rules_schema()
