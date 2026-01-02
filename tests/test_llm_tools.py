import asyncio
import os
import sys
import json
from typing import List, Dict, Any

# 添加项目根目录到路径
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ==========================================
# 1. 环境与 Mock 设置 (为了让脚本能独立运行)
# ==========================================

# 模拟 Logger
class MockLogger:
    def debug(self, msg): print(f"[DEBUG] {msg}")
    def info(self, msg): print(f"[INFO] {msg}")
    def warning(self, msg): print(f"[WARN] {msg}")
    def error(self, msg, exc_info=False): print(f"[ERROR] {msg}")

# 模拟工具函数
def get_logger(name): return MockLogger()
def track_tokens(**kwargs): print(f"[TOKEN TRACKING] {kwargs}")

# 模拟基类
class LLMBase:
    def __init__(self, model_name, base_url, api_key, **kwargs):
        self.model_name = model_name
        self.api_key = api_key
        self.kwargs = kwargs

Message = Dict[str, Any]

# 将 Mock 注入 sys.modules，这样你的 llm_openai.py 导入时就不会报错
# (前提：你需要把你的 llm_openai.py 内容稍微调整一下，或者确保运行环境能找到依赖)
# 如果你在完整的项目结构中运行，请删除下面的 sys.modules 注入代码
from unittest.mock import MagicMock
sys.modules['..core'] = MagicMock(get_logger=get_logger)
sys.modules['..utils'] = MagicMock(track_tokens=track_tokens)
sys.modules['.llm_base'] = MagicMock(LLMBase=LLMBase, Message=Message)

# ==========================================
# 2. 导入你的类
# ==========================================
# 假设你的文件名为 llm_openai.py，且在同一目录下
try:
    from src.llm.llm_factory import LLMFactory
except ImportError:
    # 如果导入失败，请将你的类代码直接粘贴到这里，覆盖这一行
    print("❌ 无法导入 OpenAICompatibleLLM，请确保文件在同一目录或手动粘贴类代码。")
    sys.exit(1)

# ==========================================
# 3. Narrator 模拟测试逻辑
# ==========================================

# 定义 Narrator 会用到的工具 (Schema)
NARRATOR_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "move_entity",
            "description": "移动当前角色到相邻的房间。",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string", 
                        "enum": ["North", "South", "East", "West"],
                        "description": "移动的方向"
                    }
                },
                "required": ["direction"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_location_view",
            "description": "获取当前位置的详细描述。",
            "parameters": {"type": "object", "properties": {}}
        }
    }
]

async def test_narrator_flow():
    # --- 配置 ---
    llm = LLMFactory.get_llm("smart"    )

    # --- 场景 1: 纯闲聊 (测试流式文本) ---
    print("\n" + "="*50)
    print("🧪 测试场景 1: 纯闲聊 (Streaming Text)")
    print("="*50)
    
    messages = [
        {"role": "system", "content": "你是跑团主持人 Narrator。"},
        {"role": "user", "content": "你好，简单介绍一下这个模组的背景。"}
    ]

    full_response = ""
    print("Narrator: ", end="")
    
    # 调用 chat (不传 tools)
    async for chunk in llm.chat(messages):
        if isinstance(chunk, str):
            print(chunk, end="", flush=True)
            full_response += chunk
        elif isinstance(chunk, dict):
            print(f"\n[Unexpected Dict]: {chunk}")
    
    print("\n\n✅ 闲聊测试完成。")

    # --- 场景 2: 意图识别 (测试 Function Calling) ---
    print("\n" + "="*50)
    print("🧪 测试场景 2: 工具调用 (Tool Calling)")
    print("="*50)

    # 模拟用户想要移动
    messages.append({"role": "assistant", "content": full_response})
    messages.append({"role": "user", "content": "这地方太阴森了，我要向北移动，离开这里！"})

    print(f"User: {messages[-1]['content']}")
    print("Narrator (Thinking)...")

    tool_calls_received = []
    
    # 调用 chat (传入 tools)
    async for chunk in llm.chat(messages, tools=NARRATOR_TOOLS):
        
        # 情况 A: 模型可能一边思考一边说话 (Thinking Process)
        if isinstance(chunk, str):
            # DeepSeek 有时会输出思维链内容，或者空的思考字符
            print(chunk, end="", flush=True)
            
        # 情况 B: 模型决定调用工具 (这是你要测试的核心)
        elif isinstance(chunk, dict) and "tool_calls" in chunk:
            tool_calls_received = chunk["tool_calls"]
            print(f"\n\n🛠️  捕捉到工具调用请求: {json.dumps(tool_calls_received, indent=2, ensure_ascii=False)}")

    # 验证结果
    if tool_calls_received:
        first_call = tool_calls_received[0]
        func_name = first_call['function']['name']
        args = json.loads(first_call['function']['arguments'])
        
        if func_name == "move_entity" and args.get("direction") in ["North", "北"]:
             print("\n✅ 测试通过：模型正确识别了移动意图。")
        else:
             print(f"\n⚠️  测试存疑：模型调用了 {func_name} 参数 {args}，请检查是否符合预期。")
    else:
        print("\n❌ 测试失败：模型没有调用任何工具，它可能直接回复了文本。")

if __name__ == "__main__":
    if "sk-your-key-here" in os.getenv("LLM_API_KEY", "sk-your-key-here"):
        print("⚠️  警告: 未设置 LLM_API_KEY，请在环境变量或代码中填入正确的 Key。")
    
    try:
        asyncio.run(test_narrator_flow())
    except KeyboardInterrupt:
        print("\n测试终止。")
    except Exception as e:
        print(f"\n❌ 发生错误: {e}")