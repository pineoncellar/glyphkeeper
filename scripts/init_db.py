"""
数据库初始化脚本（v3 — PgManager + pgembed 统一初始化）

使用方式:
    uv run python scripts/init_db.py          # 启动并验证 pgembed
"""

import os
import sys
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))


async def init_via_pgmanager():
    """通过 PgManager 初始化数据库（嵌入式 pgembed）"""
    from src.tools.pg_manager import PgManager

    await PgManager.reset_instance()
    mgr = await PgManager.get_instance(force_local=True)
    print(f"[INFO] PG 后端: {mgr.backend.value}")
    print(f"[INFO] 连接 URI: {mgr.uri}")

    if not mgr.available:
        print("[WARN] PG 不可用，尝试启动...")
        await mgr.start()
        if not mgr.available:
            print("[FAIL] pgembed 不可用。请确认已安装: uv pip install pgembed")
            return False

    if not mgr._started:
        await mgr.start()

    # 验证连接
    health = await mgr.health()
    print(f"[INFO] 健康状态: {health.get('status')}")
    if health.get('pg_version'):
        print(f"[INFO] PG 版本: {health['pg_version']}")
    if health.get('extensions'):
        print(f"[INFO] 已安装扩展数: {health['extensions']}")

    # 验证 pgvector
    import asyncpg
    conn = await asyncpg.connect(mgr.uri)
    ver = await conn.fetchval(
        "SELECT extversion FROM pg_extension WHERE extname='vector'"
    )
    await conn.close()
    print(f"[INFO] pgvector 版本: {ver}")

    print("[OK] 数据库初始化完成！")
    return True


async def main():
    """主入口"""
    print("PgManager + pgembed 初始化...")
    return await init_via_pgmanager()


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
