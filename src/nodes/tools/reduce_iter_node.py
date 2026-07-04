"""
@File     :   reduce_iter_node.py
@Desc     :   循环内即时 Reducer — 消费 deterministic_changes 写回 GameState
@Note     :   读取 executed_actions[-1].deterministic_changes，
              处理特殊 key（_inventory_append / _mark_searched）后，
              其余普通变化直接作为 state_patch 返回。
              _inventory_append 采用整层玩家槽覆写避免 LangGraph 浅合并丢失兄弟字段。
              _mark_searched 向 EventStore 发射事件并异步更新 PG。
"""

from __future__ import annotations

from src.state.game_state import GameState, get_player
from src.tools import get_logger

logger = get_logger(__name__)


async def _emit_item_state_change(state: GameState, target_key: str):
    """向 EventStore 发射 ITEM_STATE_CHANGE 事件并更新 PG

    非阻塞，失败仅记警告，不影响游戏主循环。
    """
    session_id = state.get("session_id", "")
    if not session_id:
        return
    try:
        from src.memory.event_store import create_event_store
        es = await create_event_store()
        await es.append(
            session_id=session_id,
            event_type="ItemStateChanged",
            data={"target_key": target_key, "new_state": "searched"},
            source_node="reduce_iter",
        )
        # 尝试同步更新 PG interactables 状态
        try:
            from src.state.read_models import StaticReadStore
            store = StaticReadStore()
            conn = await store._get_conn()
            if conn and not conn.is_closed():
                # 只更新非 impromptu_ 前缀的真实物品 key
                if not target_key.startswith("impromptu_"):
                    await conn.execute(
                        "UPDATE interactables SET state = 'searched' WHERE key = $1",
                        target_key,
                    )
                else:
                    # impromptu_ 前缀的即兴标记，写入运行时事件表
                    await conn.execute(
                        """INSERT INTO runtime_events (session_id, event_type, payload, created_at)
                           VALUES ($1, 'impromptu_marked', $2::jsonb, NOW())
                           ON CONFLICT DO NOTHING""",
                        session_id,
                        f'{{"key": "{target_key}"}}',
                    )
        except Exception as pg_e:
            logger.debug(f"reduce_iter: PG 状态回写失败（非阻塞）: {pg_e}")
    except Exception as e:
        logger.debug(f"reduce_iter: 事件发射失败（非阻塞）: {e}")


async def reduce_iter_node(state: GameState) -> dict:
    """循环内即时 Reducer 节点

    消费 deterministic_changes 中的特殊 key:
      _inventory_append — 整层读取玩家槽，追加物品后整层覆写
      _mark_searched    — 发射 ItemStateChanged 事件 + PG 状态回写
    其余普通 key 直接作为 state_patch 返回。
    """
    actions = state.get("executed_actions", [])
    if not actions:
        return {}

    last = actions[-1]
    changes = dict(last.get("deterministic_changes", {}))

    if not changes:
        return {}

    patch = {}

    # _inventory_append — 整层玩家槽覆写
    # 支持单物品 str（即兴落包）和批量物品 list（线索 loot_items）
    append_item = changes.pop("_inventory_append", None)
    if append_item:
        items_to_add = append_item if isinstance(append_item, list) else [append_item]
        uid = state.get("user_id", "default")
        player = dict(get_player(state, uid))
        char = dict(player.get("character", {}))
        inv = list(char.get("inventory", []))
        for item in items_to_add:
            if item and item not in inv:
                inv.append(item)
                logger.debug(f"reduce_iter: 落包 '{item}' (共 {len(inv)} 件)")
        char["inventory"] = inv
        player["character"] = char
        patch["players"] = {uid: player}

    # _mark_searched — 发射事件 + 更新 PG
    mark = changes.pop("_mark_searched", None)
    if mark:
        await _emit_item_state_change(state, mark)

    # 剩余普通变化直接转发
    patch.update(changes)

    if patch:
        logger.debug(f"reduce_iter: 即时回写 {list(patch.keys())}")

    return patch
