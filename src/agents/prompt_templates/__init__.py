"""
Prompt Templates - 提示词模板库

存放所有 Agent 使用的 System Prompt 和提示词工程：

模板分类：
1. Narrator Prompts:
   - narrator_system.txt: 叙事者的系统提示词
   - narrator_react.txt: ReAct 循环的提示模板
   - narrator_combat.txt: 战斗场景专用提示
   - narrator_exploration.txt: 探索场景提示

2. Orchestrator Prompts:
   - orchestrator_system.txt: 路由代理的系统提示
   - intent_classification.txt: 意图分类提示

3. Search Prompts:
   - search_game_narrator.txt: 游戏叙事搜索
   - search_game_archivist.txt: 规则查询搜索

4. Few-shot Examples:
   - examples_narrator.json: 叙事示例
   - examples_react.json: ReAct 循环示例

使用方式：
- 文本文件 (.txt): 直接读取作为 system prompt
- JSON 文件 (.json): 存储 few-shot examples
- Jinja2 模板 (.j2): 支持变量替换的动态模板

用法示例：
    from .prompt_templates import load_prompt
    
    system_prompt = load_prompt("narrator_system.txt")
    examples = load_prompt("examples_react.json")
"""
