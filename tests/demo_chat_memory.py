import asyncio
import sys
import os
import uuid

# 将项目根目录添加到 python 路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.memory.manager import MemoryManager
from src.memory.strategies import TokenCountStrategy
from src.memory.models import DialogueRecord, MemoryTrace
from src.memory.database import db_manager, Base
from src.core.config import get_settings
from sqlalchemy import select, func

async def main():
    print("=== GlyphKeeper 聊天记忆模块演示 ===")
    
    # 1. 初始化 DB 表 (确保表存在)
    print("\n[1] 初始化数据库表...")
    # 注意：在生产环境中，表结构通常由 alembic 管理
    # 这里为了演示方便，直接使用 create_all
    async with db_manager.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("    -> 表结构已就绪")

    # 2. 初始化 MemoryManager
    investigator_id = uuid.uuid4()
    print(f"\n[2] 初始化 MemoryManager (Investigator ID: {investigator_id})...")
    manager = MemoryManager(investigator_id=investigator_id)
    
    # 3. 调整策略：为了演示，将 Token 阈值设得很低 (例如 50 tokens)
    # 这样几句话就能触发固化
    print("    -> 调整固化策略: Max Tokens = 50")
    manager.strategies = [TokenCountStrategy(max_tokens=50)]

    # 4. 模拟对话
    dialogues = [
        ("user", "我们进入了这间废弃的宅邸，空气中弥漫着霉味。"),
        ("assistant", "宅邸的大厅显得空旷而阴森，墙上的肖像画似乎在注视着你们。"),
        ("user", "我检查一下地上的脚印。"),
        ("assistant", "你发现了一些泥泞的脚印，通向二楼的楼梯。"),
        ("user", "好的，我们小心翼翼地走上楼梯。"),
        ("assistant", "楼梯发出嘎吱嘎吱的响声，二楼的走廊一片漆黑。"),
        ("user", "我点亮手里的提灯。"),
        ("assistant", "提灯的光芒照亮了走廊，你看到尽头有一扇半掩的门。"),
    ]

    print(f"\n[3] 开始模拟对话 ({len(dialogues)} 轮)...")
    
    for i, (role, content) in enumerate(dialogues):
        print(f"    Turn {i+1}: [{role}] {content[:20]}...")
        await manager.add_dialogue(role, content)
        
        # 简单的延时，模拟真实交互
        await asyncio.sleep(0.5)

    # 5. 检查数据库状态
    print("\n[4] 检查记忆状态...")
    async with db_manager.session_factory() as session:
        # 统计总记录数
        # 注意：这里统计的是全表的，可能会包含之前的测试数据
        # 严谨起见应该 filter by investigator_id，但演示环境可能无所谓
        stmt_count = select(func.count()).select_from(DialogueRecord)
        total_records = (await session.execute(stmt_count)).scalar()
        
        # 统计已固化记录数
        stmt_consolidated = select(func.count()).select_from(DialogueRecord).where(DialogueRecord.is_consolidated == True)
        consolidated_records = (await session.execute(stmt_consolidated)).scalar()
        
        # 查看生成的 MemoryTrace
        stmt_traces = select(MemoryTrace).order_by(MemoryTrace.created_at.desc()).limit(5)
        traces = (await session.execute(stmt_traces)).scalars().all()
        
        print(f"    -> 总对话记录 (全表): {total_records}")
        print(f"    -> 已固化记录 (全表): {consolidated_records}")
        print(f"    -> 最近生成的记忆摘要 (MemoryTrace): {len(traces)} 条")
        
        for trace in traces:
            print(f"       - Trace [{trace.start_turn}-{trace.end_turn}]: {trace.summary[:50]}...")

    # 6. 测试上下文构建
    print("\n[5] 测试 Prompt Context 构建...")
    query = "我们在二楼看到了什么？"
    print(f"    -> Query: {query}")
    
    context = await manager.build_prompt_context(query)
    
    print("\n--- Generated Context Start ---")
    print(context)
    print("--- Generated Context End ---")

if __name__ == "__main__":
    asyncio.run(main())