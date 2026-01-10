from typing import Dict, Any
from ..core.events import Intent, IntentType, ResolutionResult
from .physical import PhysicalComponent
from .social import SocialComponent
from .combat import CombatComponent
from .navigation import NavigationComponent
from .sanity import SanityComponent
from .health import HealthComponent
from .dice import DiceRoller

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

    def resolve(self, intent: Intent) -> ResolutionResult:
        """
        处理意图的主要入口点。
        """
        if intent.type == IntentType.PHYSICAL_INTERACT:
            return self.physical.handle_interaction(intent.target, intent.action_verb, intent.params)
        elif intent.type == IntentType.SOCIAL_INTERACT:
            return self.social.handle_interaction(intent.target, intent.action_verb, intent.params)
        elif intent.type == IntentType.COMBAT_ACTION:
            return self.combat.handle_action(intent.target, intent.action_verb, intent.params)
        elif intent.type == IntentType.MOVE:
            return self.navigation.move(intent.target)
        
        return ResolutionResult(success=False, outcome_desc="未知的意图类型")
