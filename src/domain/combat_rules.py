"""
战斗规则内核

职责:
  - 伤害计算（含伤害加值 DB）
  - 命中判定与部位判定
  - 护甲减伤
  - 武器属性定义与管理

函数:
  - calculate_damage(weapon, db, success_level) -> int
  - determine_hit_location(roll_value) -> str
  - apply_armor(damage, armor_value) -> int
  - resolve_combat_round(actions: List[CombatAction]) -> CombatRoundResult

数据:
  - WEAPONS: Dict[str, WeaponStats]  武器数据表
  - ARMOR_TYPES: Dict[str, int]       护甲类型

原则: 100% 确定性，无 LLM 调用
"""
