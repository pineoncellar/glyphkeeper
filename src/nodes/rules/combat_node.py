"""
@File     :   combat_node.py
@Desc     :   战斗规则节点 — 执行战斗行动裁决与伤害计算
@Note     :   100% 确定性逻辑，调用 domain/combat_rules.py

Node 签名:
    async def combat_node(state: GameState) -> dict:
        从 intent 提取战斗参数 → 调用 resolve_combat_round → 返回 resolution
"""

from __future__ import annotations

from typing import Any, Optional
from src.state.game_state import GameState
from src.domain.combat_rules import (
    WeaponStats, CombatRoundResult, WEAPONS,
    resolve_combat_round, calculate_damage, determine_hit_location,
    apply_armor, parse_damage_bonus,
)
from src.domain.coc_rules import SuccessLevel
from src.tools import get_logger

logger = get_logger(__name__)


def _get_weapon(weapon_name: str) -> Optional[WeaponStats]:
    """按名称查找武器（支持模糊匹配）"""
    if not weapon_name:
        return None
    # 精确匹配
    if weapon_name in WEAPONS:
        return WEAPONS[weapon_name]
    # 模糊匹配
    for key, wpn in WEAPONS.items():
        if weapon_name in key or key in weapon_name:
            return wpn
    return None


async def combat_node(state: GameState) -> dict:
    """
    战斗裁决节点。

    从 intent_queue 读取当前战斗意图，执行完整战斗轮裁决。
    结果以 executed_actions 追加，同时更新 combat_active 战斗轮标记。
    """
    idx = state.get("current_intent_idx", 0)
    queue = state.get("intent_queue", [])
    current_intent = queue[idx] if idx < len(queue) else {}
    intent_data = current_intent.get("data", {})
    character_data = state.get("character")

    action = intent_data.get("action", "attack")
    weapon_name = intent_data.get("weapon_name", "拳头")
    skill_name = intent_data.get("skill_name", "斗殴")

    actor_name = intent_data.get("character_name", "")
    if not actor_name and character_data:
        actor_name = character_data.get("name", "调查员")

    target_name = intent_data.get("target_name", "未知目标")
    target_armor = intent_data.get("target_armor", 0)
    bonus_dice = intent_data.get("bonus_dice", 0)
    target_bonus = intent_data.get("target_bonus", 0)

    actor_skill = intent_data.get("skill_value")
    if actor_skill is None and character_data:
        skills = character_data.get("skills") or {}
        actor_skill = skills.get(skill_name)

    target_skill_name = intent_data.get("target_skill", "闪避")
    target_skill = intent_data.get("target_skill_value")
    if target_skill is None:
        combatants = state.get("combatants", [])
        for c in combatants:
            if c.get("name") == target_name:
                skills = c.get("skills") or {}
                target_skill = skills.get(target_skill_name)
                target_armor = c.get("armor", target_armor)
                break

    if actor_skill is None:
        actor_skill = 50
    if target_skill is None:
        target_skill = 50

    weapon = _get_weapon(weapon_name)
    if weapon is None:
        logger.warning(f"combat_node: 未知武器 '{weapon_name}'，使用拳头")
        weapon = WEAPONS["拳头"]

    db = intent_data.get("damage_bonus", "0")
    if db == "0" and character_data:
        db = character_data.get("damage_bonus", "0")

    resolution = None
    try:
        if action == "dodge":
            from src.domain.checks import skill_check
            dodge_result = skill_check(actor_skill, bonus_dice=bonus_dice)
            resolution = {
                "success": True,
                "node_type": "combat_dodge",
                "action": "dodge",
                "actor_name": actor_name,
                "skill_value": actor_skill,
                "roll_value": dodge_result.roll_value,
                "success_level": dodge_result.success_level.value,
                "is_success": dodge_result.is_success,
                "description": f"{actor_name}尝试闪避，"
                f"{'成功' if dodge_result.is_success else '失败'}"
                f"（骰出{dodge_result.roll_value}）",
            }
        else:
            round_result: CombatRoundResult = resolve_combat_round(
                actor_skill=actor_skill,
                target_skill=target_skill,
                weapon=weapon,
                db=db,
                target_armor=target_armor,
                actor_bonus=bonus_dice,
                target_bonus=target_bonus,
            )

            resolution = {
                "success": True,
                "node_type": "combat_attack",
                "action": action,
                "actor_name": actor_name,
                "target_name": target_name,
                "attack_roll": round_result.attack_roll,
                "hit": round_result.hit,
                "damage": round_result.damage,
                "hit_location": round_result.hit_location,
                "armor_reduced": round_result.armor_reduced,
                "net_damage": round_result.net_damage,
                "weapon": weapon.name,
                "weapon_damage": weapon.damage,
                "db": db,
            }

            if round_result.hit:
                desc = (
                    f"{actor_name}使用{weapon.name}攻击"
                    f"{target_name}，命中{round_result.hit_location}！"
                    f"造成{round_result.damage}点伤害"
                )
                if round_result.armor_reduced > 0:
                    desc += f"（护甲吸收{round_result.armor_reduced}点）"
                desc += f"，实际伤害{round_result.net_damage}点"
                resolution["description"] = desc
            else:
                resolution["description"] = (
                    f"{actor_name}攻击{target_name}，被闪避"
                )

            if round_result.hit and round_result.net_damage > 0:
                combatants = list(state.get("combatants", []))
                for c in combatants:
                    if c.get("name") == target_name:
                        hp = c.get("hit_points", 0) - round_result.net_damage
                        c["hit_points"] = max(0, hp)
                        break
                resolution["updated_combatants"] = combatants

        logger.info(
            f"combat_node: {actor_name} {action} vs {target_name} "
            f"→ {'命中' if resolution.get('hit') else '未命中'}"
        )

        return {
            "executed_actions": [{
                "intent_id": f"intent_{idx}",
                "intent_type": "COMBAT_ACTION",
                "rule_context": resolution,
                "deterministic_changes": {
                    "combat_active": True,
                },
                "raw_fixed_text": "",
                "flavor_context": current_intent.get("flavor_context", ""),
            }],
            "combat_active": True,
            "combat_round": state.get("combat_round", 0) + 1,
        }

    except Exception as e:
        logger.error(f"combat_node: 战斗裁决失败: {e}")
        return {
            "resolution": {
                "success": False,
                "error": str(e),
                "node_type": "combat",
                "action": action,
                "actor_name": actor_name,
            },
        }


