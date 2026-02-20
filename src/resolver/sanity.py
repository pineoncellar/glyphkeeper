from typing import Any, Dict, List, Optional
import random
from ..core import get_logger
from ..memory.bridge import read_model, queue_model_update, commit_model_changes
from .base import BaseComponent
from .dice import DiceRoller

logger = get_logger(__name__)

class RealTimeMadnessTable:
    """表 7：疯狂发作 - 即时症状 (掷 1D10)"""
    _data = {
        1: "失忆：调查员对自己上一次抵达安全的场所后发生的事一无所知。在其看来上一刻他还在吃着早餐，而下一刻就已经身处怪物面前。这将持续 1D10 轮。",
        2: "假性残疾：调查员陷入因心理作用引起的失明、耳聋或肢体失能中，这一效果将持续 1D10 轮。",
        3: "暴力倾向：调查员沉浸于狂怒，开始对四周的一切施加失控的暴力与破坏行为，无论敌友。这一效果持续 1D10 轮。",
        4: "偏执妄想：调查员陷入严重的偏执妄想之中，持续 1D10 轮。所有人都在与他为敌！没⼈值得信任！他正在被窥视着，有人背叛了他，他所看见的皆是虚伪的幻象。",
        5: "人际依赖：浏览调查员背景故事的“重要之人”条目。调查员会将当前场景中的另一人误当做他的重要之人。调查员将依照他与重要之人之间关系的性质行事。这一效果持续 1D10 轮。",
        6: "昏厥：调查员会立即昏倒，并在 1D10 轮后苏醒。",
        7: "惊慌逃窜：调查员会无法自制地用一切可能的方法远远逃开，即使这意味着他需要开走唯一的一辆车并抛下其他所有人。他会持续逃窜 1D10 轮。",
        8: "歇斯底里：调查员情不自禁地开始狂笑、哭泣、尖叫，等等。这会持续 1D10 轮。",
        9: "恐惧症：调查员患上一项新的恐惧症。掷 1D100 并查阅表 9：范例恐惧症（第 160 页），或由守秘人选择一项。即使引发这些恐惧症的源头并不在身边，调查员仍会在 1D10 轮内想象那些东西正在那里。",
        10: "躁狂症：调查员患上一项新的躁狂症。掷 1D100 并查阅表 10：范例躁狂症（第 161 页），或由守秘人选择一项。调查员会在接下来的 1D10 轮中沉浸在他新的躁狂症中。"
    }

    @classmethod
    def roll(cls) -> str:
        # TODO: 实现恐惧症与躁狂症的具体内容抽取
        roll_val = random.randint(1, 8)
        return f"{roll_val}. {cls._data.get(roll_val, '未知症状')}"


