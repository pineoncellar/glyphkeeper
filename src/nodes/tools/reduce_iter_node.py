"""
@File     :   reduce_iter_node.py
@Desc     :   循环内即时 Reducer — 消费 deterministic_changes 写回 GameState，随后评估触发器
@Note     :   先处理 deterministic_changes 中特殊 key（_inventory_append / _mark_searched 等），
              剩余普通变化直接作为 state_patch 返回。
              随后对当前 state 跑一轮 TriggerEvaluator，命中则将追加 patch 和 echo_text
              合并到返回结果中。整个过程零拓扑改动，触发器求值失败仅记警告。
              _inventory_append 采用整层玩家槽覆写避免 LangGraph 浅合并丢失兄弟字段。
              _mark_searched 向 EventStore 发射事件并异步更新 PG。
"""

from __future__ import annotations

from src.state.game_state import GameState, get_player, get_current_player
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
        # 落包后同步清理掉落池（捡回地上物品后销账）
        dropped = dict(state.get("_dropped_items", {}))
        cleaned = False
        for loc, items in list(dropped.items()):
            for item in items_to_add:
                if item in items:
                    items.remove(item)
                    cleaned = True
        if cleaned:
            patch["_dropped_items"] = dropped

    # _inventory_remove — 整层玩家槽覆写，剔除物品
    remove_item = changes.pop("_inventory_remove", None)
    if remove_item:
        uid = state.get("user_id", "default")
        player = dict(get_player(state, uid))
        char = dict(player.get("character", {}))
        inv = list(char.get("inventory", []))
        if remove_item in inv:
            inv.remove(remove_item)
            logger.debug(f"reduce_iter: 离包 '{remove_item}' (剩 {len(inv)} 件)")
        char["inventory"] = inv
        player["character"] = char
        patch["players"] = {uid: player}

    # _scene_interactable_append — 将物品写入当前场景的掉落池
    drop_item = changes.pop("_scene_interactable_append", None)
    if drop_item:
        current_loc = get_current_player(state).get("current_location", "")
        if current_loc:
            dropped = dict(state.get("_dropped_items", {}))
            dropped.setdefault(current_loc, [])
            if drop_item not in dropped[current_loc]:
                dropped[current_loc].append(drop_item)
                logger.debug(f"reduce_iter: 场景落物 '{drop_item}' @ {current_loc}")
            patch["_dropped_items"] = dropped

    # _mark_searched — 发射事件 + 更新 PG
    mark = changes.pop("_mark_searched", None)
    if mark:
        await _emit_item_state_change(state, mark)

    # 剩余普通变化直接转发
    patch.update(changes)

    # ── 触发器评估：在 deterministic 回写之后，对当前 state 快照跑匹配 ──
    # 命中则合并追加 patch + echo_text，失败仅记警告不影响游戏主循环
    trigger_result = await _evaluate_triggers_after_reduce(state, patch)

    if trigger_result:
        trig_patch = trigger_result.get("patch", {})
        # 合并触发器 patch 到主 patch
        for k, v in trig_patch.items():
            if k in patch:
                if isinstance(patch[k], list) and isinstance(v, list):
                    patch[k].extend(v)
                elif isinstance(patch[k], dict) and isinstance(v, dict):
                    patch[k].update(v)
                else:
                    patch[k] = v
            else:
                patch[k] = v

        # 追加 echo_text 到本轮最后一条 action 的 flavor_context
        echo_text = trigger_result.get("echo_text", "")
        if echo_text and actions:
            flavor = actions[-1].get("flavor_context", "")
            if flavor:
                actions[-1]["flavor_context"] = flavor + "\n" + echo_text
            else:
                actions[-1]["flavor_context"] = echo_text

        # 结团标记由 engine 层在 graph.ainvoke 返回后拦截
        if trigger_result.get("ending_id"):
            patch["control"] = "SUSPEND_ENDING"
            patch["_ending_id"] = trigger_result["ending_id"]

    if patch:
        logger.debug(f"reduce_iter: 即时回写 {list(patch.keys())}")

    return patch


# ====================================================================
# 触发器评估辅助
# ====================================================================

async def _evaluate_triggers_after_reduce(state: dict, current_patch: dict) -> dict:
    """在 reduce_iter 末尾执行触发器评估

    从 PG 加载静态触发器 + 运行时状态，构造 EvalContext 后求值。
    全程 try-and-ignore，失败仅记 warning，不抛异常。
    """
    session_id = state.get("session_id", "")
    world_id = state.get("world_id", "")
    module_name = state.get("scenario_name", "")

    if not session_id or not module_name:
        return {}

    try:
        from src.state.read_models import StaticReadStore
        store = StaticReadStore()
        conn = await store._get_conn()

        # 加载静态触发器
        triggers = await store.get_triggers_by_module(module_name, world_id)
        if not triggers:
            return {}

        # 加载运行时状态
        trigger_states = await store.get_trigger_states(session_id)

        # 构造求值上下文
        from src.domain.triggers import EvalContext, evaluate_triggers

        # 组装一个合成 state 供触发器求值：当前 state + 本轮 patch 的推测结果
        # 这样触发器能"看到"本轮刚刚发生的变更（如物品入包、位置变化）
        merged_state = dict(state)
        for k, v in current_patch.items():
            if k == "players":
                # 玩家槽深度合并（避免整层覆写丢失未变更的兄弟玩家）
                merged_players = dict(merged_state.get("players", {}))
                for uid, pdata in v.items():
                    merged_players[uid] = {**merged_players.get(uid, {}), **pdata}
                merged_state["players"] = merged_players
            elif isinstance(merged_state.get(k), list) and isinstance(v, list):
                merged_state[k] = merged_state.get(k, []) + v
            elif isinstance(merged_state.get(k), dict) and isinstance(v, dict):
                merged_state[k] = {**merged_state.get(k, {}), **v}
            else:
                merged_state[k] = v

        ctx = EvalContext(
            state=merged_state,
            session_id=session_id,
            world_id=world_id,
            read_store=store,
        )

        result = evaluate_triggers(triggers, trigger_states, ctx)

        fired = result.get("fired_triggers", [])
        if not fired:
            return {}

        # 更新 PG 运行时状态
        for tid in fired:
            await store.upsert_trigger_state(
                session_id, tid,
                increment_fired=True,
                disable=any(
                    t.get("is_one_off", True)
                    for t in triggers if t.get("trigger_id") == tid
                ),
            )

        logger.info(f"reduce_iter: 触发器命中 {fired}")

        return {
            "patch": result.get("patch", {}),
            "echo_text": result.get("echo_text", ""),
            "ending_id": result.get("ending_id", ""),
        }

    except Exception as e:
        logger.warning(f"reduce_iter: 触发器评估失败（非阻塞）: {e}")
        return {}
