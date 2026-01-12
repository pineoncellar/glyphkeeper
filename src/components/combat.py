"""
战斗轮系统处理组件
负责战斗的初始化、结束、行动顺序计算及战斗动作处理
"""
from typing import Any, Dict, Optional, Tuple
from ..core import get_logger
from ..core.events import ResolutionResult
from ..memory.bridge import read_model, queue_model_update
from .base import BaseComponent
from .dice import DiceRoller
from .health import HealthComponent

logger = get_logger(__name__)

class CombatComponent(BaseComponent):
    def initialize(self):
        self.health = HealthComponent(self.engine)

    async def handle_action(self, target: str, action: str, params: dict) -> ResolutionResult:
        """
        统一处理战斗动作的分发
        target: 动作的目标（如果是攻击，则是受击者；如果是逃跑，则是自身）
        action: 动作名称，如 "attack", "maneuver", "disengage", "other"
        params: 其他参数，如 skill_name, weapon_damage 等
        """
        attacker = params.get("attacker", "Unknown") # 某些情况下 target 是受害者，attacker 在 params 里
        
        # 0. 检查行动合理性 (如果在战斗中)
        validity = await self.check_action_validity(attacker, action, params.get("description", ""))
        if not validity.success:
            return validity

        if action == "attack":
            # 这种情况下，target 是受击者
            return await self.attack(
                attacker_name=attacker,
                target_name=target,
                skill_name=params.get("skill_name", "Fighting"),
                weapon_damage=params.get("weapon_damage", "1d3")
            )
        elif action == "maneuver":
            return await self.maneuver(
                attacker_name=attacker,
                target_name=target,
                maneuver_desc=params.get("description", "战技")
            )
        elif action == "disengage":
            # 逃跑的目标是自己
            return await self.disengage(entity_name=target)
        elif action == "other":
            return await self.other_action(
                entity_name=target,
                action_desc=params.get("description", "未知行动")
            )
        
        return ResolutionResult(False, False, f"未知的战斗动作: {action}")

    async def start_combat(self, location_name: str, participants: list[str]) -> ResolutionResult:
        """
        开启战斗轮
        location_name: 发生战斗的场景名称
        participants: 参与战斗的实体名称列表
        """
        # 1. 标记场景
        loc = await read_model("Location", {"name": location_name})
        if not loc:
            return ResolutionResult(True, False, f"场景不存在: {location_name}")
            
        loc_tags = loc.get("tags", []) or []
        if "COMBAT_ACTIVE" not in loc_tags:
            loc_tags.append("COMBAT_ACTIVE")
            queue_model_update("Location", {"id": loc["id"], "tags": loc_tags})
            
        # 2. 标记参与者
        for name in participants:
            entity = await read_model("Entity", {"name": name})
            if entity:
                tags = entity.get("tags", []) or []
                if "IN_COMBAT" not in tags:
                    tags.append("IN_COMBAT")
                    queue_model_update("Entity", {"id": entity["id"], "tags": tags})
        
        return ResolutionResult(True, True, f"战斗开始于 {location_name}，参与者: {', '.join(participants)}")

    async def end_combat(self, location_name: str) -> ResolutionResult:
        """
        结束战斗
        """
        loc = await read_model("Location", {"name": location_name})
        if not loc:
            return ResolutionResult(True, False, f"场景不存在: {location_name}")
            
        # 移除场景 Tag
        loc_tags = loc.get("tags", []) or []
        if "COMBAT_ACTIVE" in loc_tags:
            loc_tags.remove("COMBAT_ACTIVE")
            queue_model_update("Location", {"id": loc["id"], "tags": loc_tags})
            
        # 移除该场景下所有实体的 IN_COMBAT Tag
        entities = await read_model("Entity", {"location_id": loc["id"]}, one=False) or []
        for entity in entities:
            tags = entity.get("tags", []) or []
            if "IN_COMBAT" in tags:
                tags.remove("IN_COMBAT")
                queue_model_update("Entity", {"id": entity["id"], "tags": tags})
                
        return ResolutionResult(True, True, f"战斗结束于 {location_name}")

    async def initialize_combat_round(self, location_name: str) -> Dict[str, Any]:
        """
        初始化/重置战斗轮，计算行动顺序
        返回: {"ok": bool, "order": List[str], "details": str}
        """
        loc = await read_model("Location", {"name": location_name})
        if not loc:
            return {"ok": False, "reason": f"场景不存在: {location_name}"}
            
        # 获取该场景下所有带有 IN_COMBAT 标签的实体
        # 由于 read_model 只能简单的 exact match，我们先获取所有实体再过滤
        all_entities = await read_model("Entity", {"location_id": loc["id"]}, one=False) or []
        combatants = []
        
        for entity in all_entities:
            tags = entity.get("tags", []) or []
            if "IN_COMBAT" in tags:
                stats = entity.get("stats", {}) or {}
                dex = int(stats.get("DEX", 50))
                combatants.append({"name": entity["name"], "dex": dex})
        
        # 按 DEX 降序排序
        # 规则书：DEX 相同比较 Combat Skill? 这里暂且随机或保持原样
        combatants.sort(key=lambda x: x["dex"], reverse=True)
        
        order_names = [c["name"] for c in combatants]
        details = "行动顺序: " + " -> ".join([f"{c['name']}({c['dex']})" for c in combatants])
        
        return {
            "ok": True, 
            "order": order_names, 
            "details": details
        }

    async def check_action_validity(self, entity_name: str, action_type: str, action_desc: str) -> ResolutionResult:
        """
        检查行动在当前状态下是否合理
        如果在战斗中，非战斗动作（如撬锁、阅读）可能会被拒绝
        """
        entity = await read_model("Entity", {"name": entity_name})
        if not entity:
            # 实体不存在，不做限制
            return ResolutionResult(True, True, "实体不存在，跳过检查")
            
        tags = entity.get("tags", []) or []
        if "IN_COMBAT" not in tags:
            return ResolutionResult(True, True, "不在战斗中，行动允许")
            
        # 在战斗中
        if action_type in ["attack", "maneuver", "disengage"]:
            return ResolutionResult(True, True, "战斗动作允许")
            
        # 其他动作检查
        # 简单的关键词过滤
        # TODO: 接入 LLM 进行更复杂的语义判断
        # 允许的快速动作关键词
        allowed_keywords = ["喊", "说", "看", "瞥", "shout", "yell", "look", "glance", "quick"]
        # 禁止的耗时动作关键词
        forbidden_keywords = ["撬锁", "阅读", "搜查", "急救", "治疗", "lockpick", "read", "search", "first aid", "heal"]
        
        desc_lower = action_desc.lower()
        
        for kw in forbidden_keywords:
            if kw in desc_lower:
                return ResolutionResult(True, False, f"战斗中无法执行耗时行动: {action_desc} (包含关键词 '{kw}')")
                
        # 默认允许，但给个警告提示（可选）
        return ResolutionResult(True, True, "战斗中执行非标准动作 (需KP/LLM裁定耗时)")

    async def attack(self, attacker_name: str, target_name: str, skill_name: str, weapon_damage: str = "1d3") -> ResolutionResult:
        """
        发动一次攻击
        1. 攻击者检定技能 (Fighting/Shooting)
        2. 防御者检定闪避 (Dodge)
        3. 对抗判定
        4. 结算伤害
        """
        # 1. 获取数据
        attacker = await read_model("Entity", {"name": attacker_name})
        target = await read_model("Entity", {"name": target_name})
        
        if not attacker or not target:
            return ResolutionResult(True, False, "攻击者或目标不存在")
            
        # 2. 攻击检定
        # 获取攻击技能值
        attacker_stats = attacker.get("stats", {}) or {}
        # 如果是 Fighting，通常指的是 Fighting (Brawl)
        atk_skill_val = int(attacker_stats.get(skill_name, attacker_stats.get("Fighting", 25)))
        
        atk_result = DiceRoller.check_success(atk_skill_val)
        atk_desc = self._get_success_level_desc(atk_result)
        
        # 3. 防御检定 (默认闪避)
        target_stats = target.get("stats", {}) or {}
        dodge_val = int(target_stats.get("Dodge", int(target_stats.get("DEX", 50)) // 2))
        
        def_result = DiceRoller.check_success(dodge_val)
        def_desc = self._get_success_level_desc(def_result)
        
        log_desc = f"{attacker_name} 使用 {skill_name}({atk_skill_val}) 攻击 -> {atk_desc}\n" \
                   f"{target_name} 尝试闪避({dodge_val}) -> {def_desc}"

        # 4. 对抗判定 (CoC 7e: 攻击者成功等级 >= 防御者成功等级? 不，闪避是大成功赢成功，成功赢失败等)
        # check_success 返回: 0大成功, 1极难, 2困难, 3成功, 4失败, 5大失败
        # 数值越小越好
        
        hit = False
        is_counter_attack = False # 是否反击（暂未实现反击逻辑，仅实现闪避）
        
        if atk_result > 3: # 攻击失败 (4, 5)
            outcome = "攻击未命中"
        elif def_result < atk_result: # 防御方成功等级更高 (数值更小)
            outcome = "被闪避"
        elif def_result == atk_result and def_result <= 3: 
            # 同等级成功，攻击者优先? 规则书: "Dodge: The defender wins ties"
            outcome = "被闪避 (同等级)"
        else:
            # 攻击者成功等级更高，或防御者失败
            hit = True
            outcome = "命中"

        final_desc = f"{log_desc}\n结果: {outcome}"
        
        if hit:
            # 5. 伤害结算
            dmg_res = DiceRoller.roll(weapon_damage)
            damage = dmg_res.total
            
            # 计算 DB (Damage Bonus) - 仅近战有效，暂定 Fighting 都是近战
            if "Fighting" in skill_name or "Brawl" in skill_name:
                db, _ = self._calculate_db_and_build(attacker_stats)
                if db:
                    db_res = DiceRoller.roll(db)
                    damage += db_res.total
                    final_desc += f"\n伤害加值: {db_res.details}"
            
            # 极难成功(1)及以上: 某些规则下满伤或穿刺，这里暂简化为正常伤害
            # TODO: 穿刺规则
            
            final_desc += f"\n造成伤害: {damage} ({weapon_damage}={dmg_res.details})"
            
            # 应用伤害
            health_res = await self.health.inflict_damage(target_name, damage)
            if health_res["ok"]:
                final_desc += f"\n{target_name} 状态: {health_res.get('description', '')}"
            else:
                final_desc += f"\n伤害应用失败: {health_res.get('reason')}"

        return ResolutionResult(True, hit, final_desc)

    async def maneuver(self, attacker_name: str, target_name: str, maneuver_desc: str) -> ResolutionResult:
        """
        执行战技 (Combat Maneuver)
        流程：
        1. 比较 Build (体格)
        2. 如果 Attacker Build < Target Build - 3，直接失败
        3. 否则进行 Fighting (Brawl) 对抗
        """
        attacker = await read_model("Entity", {"name": attacker_name})
        target = await read_model("Entity", {"name": target_name})
        
        if not attacker or not target:
            return ResolutionResult(True, False, "实体不存在")
            
        attacker_stats = attacker.get("stats", {}) or {}
        target_stats = target.get("stats", {}) or {}
        
        _, atk_build = self._calculate_db_and_build(attacker_stats)
        _, def_build = self._calculate_db_and_build(target_stats)
        
        if atk_build < def_build - 2:
            return ResolutionResult(True, False, f"战技失败: 体格差距过大 ({atk_build} vs {def_build})")
            
        # 惩罚骰数量
        penalty_dice = 0
        if atk_build < def_build:
            penalty_dice = def_build - atk_build
            
        # 战技检定 (Fighting Brawl)
        skill_val = int(attacker_stats.get("Fighting", 25))
        
        # 简单的惩罚骰模拟: 减去惩罚骰数量 * 10 (近似) 或者调用带 advantage 的 check
        # 这里 DiceRoller.check_success 支持 advantage (负数为惩罚)
        atk_result = DiceRoller.check_success(skill_val, advantage=-penalty_dice)
        
        # 防御者 (Dodge or Fighting)
        # 假设防御者用 Fighting 抵抗
        def_skill_val = int(target_stats.get("Fighting", 25))
        def_result = DiceRoller.check_success(def_skill_val)
        
        atk_desc = self._get_success_level_desc(atk_result)
        def_desc = self._get_success_level_desc(def_result)
        
        log = f"{attacker_name} 施展战技 '{maneuver_desc}' (Build {atk_build} vs {def_build}, 惩罚 {penalty_dice})\n" \
              f"攻击检定: {atk_desc}, 防御检定: {def_desc}"
              
        success = False
        if atk_result > 3:
            outcome = "失败"
        elif def_result < atk_result:
            outcome = "被抵抗"
        elif def_result == atk_result:
            outcome = "被抵抗 (同级)" # 战技通常也视为攻击，攻击者需胜出？规则书: "The side with the higher level of success wins." Ties go to defender usually? "If both achieve same level... the maneuver fails."
        else:
            success = True
            outcome = "成功"
            
        return ResolutionResult(True, success, f"{log}\n结果: {outcome}")

    async def disengage(self, entity_name: str) -> ResolutionResult:
        """
        脱离战斗
        通常需要检定 DEX 或 Dodge，或者是 Move 对抗
        这里简化为一次 DEX 检定
        """
        entity = await read_model("Entity", {"name": entity_name})
        if not entity:
            return ResolutionResult(True, False, "实体不存在")
            
        stats = entity.get("stats", {}) or {}
        dex = int(stats.get("DEX", 50))
        
        res = DiceRoller.check_success(dex)
        desc = self._get_success_level_desc(res)
        
        success = res <= 3
        return ResolutionResult(True, success, f"{entity_name} 试图脱离战斗 (DEX {dex}): {desc}")

    async def other_action(self, entity_name: str, action_desc: str) -> ResolutionResult:
        """
        其他行动
        """
        return ResolutionResult(True, True, f"{entity_name} 执行了行动: {action_desc}")

    def _get_success_level_desc(self, level: int) -> str:
        mapping = {
            0: "大成功",
            1: "极难成功",
            2: "困难成功",
            3: "成功",
            4: "失败",
            5: "大失败"
        }
        return mapping.get(level, "未知")

    def _calculate_db_and_build(self, stats: Dict[str, Any]) -> Tuple[Optional[str], int]:
        """
        计算伤害加值 (DB) 和体格 (Build)
        基于 STR + SIZ
        """
        strength = int(stats.get("STR", 50))
        size = int(stats.get("SIZ", 50))
        total = strength + size
        
        if total <= 64:
            return "-2", -2
        elif total <= 84:
            return "-1", -1
        elif total <= 124:
            return "0", 0
        elif total <= 164:
            return "1d4", 1
        elif total <= 204:
            return "1d6", 2
        elif total <= 284:
            return "2d6", 3
        elif total <= 364:
            return "3d6", 4
        elif total <= 444:
            return "4d6", 5
        else:
            # 445+ 每 80 点增加 1d6 和 +1 Build
            extra = (total - 444) // 80 + 1
            return f"{4 + extra}d6", 5 + extra
