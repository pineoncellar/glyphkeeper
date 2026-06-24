from pydantic import BaseModel, Field
from typing import Literal, Optional

class NarratorInput(BaseModel):
    # 游戏唯一id
    session_id: str = Field(..., description="唯一的游戏会话ID")
    
    # 身份
    character_name: str = Field(..., description="当前行动的角色名称 (Entity Name)")
    
    # 内容
    content: str = Field(..., description="玩家输入的文本内容")
    
    # 交互类型
    # action: 试图改变世界的行为 (需要 ReAct 推理)
    # dialogue: 纯说话 (可能只需要简单的记录或回应)
    # ooc: 场外话 (如询问规则)
    type: Literal["action", "dialogue", "ooc"] = "action"
