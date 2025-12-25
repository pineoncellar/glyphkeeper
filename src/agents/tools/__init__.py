"""
Agent Tools - 代理工具集

为 Agent (尤其是 Narrator) 提供可调用的工具函数：
- dice_roller: 掷骰子和判定逻辑
- db_tools: 包装 memory 层，提供 LLM-friendly 接口

工具设计原则：
- 输入输出简单清晰（适合 LLM Function Calling）
- 包含详细的 docstring（用于生成 OpenAI Function Schema）
- 异常处理完善，返回友好错误信息

用法示例：
    from .tools import roll_dice, get_entity_info
    
    result = roll_dice("3d6+5")
    npc_data = await get_entity_info(entity_id=1)
"""
