"""
血量系统相关逻辑
"""
from typing import Any, Dict, List, Optional
from ..core import get_logger
from ..memory import fetch_model_data, save_model_data, transaction_context
from .base import BaseComponent
from .dice import DiceRoller

logger = get_logger(__name__)

class HealthComponent(BaseComponent):
    def initialize(self):
        pass

    async def _modify_entity_health(self, entity_name: str, delta: int) -> Dict[str, Any]:
        """
        内部函数，修改实体的血量
        entity_name: 实体名称
        delta: 血量变化值，正值表示恢复，负值表示伤害
        """
        entity = await fetch_model_data("Entity", {"name": entity_name})
        if not entity:
            logger.error(f"实体不存在: {entity_name}")
            return {"ok": False, "reason": f"实体不存在: {entity_name}"}
        
        stats = entity.get("stats", {}) or {}
        
        # 如果目标没有 HP，则设默认值 10
        if "hp" not in stats:
            stats["hp"] = 10
        
        before = int(stats.get("hp", 0))
        after = before + delta
        stats["hp"] = max(after, 0)  # 血量不能低于0
        hp_max = int((int(stats.get("CON", 50)) + int(stats.get("SIZ", 50))) / 10) # 计算血量上限
        stats["hp"] = min(after, hp_max)  # 血量不能高于血量上限
        
        # 保存修改
        await save_model_data("Entity", {"id": entity["id"], "stats": stats})

        logger.debug(f"实体 {entity_name} 血量修改: {before} -> {stats['hp']}")
        return {
            "ok": True,
            "entity": entity["name"],
            "resource": "hp",
            "before": before,
            "delta": delta,
            "after": stats["hp"],
        }
    
    async def first_aid(self, entity_name: str) -> Dict[str, Any]:
        """
        对实体进行急救，恢复血量
        entity_name: 实体名称

        急救是临时的稳定伤势的操作，成功后恢复1点血量，若实体处于濒死状态则会获得一点临时生命值。
        但此后需要一小时内接受医学治疗，否则会再次进入濒死状态。
        """

        modify_data = await self._modify_entity_health(entity_name, 1) # 恢复1点血量
        if not modify_data.get("ok"):
            return {"ok": False, "reason": modify_data.get("reason", "未知错误")}
        
        entity = await fetch_model_data("Entity", {"name": entity_name})
        if not entity:
            logger.error(f"实体不存在: {entity_name}")
            return {"ok": False, "reason": f"实体不存在: {entity_name}"}
        
        tags = entity.get("tags", []) or []
        description = None
        # 急救成功时，若实体为濒死状态则脱离濒死，恢复1点临时生命值
        if "dying" in tags:
            tags.remove("dying")
            tags.append("leave_dying")  # 标记为刚脱离濒死状态
            await save_model_data("Entity", {"id": entity["id"], "tags": tags})
            logger.debug(f"实体 {entity_name} 脱离濒死状态")
            description = "脱离濒死状态"

        return {
            "ok": True,
            "entity": entity["name"],
            "resource": "hp",
            "before": modify_data["before"],
            "delta": modify_data["delta"],
            "after": modify_data["after"],
            "description": description,
        }
    
    async def medicine_heal(self, entity_name: str) -> Dict[str, Any]:
        """
        使用医学治疗实体，恢复1d3的血量
        entity_name: 实体名称

        医学治疗是正式的治疗操作，需要花费至少一小时，而且要求稳定安全的环境，成功后恢复1d3点血量。
        若实体之前处于濒死状态或拥有临时生命值，则脱离濒死状态，移除相关标记。
        """
        # TODO: 濒死时，医学治疗必须在成功的急救后才能进行，但是规则书没说急救失败怎么办
        amount = DiceRoller.roll("1d3").total

        modify_data = await self._modify_entity_health(entity_name, amount)
        if not modify_data.get("ok"):
            return {"ok": False, "reason": modify_data.get("reason", "未知错误")}
        
        entity = await fetch_model_data("Entity", {"name": entity_name})
        if not entity:
            logger.error(f"实体不存在: {entity_name}")
            return {"ok": False, "reason": f"实体不存在: {entity_name}"}
        
        tags = entity.get("tags", []) or []
        description = None
        # 医学治疗成功时，若实体为濒死状态或拥有临时生命值，则脱离濒死，移除标记
        if "dying" in tags or "leave_dying" in tags:
            if "dying" in tags:
                tags.remove("dying")
            if "leave_dying" in tags:
                tags.remove("leave_dying")
            await save_model_data("Entity", {"id": entity["id"], "tags": tags})
            logger.debug(f"实体 {entity_name} 脱离濒死状态")
            description = "脱离濒死状态"
    
        
        return {
            "ok": True,
            "entity": modify_data["entity"],
            "resource": modify_data["resource"],
            "before": modify_data["before"],
            "delta": modify_data["delta"],
            "after": modify_data["after"],
            "description": description,
        }
    
    async def daily_heal(self, entity_name: str) -> Dict[str, Any]:
        """
        实体每日自然恢复血量
        entity_name: 实体名称

        每日自然恢复是指实体在非重伤状态下，每天自动恢复1点血量。
        """
        modify_data = await self._modify_entity_health(entity_name, 1) # 恢复1点血量
        if not modify_data.get("ok"):
            return {"ok": False, "reason": modify_data.get("reason", "未知错误")}
        
        return {
            "ok": True,
            "entity": modify_data["entity"],
            "resource": modify_data["resource"],
            "before": modify_data["before"],
            "delta": modify_data["delta"],
            "after": modify_data["after"],
        }

    async def weekly_heal(self, entity_name: str, peaceful_environment: bool) -> Dict[str, Any]:
        """
        实体每周自然恢复血量
        entity_name: 实体名称

        每周自然恢复是指实体在重伤状态下，每周需要进行体质检定，失败则不恢复血量，成功则恢复1d3点血量，极难则恢复2d3点血量。
        若实体处于和平环境（如疗养院、医院等），则得到一颗奖励骰。
        """
        # 使用事务上下文，确保检定和血量修改的一致性
        async with transaction_context() as tx:
            # 传递事务上下文给 skill_check
            check_result = await DiceRoller.skill_check(
                entity_name, 
                "体质", 
                advantage=1 if peaceful_environment else 0,
                tx=tx  # 复用同一事务
            )
            
            if check_result.success_level < 2: # 极难成功
                amount = DiceRoller.roll("2d3").total
            elif check_result.success_level < 4: # 成功
                amount = DiceRoller.roll("1d3").total
            else: # 失败
                amount = 0

            # 在同一事务中修改血量
            entity = await tx.fetch("Entity", {"name": entity_name})
            if not entity:
                return {"ok": False, "reason": f"实体不存在: {entity_name}"}
            
            stats = entity.get("stats", {}) or {}
            if "hp" not in stats:
                stats["hp"] = 10
            
            before = int(stats.get("hp", 0))
            after = before + amount
            stats["hp"] = max(after, 0)
            hp_max = int((int(stats.get("CON", 50)) + int(stats.get("SIZ", 50))) / 10)
            stats["hp"] = min(after, hp_max)
            
            await tx.save("Entity", {"id": entity["id"], "stats": stats})
            
            return {
                "ok": True,
                "entity": entity_name,
                "resource": "hp",
                "before": before,
                "delta": amount,
                "after": stats["hp"],
                "check_result": check_result,
            }
    
    async def inflict_damage(self, entity_name: str, damage: int) -> Dict[str, Any]:
        """
        对实体造成伤害，减少血量
        entity_name: 实体名称
        damage: 伤害值，为正

        若实体一次性受到大于等于最大血量一半的伤害，则立即倒地，进入重伤状态。
        若实体血量降至0，且没有重伤状态，则进入昏迷状态；若已经处于重伤状态，则进入濒死状态。
        若实体一次性受到大于等于最大血量的伤害，则直接死亡。
        """
        # 使用事务上下文确保读-改-写的原子性
        async with transaction_context() as tx:
            entity = await tx.fetch("Entity", {"name": entity_name})
            
            if not entity:
                return {"ok": False, "reason": f"实体不存在: {entity_name}"}

            stats = entity.get("stats", {}) or {}
            tags = entity.get("tags", []) or []
            
            # 基础数值准备
            if "hp" not in stats:
                stats["hp"] = 10
            
            current_hp = int(stats.get("hp", 0))
            con = int(stats.get("CON", 50))
            siz = int(stats.get("SIZ", 50))
            max_hp = int((con + siz) / 10)
            
            # 判定一次性伤害是否导致直接死亡
            if damage >= max_hp:
                stats["hp"] = 0
                if "DEAD" not in tags:
                    tags.append("DEAD")
                # 死亡清除其他状态
                for t in ["UNCONSCIOUS", "DYING", "SERIOUSLY_INJURED", "PRONE"]:
                    if t in tags:
                        tags.remove(t)
                
                await tx.save("Entity", {"id": entity["id"], "stats": stats, "tags": tags})
                
                return {
                    "ok": True,
                    "entity": entity_name,
                    "damage": damage,
                    "current_hp": 0,
                    "status_change": "DEAD",
                    "description": "受到了毁灭性的伤害，直接死亡。"
                }

            # 判定重伤 (大于等于当前血量的一半)
            # 注意：如果当前血量很低（例如1），一半是0.5，伤害1就满足。
            is_major_wound = False
            if damage >= (max_hp / 2):
                is_major_wound = True
                if "SERIOUSLY_INJURED" not in tags:
                    tags.append("SERIOUSLY_INJURED")
                if "PRONE" not in tags:
                    tags.append("PRONE") # 倒地

            # 扣减血量
            new_hp = current_hp - damage
            
            status_desc = []
            if is_major_wound:
                status_desc.append("受到重伤并倒地")

            # 判定濒死或昏迷
            if new_hp <= 0:
                new_hp = 0
                # 如果有重伤标记（包括刚刚获得的），则进入濒死
                if "SERIOUSLY_INJURED" in tags:
                    if "DYING" not in tags:
                        tags.append("DYING")
                    status_desc.append("进入濒死状态")
                else:
                    # 没有重伤标记，则昏迷
                    if "UNCONSCIOUS" not in tags:
                        tags.append("UNCONSCIOUS")
                    status_desc.append("陷入昏迷")
            
            stats["hp"] = new_hp
            
            await tx.save("Entity", {"id": entity["id"], "stats": stats, "tags": tags})
            
            return {
                "ok": True,
                "entity": entity_name,
                "damage": damage,
                "before_hp": current_hp,
                "after_hp": new_hp,
                "is_major_wound": is_major_wound,
                "tags": tags,
                "description": "，".join(status_desc) if status_desc else "受到伤害"
            }
