"""
Prompt Templates - 提示词模板库 (融合架构)

基于"融合架构"方法论，提供模块化、动态化的 Prompt 生成能力。

核心组件：
1. PromptAssembler: 动态 Prompt 构建器
2. SceneMode: 场景模式枚举（探索/战斗/对话/调查）
3. Templates: 各种预设模板库

架构层级：
- 第一层：核心人设与法则（固定）
- 第二层：游戏状态（动态）
- 第三层：RAG 记忆上下文（动态）
- 第四层：对话历史（动态）
- 第五层：工具执行结果（动态）

用法示例：
    from .assembler import PromptAssembler, SceneMode
    
    prompt = PromptAssembler.build(
        player_name="调查员",
        game_state={...},
        rag_context={...},
        history_str="...",
        user_input="我检查书桌",
        tool_results=None
    )
"""

from ..assembler import PromptAssembler, SceneMode

__all__ = ["PromptAssembler", "SceneMode"]

