"""
角色域模型

职责:
  - 调查员角色数据类与校验
  - 属性生成与分配
  - 技能列表管理与成长
  - 职业模板定义

类:
  - Character: 核心角色数据
  - Stats: 八项属性值对象
  - Skills: 技能集合管理
  - Occupation: 职业模板

函数:
  - create_investigator(name, occupation, stats) -> Character
  - apply_skill_growth(character, skill_name, roll_value) -> bool
  - calculate_damage_bonus(str_score) -> str
  - calculate_build(str_score, siz_score) -> int
  - calculate_move(dex_score, str_score, siz_score) -> int
"""
