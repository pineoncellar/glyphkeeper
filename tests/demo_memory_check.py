import asyncio
import sys
import os

# 将项目根目录添加到 python 路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.memory.database import DatabaseManager, Base, init_db
from src.memory.repositories.location_repo import LocationRepository
from src.memory.repositories.entity_repo import EntityRepository
from src.memory.repositories.knowledge_repo import KnowledgeRepository
from src.memory.repositories.interactable_repo import InteractableRepository
from src.memory.repositories.clue_discovery_repo import ClueDiscoveryRepository
from src.memory.models import Location, Entity, Knowledge, SourceType
from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger("demo_memory")

async def main():
    print("=== GlyphKeeper 记忆模块演示 ===")
    
    settings = get_settings()
    active_world = settings.project.active_world
    print(f"当前激活世界: {active_world}")
    print(f"对应 Schema: world_{active_world}")

    # 1. 初始化数据库管理器
    print("\n[1] 正在初始化数据库连接...")
    db_manager = DatabaseManager()
    
    try:
        # 2. 创建表 (确保架构存在)
        print("[2] 正在初始化数据库表 (Schema + Tables)...")
        await init_db()
        print("表结构初始化成功。")

        # 3. 执行操作
        print("\n[3] 正在测试仓库操作...")
        async with db_manager.session_factory() as session:
            # 初始化仓库
            loc_repo = LocationRepository(session)
            entity_repo = EntityRepository(session)
            knowledge_repo = KnowledgeRepository(session)
            interactable_repo = InteractableRepository(session)
            clue_discovery_repo = ClueDiscoveryRepository(session)

            # 创建一个地点
            print("    -> 正在创建地点: '阿卡姆疯人院'")
            asylum = await loc_repo.create(
                name="阿卡姆疯人院",
                base_desc="城镇郊区一座黑暗而不祥的建筑。",
                tags=["HORRIBLE", "MEDICAL"],
                exits={"north": "阿卡姆森林"}
            )
            print(f"       已创建地点 ID: {asylum.id}")

            # 创建一个实体
            print("    -> 正在创建实体: '阿米蒂奇博士'")
            doctor = await entity_repo.create(
                name="阿米蒂奇博士",
                tags=["NPC", "SCHOLAR"],
                stats={"sanity": 70, "knowledge": 90}
            )
            print(f"       已创建实体 ID: {doctor.id}")

            # 将实体移动到地点
            print("    -> 正在将阿米蒂奇博士移动到阿卡姆疯人院")
            await entity_repo.update_location(doctor.id, asylum.id)
            
            # 验证数据
            print("\n[4] 正在验证数据持久化...")
            # 重新获取加载了位置的实体
            # 注意：在实际应用中，我们可能需要处理延迟加载或急切加载选项
            # 对于此演示，我们将再次按 ID 获取
            
            fetched_doctor = await entity_repo.get_by_id(doctor.id)
            fetched_location = await loc_repo.get_by_id(asylum.id)
            
            print(f"    实体: {fetched_doctor.name}")
            print(f"    位置 ID: {fetched_doctor.location_id}")
            print(f"    预期位置 ID: {asylum.id}")
            
            if fetched_doctor.location_id == asylum.id:
                print("    成功: 实体位置正确。")
            else:
                print("    失败: 实体位置不匹配。")

            # 测试标签
            print("\n[5] 正在测试标签操作...")
            await loc_repo.add_tag(asylum.id, "dangerous")
            updated_loc = await loc_repo.get_by_id(asylum.id)
            print(f"    地点标签: {updated_loc.tags}")
            
            if "dangerous" in updated_loc.tags:
                print("    成功: 标签已添加。")
            else:
                print("    失败: 标签未添加。")

            # 测试知识/事实 (Facts/Knowledge)
            print("\n[6] 正在测试知识(Facts)操作...")
            
            # 1. 创建一条知识 (例如：日记内容的引用)
            print("    -> 正在创建知识: '日记秘密'")
            diary_clue = await knowledge_repo.create(
                rag_key="diary_entry_001",
                tags_granted=["secret_revealed"]
            )
            print(f"       已创建知识 ID: {diary_clue.id}")

            # 2. 创建一个关联该知识的交互物 (例如：旧日记本)
            print("    -> 正在创建交互物: '旧日记本'")
            diary_item = await interactable_repo.create(
                name="旧日记本",
                location_id=asylum.id,
                tags=["item", "readable"]
            )
            print(f"       已创建交互物 ID: {diary_item.id}")

            # 3. 创建线索发现记录 (连接知识与交互物)
            print("    -> 正在创建线索发现记录 (连接知识与交互物)")
            discovery = await clue_discovery_repo.create(
                knowledge_id=diary_clue.id,
                interactable_id=diary_item.id,
                discovery_flavor_text="你翻开日记，泛黄的纸页上记录着疯狂的呓语...",
                required_check={"skill": "Library Use", "difficulty": 50}
            )
            print(f"       已创建线索发现 ID: {discovery.id}")

            # 4. 验证关联
            fetched_discoveries = await clue_discovery_repo.get_by_interactable(diary_item.id)
            if any(d.knowledge_id == diary_clue.id for d in fetched_discoveries):
                print("    成功: 交互物正确关联了知识 (通过 ClueDiscovery)。")
            else:
                print("    失败: 交互物关联知识失败。")

    except Exception as e:
        print(f"\n[!] 演示过程中出错: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # 清理 (可选: 如果需要，可以删除表或数据，但现在我们保留它)
        await db_manager.engine.dispose()
        print("\n=== 演示完成 ===")

if __name__ == "__main__":
    asyncio.run(main())
