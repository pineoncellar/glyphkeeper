"""
游戏时间工具

职责:
  - 管理游戏内时间流逝
  - 时间段划分（清晨/上午/下午/傍晚/深夜）
  - 时间推进与时间相关事件触发
  - 现实时间 ↔ 游戏时间映射

常量:
  - TIME_SLOTS: 时间段枚举
  - SLOT_DURATION: 每个时段的游戏内时长

函数:
  - advance_time(current_slot, steps) -> TimeSlot
  - get_current_time_description(slot) -> str
"""
