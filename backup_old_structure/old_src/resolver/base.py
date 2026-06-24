"""
基础组件类，所有游戏引擎和解析器的组件均继承自此类
"""
from abc import ABC, abstractmethod
from typing import Any, Dict

class BaseComponent(ABC):
    """基础组件类"""
    def __init__(self, engine):
        self.engine = engine  # 引用 GameEngine 或 Resolver 上下文

    @abstractmethod
    def initialize(self):
        pass
