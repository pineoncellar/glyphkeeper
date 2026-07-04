"""
@File     :   character.py
@Desc     :   调查员角色域模型 — 数据类、衍生属性计算、职业模板、技能成长
"""

import uuid
from dataclasses import dataclass, field
from typing import Dict, Optional
from src.tools.dice import roll_d100, roll_dice


@dataclass
class Stats:
    """八项属性值对象"""
    strength: int = 50
    constitution: int = 50
    size: int = 50
    dexterity: int = 50
    appearance: int = 50
    intelligence: int = 50
    power: int = 50
    education: int = 50

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
    """调查员角色 — 身份、属性、状态池、技能、背包、背景、法术的完整容器"""
    id: str = ""
    # 身份元数据
    name: str = ""
    gender: str = ""
    age: int = 0
    birthplace: str = ""
    occupation: str = ""
    stats: Stats = field(default_factory=Stats)
    skills: Dict[str, int] = field(default_factory=dict)
    # HP 系统
    hit_points: int = 0
    max_hit_points: int = 0
    major_wound: bool = False          # 重伤标记
    unconscious: bool = False           # 昏迷
    dying: bool = False                 # 濒死
    # MP 系统
    magic_points: int = 0
    max_magic_points: int = 0
    # SAN 系统
    sanity: int = 0
    max_sanity: int = 0
    initial_sanity: int = 0             # 初始理智（不定性疯狂恢复阈值）
    sanity_loss_today: int = 0          # 本日理智损失累计
    temp_insanity: bool = False         # 临时疯狂标记
    indefinite_insanity: bool = False   # 不定性疯狂标记
    # 幸运
    luck: int = 0
    # 面板衍生值
    damage_bonus: str = "0"
    build: int = 0
    move: int = 8
    armor: int = 0
    # 背包
    inventory: list = field(default_factory=list)
    # 背景故事（经典七大项）
    appearance_desc: str = ""
    belief: str = ""
    significant_person: str = ""
    significant_place: str = ""
    cherished_possession: str = ""
    trait: str = ""
    injury_scar: str = ""
    # 法术
    spells: list = field(default_factory=list)
    # 完整背景故事
    full_backstory: str = ""
    # 恐惧症和躁狂症
    phobias_manias: str = ""


def create_investigator(
    name: str,
    occupation: str,
    stats: Stats,
    occupation_skills: Dict[str, int],
    gender: str = "",
    age: int = 0,
    birthplace: str = "",
    appearance_desc: str = "",
    belief: str = "",
    significant_person: str = "",
    significant_place: str = "",
    cherished_possession: str = "",
    trait: str = "",
    injury_scar: str = "",
    spells: Optional[list] = None,
) -> Character:
    """
    创建调查员角色，自动计算所有衍生属性。

    先算基础衍生值，再设基础技能表，最后合并职业技能。
    """
    max_hp = calculate_max_hp(stats.constitution, stats.size)
    max_mp = calculate_max_mp(stats.power)
    max_san = stats.power  # 运行时随克苏鲁神话递减
    initial_sanity = max_san
    luck = roll_dice("3D6") * 5
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
        gender=gender,
        age=age,
        birthplace=birthplace,
        occupation=occupation,
        stats=stats,
        skills=all_skills,
        hit_points=max_hp,
        max_hit_points=max_hp,
        magic_points=max_mp,
        max_magic_points=max_mp,
        sanity=max_san,
        max_sanity=max_san,
        initial_sanity=initial_sanity,
        luck=luck,
        damage_bonus=db,
        build=build,
        move=move,
        armor=0,
        appearance_desc=appearance_desc,
        belief=belief,
        significant_person=significant_person,
        significant_place=significant_place,
        cherished_possession=cherished_possession,
        trait=trait,
        injury_scar=injury_scar,
        spells=spells or [],
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
    技能成长：检定成功后掷 D100，大于当前值则 +1D10。
    如果成长的是克苏鲁神话，同步重算 SAN 上限。

    返回是否成长。
    """
    current_value = character.skills.get(skill_name)
    if current_value is None:
        return False

    if roll_value > current_value:
        growth = roll_dice("1D10")
        character.skills[skill_name] = current_value + growth
        if skill_name == "克苏鲁神话":
            update_sanity_cap(character)
        return True

    return False


def update_sanity_cap(character: Character) -> int:
    """
    重算 SAN 上限 = min(POW, 99 - 克苏鲁神话)。

    每次克苏鲁神话技能成长后调用，确保上限随知识增长递减。
    """
    cthulhu_mythos = character.skills.get("克苏鲁神话", 0)
    new_max = min(character.stats.power, 99 - cthulhu_mythos)
    character.max_sanity = max(0, new_max)
    return character.max_sanity


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