class SummaryMadnessTable:
    """表 7：疯狂发作 - 总结症状 (掷 1D10)"""
    _data = {
        1: "失忆：调查员恢复神志时身处陌生地点，连自己是谁都不记得。记忆会随时间流逝逐渐恢复。",
        2: "被劫：调查员 1D10 小时后恢复神志时，财物已遭人打劫，但没有受到人身伤害。如果其携带者宝贵之物（参考调查员背景故事），进行一次幸运检定决定它是否被盗。其他所有值钱的物品都会自动丢失。",
        3: "遍体鳞伤：调查员 1D10 小时后恢复神志时，遍体鳞伤，浑身淤青。生命值降低至疯狂前的一半，但这不会造成重伤。调查员的财物没有被劫走。这些伤害如何造成由守秘人决定。",
        4: "暴力：调查员的情绪在暴力和破坏的冲动中爆发。调查员恢复神志时可能记得自己做过的事，也可能不记得。调查员对谁、对什么东西施以暴力，是杀死还是仅仅造成伤害，这些都由守秘人决定。",
        5: "思想与信念：浏览调查员背景故事的“思想与信念”条目。调查员选择其中一项，将它以极端、疯魔、形之于色的方式展现出来。例如，信仰宗教的人后来可能在地铁上大声宣讲福音。",
        6: "重要之人：浏览调查员背景故事的“重要之人”条目，及其重要的原因。在略过的时间中（1D10 小时或更久），调查员会尽一切努力接近重要之人，并以某种行动展现他们之间的关系。",
        7: "被收容：调查员恢复神志时身处精神病房或者警局拘留室当中。调查员会逐渐回想起他们身处此地的原因。",
        8: "惊慌逃窜：调查员恢复神志时已经身处很远的地方，可能在荒野中迷失了方向，或是正坐在火车或长途巴士上。",
        9: "恐惧症：调查员患上一项新的恐惧症。掷 1D100 并查阅表 9：范例恐惧症（第 160 页），或由守秘人选择一项。调查员 1D10 小时以后恢复神志，并采取了一切预防措施逃避新患上的恐惧症。",
        10: "躁狂症：调查员患上一项新的躁狂症。掷 1D100 并查阅表 10：范例躁狂症（第 161 页），或由守秘人选择一项。调查员 1D10 小时以后恢复神志。在疯狂发作期间，调查员完全沉溺于新的躁狂症状中。症状对其他人是否明显由守秘人和玩家决定。"
    }

    @classmethod
    def roll(cls) -> str:
        # TODO: 实现恐惧症与躁狂症的具体内容抽取
        roll_val = random.randint(1, 8)
        return f"{roll_val}. {cls._data.get(roll_val, '未知症状')}"


