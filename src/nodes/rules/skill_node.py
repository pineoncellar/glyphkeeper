"""
@File     :   skill_node.py
@Desc     :   技能检定节点 — 执行技能检定并返回结构化结果
@Note     :   100% 确定性逻辑，调用 domain/checks.py

Node 签名:
    async def skill_node(state: GameState) -> dict:
        从 intent 提取 skill_name → 查角色技能值 → 执行 skill_check → 返回 resolution
"""

from __future__ import annotations

from typing import Any
from src.state.game_state import GameState
from src.domain.checks import skill_check, CheckResult
from src.domain.coc_rules import Difficulty, SuccessLevel
from src.domain.character import Character
from src.tools import get_logger

logger = get_logger(__name__)


def _parse_difficulty(diff_str: Any) -> Difficulty:
    """安全解析难度枚举"""
    if isinstance(diff_str, Difficulty):
        return diff_str
    if isinstance(diff_str, str):
        try:
            return Difficulty(diff_str.upper())
        except ValueError:
            pass
    return Difficulty.REGULAR


def _success_label(level: SuccessLevel) -> str:
    return {
        SuccessLevel.CRITICAL: "大成功",
        SuccessLevel.EXTREME: "极难成功",
        SuccessLevel.HARD: "困难成功",
        SuccessLevel.REGULAR: "常规成功",
        SuccessLevel.FAILURE: "失败",
        SuccessLevel.FUMBLE: "大失败",
    }.get(level, "未知")


async def skill_node(state: GameState) -> dict:
    """
    技能检定节点。

    从 state["intent"] 中读取检定参数，执行技能检定。

    intent.data 期望字段:
        skill_name: str        — 技能名称（如 "侦查", "图书馆利用"）
        skill_value: int       — 技能值（可选，默认从角色数据读取）
        difficulty: str        — 难度等级（REGULAR/HARD/EXTREME）
        bonus_dice: int        — 奖励骰（默认 0）
        penalty_dice: int      — 惩罚骰（默认 0）
    """
    intent = state.get("intent") or {}
    intent_data = intent.get("data") or {}

    skill_name = intent_data.get("skill_name", "")
    difficulty = _parse_difficulty(intent_data.get("difficulty", "REGULAR"))
    bonus_dice = intent_data.get("bonus_dice", 0)
    penalty_dice = intent_data.get("penalty_dice", 0)

    if not skill_name:
        logger.warning("skill_node: 缺少 skill_name")
        return {
            "resolution": {
                "success": False,
                "error": "缺少技能名称",
                "skill_name": "",
            },
        }

    # ── 获取技能值 ──
    skill_value = intent_data.get("skill_value")
    if skill_value is None:
        # 尝试从角色数据读取
        character_data = state.get("character")
        if character_data:
            skills = character_data.get("skills") or {}
            skill_value = skills.get(skill_name)
            if skill_value is None:
                # 尝试模糊匹配
                for k, v in skills.items():
                    if skill_name in k or k in skill_name:
                        skill_value = v
                        break

    if skill_value is None:
        skill_value = intent_data.get("skill_value", 50)
        logger.debug(f"skill_node: 未找到技能 '{skill_name}' 的值，使用默认值 {skill_value}")

    # ── 执行检定 ──
    try:
        result: CheckResult = skill_check(skill_value, difficulty, bonus_dice, penalty_dice)
    except Exception as e:
        logger.error(f"skill_node: 检定失败: {e}")
        return {
            "resolution": {
                "success": False,
                "error": str(e),
                "skill_name": skill_name,
            },
        }

    # ── 构建结果 ──
    resolution = {
        "success": True,
        "node_type": "skill_check",
        "skill_name": skill_name,
        "skill_value": skill_value,
        "roll_value": result.roll_value,
        "success_level": result.success_level.value,
        "success_label": _success_label(result.success_level),
        "is_success": result.is_success,
        "is_failure": result.is_failure,
        "difficulty": difficulty.value,
        "bonus_dice": bonus_dice,
        "penalty_dice": penalty_dice,
        "is_push": result.is_push,
    }

    logger.info(
        f"skill_node: {skill_name}({skill_value}) "
        f"→ roll={result.roll_value} {resolution['success_label']}"
    )

    state_patch = {"resolution": resolution}

    # 检定成功后查询目标是否有关联线索
    # archivist_result 会被传给 NarratorNode 做叙事拼接
    if result.is_success:
        target_key = intent_data.get("target", "")
        if target_key:
            try:
                from src.tools.archivist import Archivist
                session_id = state.get("session_id", "default")
                character_data = state.get("character") or {}
                character_name = character_data.get("name", "")

                archivist = Archivist()
                clue_result = await archivist.inspect_target(
                    session_id=session_id,
                    target_key=target_key,
                    skill_name=skill_name,
                    roll_value=result.roll_value,
                    character_name=character_name,
                )
                if clue_result:
                    state_patch["archivist_result"] = clue_result
                    logger.info(
                        f"skill_node: 线索发现! "
                        f"knowledge={clue_result.get('knowledge_id')}"
                    )
            except Exception as e:
                logger.warning(f"skill_node: Archivist 调用失败: {e}")

    return state_patch


async def batch_skill_check(state: GameState) -> dict:
    """
    批量技能检定节点 — 一次检定多项技能。

    intent.data.skills 格式:
        [{"name": "侦查", "difficulty": "HARD"}, {"name": "聆听", "bonus_dice": 1}]
    """
    intent = state.get("intent") or {}
    intent_data = intent.get("data") or {}
    skills_config = intent_data.get("skills", [])

    if not skills_config:
        return await skill_node(state)

    results = []
    character_data = state.get("character")

    for sc in skills_config:
        sname = sc.get("name", "")
        sdiff = _parse_difficulty(sc.get("difficulty", "REGULAR"))
        sbonus = sc.get("bonus_dice", 0)
        spenalty = sc.get("penalty_dice", 0)

        sval = sc.get("skill_value")
        if sval is None and character_data:
            skills = character_data.get("skills") or {}
            sval = skills.get(sname)

        if sval is None:
            sval = 50

        try:
            r = skill_check(sval, sdiff, sbonus, spenalty)
            results.append({
                "skill_name": sname,
                "skill_value": sval,
                "roll_value": r.roll_value,
                "success_level": r.success_level.value,
                "success_label": _success_label(r.success_level),
                "is_success": r.is_success,
                "difficulty": sdiff.value,
            })
        except Exception as e:
            results.append({
                "skill_name": sname,
                "error": str(e),
            })

    all_success = all(r.get("is_success", False) for r in results)

    return {
        "resolution": {
            "success": True,
            "node_type": "batch_skill_check",
            "results": results,
            "all_success": all_success,
            "success_count": sum(1 for r in results if r.get("is_success")),
            "total_count": len(results),
        },
    }
