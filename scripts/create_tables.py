import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 python path
sys.path.append(str(Path(__file__).parent.parent))

from src.core import get_logger
from src.memory.database import init_db

logger = get_logger(__name__)

async def create_all_tables():
    """创建所有数据库表"""
    logger.info("开始创建数据库表...")
    
    # 使用 database.py 中的 init_db 函数
    await init_db()
    
    logger.info("数据库表创建完成！")

if __name__ == "__main__":
    asyncio.run(create_all_tables())