class SanityComponent(BaseComponent):
    def initialize(self):
        pass

    async def _modify_entity_sanity(self, entity_name: str, delta: int, entity_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """内部函数，修改实体的理智值"""
        if entity_data:
            entity = entity_data
        else:
            entity = await read_model("Entity", {"name": entity_name})
            
        if not entity:
            logger.error(f"实体不存在: {entity_name}")
            return {"ok": False, "reason": f"实体不存在: {entity_name}"}

        stats = entity.get("stats", {}) or {}
        # 复制 stats 以免副作用
        stats = stats.copy()
        
        # 确保有基础属性
        if "SAN" not in stats:
            stats["SAN"] = int(stats.get("POW", 50))
            
        current_san = int(stats.get("SAN", 50))
        # 限制范围：0 到 POW (这里简化为 POW 作为最大理智)
        max_san = int(stats.get("POW", 50))
        
        new_san = current_san + delta
        new_san = max(0, min(new_san, max_san))
        
        stats["SAN"] = new_san
        
        # 如果是扣除理智，记录累积损失（用于不定性疯狂判断）
        if delta < 0:
            current_loss = int(stats.get("sanity_loss_accumulated", 0))
            stats["sanity_loss_accumulated"] = current_loss + abs(delta)

        # 加入修改队列
        queue_model_update("Entity", {"id": entity["id"], "stats": stats})
        
        logger.debug(f"实体 {entity_name} 理智修改: {current_san} -> {new_san} (delta: {delta})")
        
        return {
            "ok": True,
            "entity": entity["name"],
            "resource": "san",
            "before": current_san,
            "delta": delta,
            "after": new_san,
            "stats": stats, # 返回最新的 stats 供后续逻辑使用
            "entity_data": entity 
        }

    async def _be_in_crazy_state(self, entity_name: str, crazy_type: str, entity_data: Dict[str, Any] = None):
        """
        内部函数，使实体进入疯狂状态
        实体若为调查员，则检查同场景下是否有其他调查员，若有则抽取即时症状，持续1d10小时，否则抽取总结症状
        """
        if entity_data:
            entity = entity_data
        else:
            entity = await read_model("Entity", {"name": entity_name})
            
        if not entity:
            return

        tags = entity.get("tags", []) or []
            
        # 症状抽取逻辑
        symptom_desc = ""
        duration_desc = ""
        
        # 判定是否为调查员
        is_investigator = "Investigator" in tags
        
        if is_investigator:
            # 检查同场景是否有其他调查员
            location_id = entity.get("location_id")
            has_others = False
            
            if location_id:
                # 获取同场景实体
                others = await read_model("Entity", {"location_id": location_id}, one=False) or []
                for other in others:
                    if other["id"] == entity["id"]:
                        continue
                    other_tags = other.get("tags", []) or []
                    if "Investigator" in other_tags or other.get("type") == "Investigator":
                        has_others = True
                        break
            
            # 1d10
            duration_val = random.randint(1, 10)
            
            if has_others:
                # 即时症状 (Real-time)
                duration_desc = f"{duration_val}轮"
                symptom_desc = RealTimeMadnessTable.roll()
            else:
                # 总结症状 (Summary)
                duration_desc = f"{duration_val}小时"
                symptom_desc = SummaryMadnessTable.roll()
        else:
            symptom_desc = "陷入了疯狂状态"
            duration_desc = "未知"

        logger.info(f"实体 {entity_name} 进入 {crazy_type} 疯狂: {symptom_desc} ({duration_desc})")
        return {
            "type": crazy_type,
            "symptom": symptom_desc,
            "duration": duration_desc
        }

    async def check_sanity(self, entity_name: str, sc_dice_str: str):
        """
        对实体进行理智检定
        
        entity_name: 实体名称
        sc_dice_str: sc表达式，如"1/1d3", "1d20/1d100"等
        """
        # 获取实体
        entity = await read_model("Entity", {"name": entity_name})
        if not entity:
            return {"ok": False, "reason": f"实体不存在: {entity_name}"}
            
        stats = entity.get("stats", {}) or {}
        # 默认 SAN 为 POW
        current_san = int(stats.get("SAN", stats.get("POW", 50)))
        
        # 执行检定 (d100)
        success_level = DiceRoller.check_success(current_san)
        is_success = success_level <= 3
        
        level_desc_map = {
            0: "大成功",
            1: "极难成功",
            2: "困难成功",
            3: "成功",
            4: "失败",
            5: "大失败"
        }
        level_str = level_desc_map.get(success_level, "未知")
        
        # 解析表达式并掷骰
        try:
            success_expr, fail_expr = sc_dice_str.split("/")
        except ValueError:
            return {"ok": False, "reason": f"SC表达式格式错误: {sc_dice_str}"}
            
        loss_amount = 0
        calc_desc = ""

        if success_level == 0: # 大成功，扣除成功表达式最小值
             expr_to_roll = success_expr
             loss_amount = DiceRoller.get_min_value(expr_to_roll)
             calc_desc = f"{expr_to_roll}(Min)={loss_amount}"
             
        elif success_level == 5: # 大失败，扣除失败表达式最大值
             expr_to_roll = fail_expr
             loss_amount = DiceRoller.get_max_value(expr_to_roll)
             calc_desc = f"{expr_to_roll}(Max)={loss_amount}"
             
        else:
             expr_to_roll = success_expr if is_success else fail_expr
             loss_result = DiceRoller.roll(expr_to_roll)
             loss_amount = loss_result.total
             calc_desc = f"{expr_to_roll}={loss_result.details}"
        
        # 扣除理智
        modify_res = await self._modify_entity_sanity(entity_name, -loss_amount, entity_data=entity)
        if not modify_res["ok"]:
            return modify_res
            
        new_stats = modify_res["stats"]
        new_san = modify_res["after"]
        
        # 疯狂规则判定
        crazy_result = None
        result_desc = []
        result_desc.append(f"理智检定: {level_str} (SAN: {current_san})")
        result_desc.append(f"理智减少: {loss_amount} ({calc_desc})")
        
        tags = entity.get("tags", []) or []
        has_indet_crazy = "CRAZY_INDET" in tags

        # 永久疯狂：理智降为0
        if new_san <= 0:
            crazy_result = await self.perm_crazy(entity_name, entity_data=entity)
            
        else:
            # 不定性疯狂：
            # 已经有不定性疯狂Tag 且 受到理智损失 -> 触发一次疯狂
            # 单日（累积）扣除 >= 1/5 POW -> 获得不定性疯狂Tag并触发
            
            pow_val = int(stats.get("POW", 50))
            accumulated_loss = int(new_stats.get("sanity_loss_accumulated", 0))
            
            if (has_indet_crazy and loss_amount > 0) or (accumulated_loss >= (pow_val / 5)):
                crazy_result = await self.indet_crazy(entity_name, entity_data=entity)
                
            # 临时疯狂：一次性扣除 >= 5
            elif loss_amount >= 5:
                crazy_result = await self.tmp_crazy(entity_name, entity_data=entity)

        if crazy_result and crazy_result.get("description"):
            result_desc.append(crazy_result["description"])
        
        return {
            "ok": True,
            "entity": entity_name,
            "check_result": level_str,
            "success_level": success_level,
            "target": current_san,
            "loss": loss_amount,
            "current_san": new_san,
            "crazy_result": crazy_result,
            "description": "\n".join(result_desc)
        }
    
    async def recover_sanity(self, entity_name: str, recover_dice_str: str):
        """
        对实体进行理智恢复
        """
        roll_res = DiceRoller.roll(recover_dice_str)
        amount = roll_res.total
        
        modify_res = await self._modify_entity_sanity(entity_name, amount)
        if not modify_res["ok"]:
            return modify_res
            
        return {
            "ok": modify_res["ok"],
            "entity": entity_name,
            "recover_amount": amount,
            "current_san": modify_res.get("after"),
            "description": f"理智恢复 {amount} 点"
        }

    async def tmp_crazy(self, entity_name: str, entity_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        临时疯狂处理
        进行智力检定，成功则进入疯狂状态，失败则无事发生
        """
        int_check = await DiceRoller.skill_check(entity_name, "INT")
        
        if int_check.success_level <= 3: # 成功
             # 进入疯狂
             # 添加临时疯狂 Tag
             if entity_data:
                tags = entity_data.get("tags", []) or []
                if "CRAZY_TEMP" not in tags:
                    tags.append("CRAZY_TEMP")
                    queue_model_update("Entity", {"id": entity_data["id"], "tags": tags})
             
             crazy_info = await self._be_in_crazy_state(entity_name, "Temporary", entity_data)
             return {
                 "triggered": True,
                 "check_result": int_check,
                 "crazy_info": crazy_info,
                 "description": f"智力检定成功({int_check.total}) -> 理解了恐怖\n【临时疯狂】{crazy_info['symptom']} (持续{crazy_info['duration']})"
             }
        else:
             return {
                 "triggered": False,
                 "check_result": int_check,
                 "description": f"智力检定失败({int_check.total}) -> 潜意识屏蔽了恐怖，未陷入疯狂"
             }

    async def indet_crazy(self, entity_name: str, entity_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        不定性疯狂处理
        获得不定性疯狂Tag（如果还没有），并进入一次疯狂状态
        """
        if entity_data:
            # 添加不定性疯狂 Tag
            tags = entity_data.get("tags", []) or []
            if "CRAZY_INDET" not in tags:
                tags.append("CRAZY_INDET")
                queue_model_update("Entity", {"id": entity_data["id"], "tags": tags})
        
        crazy_info = await self._be_in_crazy_state(entity_name, "Indefinite", entity_data)
        return {
            "triggered": True,
            "crazy_info": crazy_info,
            "description": f"【不定性疯狂】{crazy_info['symptom']} (持续{crazy_info['duration']})"
        }

    async def perm_crazy(self, entity_name: str, entity_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        永久疯狂处理
        玩家失去对角色的控制权，由KP接管
        """
        if entity_data:
            # 添加永久疯狂 Tag
            tags = entity_data.get("tags", []) or []
            if "CRAZY_PERM" not in tags:
                tags.append("CRAZY_PERM")
                queue_model_update("Entity", {"id": entity_data["id"], "tags": tags})

        crazy_info = await self._be_in_crazy_state(entity_name, "Permanent", entity_data)
        return {
             "triggered": True,
             "crazy_info": crazy_info,
             "description": f"【永久疯狂】{crazy_info['symptom']}\n玩家失去角色控制权。"
        }
