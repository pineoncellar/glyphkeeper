import asyncio
import os
import sys

# 确保路径正确
sys.path.append(os.getcwd())

# 导入所有组件
from src.memory.database import db_manager, init_db
from src.agents.narrator import Narrator
from src.memory.manager import MemoryManager
from src.memory.repositories import LocationRepository, EntityRepository
from src.core import get_settings

# 使用项目配置
settings = get_settings()

async def init_world(session):
    """初始化最简单的世界数据"""
    loc_repo = LocationRepository(session)
    ent_repo = EntityRepository(session)
    
    # 检查是否已初始化
    if await loc_repo.get_by_key("loc_study"):
        print("🌍 世界已存在，跳过初始化。")
        return await ent_repo.get_by_key("player_01")

    print("🌍 初始化世界...")
    study = await loc_repo.create(
        key="loc_study", name="古旧书房",
        base_desc="一间充满霉味的书房，窗外雷雨交加。桌上放着一封未寄出的信。",
        tags=["indoor"],
        exits={"North": "loc_hallway"}
    )
    hallway = await loc_repo.create(
        key="loc_hallway", name="幽暗走廊",
        base_desc="长长的走廊，两侧挂着祖先的画像，它们的眼睛似乎盯着你。",
        tags=["indoor"],
        exits={"South": "loc_study"}
    )
    player = await ent_repo.create(
        key="player_01", name="调查员",
        location_id=study.id,
        stats={"hp": 10, "san": 60}
    )
    return player

async def main():
    print("🌍 正在初始化数据库...")
    
    # 1. 初始化数据库（自动创建 schema 和表）
    await init_db()
    
    # 2. 初始化世界
    async with db_manager.session_factory() as session:
        player = await init_world(session)
        await session.commit()
        player_id = player.id

    # 3. 初始化 MemoryManager
    memory_manager = MemoryManager(investigator_id=player_id)
    
    # 4. 初始化 Narrator（自动创建 Archivist 和获取 LLM）
    narrator = Narrator(memory_manager)

    print("\n" + "="*40)
    print("🕯️  GlyphKeeper: The Awakening 🕯️")
    print("="*40)
    print("系统就绪。输入 'quit' 退出。\n")

    while True:
        try:
            user_input = input("\n👤 You: ").strip()
            if not user_input: continue
            if user_input.lower() in ["quit", "exit"]: break

            print("\n🎲 KP: ", end="")
            
            # 流式接收 Narrator 的输出
            async for chunk in narrator.chat(user_input):
                print(chunk, end="", flush=True)
            
            print("")  # 换行

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"\n❌ Error: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n👋 再见！")