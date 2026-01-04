"""
规则查询服务使用示例
演示如何使用独立的规则数据库和 LightRAG 实例
"""
import asyncio
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.memory import get_rule_service


async def demo_rule_query():
    """演示规则查询"""
    print("=" * 60)
    print("规则查询服务示例")
    print("=" * 60)
    
    # 1. 获取规则服务实例
    rule_service = get_rule_service()
    
    # 2. 健康检查
    print("\n【1】健康检查...")
    health = await rule_service.health_check()
    print(f"状态: {health}")
    
    # 3. 查询规则
    print("\n【2】查询 COC7th 规则...")
    questions = [
        "什么是理智值检定？",
        "战斗轮如何进行？",
        "技能检定的难度等级有哪些？"
    ]
    
    for question in questions:
        print(f"\n问题: {question}")
        try:
            answer = await rule_service.query_rule(
                question, 
                mode="hybrid",
                top_k=3
            )
            print(f"答案:\n{answer}\n")
        except Exception as e:
            print(f"查询失败: {e}")
    
    print("\n" + "=" * 60)


async def demo_insert_rule():
    """演示插入规则文档"""
    print("=" * 60)
    print("规则文档插入示例")
    print("=" * 60)
    
    rule_service = get_rule_service()
    
    # 示例：插入一段规则文本
    rule_content = """
    理智值检定 (Sanity Check)
    
    当调查员遭遇超自然恐怖时，需要进行理智值检定。
    
    检定方法：
    1. 投掷 1D100
    2. 如果结果小于等于当前理智值，检定成功
    3. 检定失败时，根据遭遇的恐怖程度损失理智值
    
    理智损失格式通常为 0/1D6，表示：
    - 检定成功损失 0 点
    - 检定失败损失 1D6 点
    
    当理智值归零时，调查员会陷入疯狂。
    """
    
    print("\n插入规则文档...")
    try:
        await rule_service.insert_rule_document(rule_content, doc_id="sanity_check_rule")
        print("✓ 文档插入成功")
    except Exception as e:
        print(f"✗ 文档插入失败: {e}")
    
    print("\n" + "=" * 60)


async def demo_comparison():
    """演示世界数据 vs 规则数据的隔离"""
    print("=" * 60)
    print("数据隔离演示")
    print("=" * 60)
    
    # 世界数据 (使用原有的 RAG)
    from src.memory import get_rag_engine
    world_rag = await get_rag_engine()  # 需要 await
    
    # 规则数据 (使用新的规则服务)
    rule_service = get_rule_service()
    
    question = "调查员如何进行技能检定？"
    
    print(f"\n问题: {question}\n")
    
    print("【世界数据】查询 (world_{active_world} schema):")
    try:
        world_answer = await world_rag.query(question, mode="hybrid")
        print(f"答案: {world_answer}\n")
    except Exception as e:
        print(f"查询失败: {e}\n")
    
    print("【规则数据】查询 (coc7th_rules schema):")
    try:
        rule_answer = await rule_service.query_rule(question)
        print(f"答案: {rule_answer}\n")
    except Exception as e:
        print(f"查询失败: {e}\n")
    
    print("两个数据源完全独立，互不影响！")
    print("=" * 60)


async def main():
    """主函数：在同一个 event loop 中运行所有演示"""
    # 依次运行各个演示
    # await demo_rule_query()
    # await demo_insert_rule()
    await demo_comparison()


if __name__ == "__main__":
    print("""
    使用前准备:
    1. 运行 python scripts/init_rules_db.py 初始化规则 schema
    2. 准备 COC7th 规则文档 (PDF/TXT/JSON)
    3. 使用 scripts/ingest_rules.py 导入规则数据
    
    运行示例：
    - demo_rule_query(): 查询规则
    - demo_insert_rule(): 插入规则文档
    - demo_comparison(): 演示数据隔离
    """)
    
    # 在同一个 event loop 中运行所有演示（避免 event loop 错误）
    asyncio.run(main())
