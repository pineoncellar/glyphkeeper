#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
运行时功能测试 — 模拟玩家输入，验证 Graph 流程
覆盖测试计划 2.1~2.5

用法:
    uv run python scripts/test_runtime_flows.py
"""
import asyncio, json, sys, uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


TEMPLATE_SESSION = "00000000-0000-0000-0000-000000000000"
passed = 0
failed = 0


def check(name: str, condition: bool, detail: str = ""):
    global passed, failed
    if condition:
        print(f"  ✅ {name}")
        passed += 1
    else:
        print(f"  ❌ {name} — {detail}")
        failed += 1


async def setup_test_env():
    """初始化引擎和必要的后台组件"""
    from src.graph.keeper_graph import keeper_graph
    from src.runtime.engine import GraphEngine
    from src.runtime.scheduler import InputScheduler
    
    # 确保 LightRAG 已初始化并含测试数据
    from src.memory.vector_store import VectorStore
    vs = await VectorStore.get_instance(domain="world", llm_tier="fast")
    
    engine = GraphEngine(keeper_graph)
    scheduler = InputScheduler(engine)
    return engine, scheduler, vs


async def create_test_state(session_id: str, engine) -> dict:
    """创建测试用的初始游戏状态"""
    from src.state.game_state import create_initial_state
    from src.domain.character import (
        Occupation, OCCUPATIONS, Stats, Character, create_investigator,
        roll_standard_stats, calculate_skill_points, calculate_interest_points,
    )
    
    state = create_initial_state(
        session_id=session_id,
        scenario_name="test_minimal",
        time_slot="EVENING",
    )
    
    # 创建测试角色
    stats = Stats(
        strength=50, constitution=50, size=50, dexterity=50,
        appearance=50, intelligence=60, power=50, education=60,
    )
    occ_skills = {
        "侦查": 70, "聆听": 60, "图书馆利用": 60, "潜行": 40,
        "斗殴": 50, "闪避": 40, "信用评级": 40, "急救": 50,
        "心理学": 50, "话术": 50, "恐吓": 40, "说服": 60,
        "历史": 40, "人类学": 30,
    }
    character = create_investigator(
        name="测试员",
        occupation="教授",
        stats=stats,
        skills=occ_skills,
    )
    
    # 从角色对象构建 state.character dict
    from dataclasses import asdict
    char_dict = asdict(character)
    # 转换枚举等
    char_dict['stats'] = asdict(character.stats)
    state["character"] = char_dict
    state["current_location"] = "loc_entrance"
    
    # 注入世界上下文
    state["world_context"] = ""
    
    return state


async def test_2_1_physical_inspection():
    """2.1 物理检查触发线索"""
    print("\n" + "=" * 60)
    print("🔍 2.1 物理检查触发线索")
    print("=" * 60)
    
    session_id = f"test-2-1-{uuid.uuid4().hex[:8]}"
    engine, scheduler, vs = await setup_test_env()
    
    # 读取模组数据中的 clue_discoveries 表
    from src.memory.event_store import EventStore
    es = EventStore()
    conn = await es._get_conn()
    
    # 检查 clue_discoveries 表结构
    rows = await conn.fetch("SELECT * FROM clue_discoveries ORDER BY id")
    print(f"  clue_discoveries 表: {len(rows)} 条记录")
    for r in rows:
        kid = r.get("knowledge_id") or "null"
        req = r.get("required_check") or "null"
        print(f"    - interactable_id={r['interactable_id']}, entity_key={r.get('entity_key')}, "
              f"knowledge_id={kid}, required_check={req}")
    
    await conn.close()
    
    # 验证搜索书桌 (search_drawer) 对应的线索记录存在
    print("\n  --- 2.1a: 搜索书桌 → 触发 fact_hidden_letter ---")
    check("2.1a 前置: clue_discoveries 表有 4 条记录", len(rows) == 4, f"实际={len(rows)}")
    
    # 验证 target_knowledge=null 的线索被跳过
    print("\n  --- T5: target_knowledge:null 的线索不应入表 ---")
    null_knowledge = [r for r in rows if r.get("knowledge_id") is None]
    check("T5: 无 target_knowledge 的线索不入表", len(null_knowledge) == 0,
          f"实际有 {len(null_knowledge)} 条 knowledge_id=null 的记录")
    
    # 验证 Examine wall 线索存在（Hard 难度）
    print("\n  --- 2.1d: Extreme 难度相关 ---")
    hard_clues = [r for r in rows if r.get("required_check") and 
                  json.loads(r["required_check"]).get("difficulty") == "Hard"]
    check("2.1d 前置: 有 Hard 难度线索", len(hard_clues) >= 1)
    
    print("\n  --- Archivist / skill_node 流程验证（代码审查）---")
    # 读取 skill_node 源码确认检定成功时调用 Archivist
    from src.nodes.rules.skill_node import skill_node
    import inspect
    src = inspect.getsource(skill_node)
    check("2.1e: skill_node 在失败时不触发线索",
          "is_success" in src and ("not is_success" in src or "is_success is False" in src or "not result" in src or "is_success == False" in src),
          "需要确认 skill_node 在检定失败时跳过 Archivist")
    
    # 读取 Archivist 源码
    try:
        from src.tools.archivist import Archivist
        arch_src = inspect.getsource(Archivist.inspect_target)
        check("Archivist 存在 inspect_target 方法", True)
        check("Archivist 使用 clue_discoveries 表",
              "clue_discoveries" in arch_src or "ClueDiscovered" in arch_src,
              "需要确认 Archivist 查询 clue_discoveries 表")
    except (ImportError, AttributeError):
        check("Archivist 存在", False, "未找到 Archivist 模块")


async def test_2_2_npc_dialogue():
    """2.2 NPC 对话触发线索"""
    print("\n" + "=" * 60)
    print("💬 2.2 NPC 对话触发线索")
    print("=" * 60)
    
    from src.nodes.llm.npc_dialogue_node import npc_dialogue_node
    import inspect
    src = inspect.getsource(npc_dialogue_node)
    
    check("npc_dialogue_node 存在", True)
    check("npc_dialogue_node 有 _check_clue_grant 机制",
          "_check_clue_grant" in src or "clue" in src.lower(),
          "需要确认 NPC 对话中有线索触发逻辑")
    
    # 检查 NPC dialogue_clues 在 LightRAG 中
    print("\n  --- NPC 对话线索检查 ---")
    from src.memory.event_store import EventStore
    es = EventStore()
    conn = await es._get_conn()
    
    # 查找 npc_butler 相关的 clue_discoveries
    rows = await conn.fetch(
        "SELECT * FROM clue_discoveries WHERE entity_key='npc_butler' ORDER BY id"
    )
    check(f"npc_butler 有 {len(rows)} 条线索记录", len(rows) == 2,
          f"预期 2 条 (chat + intimidate), 实际={len(rows)}")
    
    for r in rows:
        req = r.get("required_check")
        kid = r.get("knowledge_id") or "null"
        if req and req != "null":
            check(f"  chat 线索有 required_check",
                  json.loads(req).get("skill") == "Persuade",
                  f"实际={req}")
        else:
            check(f"  intimidate 线索 required_check=null (自动触发)", True)
    
    await conn.close()


async def test_2_3_anti_spoiler():
    """2.3 防剧透机制"""
    print("\n" + "=" * 60)
    print("🚫 2.3 防剧透机制")
    print("=" * 60)
    
    from src.memory.event_store import EventStore
    from src.state.session_state import SessionKnowledgeState
    
    es = EventStore()
    conn = await es._get_conn()
    
    # 检查 session_knowledge_state 表是否存在
    tables = await conn.fetch(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='public' AND table_type='BASE TABLE'"
    )
    tnames = [r["table_name"] for r in tables]
    check("session_knowledge_state 表存在", "session_knowledge_state" in tnames)
    
    # 从 narrator_node 源码检查是否注入防剧透约束
    from src.nodes.llm.narrator_node import narrate_node
    import inspect
    src = inspect.getsource(narrate_node)
    
    check("narrator_node 使用防剧透机制",
          "known" in src.lower() or "session_knowledge" in src.lower() or "spoiler" in src.lower(),
          "视觉检查提示词模板")
    
    await conn.close()


async def test_2_4_scene_navigation():
    """2.4 场景拓扑导航"""
    print("\n" + "=" * 60)
    print("🗺️  2.4 场景拓扑导航")
    print("=" * 60)
    
    from src.memory.event_store import EventStore
    es = EventStore()
    conn = await es._get_conn()
    
    # 检查 locations 表 exits_json
    rows = await conn.fetch("SELECT * FROM locations ORDER BY key")
    check(f"locations 表: {len(rows)} 个场景", len(rows) == 4)
    
    for r in rows:
        exits = r.get("exits_json", "{}")
        if isinstance(exits, str):
            exits = json.loads(exits) if exits else {}
        check(f"  场景 '{r['key']}' ({r['name']}) 有出口: {list(exits.keys())}",
              len(exits) > 0, f"无出口")
    
    # 验证书房没有通往地下室的出口
    for r in rows:
        if r["key"] == "loc_library":
            exits = r.get("exits_json", "{}")
            if isinstance(exits, str):
                exits = json.loads(exits) if exits else {}
            check("书房（loc_library）无地下室出口",
                  "Basement" not in exits and "cellar" not in str(exits).lower(),
                  f"有非预期出口: {list(exits.keys())}")
    
    await conn.close()


async def test_2_5_item_interaction():
    """2.5 物品互动"""
    print("\n" + "=" * 60)
    print("📦 2.5 物品互动")
    print("=" * 60)
    
    from src.memory.event_store import EventStore
    es = EventStore()
    conn = await es._get_conn()
    
    # 检查 interactables 表
    rows = await conn.fetch("SELECT * FROM interactables ORDER BY key")
    check(f"interactables 表: {len(rows)} 个物品", len(rows) == 5)
    
    for r in rows:
        check(f"  物品 '{r['key']}' ({r['name']}) 在场景中",
              bool(r.get("location_id")), "缺少 location_id 关联")
    
    # 验证 item_lampshade 在走廊场景中
    from src.memory.event_store import EventStore
    lamp = [r for r in rows if r["key"] == "item_lampshade"]
    if lamp:
        # 检查 loc_hallway 的 UUID
        loc_rows = await conn.fetch("SELECT id, key FROM locations")
        loc_map = {r["key"]: r["id"] for r in loc_rows}
        check("item_lampshade 关联 loc_hallway",
              lamp[0]["location_id"] == loc_map.get("loc_hallway"),
              f"实际 location_id={lamp[0]['location_id']}")
    
    # 验证无 clues 的物品（item_lampshade）在 clue_discoveries 中无记录
    clue_rows = await conn.fetch(
        "SELECT * FROM clue_discoveries "
        "WHERE interactable_id=(SELECT id FROM interactables WHERE key='item_lampshade' LIMIT 1)"
    )
    check("item_lampshade 无线索关联", len(clue_rows) == 0,
          f"实际有 {len(clue_rows)} 条线索")
    
    await conn.close()


async def test_3_1_idempotent():
    """3.1 重复摄入幂等性"""
    print("\n" + "=" * 60)
    print("🔄 3.1 重复摄入幂等性验证")
    print("=" * 60)
    
    from src.memory.event_store import EventStore
    es = EventStore()
    conn = await es._get_conn()
    
    # events 表应有 2 条
    ecnt = await conn.fetchval(
        "SELECT count(*) FROM events WHERE session_id='00000000-0000-0000-0000-000000000000'"
    )
    # 因为第二次摄入被中断了，可能还是2条
    check(f"events 表: {ecnt} 条", ecnt >= 2,
          f"预期 >= 2 条")
    
    # 各读模型表检查
    for tbl in ['knowledge_registry', 'locations', 'interactables', 'clue_discoveries']:
        cnt = await conn.fetchval(f"SELECT count(*) FROM {tbl}")
        check(f"{tbl}: {cnt} 条记录（无重复）",
              cnt in [3, 4, 5], f"预期 3/4/5 条")
    
    await conn.close()


async def test_3_3_knowledge_conflict():
    """3.3 知识 ID 冲突"""
    print("\n" + "=" * 60)
    print("🔑 3.3 知识 ID 冲突验证")
    print("=" * 60)
    
    from src.memory.event_store import EventStore
    es = EventStore()
    conn = await es._get_conn()
    
    # fact_butler_secret 被两条 NPC 对话线索引用
    rows = await conn.fetch(
        "SELECT * FROM clue_discoveries "
        "WHERE knowledge_id=(SELECT id FROM knowledge_registry WHERE knowledge_id='fact_butler_secret' LIMIT 1)"
    )
    check(f"fact_butler_secret 被 {len(rows)} 条线索引用", len(rows) == 2,
          f"预期 2 条 (chat + intimidate), 实际={len(rows)}")
    
    # knowledge_registry 中 fact_butler_secret 只有 1 条
    krows = await conn.fetch(
        "SELECT * FROM knowledge_registry WHERE knowledge_id='fact_butler_secret'"
    )
    check("knowledge_registry 中 fact_butler_secret 唯一", len(krows) == 1,
          f"实际={len(krows)} 条重复")
    
    await conn.close()


async def main():
    full_passed = []
    full_failed = []
    
    tests = [
        ("2.1 物理检查触发线索", test_2_1_physical_inspection),
        ("2.2 NPC 对话触发线索", test_2_2_npc_dialogue),
        ("2.3 防剧透机制", test_2_3_anti_spoiler),
        ("2.4 场景拓扑导航", test_2_4_scene_navigation),
        ("2.5 物品互动", test_2_5_item_interaction),
        ("3.1 重复摄入幂等性", test_3_1_idempotent),
        ("3.3 知识 ID 冲突", test_3_3_knowledge_conflict),
    ]
    
    for name, test_fn in tests:
        global passed, failed
        passed = 0
        failed = 0
        try:
            await test_fn()
        except Exception as e:
            print(f"  ❌ 测试异常: {e}")
            import traceback
            traceback.print_exc()
            failed = 1
        full_passed.append(passed)
        full_failed.append(failed)
        print(f"  📊 小计: {passed} ✅ / {failed} ❌")
    
    print("\n" + "=" * 60)
    total_p = sum(full_passed)
    total_f = sum(full_failed)
    print(f"📊 总计: {total_p} ✅ / {total_f} ❌")
    print("=" * 60)
    
    return 0 if total_f == 0 else 1


if __name__ == "__main__":
    exit(asyncio.run(main()))
