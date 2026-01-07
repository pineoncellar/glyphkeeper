"""
初始化游戏会话脚本
为测试环境创建默认的 GameSession 数据
"""
import sys
import asyncio
from pathlib import Path
from uuid import UUID

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.memory.database import db_manager
from src.memory.models import GameSession, TimeSlot, Entity
from src.memory.repositories.session_repo import SessionRepository
from src.memory.repositories.entity_repo import EntityRepository
from src.core.logger import get_logger

logger = get_logger(__name__)

# 固定的测试 session ID（与 cli_runner.py 保持一致）
# UUID 格式: 00000000-0000-0000-0000-000000000001
TEST_SESSION_ID = "00000000-0000-0000-0000-000000000001"
TEST_SESSION_UUID = UUID(TEST_SESSION_ID)


async def init_default_session():
    """初始化默认的游戏会话"""
    
    async with db_manager.session_factory() as session:
        session_repo = SessionRepository(session)
        entity_repo = EntityRepository(session)
        
        # 1. 检查 session 是否已存在
        existing_session = await session_repo.get_by_id(TEST_SESSION_UUID)
        if existing_session:
            logger.info(f"会话已存在: {TEST_SESSION_ID}")
            print(f"✅ 会话已存在: {TEST_SESSION_ID}")
            print(f"   - 时间段: {existing_session.time_slot.value}")
            print(f"   - 节拍数: {existing_session.beat_counter}")
            print(f"   - 全局标签: {existing_session.active_global_tags}")
            print(f"   - 调查员数: {len(existing_session.investigator_ids)}")
            
            # 询问是否重置
            response = input("\n是否重置该会话? (y/n): ").strip().lower()
            if response != 'y':
                print("已取消操作。")
                return
            
            # 删除现有 session
            await session.delete(existing_session)
            await session.commit()
            logger.info("已删除旧会话")
        
        # 2. 查找艾德薇诗
        edelweiss = await entity_repo.get_by_name("艾德薇诗")
        investigator_ids = []
        
        if edelweiss:
            investigator_ids = [str(edelweiss.id)]
            logger.info(f"找到调查员: 艾德薇诗 (ID: {edelweiss.id})")
            print(f"\n📋 找到调查员: 艾德薇诗")
            print(f"   - Entity ID: {edelweiss.id}")
            print(f"   - 当前位置: {edelweiss.location_id}")
        else:
            logger.warning("未找到艾德薇诗，将创建空会话")
            print("\n⚠️  警告: 未找到调查员'艾德薇诗'")
            print("   会话将创建，但不包含调查员")
        
        # 3. 创建新的 GameSession
        new_session = GameSession(
            id=TEST_SESSION_UUID,
            time_slot=TimeSlot.MORNING,
            beat_counter=0,
            active_global_tags=[],
            investigator_ids=investigator_ids
        )
        
        session.add(new_session)
        await session.commit()
        await session.refresh(new_session)
        
        logger.info(f"成功创建会话: {TEST_SESSION_ID}")
        print(f"\n✅ 成功创建游戏会话!")
        print(f"\n会话详情:")
        print(f"  - Session ID: {TEST_SESSION_ID}")
        print(f"  - UUID: {new_session.id}")
        print(f"  - 时间段: {new_session.time_slot.value}")
        print(f"  - 节拍数: {new_session.beat_counter}")
        print(f"  - 全局标签: {new_session.active_global_tags}")
        print(f"  - 调查员列表: {new_session.investigator_ids}")
        
        return new_session


async def show_all_sessions():
    """显示所有会话"""
    async with db_manager.session_factory() as session:
        from sqlalchemy import select
        stmt = select(GameSession)
        result = await session.execute(stmt)
        sessions = result.scalars().all()
        
        if not sessions:
            print("\n📭 数据库中没有任何会话")
            return
        
        print(f"\n📋 数据库中的所有会话 (共 {len(sessions)} 个):")
        print("-" * 70)
        for gs in sessions:
            print(f"ID: {gs.id}")
            print(f"  - 时间段: {gs.time_slot.value}")
            print(f"  - 节拍数: {gs.beat_counter}")
            print(f"  - 调查员数: {len(gs.investigator_ids)}")
            print(f"  - 标签: {gs.active_global_tags}")
            print()


async def delete_session_by_id(session_id: str):
    """删除指定会话"""
    try:
        session_uuid = UUID(session_id)
    except ValueError:
        print(f"❌ 无效的 UUID: {session_id}")
        return
    
    async with db_manager.session_factory() as session:
        session_repo = SessionRepository(session)
        existing = await session_repo.get_by_id(session_uuid)
        
        if not existing:
            print(f"❌ 会话不存在: {session_id}")
            return
        
        await session.delete(existing)
        await session.commit()
        print(f"✅ 已删除会话: {session_id}")


def main():
    """主函数"""
    import argparse
    
    parser = argparse.ArgumentParser(description="游戏会话管理工具")
    parser.add_argument(
        "action",
        choices=["init", "list", "delete"],
        help="操作类型: init=初始化默认会话, list=列出所有会话, delete=删除指定会话"
    )
    parser.add_argument(
        "--id",
        type=str,
        help="要删除的会话 UUID (用于 delete 操作)"
    )
    
    args = parser.parse_args()
    
    print("\n" + "=" * 70)
    print("  GlyphKeeper - 游戏会话管理工具")
    print("=" * 70)
    
    if args.action == "init":
        asyncio.run(init_default_session())
    elif args.action == "list":
        asyncio.run(show_all_sessions())
    elif args.action == "delete":
        if not args.id:
            print("❌ 错误: 删除操作需要指定 --id 参数")
            return
        asyncio.run(delete_session_by_id(args.id))
    
    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
