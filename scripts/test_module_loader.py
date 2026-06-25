"""Test the module loader"""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.state.module_loader import ModuleLoader


async def test():
    loader = ModuleLoader()

    # 1. Test list_modules
    modules = await loader.list_modules()
    print(f"=== 已摄入模组 ({len(modules)} 个) ===")
    for m in modules:
        print(f"  {m}")

    if modules:
        # 2. Test load
        name = modules[0]["name"]
        state = await loader.load("test-session", name)
        if state:
            print()
            print(f'=== 模组 "{name}" 已载入 ===')
            print(f'  scenario_name: {state["scenario_name"]}')
            print(f'  time_slot: {state["time_slot"]}')
            print(f'  game_phase: {state["game_phase"]}')
            print(f'  active_tags: {state["active_tags"]}')
            narrative = state.get("narrative", "")
            print(f'  narrative: {narrative[:80]}...' if narrative else "  narrative: (无)")
            wc = state.get("world_context", "")
            print(f'  world_context: {wc[:50]}...' if wc else "  world_context: (无)")

    print()
    print("ModuleLoader test PASSED!")


asyncio.run(test())
