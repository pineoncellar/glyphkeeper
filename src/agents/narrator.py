"""
Narrator Agent - 叙事者代理

职责：
- 推进游戏故事情节
- 生成沉浸式场景描述
- 处理玩家行动并生成结果
- 调用工具（掷骰子、查询状态）
- 实现 ReAct (Reasoning + Acting) 循环

核心流程：
1. Thought: 分析玩家行动，推理可能结果
2. Action: 调用工具（dice_roller, db_tools）
3. Observation: 获取工具结果
4. Response: 生成叙事性回复

ReAct 工具链：
- roll_dice(): 掷骰子判定
- get_location_state(): 查询地点状态
- get_entity_info(): 查询 NPC 信息
- update_game_state(): 更新游戏状态

用法示例：
    narrator = Narrator(llm_model="gpt-4")
    response = await narrator.narrate(player_action, context)
"""
