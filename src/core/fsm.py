from enum import Enum, auto

class GameState(Enum):
    EXPLORATION = auto()
    COMBAT = auto()
    DIALOGUE = auto()
    MENU = auto()
    CUTSCENE = auto()

class FSM:
    def __init__(self):
        self.current_state = GameState.EXPLORATION
        self.history = []

    def transition_to(self, new_state: GameState):
        self.history.append(self.current_state)
        self.current_state = new_state
        print(f"State transition: {self.history[-1]} -> {self.current_state}")
