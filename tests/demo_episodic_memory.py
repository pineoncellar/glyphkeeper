import asyncio
import sys
import os

# 将项目根目录添加到 python 路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.memory.episodic_memory import EpisodicMemory
from src.core.config import get_settings
from src.core.logger import get_logger

logger = get_logger("demo_episodic")

async def main():
    print("=== GlyphKeeper 情景记忆 (真实 LLM/DB 调用) 演示 ===")
    
    settings = get_settings()
    active_world = settings.project.active_world
    print(f"当前激活世界: {active_world}")
    print("注意: 这将消耗实际的 Token 并连接到数据库。")
    
    try:
        # 初始化情景记忆
        # 这将触发 RAGEngine 的初始化，包括 LightRAG 和数据库连接
        print("\n[1] 正在初始化情景记忆模块 (可能需要几秒钟)...")
        memory = EpisodicMemory()
        
        # 预热/检查 RAG 引擎是否就绪
        # 虽然 insert_game_event 会自动调用 get_rag_engine，但显式调用可以捕获初始化错误
        print("    -> 正在连接 RAG 引擎...")
        await memory.get_rag_engine()
        print("    -> RAG 引擎已就绪。")
        
        # 测试 1: 插入游戏事件
        print("\n[2] 正在测试事件插入 (写入向量库)...")
        event_text = "玩家 A 在书桌抽屉里发现了一本神秘的日记，上面记载着关于'星之彩'的传说。"
        tags = ["clue", "diary", "mythos", "color_out_of_space"]
        
        print(f"    -> 正在插入事件: '{event_text}'")
        print(f"    -> 标签: {tags}")
        
        success = await memory.insert_game_event(event_text, tags)
        
        if success:
            print("    成功: 事件已发送至 RAG 引擎。")
        else:
            print("    失败: 事件插入失败。")

        # 测试 2: 检索上下文
        print("\n[3] 正在测试上下文检索 (混合检索)...")
        query = "玩家发现了什么关于神话的线索？"
        context_tags = ["clue", "mythos"]
        
        print(f"    -> 正在查询: '{query}'")
        print(f"    -> 上下文标签: {context_tags}")
        
        # 注意：LightRAG 的索引可能不是实时的，或者需要显式提交/索引
        # 对于演示，我们尝试直接检索。如果 LightRAG 是异步索引的，可能查不到刚插入的数据。
        # 但通常 insert 操作在 LightRAG 中会处理嵌入。
        
        result = await memory.retrieve_context(query, context_tags)
        
        print(f"\n    检索结果:\n    {result}")
        
        if result:
            print("\n    成功: 上下文已检索。")
        else:
            print("\n    警告: 未检索到内容 (可能是因为数据尚未索引或匹配度不足)。")

    except Exception as e:
        print(f"\n[!] 演示过程中出错: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print("\n=== 演示完成 ===")

if __name__ == "__main__":
    asyncio.run(main())
