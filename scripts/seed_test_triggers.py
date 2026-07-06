# -*- coding: utf-8 -*-
"""
@File     :   seed_test_triggers.py
@Desc     :   向 PG static_triggers 表写入测试触发器，供 ./start 后手动验证触发求值
@Note     :   独立于模组摄入管线运行。写入的触发器关联 module_name=mtest，
              用 /start mtest 开新世界后即可在游戏过程中自动触发。

使用方式:
    uv run python scripts/seed_test_triggers.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# 将项目根目录加入 Python 路径，确保 from src 导入可用
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


_TEST_TRIGGERS = [
    {
        "trigger_id": "test_enter_ghoul_nest",
        "module_name": "mtest",
        "description": "踏入食尸鬼巢穴时锁死退路，并刷出一把神秘钥匙（需要 /start mtest 世界才有此场景）",
        "priority": 10,
        "is_one_off": True,
        "conditions_json": {
            "type": "AT_LOCATION",
            "params": {"key": "loc_ghoul_nest"},
        },
        "actions_json": [
            {
                "type": "MODIFY_LOCATION_DESC",
                "params": {
                    "location_key": "loc_ghoul_nest",
                    "new_desc_suffix": (
                        "身后的铁门轰然关闭，发出刺耳的金属摩擦声。"
                        "退路已被彻底封死。"
                    ),
                },
            },
            {
                "type": "SPAWN_ITEM",
                "params": {
                    "location_key": "loc_ghoul_nest",
                    "item_key": "rusted_iron_key",
                },
            },
            {
                "type": "SET_GLOBAL_FLAG",
                "params": {"key": "nest_entrance_sealed"},
            },
            {
                "type": "APPEND_ECHO",
                "params": {"text": "在你身后，沉重的铁门哐当一声自动合拢，门闩咔嗒锁死。同时你注意到墙角的碎石堆里露出一截锈蚀的铁钥匙。"},
            },
        ],
    },
    {
        "trigger_id": "test_pickup_key_triggers_ending",
        "module_name": "mtest",
        "description": "拿到 rusted_iron_key 后触发结局——测试 TRIGGER_ENDING 动作",
        "priority": 5,
        "is_one_off": True,
        "conditions_json": {
            "type": "HAS_ITEM",
            "params": {"key": "rusted_iron_key"},
        },
        "actions_json": [
            {
                "type": "GRANT_TAG",
                "params": {"tag": "ending_key_obtained"},
            },
            {
                "type": "APPEND_ECHO",
                "params": {"text": "当你握住那把锈蚀的铁钥匙时，整座巢穴开始震颤。天花板裂缝中落下碎石，你知道——终于到了做个了断的时刻。"},
            },
            {
                "type": "TRIGGER_ENDING",
                "params": {"ending_id": "test_iron_key_ending"},
            },
        ],
    },
    {
        "trigger_id": "test_tag_unlocks_second_trigger",
        "module_name": "mtest",
        "description": "演示级联触发器：拿到钥匙且处于 hub 时刷出逃生密道",
        "priority": 3,
        "is_one_off": True,
        "conditions_json": {
            "AND": [
                {"type": "HAS_ITEM", "params": {"key": "rusted_iron_key"}},
                {"type": "AT_LOCATION", "params": {"key": "loc_test_hub"}},
            ],
        },
        "actions_json": [
            {
                "type": "MODIFY_LOCATION_DESC",
                "params": {
                    "location_key": "loc_test_hub",
                    "new_desc_suffix": "原本平整的西墙现出了一道暗门的轮廓。钥匙孔的形状与你手中那把锈蚀铁钥匙完全吻合。",
                },
            },
            {
                "type": "APPEND_ECHO",
                "params": {"text": "西墙上一道细长的裂纹吸引了你的注意——那看起来像是一扇隐藏的暗门。"},
            },
        ],
    },
    {
        "trigger_id": "test_sanity_below_30",
        "module_name": "mtest",
        "description": "理智低于 30 时触发幻觉低语",
        "priority": 0,
        "is_one_off": False,
        "conditions_json": {
            "type": "SANITY_BELOW",
            "params": {"threshold": 30},
        },
        "actions_json": [
            {
                "type": "APPEND_ECHO",
                "params": {"text": "你感到耳边响起了低沉的呓语，似乎来自墙壁深处……理智正在瓦解的征兆。"},
            },
            {
                "type": "GRANT_TAG",
                "params": {"tag": "hearing_whispers"},
            },
        ],
    },
]


async def main():
    from src.state.read_models import StaticReadStore
    from src.memory.vector_store import VectorStore

    seed_ws = VectorStore.seed_workspace_name("mtest")
    store = StaticReadStore()
    conn = await store.connect_script()

    # 先查已存在的触发器数量
    existing = await conn.fetchval(
        "SELECT COUNT(*) FROM static_triggers WHERE module_name='mtest' AND world_id=$1",
        seed_ws,
    )
    print(f"种子 '{seed_ws}' 中已有 {existing} 条触发器")

    count = await store.bulk_insert_triggers(_TEST_TRIGGERS, world_id=seed_ws)
    print(f"写入 {count}/{len(_TEST_TRIGGERS)} 条测试触发器到种子 '{seed_ws}'")

    # 验证
    rows = await conn.fetch(
        "SELECT trigger_id, description, world_id FROM static_triggers"
        " WHERE module_name='mtest' ORDER BY priority DESC"
    )
    print(f"\n当前 static_triggers 中 mtest 的触发器列表:")
    for r in rows:
        wid_short = r["world_id"][:20] if r["world_id"] else "(共享)"
        print(f"  {r['trigger_id']:40s} | {r['description'][:40]:40s} | 世界: {wid_short}")

    await store.close()
    print(f"\n[OK] 测试触发器已写入种子 '{seed_ws}'。")
    print("下次 /start mtest 开新世界时，触发器会自动复制到新世界。")


if __name__ == "__main__":
    asyncio.run(main())
