"""
命令行测试工具 - 艾德薇诗的冒险
用于测试 Narrator 的交互式功能
"""
import sys
import asyncio
import uuid
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.agents.narrator import Narrator
from src.agents.tools.schemas import NarratorInput
from src.memory.manager import MemoryManager
from src.core.logger import get_logger

logger = get_logger(__name__)


async def init_opening(narrator: Narrator, session_id: str, character_name: str):
    """初始化开场白并记录到系统"""
    opening_text = """你正站在密歇根州阿诺兹堡市的艾尔斯伯里大街上。

面前伫立着一栋门牌号为 218号 的小屋，外观看起来并不起眼，甚至透着几分孤寂。这里曾是道格拉斯·金博尔——那位离群索居的爱书人的住所，如今他的侄子托马斯住在这里。

尽管周围的街道看起来平静如常，但这栋房子最近刚刚遭遇了一起奇怪的非法入侵。委托人托马斯·金博尔此刻就在屋内等你,他声称家里遭了贼，但丢失的却仅仅是几本对他已失踪的叔叔而言至关重要的旧书。"""
    
    # 作为系统开场记录到记忆中
    await narrator.memory.add_dialogue("system", f"[开场] {opening_text}")
    
    # 再添加一条助手消息，模拟 Narrator 的开场叙述
    await narrator.memory.add_dialogue("assistant", opening_text)
    
    return opening_text


async def run_interactive_session():
    """运行交互式测试会话"""
    print("\n" + "=" * 70)
    print("  GlyphKeeper - 克苏鲁跑团测试工具")
    print("=" * 70)
    
    # 固定参数
    character_name = "艾德薇诗"
    # 使用固定的测试 session_id (UUID 格式)，避免每次随机生成
    session_id = "00000000-0000-0000-0000-000000000001"
    
    print(f"\n📋 会话信息:")
    print(f"  - 角色: {character_name}")
    print(f"  - 会话ID: {session_id}")
    
    try:
        # 初始化组件
        print("\n⚙️  正在初始化系统...")
        memory_manager = MemoryManager()
        narrator = Narrator(memory_manager)
        
        # 设置开场
        print("\n📖 正在加载开场...")
        opening = await init_opening(narrator, session_id, character_name)
        
        print("\n" + "-" * 70)
        print(opening)
        print("-" * 70)
        
        print("\n✅ 系统已就绪！")
        print("\n💡 提示:")
        print("  - 输入你的行动或对话")
        print("  - 输入 'quit' 或 'exit' 退出")
        print("  - 输入 'history' 查看对话历史")
        print("\n" + "=" * 70)
        
        # 主循环
        while True:
            try:
                # 获取用户输入
                user_input = input(f"\n[{character_name}] >>> ").strip()
                
                if not user_input:
                    continue
                
                # 特殊命令处理
                if user_input.lower() in ['quit', 'exit', 'q']:
                    print("\n👋 再见！愿你的理智值永存...")
                    break
                
                if user_input.lower() == 'history':
                    # 显示历史记录
                    history = await narrator.memory.get_recent_context(limit=20)
                    print("\n" + "=" * 70)
                    print("对话历史:")
                    print("-" * 70)
                    for record in history:
                        role_name = {
                            "system": "系统",
                            "user": "玩家",
                            "assistant": "守密人"
                        }.get(record.role, record.role)
                        print(f"[{role_name}] {record.content}\n")
                    print("=" * 70)
                    continue
                
                # 构建输入
                narrator_input = NarratorInput(
                    session_id=session_id,
                    character_name=character_name,
                    content=user_input,
                    type="action"
                )
                
                # 调用 Narrator
                print(f"\n[守密人] ", end="", flush=True)
                
                async for chunk in narrator.chat(narrator_input):
                    print(chunk, end="", flush=True)
                
                print()  # 换行
                
            except KeyboardInterrupt:
                print("\n\n⚠️  检测到中断信号...")
                confirm = input("确定要退出吗? (y/n): ").strip().lower()
                if confirm == 'y':
                    print("\n👋 再见！")
                    break
            except Exception as e:
                logger.error(f"处理输入时出错: {e}", exc_info=True)
                print(f"\n❌ 发生错误: {e}")
                print("系统将继续运行...\n")
    
    except Exception as e:
        logger.error(f"初始化失败: {e}", exc_info=True)
        print(f"\n❌ 初始化失败: {e}")
        return


def main():
    """主入口"""
    try:
        asyncio.run(run_interactive_session())
    except KeyboardInterrupt:
        print("\n\n程序已终止")
    except Exception as e:
        logger.error(f"程序异常退出: {e}", exc_info=True)
        print(f"\n❌ 程序异常: {e}")


if __name__ == "__main__":
    main()
