"""
events模块
定义了程序中，模块间传递信息的数据结构
"""
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Union

class IntentType(Enum):
    PHYSICAL_INTERACT = "PHYSICAL_INTERACT"
    SOCIAL_INTERACT = "SOCIAL_INTERACT"
    COMBAT_ACTION = "COMBAT_ACTION"
    MOVE = "MOVE"
    META = "META"  # 用于游戏设置、帮助等

@dataclass
class IntentPhysicalInteractData:
    """
    物理交互意图数据
    target: 目标对象（如物品、环境等），对象名称列表
    action_verb: 动作动词（如"检查"、"拾取"
    tool: 可选，使用的工具或物品
    """
    target: str = None
    action_verb: str = None
    tool: Optional[str] = None

@dataclass
class IntentSocialInteractData:
    """
    社交交互意图数据
    target: 目标角色名称列表，如果没对任何人说则为“自言自语”
    raw_dialogue: 原始对话内容
    intention: 交流意图（如"询问最近是否有见过脸上带刀疤的人"、"要求对方离开"）
    tone: 可选，交流的语气或风格
    """
    target: str = None
    raw_dialogue: str = None
    intention: str = None
    tone: Optional[str] = None

@dataclass
class IntentCombatActionData:
    """
    战斗行动意图数据
    target: 目标角色名称列表
    action: 战斗动作，COC战斗动作很有限，可选：["攻击", "闪避", "格挡", "战技", "逃跑"]
        其中，“战技”概括的范围很宽泛，可以是使用特定体术技能，也可以是使用特殊物品。
    weapon: 使用的武器，如果为空手则为"拳头"
    """
    target: str = None
    action: str = None
    weapon: str = None

@dataclass
class IntentMoveData:
    """
    移动意图数据
    destination: 目的地（如"图书馆"、"老宅"、"楼上"）
    """
    destination: str = None

@dataclass
class IntentMetaData:
    """
    元意图数据
    以后再写
    TODO 实现
    """
    raw_command: Optional[str] = None  # 原始命令文本


@dataclass
class Intent:
    """
    意图数据
    type: 意图类型
    data: 意图数据
        依照意图类型分类，不同的意图类型有不同的数据结构
    """
    type: IntentType
    data: Union[IntentPhysicalInteractData, IntentSocialInteractData, IntentCombatActionData, IntentMoveData, IntentMetaData] 

@dataclass
class ResolutionResult:
    """
    意图解析结果数据
    state: 解析状态，True表示成功解析，False表示失败
    success: 玩家意图执行结果，True表示执行成功，False表示执行失败
    outcome_desc: 结果描述，简要说明执行结果
    """
    state: bool
    success: bool
    outcome_desc: str
