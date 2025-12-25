"""
测试 Token Tracker 的配置加载功能
"""

# 添加项目根目录到路径
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.core.config import get_settings
from src.utils.token_tracker import TokenTracker

def test_config_loading():
    """测试配置加载"""
    print("=" * 50)
    print("测试配置加载")
    print("=" * 50)
    
    settings = get_settings()
    
    print(f"\n项目名称: {settings.PROJECT_NAME}")
    print(f"调试模式: {settings.DEBUG}")
    print(f"成本追踪: {settings.MODEL_COST_TRACKING}")
    
    print("\n模型配置:")
    for tier, config in settings.model_tiers.items():
        print(f"\n  [{tier}]")
        print(f"    模型: {config.model_name}")
        print(f"    输入成本: ¥{config.input_cost} / M tokens")
        print(f"    输出成本: ¥{config.output_cost} / M tokens")
    
    print("\n向量存储配置:")
    print(f"  模型: {settings.vector_store.embedding_model_name}")
    print(f"  输入成本: ¥{settings.vector_store.input_cost} / M tokens")
    print(f"  输出成本: ¥{settings.vector_store.output_cost} / M tokens")


def test_token_tracker():
    """测试 Token Tracker"""
    print("\n" + "=" * 50)
    print("测试 Token Tracker")
    print("=" * 50)
    
    tracker = TokenTracker.get_instance()
    
    # 测试模型: deepseek-ai/DeepSeek-V3.2
    print("\n记录测试使用:")
    tracker.track(
        model="deepseek-ai/DeepSeek-V3.2",
        prompt_tokens=1000,
        completion_tokens=500,
        operation="test"
    )
    
    # 测试嵌入模型: Pro/BAAI/bge-m3
    tracker.track(
        model="Pro/BAAI/bge-m3",
        prompt_tokens=2000,
        completion_tokens=0,
        operation="embedding"
    )
    
    print("\n" + tracker.format_stats())
    
    print("\n按模型统计:")
    for model, stats in tracker.get_stats_by_model().items():
        print(f"\n  {model}:")
        print(f"    调用次数: {stats.call_count}")
        print(f"    总 Tokens: {stats.total_tokens:,}")
        print(f"    成本: ¥{stats.total_cost_cny:.6f}")


if __name__ == "__main__":
    test_config_loading()
    test_token_tracker()
    print("\n" + "=" * 50)
    print("测试完成！")
    print("=" * 50)