async def init_combat_node(state: GameState) -> dict:
    """
    战斗初始化节点 — 设置战斗轮状态。

    intent.data 期望字段:
        enemies: list[dict]    — 敌人列表 [{name, skills, hit_points, armor, ...}]
        allies: list[dict]     — 队友列表（可选）
    """
    intent = state.get("intent") or {}
    intent_data = intent.get("data") or {}

    combatants = list(state.get("combatants", []))
    enemies = intent_data.get("enemies", [])
    allies = intent_data.get("allies", [])

    # 添加角色
    character_data = state.get("character")
    if character_data and not any(c.get("name") == character_data.get("name") for c in combatants):
        combatants.append({
            "name": character_data.get("name", "调查员"),
            "skills": character_data.get("skills", {}),
            "hit_points": character_data.get("hit_points", 10),
            "max_hit_points": character_data.get("max_hit_points", 10),
            "armor": character_data.get("armor", 0),
            "damage_bonus": character_data.get("damage_bonus", "0"),
            "is_player": True,
        })

    for e in enemies:
        if not any(c.get("name") == e.get("name") for c in combatants):
            combatants.append({**e, "is_player": False})

    for a in allies:
        if not any(c.get("name") == a.get("name") for c in combatants):
            combatants.append({**a, "is_player": True})

    logger.info(
        f"init_combat_node: 战斗开始，参战方 {len(combatants)} 人"
    )

    return {
        "combat_active": True,
        "combat_round": 1,
        "combatants": combatants,
        "game_phase": "combat",
        "resolution": {
            "success": True,
            "node_type": "combat_init",
            "combatants_count": len(combatants),
            "enemies_count": len(enemies),
        },
    }
