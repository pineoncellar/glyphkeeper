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

from enum import Enum
from typing import List


class TimeSlot(Enum):
    """游戏内时间段"""
    DAWN = "DAWN"
    MORNING = "MORNING"
    AFTERNOON = "AFTERNOON"
    EVENING = "EVENING"
    NIGHT = "NIGHT"
    LATE_NIGHT = "LATE_NIGHT"


# 时间段顺序（用于循环推进）
TIME_SLOT_ORDER: List[TimeSlot] = [
    TimeSlot.DAWN,
    TimeSlot.MORNING,
    TimeSlot.AFTERNOON,
    TimeSlot.EVENING,
    TimeSlot.NIGHT,
    TimeSlot.LATE_NIGHT,
]


def advance_time(current: TimeSlot, steps: int = 1) -> TimeSlot:
    """
    推进游戏时间，循环推进。
    
    参数:
      current: 当前时间段
      steps: 推进的步数（默认 1）
    
    返回: 推进后的时间段
    """
    if steps < 0:
        steps = 0
    
    try:
        idx = TIME_SLOT_ORDER.index(current)
    except ValueError:
        return TimeSlot.DAWN
    
    new_idx = (idx + steps) % len(TIME_SLOT_ORDER)
    return TIME_SLOT_ORDER[new_idx]


def get_time_description(slot: TimeSlot) -> str:
    """
    返回时间段的自然语言描述。
    估计用不上。
    """
    descriptions = {
        TimeSlot.DAWN: "破晓时分，天色微明",
        TimeSlot.MORNING: "清晨的阳光洒落",
        TimeSlot.AFTERNOON: "午后的阳光斜照",
        TimeSlot.EVENING: "暮色渐沉，华灯初上",
        TimeSlot.NIGHT: "夜色已深，万籁俱寂",
        TimeSlot.LATE_NIGHT: "深夜，月光透过云层洒下",
    }
    return descriptions.get(slot, "未知时间")
