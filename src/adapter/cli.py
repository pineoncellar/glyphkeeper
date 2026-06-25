# -*- coding: utf-8 -*-
"""
@File     :   cli.py
@Desc     :   CLI 适配器 — 终端交互式 REPL
@Note     :   继承 AbstractAdapter，通过 stdin/stdout 与玩家交互

使用方式:
    uv run python -m src.adapter.cli
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any, Optional

from src.tools import get_logger
from src.runtime.engine import GraphEngine
from src.runtime.scheduler import InputScheduler
from src.adapter.base import AbstractAdapter
from src.adapter.protocol import InboundMessage, OutboundMessage, MessageType
from src.domain.character import (
    Occupation, OCCUPATIONS, Stats, Character,
    create_investigator, roll_standard_stats,
    calculate_skill_points, calculate_interest_points,
)
from src.state.player_state import PlayerLoader

logger = get_logger(__name__)

# ── ANSI 颜色常量 ──
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_RED = "\033[31m"


def _color(text: str, code: str) -> str:
    return f"{code}{text}{_RESET}" if sys.stdout.isatty() else text


_BANNER = f"""
{_color('═' * 56, _CYAN)}
{_color('  ██████╗ ██╗   ██╗██████╗ ██╗  ██╗  ██╗███████╗███████╗██████╗ ███████╗██████╗ ', _GREEN)}
{_color('  ██╔════╝ ╚██╗ ██╔╝██╔══██╗██║  ██║  ██║██╔════╝██╔════╝██╔══██╗██╔════╝██╔══██╗', _GREEN)}
{_color('  ██║  ███╗ ╚████╔╝ ██████╔╝███████║  ██║█████╗  █████╗  ██████╔╝█████╗  ██████╔╝', _GREEN)}
{_color('  ██║   ██║  ╚██╔╝  ██╔═══╝ ██╔══██║  ██║██╔══╝  ██╔══╝  ██╔═══╝ ██╔══╝  ██╔══██╗', _GREEN)}
{_color('  ╚██████╔╝   ██║   ██║     ██║  ██║  ██║███████╗███████╗██║     ███████╗██║  ██║', _GREEN)}
{_color('   ╚═════╝    ╚═╝   ╚═╝     ╚═╝  ╚═╝  ╚═╝╚══════╝╚══════╝╚═╝     ╚══════╝╚═╝  ╚═╝', _GREEN)}
{_color('═' * 56, _CYAN)}
{_color('   GlyphKeeper — CoC 7版 AI 守密人系统', _BOLD)}
{_color('   CLI Adapter - 输入 /help 查看命令', _DIM)}
{_color('═' * 56, _CYAN)}
"""


class CliAdapter(AbstractAdapter):
    """CLI 适配器 — 终端交互式跑团界面"""

    def __init__(
        self,
        engine: Optional[GraphEngine] = None,
        scheduler: Optional[InputScheduler] = None,
        session_id: str = "cli-default",
    ):
        super().__init__(engine, scheduler, session_id)
        self._turn_count = 0
        self._available_module: Optional[str] = None
        self._player_loader = PlayerLoader()
        self._character: Optional[Character] = None

    # ── AbstractAdapter 接口实现 ──

    async def parse(self, raw_input: Any) -> InboundMessage:
        """将终端原始输入解析为 InboundMessage"""
        text = str(raw_input).strip() if raw_input else ""

        if not text:
            return InboundMessage(type="", text="", session_id=self.session_id)

        if text.startswith("/"):
            return InboundMessage.system_cmd(text, self.session_id)

        return InboundMessage.player_input(text, self.session_id)

    async def send(self, message: OutboundMessage):
        """将 OutboundMessage 输出到终端"""
        if message.type == MessageType.NARRATIVE:
            print()
            print(_color("━" * 50, _DIM))
            print(_color(message.text, _CYAN))
            print(_color("━" * 50, _DIM))

        elif message.type == MessageType.SYSTEM_MSG:
            level = message.data.get("level", "info")
            if level == "error":
                print(_color(f"❌ {message.text}", _RED))
            elif level == "warn":
                print(_color(f"⚠️  {message.text}", _YELLOW))
            else:
                print(_color(f"ℹ️  {message.text}", _DIM))

        elif message.type == MessageType.SESSION_INFO:
            d = message.data
            print()
            print(_color("─ 会话状态 ───────────────────────", _BOLD))
            print(f"  角色:         {d.get('character_name', '未创建')}")
            if d.get("character_name"):
                print(f"  职业:         {d.get('occupation', '')}")
            print(f"  轮次:         {self._turn_count}")
            print(f"  游戏阶段:     {d.get('game_phase', 'unknown')}")
            print(f"  战斗进行中:   {'是' if d.get('combat_active') else '否'}")
            print(f"  激活标签:     {d.get('active_tags', [])}")
            if d.get("pending_dice"):
                print(f"  等待掷骰:     {d['pending_dice']}")
            print(_color("──────────────────────────────────", _DIM))

        elif message.type == MessageType.DICE_REQUEST:
            print(_color(f"\n🎲 请掷骰: {message.text}", _YELLOW))

    # ── 角色创建向导 ──

    async def _character_creation_wizard(self) -> Character:
        """互动式调查员角色创建向导"""
        print()
        print(_color("═" * 50, _CYAN))
        print(_color("         🎭 调查员角色创建", _BOLD))
        print(_color("═" * 50, _CYAN))
        print()

        # 1. 输入姓名
        name = ""
        while not name.strip():
            raw = await asyncio.get_event_loop().run_in_executor(
                None, lambda: input(f"{_color('姓名', _GREEN)} > ")
            )
            name = raw.strip()
        print()

        # 2. 选择职业
        print(_color("─ 选择职业 ───────────────────────", _BOLD))
        for i, occ in enumerate(OCCUPATIONS, 1):
            print(f"  {i:>2}. {_color(occ.name, _CYAN)}")
            print(f"      {occ.description}")
            print(f"      本职技能: {', '.join(occ.skills[:4])}{' …' if len(occ.skills) > 4 else ''}")
        print()

        occ_idx = 0
        while not (1 <= occ_idx <= len(OCCUPATIONS)):
            raw = await asyncio.get_event_loop().run_in_executor(
                None, lambda: input(f"{_color('选择职业编号', _GREEN)} (1-{len(OCCUPATIONS)}) > ")
            )
            try:
                occ_idx = int(raw.strip())
            except ValueError:
                pass
        occupation = OCCUPATIONS[occ_idx - 1]
        print(f"\n  已选择: {_color(occupation.name, _CYAN)}")
        print()

        # 3. 骰点属性
        stats = None
        print(_color("─ 属性骰点 ───────────────────────", _BOLD))
        while True:
            stats = roll_standard_stats()
            s = stats.to_dict()
            print(f"  STR {s['STR']:>3}  |  CON {s['CON']:>3}  |  SIZ {s['SIZ']:>3}")
            print(f"  DEX {s['DEX']:>3}  |  APP {s['APP']:>3}  |  INT {s['INT']:>3}")
            print(f"  POW {s['POW']:>3}  |  EDU {s['EDU']:>3}")
            print()
            raw = await asyncio.get_event_loop().run_in_executor(
                None, lambda: input(f"{_color('接受(v) / 重骰(r)', _GREEN)} > ")
            )
            if raw.strip().lower() in ("v", "y", "yes", ""):
                break
            print()

        # 4. 技能分配
        print()
        print(_color("─ 职业技能分配 ───────────────────", _BOLD))
        total_occ_pts = calculate_skill_points(occupation, stats)
        credit_min, credit_max = occupation.credit_range
        print(f"  职业技能点: {_color(str(total_occ_pts), _YELLOW)} ({occupation.skill_points_formula})")
        print(f"  信用评级范围: {credit_min}-{credit_max}")
        print()

        occ_skills = {}
        base_skills = _get_base_skill_values(stats)
        per_skill_base = total_occ_pts // len(occupation.skills)

        for skill_name in occupation.skills:
            base = base_skills.get(skill_name, 1)
            allocated = per_skill_base
            prompt_text = f"  {skill_name} (基础{base}, 已分配+{allocated}={base+allocated})"
            print(prompt_text)

            # 信用评级特殊处理：限制在职业范围
            if skill_name == "信用评级":
                allocated = max(0, min(allocated, credit_max))
                allocated = max(credit_min, allocated)

            occ_skills[skill_name] = base + allocated

        # 节省未用尽的点数（加到第一个技能）
        used = sum(occ_skills.values()) - sum(base_skills.get(s, 1) for s in occupation.skills)
        leftover = total_occ_pts - used
        if leftover > 0 and occupation.skills:
            first_skill = occupation.skills[0]
            occ_skills[first_skill] = occ_skills.get(first_skill, base_skills.get(first_skill, 1)) + leftover

        # 兴趣技能点
        print()
        print(_color("─ 兴趣技能 ───────────────────────", _BOLD))
        interest_pts = calculate_interest_points(stats)
        print(f"  兴趣技能点: {_color(str(interest_pts), _YELLOW)} (INT×2 = {stats.intelligence}×2)")
        print()

        # 显示完整角色
        character = create_investigator(name, occupation.name, stats, occ_skills)

        # 5. 创建完成
        self._character = character
        self._player_loader.save_character(self.session_id, character)

        print()
        print(_color("═" * 50, _GREEN))
        print(_color(f"  ✅ 调查员 [{character.name}] 创建完成！", _BOLD))
        print(_color("═" * 50, _GREEN))
        print()
        self._show_character_sheet(character)
        print()
        return character

    def _show_character_sheet(self, char: Optional[Character] = None):
        """显示调查员属性卡"""
        c = char or self._character
        if c is None:
            print(_color("⚠️  未创建角色", _YELLOW))
            return
        s = c.stats.to_dict()
        print(_color("┌─────────────────────────────────────────────────┐", _BOLD))
        print(_color(f"  姓名: {c.name:<20s}  职业: {c.occupation:<12s}", _CYAN))
        print(_color(f"───────────────────────────────────────────────────", _DIM))
        print(f"  STR:{s['STR']:>3}  CON:{s['CON']:>3}  SIZ:{s['SIZ']:>3}  DEX:{s['DEX']:>3}")
        print(f"  APP:{s['APP']:>3}  INT:{s['INT']:>3}  POW:{s['POW']:>3}  EDU:{s['EDU']:>3}")
        print(f"───────────────────────────────────────────────────")
        print(f"  HP:{c.hit_points:>2}/{c.max_hit_points:>2}  SAN:{c.sanity:>2}/{c.max_sanity:>2}  MP:{c.magic_points:>2}/{c.max_magic_points:>2}")
        print(f"  DB:{c.damage_bonus:>4s}  Build:{c.build:>2}  MOV:{c.move:>2}  Armor:{c.armor:>2}")
        print()
        # 显示重要技能
        key_skills = ["侦查", "聆听", "图书馆利用", "潜行", "斗殴", "闪避", "信用评级", "急救"]
        print(_color(f"  关键技能:", _DIM))
        parts = []
        for sk in key_skills:
            val = c.skills.get(sk, 0)
            val_str = _color(f"{val:>3}", _GREEN) if val >= 50 else str(val)
            parts.append(f"  {sk}:{val_str}")
        # 每行4个
        for i in range(0, len(parts), 3):
            print(" " + "".join(parts[i:i+3]))
        print(_color("└─────────────────────────────────────────────────┘", _BOLD))

    # ── 主循环 ──

    async def run_impl(self):
        """CLI 交互主循环"""
        print(_BANNER)

        # 检测已有角色
        if self._player_loader.character_exists(self.session_id):
            loaded = self._player_loader.load_character(self.session_id)
            if loaded:
                self._character = loaded
                print(_color(f"\n📋 检测到已有角色: {_color(loaded.name, _GREEN)} ({loaded.occupation})", _DIM))
                raw = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input(f"{_color('直接进入游戏(v) / 重建(r)', _GREEN)} > ")
                )
                if raw.strip().lower() in ("r", "重建"):
                    self._character = await self._character_creation_wizard()
                else:
                    print()
                    self._show_character_sheet(loaded)
                    print()
            else:
                self._character = await self._character_creation_wizard()
        else:
            self._character = await self._character_creation_wizard()

        print(_color("💡 直接输入文本开始探索，输入 /help 查看命令", _DIM))
        print()

        self._running = True
        while self._running:
            try:
                raw = await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: input(f"{_color('🎲', _YELLOW)} {_color('输入 >', _BOLD)} "),
                )
            except (KeyboardInterrupt, EOFError):
                print()
                break

            msg = await self.parse(raw)
            if not msg.type:
                continue

            # 特殊处理 /quit
            if msg.type == MessageType.SYSTEM_CMD and msg.text.strip().lower() in ("/quit", "/q"):
                await self.send(OutboundMessage.system_msg("正在退出..."))
                break

            # 特殊处理 /roll 或 /r 技能名
            if msg.type == MessageType.SYSTEM_CMD and msg.text.strip().lower().startswith(("/roll", "/r ")):
                parts = msg.text.strip().split(maxsplit=1)
                skill = parts[1] if len(parts) > 1 else ""
                await self._manual_roll(skill)
                continue

            # 处理 /sheet
            if msg.type == MessageType.SYSTEM_CMD and msg.text.strip().lower() in ("/sheet", "/s"):
                self._show_character_sheet()
                continue

            # 处理 /ingest（不是游戏回合）
            if msg.type == MessageType.SYSTEM_CMD and msg.text.strip().lower().startswith("/ingest"):
                out = await self.handle(msg)
                await self.send(out)
                print()
                continue

            # 处理 /start /modules（不是游戏回合）
            lower = msg.text.strip().lower()
            if msg.type == MessageType.SYSTEM_CMD and (
                lower.startswith("/start") or lower in ("/modules", "/list")
            ):
                out = await self.handle(msg)
                await self.send(out)
                print()
                continue

            # 常规处理
            self._turn_count += 1
            if msg.type == MessageType.PLAYER_INPUT:
                print(_color(f"\n[{self._turn_count}] 处理中...", _DIM))

            out = await self.handle(msg)
            await self.send(out)
            print()

        print(_color("\n👋 感谢使用 GlyphKeeper！", _CYAN))

    # ── 工具方法 ──

    async def _manual_roll(self, skill_name: str = ""):
        """手动掷 D100 测试，支持指定技能"""
        from src.tools.dice import roll_d100
        from src.domain.coc_rules import determine_success_level

        tens, ones, value = roll_d100()
        print()
        print(_color("─ 掷骰结果 ───────────────────────", _BOLD))
        print(f"  掷骰值:       {_color(str(value), _YELLOW)} ({tens * 10} + {ones})")

        if skill_name and self._character:
            skill_val = self._character.skills.get(skill_name, 0)
            if skill_val:
                level = determine_success_level(skill_val, value)
                color = _GREEN if level.value in ("CRITICAL", "EXTREME", "HARD", "REGULAR") else _RED
                print(f"  技能 [{skill_name}]: {skill_val}")
                print(f"  结果:          {_color(level.value, color)}")
            else:
                print(f"  未知技能: {skill_name}")
        else:
            for skill in [10, 30, 50, 70, 90]:
                level = determine_success_level(skill, value)
                marker = " ◀" if level.value in ("CRITICAL", "FUMBLE") else ""
                colored = _color(f"{level.value:>10}", _GREEN)
                print(f"  技能 {skill:>3}: {colored}{marker}")

        print(_color("──────────────────────────────────", _DIM))
        print()


# ====================================================================
# 辅助函数
# ====================================================================


def _get_base_skill_values(stats: Stats) -> dict[str, int]:
    """获取 CoC 7版 基础技能值（根据属性计算）"""
    return {
        "会计": 5, "人类学": 1, "估价": 5, "考古学": 1,
        "艺术与手艺": 5, "取悦": 15, "魅惑": 15, "攀爬": 20,
        "计算机使用": 5, "信用评级": 0, "克苏鲁神话": 0,
        "乔装": 5, "汽车驾驶": 20, "电气维修": 10, "电子学": 1,
        "话术": 5, "斗殴": 25, "手枪": 20, "急救": 30,
        "历史": 5, "恐吓": 15, "跳跃": 20, "法律": 5,
        "图书馆利用": 20, "聆听": 20, "锁匠": 1, "机械维修": 10,
        "医学": 1, "博物学": 10, "导航": 10, "神秘学": 5,
        "操作重型机械": 1, "说服": 10, "精神分析": 1, "心理学": 10,
        "骑术": 5, "妙手": 10, "侦查": 25, "潜行": 20,
        "生存": 10, "游泳": 20, "投掷": 20, "追踪": 10,
        "动物驯养": 5, "潜水": 1, "爆破": 1, "读唇": 1,
        "催眠": 1, "炮术": 1,
        "闪避": stats.dexterity // 2 if stats else 25,
        "语言(母语)": stats.education if stats else 50,
        "射击(步枪/霰弹枪)": 25,
        "射击(冲锋枪)": 15,
        "射击(机枪)": 10,
        "格斗(刀)": 15,
        "格斗(斧)": 15,
        "科学(化学)": 1, "科学(物理学)": 1, "科学(生物学)": 1,
        "科学(天文学)": 1, "科学(地质学)": 1, "科学(药学)": 1,
    }


# ── 独立入口 ──

async def main_async():
    adapter = CliAdapter()
    # 启动时尝试载入已摄入的模组
    try:
        from src.state.module_loader import ModuleLoader
        loader = ModuleLoader()
        modules = await loader.list_modules()
        if modules:
            mod = modules[0]
            name = mod["name"]
            print(_color(f"\n📖 检测到已摄入模组: {name}", _GREEN))
            print(_color(f"   {mod.get('locations', 0)} 个场景", _DIM))
            print()
            # 将模组名存入 adapter，供 /start 命令使用
            adapter._available_module = name
    except Exception as e:
        logger.debug(f"模组检测跳过: {e}")
        adapter._available_module = None

    await adapter.run()


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\n👋 再见！")


if __name__ == "__main__":
    main()
