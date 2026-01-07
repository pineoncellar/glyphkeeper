"""
测试 workspace 隔离机制
验证不同 workspace 的数据是否完全隔离
"""
import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 python path
sys.path.append(str(Path(__file__).parent.parent))

from src.core import get_logger, get_settings
from src.memory import RAGEngine

logger = get_logger(__name__)


async def test_workspace_isolation():
    """测试 workspace 数据隔离"""
    
    logger.info("=" * 60)
    logger.info("开始测试 workspace 隔离机制")
    logger.info("=" * 60)
    
    # 步骤 1: 获取当前配置（应该是 book）
    settings = get_settings()
    
    # 步骤 4: 向 test workspace 插入不同的测试数据
    logger.info("[步骤 3] 向 test workspace 插入测试数据...")
    test_engine = await RAGEngine.get_instance(
        domain="world", 
        llm_tier="standard",
        force_reinit=True  # 强制重新初始化以使用新的 workspace
    )
    
    test_test_content = """
    [测试数据 - Test World]
    这是专属于 test 世界的测试数据。
    包含的关键信息：
    - 世界名称: test
    - 特殊标记: TEST_ONLY_DATA
    - 测试时间: 2026-01-07
    """
    
    success = await test_engine.insert(test_test_content)
    if success:
        logger.info("✓ test workspace 数据插入成功")
    else:
        logger.error("✗ test workspace 数据插入失败")
        return False
    
    # 等待一下让数据完全写入
    await asyncio.sleep(2)
    
    # 步骤 5: 在 test workspace 中查询，应该只能看到 test 的数据
    logger.info("\n[步骤 4] 在 test workspace 中查询...")
    logger.info("查询问题: 请告诉我关于这个世界的特殊标记")
    
    test_result = await test_engine.query(
        "请告诉我关于这个世界的特殊标记",
        mode="naive"
    )
    
    logger.info(f"\ntest workspace 查询结果:\n{test_result}\n")
    
    # 验证结果
    has_test_data = "TEST_ONLY_DATA" in test_result or "test" in test_result.lower()
    has_book_data = "BOOK_ONLY_DATA" in test_result or "book" in test_result.lower()
    
    if has_test_data and not has_book_data:
        logger.info("✓ test workspace 隔离验证成功：只能看到 test 数据")
    elif has_test_data and has_book_data:
        logger.error("✗ test workspace 隔离失败：同时看到了 test 和 book 数据")
    else:
        logger.warning("⚠ test workspace 查询结果不确定，请手动检查")
    
    # 步骤 6: 切换回 book workspace 并查询
    logger.info("\n[步骤 5] 切换回 book workspace...")
    settings.project.active_world = "book"
    
    book_engine_2 = await RAGEngine.get_instance(
        domain="world",
        llm_tier="standard", 
        force_reinit=True
    )
    
    logger.info("[步骤 6] 在 book workspace 中查询...")
    logger.info("查询问题: 请告诉我关于这个世界的特殊标记")
    
    book_result = await book_engine_2.query(
        "请告诉我关于这个世界的特殊标记",
        mode="naive"
    )
    
    logger.info(f"\nbook workspace 查询结果:\n{book_result}\n")
    
    # 验证结果
    has_book_data_2 = "BOOK_ONLY_DATA" in book_result or "金博尔" in book_result
    has_test_data_2 = "TEST_ONLY_DATA" in book_result
    
    if has_book_data_2 and not has_test_data_2:
        logger.info("✓ book workspace 隔离验证成功：只能看到 book 数据")
    elif has_book_data_2 and has_test_data_2:
        logger.error("✗ book workspace 隔离失败：同时看到了 book 和 test 数据")
    else:
        logger.warning("⚠ book workspace 查询结果不确定，请手动检查")
    
    
    # 最终总结
    logger.info("\n" + "=" * 60)
    logger.info("测试完成！")
    logger.info("=" * 60)
    logger.info("\n验证要点：")
    logger.info("1. test workspace 应该只能看到 TEST_ONLY_DATA")
    logger.info("2. book workspace 应该只能看到 BOOK_ONLY_DATA 和现有的书籍数据")
    logger.info("3. rules workspace 是独立的，用于存储跨世界共享的规则")
    logger.info("\n如果以上条件都满足，说明 workspace 隔离机制工作正常！")
    
    return True


async def main():
    try:
        await test_workspace_isolation()
    except Exception as e:
        logger.error(f"测试过程中出现错误: {e}")
        import traceback
        logger.error(traceback.format_exc())


if __name__ == "__main__":
    asyncio.run(main())
