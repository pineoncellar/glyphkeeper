"""
意图分析节点 (替代旧 Analyzer Agent)

职责:
  - 将玩家自然语言输入转换为结构化 Intent
  - 识别意图类型（物理/社交/战斗/移动/元）
  - 提取目标、动作描述、工具等参数
  - 使用 fast 级别 LLM 以降低延迟

输入: PlayerInput (str) + GameContext
输出: Intent (结构化对象)

Prompt 模板: 定义在 prompts/intent_prompts.py（待创建）
"""
