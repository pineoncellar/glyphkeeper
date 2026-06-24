"""
Structure Extractor - 结构化数据提取器

功能：
- 使用 LLM 将非结构化文本转化为结构化 JSON
- 提取游戏模组中的关键实体（地点、NPC、线索、事件）
- 生成符合 memory/models.py 的数据结构

提取目标：
1. Locations (地点)
   - name: 地点名称
   - description: 场景描述
   - connections: 连接的其他地点
   - entities: 在此地点的 NPC/物品
   - state: 初始状态 (locked, dark, etc.)

2. Entities (NPC/角色)
   - name: 名称
   - description: 外貌/性格描述
   - stats: 属性值 (HP, AC, 技能值)
   - location: 所在地点
   - relationships: 与其他实体的关系

3. Clues (线索)
   - name: 线索名称
   - description: 线索内容
   - location: 发现地点
   - trigger_condition: 触发条件
   - related_entities: 关联实体

4. Events (事件)
   - name: 事件名称
   - trigger: 触发条件
   - description: 事件描述
   - consequences: 后续影响

核心函数：
- extract_locations(text: str) -> list[dict]
  提取所有地点信息
  
- extract_entities(text: str) -> list[dict]
  提取 NPC/角色信息
  
- extract_clues(text: str) -> list[dict]
  提取线索/物品信息
  
- extract_module_structure(text: str) -> dict
  提取完整模组结构（包含所有实体）

LLM Prompt 策略：
- 使用 Few-shot 示例指导 LLM
- 支持 JSON Schema 验证
- 支持分段解析（大文件）

用法示例：
    from .structure_extractor import extract_module_structure
    
    # 从 PDF 提取文本后
    module_data = await extract_module_structure(text)
    # 返回: {"locations": [...], "entities": [...], "clues": [...]}
"""
