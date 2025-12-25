"""
LightRAG 使用示例
演示如何使用 GlyphKeeper 的 RAG 功能
"""
import asyncio
from pathlib import Path

# 添加项目根目录到路径
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.memory import get_rag_engine, quick_query
from src.ingestion import ingest_file, ingest_text
from src.agents import SearchAgent, search
from src.utils import print_token_stats


async def main():
    """主函数"""
    print("=" * 50)
    print("GlyphKeeper - LightRAG 示例")
    print("=" * 50)
    
    # 1. 初始化 RAG 引擎
    print("\n📦 初始化 RAG 引擎...")
    # 适配新版 API: 移除 environment 参数
    engine = await get_rag_engine(llm_tier="standard")
    print(f"   ✅ 引擎已初始化: {engine.is_initialized}")
    
    # 2. 摄入示例文本
    print("\n📥 摄入示例文本...")
    sample_text = """
    龙与地下城（Dungeons & Dragons，简称D&D）是一款奇幻角色扮演游戏。
    
    游戏中有六大基本属性：
    - 力量（Strength）: 影响近战攻击和伤害
    - 敏捷（Dexterity）: 影响先攻、AC和远程攻击
    - 体质（Constitution）: 影响生命值和专注检定
    - 智力（Intelligence）: 影响法师施法和知识技能
    - 感知（Wisdom）: 影响牧师施法和察觉技能
    - 魅力（Charisma）: 影响社交技能和部分施法职业
    
    常见职业包括：战士、法师、牧师、盗贼、游侠、圣武士等。
    每个职业都有独特的能力和游戏风格。
    """
    
    success = await ingest_text(sample_text)
    print(f"   ✅ 文本摄入: {'成功' if success else '失败'}")
    
    # 3. 执行查询
    print("\n🔍 执行查询测试...")
    
    # 使用 SearchAgent
    agent = SearchAgent()
    
    questions = [
        "D&D 游戏中有哪些基本属性？",
        "敏捷属性有什么作用？",
    ]
    
    for q in questions:
        print(f"\n   问题: {q}")
        result = await agent.query(q, mode="hybrid")
        if result and result.answer:
            print(f"   答案: {result.answer[:200]}..." if len(result.answer) > 200 else f"   答案: {result.answer}")
        else:
            print(f"   答案: (查询失败或无结果)")
    
    # 4. 使用便捷函数
    print("\n🚀 使用快速查询...")
    answer = await quick_query("什么是 D&D？")
    if answer:
        print(f"   答案: {answer[:200]}..." if len(answer) > 200 else f"   答案: {answer}")
    else:
        print(f"   答案: (查询失败或无结果)")
    
    # 5. 打印 Token 统计
    print("\n📊 Token 使用统计:")
    print_token_stats()
    
    # 6. 关闭引擎
    print("\n🔒 关闭 RAG 引擎...")
    await engine.close()
    print("   ✅ 引擎已关闭")
    
    print("\n" + "=" * 50)
    print("示例运行完成！")
    print("=" * 50)


if __name__ == "__main__":
    asyncio.run(main())
