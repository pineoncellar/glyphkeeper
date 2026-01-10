import asyncio
from typing import Optional

from .fsm import FSM, GameState
from .events import Intent, IntentType, ResolutionResult
from ..agents.analyzer import Analyzer
from ..agents.writer import Writer
from ..agents.memorizer import Memorizer
from ..components.resolver import Resolver

class GameEngine:
    def __init__(self):
        self.fsm = FSM()
        self.analyzer = Analyzer()
        self.resolver = Resolver(self)
        self.writer = Writer()
        self.memorizer = Memorizer()
        
    async def process_input(self, player_input: str) -> str:
        """
        主流水线 (Deep-Think Pipeline):
        1. 感知 (Analyzer): 意图识别
        2. 裁决 (Resolver): 规则判定
        3. 表达 (Writer): 叙事生成
        4. 固化 (Memorizer): 记忆存储
        """
        # 第一阶段：感知与翻译
        intent: Intent = await self.analyzer.analyze(player_input, self.fsm.current_state.name)
        
        # 第二阶段：规则与裁决
        resolution: ResolutionResult = self.resolver.resolve(intent)
        
        # 第三阶段：表达与叙事
        narrative: str = await self.writer.write(resolution, player_input)
        
        # 第四阶段：记忆固化 (在实际应用中应为后台任务)
        await self.memorizer.memorize(narrative)
        
        return narrative

    def start(self):
        print("Game Engine Started")
