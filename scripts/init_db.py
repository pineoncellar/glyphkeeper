import psycopg2
from psycopg2 import sql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
import os
import configparser
import yaml

# 读取配置文件
config = configparser.ConfigParser()
# 假设 providers.ini 在项目根目录，而此脚本在 scripts/ 目录
config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'providers.ini')
config.read(config_path, encoding='utf-8')

if 'DATABASE' not in config:
    raise ValueError(f"配置文件 {config_path} 中缺少 [DATABASE] 部分")

db_config = config['DATABASE']

# 管理员连接信息
ADMIN_DB_CONFIG = {
    "host": db_config.get("host", "localhost"),
    "port": db_config.get("port", "5432"),
    "user": db_config.get("admin_user", "postgres"),
    "password": db_config.get("admin_password"),
    "dbname": "postgres"      # 默认管理库
}

# 项目自动创建的新用户信息
# 读取 config.yaml 获取项目名称
yaml_config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.yaml')
with open(yaml_config_path, 'r', encoding='utf-8') as f:
    yaml_config = yaml.safe_load(f)
project_name = yaml_config.get('project', {}).get('name', 'GlyphKeeper')

NEW_APP_CONFIG = {
    "db_name": project_name,
    "user": project_name,
    "password": db_config.get("password"),  # 使用管理员密码
}


def run_sql(cursor, query, params=None):
    try:
        cursor.execute(query, params)
        print(f"执行成功: {query.as_string(cursor) if isinstance(query, sql.Composed) else query}")
    except psycopg2.errors.DuplicateObject:
        print(f"已存在 (跳过): {query.as_string(cursor) if isinstance(query, sql.Composed) else query}")
    except psycopg2.errors.DuplicateDatabase:
        print(f"数据库已存在 (跳过)")
    except Exception as e:
        print(f"{e}")

def init_database():
    print(f"正在读取配置文件: {config_path}")
    
    # 连接到默认的 postgres 管理库
    try:
        conn = psycopg2.connect(**ADMIN_DB_CONFIG)
    except psycopg2.OperationalError as e:
        print(f"连接失败: {e}")
        print(f"请检查 providers.ini 中的 admin_password 是否正确 (当前尝试连接用户: {ADMIN_DB_CONFIG['user']})")
        return

    # 必须设置自动提交，否则无法执行 CREATE DATABASE
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()

    print("开始自动化初始化数据库环境...")

    # 创建新用户
    try:
        cur.execute(sql.SQL("CREATE USER {} WITH PASSWORD %s").format(
            sql.Identifier(NEW_APP_CONFIG["user"])
        ), [NEW_APP_CONFIG["password"]])
        print(f"用户 {NEW_APP_CONFIG['user']} 创建成功")
    except psycopg2.errors.DuplicateObject:
        print(f"用户 {NEW_APP_CONFIG['user']} 已存在，跳过")
    except Exception as e:
        print(f"创建用户失败: {e}")

    # 创建新数据库
    try:
        cur.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(
            sql.Identifier(NEW_APP_CONFIG["db_name"]),
            sql.Identifier(NEW_APP_CONFIG["user"])
        ))
        print(f"数据库 {NEW_APP_CONFIG['db_name']} 创建成功")
    except psycopg2.errors.DuplicateDatabase:
        print(f"数据库 {NEW_APP_CONFIG['db_name']} 已存在，跳过")
    except Exception as e:
        print(f"创建数据库失败: {e}")

    # 关闭管理员连接
    cur.close()
    conn.close()


    # 连接到新创建的数据库，安装插件
    print("\n正在连接到新数据库以安装插件...")
    
    try:
        # 使用刚才创建的配置连接，或者继续用管理员连接新库
        conn_new = psycopg2.connect(
            host=ADMIN_DB_CONFIG["host"],
            port=ADMIN_DB_CONFIG["port"],
            user=ADMIN_DB_CONFIG["user"], # 仍然用管理员身份，因为安装插件通常需要高权限
            password=ADMIN_DB_CONFIG["password"],
            dbname=NEW_APP_CONFIG["db_name"]
        )
        conn_new.autocommit = True
        cur_new = conn_new.cursor()

        # 安装 pgvector 插件
        try:
            run_sql(cur_new, "CREATE EXTENSION IF NOT EXISTS vector;")
        except Exception as e:
             print(f"安装 vector 插件失败: {e}")
             print("提示: 请确保你的 PostgreSQL 安装了 pgvector 扩展")

        # 授予新用户权限
        run_sql(cur_new, sql.SQL("GRANT ALL PRIVILEGES ON DATABASE {} TO {}").format(
            sql.Identifier(NEW_APP_CONFIG["db_name"]),
            sql.Identifier(NEW_APP_CONFIG["user"])
        ))
        run_sql(cur_new, sql.SQL("GRANT ALL ON SCHEMA public TO {}").format(
            sql.Identifier(NEW_APP_CONFIG["user"])
        ))

        print("\n数据库初始化完成！")
        cur_new.close()
        conn_new.close()
    except Exception as e:
        print(f"配置失败: {e}")

if __name__ == "__main__":
    init_database()
