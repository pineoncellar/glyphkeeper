"""
角色域模型

职责:
  - 调查员角色数据类与校验
  - 属性生成与分配
  - 技能列表管理与成长
  - 职业模板定义

类:
  - Character: 核心角色数据
  - Stats: 八项属性值对象
  - Occupation: 职业模板

函数:
  - create_investigator(name, occupation, stats) -> Character
  - apply_skill_growth(character, skill_name, roll_value) -> bool
  - calculate_combat_stats(str_score, siz_score) -> tuple[str, int]
  - calculate_move(dex_score, str_score, siz_score) -> int
"""

import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional, List
from src.tools.dice import roll_d100, roll_dice


@dataclass
class Stats:
    """八项属性"""
    strength: int = 50        # 力量 STR
    constitution: int = 50    # 体质 CON
    size: int = 50            # 体型 SIZ
    dexterity: int = 50       # 敏捷 DEX
    appearance: int = 50      # 外貌 APP
    intelligence: int = 50    # 智力 INT
    power: int = 50           # 意志 POW
    education: int = 50       # 教育 EDU

    def to_dict(self) -> dict:
        return {
            "STR": self.strength,
            "CON": self.constitution,
            "SIZ": self.size,
            "DEX": self.dexterity,
            "APP": self.appearance,
            "INT": self.intelligence,
            "POW": self.power,
            "EDU": self.education,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Stats":
        return cls(
            strength=data.get("STR", 50),
            constitution=data.get("CON", 50),
            size=data.get("SIZ", 50),
            dexterity=data.get("DEX", 50),
            appearance=data.get("APP", 50),
            intelligence=data.get("INT", 50),
            power=data.get("POW", 50),
            education=data.get("EDU", 50),
        )


@dataclass
class Character:
    """调查员角色"""
    id: str = ""
    name: str = ""
    occupation: str = ""
    stats: Stats = field(default_factory=Stats)
    skills: Dict[str, int] = field(default_factory=dict)
    sanity: int = 0
    max_sanity: int = 0
    hit_points: int = 0
    max_hit_points: int = 0
    magic_points: int = 0
    max_magic_points: int = 0
    damage_bonus: str = "0"
    build: int = 0
    move: int = 8
    armor: int = 0


def create_investigator(
    name: str,
    occupation: str,
    stats: Stats,
    occupation_skills: Dict[str, int],
) -> Character:
    """
    创建调查员角色，自动计算衍生属性。

    参数:
      name: 角色名
      occupation: 职业
      stats: 八项属性
      occupation_skills: 职业技能（技能名 → 技能值）

    返回: 完整的 Character 对象
    """
    # 计算衍生属性
    max_hp = calculate_max_hp(stats.constitution, stats.size)
    max_mp = calculate_max_mp(stats.power)
    max_san = stats.power  # 最大 SAN = POW
    db, build = calculate_combat_stats(stats.strength, stats.size)
    move = calculate_move(stats.dexterity, stats.strength, stats.size)

    # CoC 7版 基础技能数值表
    base_skills = {
        # ── 动态计算 ──
        "闪避": stats.dexterity // 2,
        "语言(母语)": stats.education,

        # ── 固定基础值 ──
        "会计": 5,
        "人类学": 1,
        "估价": 5,
        "考古学": 1,
        "取悦": 15,
        "魅惑": 15,
        "攀爬": 20,
        "计算机使用": 5,
        "信用评级": 0,
        "克苏鲁神话": 0,
        "乔装": 5,
        "汽车驾驶": 20,
        "电气维修": 10,
        "电子学": 1,
        "话术": 5,
        "斗殴": 25,
        "手枪": 20,
        "急救": 30,
        "历史": 5,
        "恐吓": 15,
        "跳跃": 20,
        "法律": 5,
        "图书馆利用": 20,
        "聆听": 20,
        "锁匠": 1,
        "机械维修": 10,
        "医学": 1,
        "博物学": 10,
        "导航": 10,
        "神秘学": 5,
        "操作重型机械": 1,
        "说服": 10,
        "精神分析": 1,
        "心理学": 10,
        "骑术": 5,
        "妙手": 10,
        "侦查": 25,
        "潜行": 20,
        "生存": 10,
        "游泳": 20,
        "投掷": 20,
        "追踪": 10,
        "动物驯养": 5,
        "潜水": 1,
        "爆破": 1,
        "读唇": 1,
        "催眠": 1,
        "炮术": 1,
    }

    # 合并技能（职业技能覆盖基础技能）
    all_skills = {**base_skills, **occupation_skills}

    return Character(
        id=str(uuid.uuid4()),
        name=name,
        occupation=occupation,
        stats=stats,
        skills=all_skills,
        sanity=max_san,
        max_sanity=max_san,
        hit_points=max_hp,
        max_hit_points=max_hp,
        magic_points=max_mp,
        max_magic_points=max_mp,
        damage_bonus=db,
        build=build,
        move=move,
        armor=0,
    )


def calculate_combat_stats(str_score: int, siz_score: int) -> tuple[str, int]:
    """
    根据 STR + SIZ 计算伤害加值（DB）和体格值（Build）。
    
    CoC 7版规则：
    STR+SIZ ≤ 64 → DB="-2", Build=-2
    65-84   → DB="-1", Build=-1
    85-124  → DB="0",  Build=0
    125-164 → DB="+1D4", Build=1
    165-204 → DB="+1D6", Build=2
    ≥ 205   → DB="+2D6", Build=3

    返回: (damage_bonus, build)
    """
    total = str_score + siz_score

    db_table = [
        (64, "-2", -2),
        (84, "-1", -1),
        (124, "0", 0),
        (164, "+1D4", 1),
        (204, "+1D6", 2),
    ]
    for threshold, db, build in db_table:
        if total <= threshold:
            return (db, build)
    return ("+2D6", 3)


def calculate_move(dex: int, str_score: int, siz_score: int) -> int:
    """
    计算移动力。
    
    CoC 7版规则：
    - DEX < SIZ 且 STR < SIZ → MOV 7
    - STR > SIZ 且 DEX > SIZ → MOV 9
    - 其余情况→ MOV 8
    """
    if dex > siz_score and str_score > siz_score:
        return 9
    if dex < siz_score and str_score < siz_score:
        return 7
    return 8


def calculate_max_hp(con: int, siz: int) -> int:
    """最大 HP = (CON + SIZ) / 2，向下取整"""
    return (con + siz) // 2


def calculate_max_mp(pow: int) -> int:
    """最大 MP = POW / 5"""
    return max(1, pow // 5)


def apply_skill_growth(character: Character, skill_name: str, roll_value: int) -> bool:
    """
    技能成长：技能检定成功后，掷 D100，如果 > 当前技能值，则 +1D10。
    返回是否成长。

    参数:
      character: 角色对象
      skill_name: 技能名称
      roll_value: 成长掷骰值（1-100）

    返回: True 如果技能成长了，False 否则
    """
    current_value = character.skills.get(skill_name)
    if current_value is None:
        return False

    # 如果掷骰值 > 当前技能值，则成长
    if roll_value > current_value:
        growth = roll_dice("1D10")
        character.skills[skill_name] = current_value + growth
        return True

    return False


# ====================================================================
# 职业模板
# ====================================================================


@dataclass
class Occupation:
    """CoC 7版 职业模板"""
    name: str
    description: str
    skills: list[str]                  # 本职技能列表
    skill_points_formula: str          # 职业技能点公式，如 "EDU*4", "EDU*2+DEX*2"
    credit_range: tuple[int, int]      # 信用评级范围 (min, max)


OCCUPATIONS: list[Occupation] = [
    Occupation("会计师", "精通财务与审计的专业人士", ["会计", "法律", "图书馆利用", "聆听", "说服", "心理学", "侦查"], "EDU*4", (10, 40)),
    Occupation("作家", "以文字为生的创作者", ["历史", "图书馆利用", "神秘学", "母语", "心理学", "侦查"], "EDU*4", (10, 30)),
    Occupation("医生", "受过专业医学训练的医师", ["医学", "急救", "精神分析", "科学(生物学)", "图书馆利用", "母语", "心理学"], "EDU*4", (30, 60)),
    Occupation("工程师", "建筑、机械或电气领域的专家", ["电气维修", "图书馆利用", "机械维修", "科学(物理学)", "操作重型机械", "侦查"], "EDU*4", (30, 50)),
    Occupation("记者", "追寻真相的新闻工作者", ["历史", "图书馆利用", "母语", "心理学", "侦查", "话术", "汽车驾驶"], "EDU*4", (10, 30)),
    Occupation("警官", "维护法律的执法人员", ["汽车驾驶", "斗殴", "手枪", "法律", "心理学", "侦查", "潜行"], "EDU*4", (20, 40)),
    Occupation("教授", "高等教育机构的研究者", ["图书馆利用", "母语", "心理学", "侦查", "科学(任一)", "历史", "神秘学"], "EDU*4", (20, 50)),
    Occupation("考古学家", "挖掘与研究古代文明的学者", ["考古学", "历史", "图书馆利用", "母语", "科学(任一)", "侦查", "攀爬"], "EDU*4", (10, 40)),
    Occupation("私家侦探", "独立调查案件的探员", ["法律", "心理学", "侦查", "潜行", "话术", "斗殴", "图书馆利用"], "EDU*4", (20, 45)),
    Occupation("退役军人", "曾在军队服役的退伍士兵", ["斗殴", "射击(步枪/霰弹枪)", "闪避", "潜行", "急救", "攀爬", "驾驶"], "EDU*4", (10, 30)),
    Occupation("古董商", "经营古物与艺术品的商人", ["会计", "估价", "历史", "艺术与手艺", "魅惑", "心理学", "侦查"], "EDU*4", (30, 50)),
]


# ====================================================================
# 属性骰点
# ====================================================================


def roll_standard_stats() -> Stats:
    """标准 CoC 7版 骰点法生成八项属性

    规则:
      - STR/CON/DEX/APP/POW:       3D6 × 5
      - SIZ/INT/EDU:               (2D6+6) × 5
    """
    return Stats(
        strength=roll_dice("3D6") * 5,
        constitution=roll_dice("3D6") * 5,
        size=(roll_dice("2D6") + 6) * 5,
        dexterity=roll_dice("3D6") * 5,
        appearance=roll_dice("3D6") * 5,
        intelligence=(roll_dice("2D6") + 6) * 5,
        power=roll_dice("3D6") * 5,
        education=(roll_dice("2D6") + 6) * 5,
    )


def calculate_skill_points(occupation: Occupation, stats: Stats) -> int:
    """根据职业公式计算可用职业技能点"""
    return _eval_formula(occupation.skill_points_formula, stats)


def calculate_interest_points(stats: Stats) -> int:
    """兴趣技能点 = INT × 2"""
    return stats.intelligence * 2


def _eval_formula(formula: str, stats: Stats) -> int:
    """解析职业技能点公式

    支持格式: "EDU*4", "EDU*2+DEX*2"
    """
    total = 0
    stat_map = stats.to_dict()
    for token in formula.replace(" ", "").split("+"):
        token = token.strip()
        if not token:
            continue
        if "*" in token:
            name, mul = token.split("*")
            value = stat_map.get(name.upper(), 0)
            total += value * int(mul)
        else:
            value = stat_map.get(token.upper(), 0)
            total += value
    return total
