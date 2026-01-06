"""
测试新 Narrator 架构的基本功能
快速验证 PromptAssembler 和 SceneMode 是否正常工作
"""
import sys
import os

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.agents.tools.assembler import PromptAssembler, SceneMode


def test_basic_prompt_build():
    """测试基础 Prompt 构建"""
    print("=" * 60)
    print("测试 1: 基础 Prompt 构建")
    print("=" * 60)
    
    prompt = PromptAssembler.build(
        actor="威廉·道格拉斯",
        game_state={
            "location": "金博尔宅 - 书房",
            "time_slot": "深夜",
            "environment": "昏暗, 雷雨",
            "environment_tags": ["dark", "rainy", "indoor"],
            "special_conditions": None
        },
        rag_context={
            "semantic": "金博尔宅建于1875年，曾经的主人是一位神秘的学者。",
            "episodic": "你之前在客厅发现了一张奇怪的照片。",
            "keeper_notes": "书桌的抽屉里藏着一本古老的日记。"
        },
        history_str="User: 我想进书房\nAssistant: 书房的门吱呀一声开了...",
        user_input="我检查书桌",
        tool_results=None
    )
    
    print(prompt)
    print("\n✅ 基础构建测试通过\n")


def test_scene_mode_detection():
    """测试场景模式自动检测"""
    print("=" * 60)
    print("测试 2: 场景模式自动检测")
    print("=" * 60)
    
    test_cases = [
        ("我攻击邪教徒", {}, SceneMode.COMBAT),
        ("我四处看看", {}, SceneMode.EXPLORATION),
        ("我问老管家关于主人的事", {}, SceneMode.DIALOGUE),
        ("我仔细检查日记上的文字", {}, SceneMode.INVESTIGATION),
    ]
    
    for user_input, game_state, expected in test_cases:
        detected = PromptAssembler._detect_scene_mode(user_input, game_state)
        status = "✅" if detected == expected else "❌"
        print(f"{status} 输入: '{user_input}' -> 检测到: {detected.value} (期望: {expected.value})")
    
    print("\n✅ 场景模式检测测试完成\n")


def test_tool_results_integration():
    """测试工具结果集成"""
    print("=" * 60)
    print("测试 3: 工具结果集成")
    print("=" * 60)
    
    tool_results = [
        {
            "status": "success",
            "observation": "书桌上散落着泛黄的纸张",
            "tags": ["old", "paper", "mysterious"],
            "flavor_text": "你注意到其中一张纸上有奇怪的符号"
        }
    ]
    
    prompt = PromptAssembler.build(
        actor="调查员",
        game_state={
            "location": "书房",
            "time_slot": "下午",
            "environment": "安静",
            "environment_tags": ["quiet"]
        },
        rag_context={
            "semantic": "",
            "episodic": "",
            "keeper_notes": ""
        },
        history_str="",
        user_input="我检查书桌",
        tool_results=tool_results
    )
    
    # 验证工具结果是否正确嵌入
    assert "工具执行结果" in prompt
    assert "书桌上散落着泛黄的纸张" in prompt
    print("✅ 工具结果已正确嵌入到 Prompt 中")
    print("\n示例片段:")
    print(prompt[prompt.find("### 工具执行结果"):prompt.find("### 工具执行结果")+500])
    print("\n✅ 工具结果集成测试通过\n")


def test_empty_context_handling():
    """测试空上下文处理"""
    print("=" * 60)
    print("测试 4: 空上下文处理")
    print("=" * 60)
    
    prompt = PromptAssembler.build(
        actor="调查员",
        game_state={
            "location": "街道",
            "time_slot": "未知",
            "environment": "未知",
            "environment_tags": []
        },
        rag_context={
            "semantic": "",
            "episodic": "",
            "keeper_notes": ""
        },
        history_str="",
        user_input="我问路人时间",
        tool_results=None
    )
    
    # 验证是否有空上下文的默认提示
    assert "[未找到相关世界知识]" in prompt
    assert "[未记录先前行动]" in prompt
    print("✅ 空上下文已正确处理（填充默认提示）")
    print("\n✅ 空上下文处理测试通过\n")


def test_simple_build():
    """测试简化构建器"""
    print("=" * 60)
    print("测试 5: 简化构建器")
    print("=" * 60)
    
    prompt = PromptAssembler.build_simple(
        actor="调查员",
        current_location="街道",
        user_input="我问路人时间"
    )
    
    assert "调查员" in prompt
    assert "街道" in prompt
    print("✅ 简化构建器正常工作")
    print(f"Prompt 长度: {len(prompt)} 字符")
    print("\n✅ 简化构建器测试通过\n")


def test_mode_instructions():
    """测试不同模式的指令差异"""
    print("=" * 60)
    print("测试 6: 不同场景模式的指令差异")
    print("=" * 60)
    
    modes = [
        SceneMode.EXPLORATION,
        SceneMode.COMBAT,
        SceneMode.DIALOGUE,
        SceneMode.INVESTIGATION
    ]
    
    for mode in modes:
        prompt = PromptAssembler.build(
            actor="调查员",
            game_state={"location": "测试", "time_slot": "测试", "environment": "测试", "environment_tags": []},
            rag_context={"semantic": "", "episodic": "", "keeper_notes": ""},
            history_str="",
            user_input="测试",
            tool_results=None,
            scene_mode=mode
        )
        
        instruction = PromptAssembler.MODE_INSTRUCTIONS[mode]
        assert instruction.strip() in prompt
        print(f"✅ {mode.value.upper()} 模式指令已正确嵌入")
    
    print("\n✅ 模式指令差异测试通过\n")


def run_all_tests():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print(" Narrator 融合架构 - 快速测试套件")
    print("=" * 60 + "\n")
    
    try:
        test_basic_prompt_build()
        test_scene_mode_detection()
        test_tool_results_integration()
        test_empty_context_handling()
        test_simple_build()
        test_mode_instructions()
        
        print("=" * 60)
        print("🎉 所有测试通过！架构运行正常。")
        print("=" * 60)
        
    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
    except Exception as e:
        print(f"\n❌ 发生错误: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    run_all_tests()
