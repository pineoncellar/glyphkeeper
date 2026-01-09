"""
演示自动开场功能

展示如何使用 Narrator.start_game() 方法启动游戏
"""
import asyncio
import sys
import os
from uuid import UUID

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.agents import Narrator
from src.memory import MemoryManager
from src.core import get_logger

logger = get_logger(__name__)


async def demo_opening():
    """演示开场流程"""
    
    print("=" * 60)
    print("GlyphKeeper 自动开场演示")
    print("=" * 60)
    print()
    
    try:

        # 2. 初始化 Narrator
        print("初始化叙事引擎...")
        narrator = Narrator("123456789")
        print("✓ 叙事引擎就绪")
        print()
        
        # 4. 启动开场
        print("生成开场白...")
        print("-" * 60)
        print()
        
        async for chunk in narrator.start_game(

        ):
            print(chunk, end="", flush=True)
        
        print()
        print()
        print("-" * 60)
        print("✓ 开场完成！")
        print()
        
        
    except Exception as e:
        logger.error(f"演示失败: {e}", exc_info=True)



if __name__ == "__main__":
    asyncio.run(demo_opening())
