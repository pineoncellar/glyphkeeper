"""
世界管理工具演示脚本

演示如何使用 DatabaseInitializer、WorldManager 和 WorldBackupRestore
"""

import asyncio
import sys
import os
from pathlib import Path

# 添加项目根目录到 Python 路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils import DatabaseInitializer, WorldManager, WorldBackupRestore
from src.core import get_logger

logger = get_logger("demo_world_manager")


async def demo_database_init():
    """演示 1: 数据库初始化"""
    logger.info("=" * 60)
    logger.info("演示 1: 数据库初始化")
    logger.info("=" * 60)

    initializer = DatabaseInitializer()
    success = initializer.init_database()

    if success:
        logger.info("✓ 数据库初始化成功")
    else:
        logger.error("✗ 数据库初始化失败")

    logger.info("")


async def demo_create_world():
    """演示 2: 创建世界"""
    logger.info("=" * 60)
    logger.info("演示 2: 创建世界")
    logger.info("=" * 60)

    manager = WorldManager()

    # 创建多个世界
    worlds = ["book"]

    for world_name in worlds:
        logger.info(f"\n创建世界: {world_name}")
        success = await manager.create_world(world_name)

        if success:
            logger.info(f"✓ 世界 '{world_name}' 创建成功")
        else:
            logger.warning(f"⚠ 世界 '{world_name}' 创建失败或已存在")

    logger.info("")


async def demo_backup_world():
    """演示 3: 备份世界"""
    logger.info("=" * 60)
    logger.info("演示 3: 备份世界")
    logger.info("=" * 60)

    br = WorldBackupRestore()

    # 备份刚创建的世界
    backup_path = await br.backup_world(
        world_name="book", remark="更新表"
    )

    if backup_path:
        logger.info(f"✓ 备份成功")
        logger.info(f"  文件: {backup_path}")
        logger.info(f"  大小: {backup_path.stat().st_size / 1024:.2f} KB")
    else:
        logger.error("✗ 备份失败")

    logger.info("")


async def demo_list_backups():
    """演示 4: 列出备份"""
    logger.info("=" * 60)
    logger.info("演示 4: 列出备份")
    logger.info("=" * 60)

    br = WorldBackupRestore()

    # 列出所有备份
    backups = await br.list_backups()

    if backups:
        logger.info(f"\n共 {len(backups)} 个备份:")
        logger.info("-" * 80)

        for i, backup in enumerate(backups, 1):
            logger.info(f"\n{i}. {backup['file']}")
            logger.info(f"   世界: {backup['world']}")
            logger.info(f"   时间: {backup['created']}")
            logger.info(f"   大小: {backup['size'] / 1024:.2f} KB")
            logger.info(f"   备注: {backup['remark']}")

        logger.info("\n" + "-" * 80)

        # 按世界过滤
        adventure_backups = await br.list_backups(world_filter="book")
        logger.info(f"\naventure_01 有 {len(adventure_backups)} 个备份")

    else:
        logger.info("✓ 没有备份")

    logger.info("")


async def demo_restore_world():
    """演示 5: 恢复世界"""
    logger.info("=" * 60)
    logger.info("演示 5: 恢复世界")
    logger.info("=" * 60)

    br = WorldBackupRestore()

    # 获取最新的备份
    backups = await br.list_backups(world_filter="book")

    if backups:
        latest_backup = backups[0]
        backup_file = f"data/backups/{latest_backup['file']}"

        logger.info(f"恢复最新备份: {latest_backup['file']}")
        logger.info(f"备注: {latest_backup['remark']}")

        success = await br.restore_world(
            world_name="book", backup_file=backup_file, overwrite=True
        )

        if success:
            logger.info("✓ 恢复成功")
        else:
            logger.error("✗ 恢复失败")

    else:
        logger.warning("⚠ 没有找到备份")

    logger.info("")


async def demo_delete_world():
    """演示 6: 删除世界"""
    logger.info("=" * 60)
    logger.info("演示 6: 删除世界")
    logger.info("=" * 60)

    manager = WorldManager()

    # 删除演示创建的世界
    world_to_delete = "book"

    logger.info(f"删除世界: {world_to_delete}")
    success = await manager.delete_world(world_to_delete, force=True)

    if success:
        logger.info(f"✓ 世界 '{world_to_delete}' 已删除")
    else:
        logger.warning(f"⚠ 删除世界失败")

    logger.info("")


async def demo_complete_workflow():
    """完整工作流演示"""
    logger.info("=" * 60)
    logger.info("完整工作流: 初始化 → 创建 → 备份 → 恢复")
    logger.info("=" * 60)

    manager = WorldManager()
    br = WorldBackupRestore()

    test_world = "demo_world"

    try:
        # 1. 创建世界
        logger.info("\n1️⃣ 创建世界...")
        if not await manager.create_world(test_world):
            logger.error("创建失败")
            return

        # 2. 备份 (初始状态)
        logger.info("\n2️⃣ 备份初始状态...")
        backup1 = await br.backup_world(test_world, remark="版本 1.0")
        if not backup1:
            logger.error("备份 1 失败")
            return

        logger.info(f"✓ 备份 1: {backup1.name}")

        # 3. 模拟做一些操作 (在实际应用中会修改数据库)
        logger.info("\n3️⃣ 模拟修改数据...")
        logger.info("  (实际应用中会有真实的数据库操作)")

        # 4. 备份 (修改后的状态)
        logger.info("\n4️⃣ 备份修改后的状态...")
        backup2 = await br.backup_world(test_world, remark="版本 2.0 - 添加了新内容")
        if not backup2:
            logger.error("备份 2 失败")
            return

        logger.info(f"✓ 备份 2: {backup2.name}")

        # 5. 列出备份
        logger.info("\n5️⃣ 列出所有备份...")
        backups = await br.list_backups(world_filter=test_world)
        logger.info(f"共 {len(backups)} 个备份")
        for backup in backups:
            logger.info(f"  - {backup['file']} ({backup['remark']})")

        # 6. 恢复到版本 1.0
        logger.info("\n6️⃣ 恢复到版本 1.0...")
        success = await br.restore_world(
            test_world, str(backup1), overwrite=True
        )
        if success:
            logger.info("✓ 已恢复到版本 1.0")
        else:
            logger.error("恢复失败")

        # 7. 清理 (删除测试世界)
        logger.info("\n7️⃣ 清理环境...")
        if await manager.delete_world(test_world, force=True):
            logger.info(f"✓ 世界 '{test_world}' 已删除")

    except Exception as e:
        logger.error(f"工作流异常: {e}")

    logger.info("\n✓ 完整工作流演示完成！")
    logger.info("")


async def main():
    """运行所有演示"""
    logger.info("\n")
    logger.info("🎮 GlyphKeeper 世界管理工具演示")
    logger.info("=" * 80)
    logger.info("")

    # 取消注释下面的演示代码来运行

    # 演示 1: 数据库初始化 (通常只需要运行一次)
    # await demo_database_init()

    # 演示 2: 创建世界
    # await demo_create_world()

    # 演示 3: 备份世界
    # await demo_backup_world()

    # # 演示 4: 列出备份
    # await demo_list_backups()

    # # 演示 5: 恢复世界
    await demo_restore_world()

    # 演示 6: 删除世界
    # await demo_delete_world()

    # 完整工作流
    # await demo_complete_workflow()

    logger.info("=" * 80)
    logger.info("所有演示完成！")
    logger.info("")


if __name__ == "__main__":
    asyncio.run(main())
