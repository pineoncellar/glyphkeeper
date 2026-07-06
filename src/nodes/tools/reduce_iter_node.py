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
        logger.debug(f"reduce_iter: deterministic_changes 为空, action_type={last.get('intent_type','?')}")
        return {}

    patch = {}
    logger.info(f"reduce_iter: 原始 deterministic_changes keys={list(changes.keys())}")

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

    注意: Graph 运行时玩家字段（current_location 等）暂存于 state 顶层，
    rehome_player_fields() 执行后才搬入 players[uid]。因此 merged_state
    需做同样归位，否则触发器的 AT_LOCATION 等条件会读到 players 中的旧值。
    """
    session_id = state.get("session_id", "")
    world_id = state.get("world_id", "")
    module_name = state.get("scenario_name", "")
    logger.info(
        f"reduce_iter: 触发器评估 session={session_id[:8] if session_id else 'EMPTY'!r} "
        f"module={module_name!r} world={world_id[:16] if world_id else 'EMPTY'!r}"
    )

    if not session_id:
        return {}

    try:
        from src.state.read_models import StaticReadStore
        store = StaticReadStore()
        conn = await store._get_conn()

        # 加载静态触发器：优先按 module_name+world_id 查，
        # 若 module_name 为空则回退到按 world_id 查（兼容 /ev 的查询方式）
        triggers = []
        if module_name:
            triggers = await store.get_triggers_by_module(module_name, world_id)
            if not triggers:
                logger.debug(f"reduce_iter: 按 module='{module_name}' 未查到触发器，尝试按 world_id 回退")
        if not triggers and world_id:
            triggers = await store.get_triggers_by_world(world_id)
            if triggers:
                logger.debug(f"reduce_iter: 按 world_id='{world_id[:20]}' 回退查到 {len(triggers)} 条触发器")
        if not triggers:
            logger.debug(f"reduce_iter: 无触发器 (session={session_id[:8]}, module='{module_name}', world='{world_id[:20]}')")
            return {}

        logger.info(
            f"reduce_iter: 加载了 {len(triggers)} 条触发器, "
            f"trigger_ids=[{', '.join(t.get('trigger_id','')[:30] for t in triggers[:5])}]"
        )

        # 加载运行时状态
        trigger_states = await store.get_trigger_states(session_id)

        # 构造求值上下文
        from src.domain.triggers import EvalContext, evaluate_triggers

        # ── 组装合成 state ──
        # 当前 state + 本轮 patch 的推测结果，并处理玩家字段归位
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

        # ── 玩家字段归位 ──
        # 将顶层属于 players[uid] 的字段同步搬入 players 槽内。
        # Graph 运行时节点将 current_location 等字段写入顶层，
        # 但触发器的 AT_LOCATION 等条件从 players[uid] 读取。
        # 此处以顶层最新值为准覆盖旧值，与 engine.py 的 rehome_player_fields()
        # 方向相反（后者以 players 内值为准），因为此处顶层即本轮回写的最新结果。
        _PLAYER_FIELDS = {
            "character", "current_location", "pending_dice",
            "npc_relations", "current_npc", "npc_dialogue", "npc_dialogue_results",
        }
        uid = state.get("user_id", "default")
        players = merged_state.setdefault("players", {})
        player = players.setdefault(uid, {})
        for field in _PLAYER_FIELDS:
            if field in merged_state and field != "players":
                val = merged_state[field]
                if field == "character" and isinstance(val, dict):
                    existing = player.get("character") or {}
                    existing.update(val)
                    player["character"] = existing
                elif field == "npc_dialogue_results" and isinstance(val, list):
                    existing = player.get("npc_dialogue_results", [])
                    player["npc_dialogue_results"] = existing + val
                else:
                    # 顶层值覆盖 players 中的旧值（顶层即本轮最新写入结果）
                    player[field] = val
        merged_state["players"] = players

        # 调试：确认玩家位置已正确归位
        pid = next(iter(players.keys()), 'N/A')
        loc_in_player = players.get(pid, {}).get("current_location", "NOT_FOUND")
        loc_top = merged_state.get("current_location", "NOT_FOUND")
        logger.info(
            f"reduce_iter: 归位后 players[{pid}].current_location={loc_in_player!r} "
            f"(top={loc_top!r})"
        )

        ctx = EvalContext(
            state=merged_state,
            session_id=session_id,
            world_id=world_id,
            read_store=store,
        )

        result = evaluate_triggers(triggers, trigger_states, ctx)
        logger.info(
            f"reduce_iter: evaluate_triggers 返回 "
            f"fired={result.get('fired_triggers', [])} "
            f"has_patch={bool(result.get('patch', {}))} "
            f"echo={bool(result.get('echo_text', ''))}"
        )

        fired = result.get("fired_triggers", [])
        if not fired:
            logger.debug(f"reduce_iter: 触发器求值完成，未命中任何触发器")
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
