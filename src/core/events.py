"""
events模块
定义了程序中，模块间传递信息的数据结构
"""
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Union, List

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
    targets: 目标名称列表
    action_description: 清洗后的行动描述
    raw_input: 玩家原始输入文本
    explicit_tool: 可选，玩家明确指定使用的工具名称
    """
    targets: List[str] = field(default_factory=list)
    # 清洗后的行动描述
    # Analyzer 将玩家的原始输入转化为一段清晰的、第三人称的行动描述。
    # 包含：动作、工具、方式、试图达成的目的。
    # 例："调查员试图小心翼翼地用发卡去拨弄锁芯，想听听里面有没有机关。"
    action_description: str = ""
    raw_input: str = ""
    explicit_tool: Optional[str] = None

@dataclass
class IntentSocialInteractData:
    """
    社交交互意图数据
    target: 目标角色名称列表，如果没对任何人说则为“自言自语”
    raw_dialogue: 原始对话内容
    intention: 交流意图（如"询问最近是否有见过脸上带刀疤的人"、"要求对方离开"）
    tone: 可选，交流的语气或风格
    TODO: 增加对话历史项
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
    action: 战斗动作，COC战斗动作很有限，可选：["攻击", "闪避", "战技", "脱战", "法术"]
        其中，“战技”概括的范围很宽泛，可以是使用特定体术技能，也可以是使用特殊物品。
    weapon: 使用的武器，如果为空手则为"拳头"
    """
    action: str = None
    target: str = None
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
    character_name: 发起意图的角色名称
    data: 意图数据
        依照意图类型分类，不同的意图类型有不同的数据结构
    """
    type: IntentType
    character_name: str
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
