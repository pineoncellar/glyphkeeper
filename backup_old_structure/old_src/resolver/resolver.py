from typing import Dict, Any
from ..core.events import Intent, IntentType, ResolutionResult, ResolutionStatus
from .physical import PhysicalComponent
from .social import SocialComponent
from .combat import CombatComponent
from .navigation import NavigationComponent
from .sanity import SanityComponent
from .health import HealthComponent
from .dice import DiceRoller
from typing import Optional

class Resolver:
    def __init__(self, engine=None):
        self.engine = engine
        self.physical = PhysicalComponent(engine)
        self.social = SocialComponent(engine)
        self.combat = CombatComponent(engine)
        self.navigation = NavigationComponent(engine)
        self.sanity = SanityComponent(engine)
        self.health = HealthComponent(engine)
        self.dice = DiceRoller()

    def resolve(self, intent: Intent, dice_result: Optional[dict] = None) -> ResolutionResult:
        """
        处理意图的主要入口点。
        :param intent: 玩家意图对象
        :param dice_result: (可选) 外部传入的掷骰结果，用于恢复挂起的流程
        """
        # 如果传入了骰子结果，这通常意味着我们在"恢复"之前的操作
        if dice_result:
            # TODO: 实现针对特定Component的恢复逻辑
            # 目前暂时只支持简单的直接返回，后续需要在各个 Component 中实现 resume_interaction 方法
            pass

        if intent.type == IntentType.PHYSICAL_INTERACT:
            return self.physical.handle_interaction(intent.target, intent.action_verb, intent.params)
        elif intent.type == IntentType.SOCIAL_INTERACT:
            return self.social.handle_interaction(intent.target, intent.action_verb, intent.params)
        elif intent.type == IntentType.COMBAT_ACTION:
            return self.combat.handle_action(intent.target, intent.action_verb, intent.params)
        elif intent.type == IntentType.MOVE:
            return self.navigation.move(intent.target)
        
        return ResolutionResult(
            status=ResolutionStatus.COMPLETED,
            success=False, 
            outcome_desc="未知的意图类型"
        )
