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
import os

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
from src.state.player_state import CharacterStore
from src.state.snapshot import SnapshotManager
from src.workers.memorizer_worker import MemorizerWorker
from src.workers.world_summarizer import WorldSummarizer
from src.workers.background_sync import BackgroundSync
from src.tools.config import get_settings

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


def _ensure_lightrag_backup_dir(snapshot_id: str) -> Path:
    """获取快照对应的 LightRAG 备份目录路径（不存在则创建）"""
    from src.tools import PROJECT_ROOT
    backup_dir = PROJECT_ROOT / "data" / "backups" / "lightrag_snapshots" / snapshot_id
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


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
        self._game_started: bool = False
        self._available_module: Optional[str] = None
        self._player_loader: Optional[CharacterStore] = None
        self._snapshot_mgr = SnapshotManager()
        self._character: Optional[Character] = None

        # Workers -- 懒初始化，由 _ensure_workers 创建
        self._memorizer: Optional[MemorizerWorker] = None
        self._summarizer: Optional[WorldSummarizer] = None
        self._sync: Optional[BackgroundSync] = None
        self._worker_tasks: list[asyncio.Task] = []

    # ── AbstractAdapter 接口实现 ──

    async def parse(self, raw_input: Any) -> InboundMessage:
        """将终端原始输入解析为 InboundMessage"""
        text = str(raw_input).strip() if raw_input else ""
        routing = dict(
            platform="cli",
            channel_id="",
            user_id=os.getlogin(),
            world_id=get_settings().project.active_world,
        )

        if not text:
            return InboundMessage(type="", text="", session_id=self.session_id, **routing)

        if text.startswith("/"):
            return InboundMessage.system_cmd(text, self.session_id, **routing)

        return InboundMessage.player_input(text, self.session_id, **routing)

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

        # 先输入调查员姓名
        name = ""
        while not name.strip():
            raw = await asyncio.get_event_loop().run_in_executor(
                None, lambda: input(f"{_color('姓名', _GREEN)} > ")
            )
            name = raw.strip()
        print()

        # 再让玩家从职业模板中选择
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

        # 然后进入属性骰点环节
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

        # 确定属性后分配职业技能点
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

        # 最后完成角色创建
        self._character = character
        if self._player_loader:
            await self._player_loader.save(self.session_id, character)

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
        self._player_loader = CharacterStore()

        print(_color("  输入 /start <模组名> 开始游戏，或 /load <存档名> 读档", _DIM))
        print(_color("  输入 /help 查看所有命令", _DIM))
        print()

        # 后台 Workers 启动（仅日志模式，等真正开始游戏后才工作）
        await self._ensure_workers()

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

            # ── 未开始游戏时，只放行特定命令 ──
            if not self._game_started:
                lower = msg.text.strip().lower()
                allowed = (
                    "/quit", "/q", "/help", "/h",
                    "/start", "/load", "/list", "/saves", "/save",
                    "/modules", "/ingest", "/debug", "/d",
                    "/scene", "/sc", "/worlds",
                )
                if msg.type == MessageType.PLAYER_INPUT or not any(
                    lower.startswith(a) for a in allowed
                ):
                    await self.send(OutboundMessage.system_msg(
                        "请先使用 /start <模组名> 开始游戏，或 /load <存档名> 读档。",
                        level="warn", session_id=self.session_id,
                    ))
                    continue

            # 特殊处理 /quit
            if msg.type == MessageType.SYSTEM_CMD and msg.text.strip().lower() in ("/quit", "/q"):
                await self.send(OutboundMessage.system_msg("正在退出..."))
                break

            # 处理 /roll 或 /r — 专业掷骰处理器
            if msg.type == MessageType.SYSTEM_CMD:
                lower_cmd = msg.text.strip().lower()
                is_roll = lower_cmd == "/roll" or lower_cmd.startswith("/roll ") or lower_cmd.startswith("/r ")
                if is_roll:
                    out = await self._handle_roll_cmd(msg.text.strip(), self.session_id)
                    await self.send(out)
                    print()
                    continue

            # 处理 /sheet
            if msg.type == MessageType.SYSTEM_CMD and msg.text.strip().lower() in ("/sheet", "/s"):
                self._show_character_sheet()
                continue

            # 处理 /save /load — 存档系统
            if msg.type == MessageType.SYSTEM_CMD:
                cmd_lower = msg.text.strip().lower()
                if cmd_lower.startswith("/save"):
                    out = await self._handle_save_cmd(cmd_lower, self.session_id)
                    await self.send(out)
                    continue
                if cmd_lower.startswith("/load"):
                    out = await self._handle_load_cmd(cmd_lower, self.session_id, msg)
                    await self.send(out)
                    continue

            # 处理 /inventory — 背包
            if msg.type == MessageType.SYSTEM_CMD and msg.text.strip().lower() in ("/inventory", "/inv", "/i"):
                out = await self._handle_inventory_cmd(self.session_id)
                await self.send(out)
                continue

            # 处理 /time — 游戏时间
            if msg.type == MessageType.SYSTEM_CMD and msg.text.strip().lower() in ("/time", "/t"):
                out = await self._handle_time_cmd(self.session_id)
                await self.send(out)
                continue

            # 处理 /debug — 原始状态
            if msg.type == MessageType.SYSTEM_CMD and msg.text.strip().lower() in ("/debug", "/d"):
                out = await self._handle_debug_cmd(self.session_id)
                await self.send(out)
                continue

            # 处理 /rag — RAG 搜索调试
            if msg.type == MessageType.SYSTEM_CMD and msg.text.strip().lower().startswith("/rag"):
                out = await self._handle_rag_cmd(msg.text.strip())
                await self.send(out)
                continue

            # 处理 /scene — 查看当前场景实体/物品/出口
            if msg.type == MessageType.SYSTEM_CMD and msg.text.strip().lower() in ("/scene", "/sc"):
                out = await self._handle_scene_cmd(self.session_id)
                await self.send(out)
                continue

            # 处理 /list — 存档列表（覆盖 base 的 /list→modules 路由）
            if msg.type == MessageType.SYSTEM_CMD and msg.text.strip().lower() in ("/list", "/saves"):
                out = await self._handle_list_saves_cmd(self.session_id)
                await self.send(out)
                continue

            # 处理 /ingest（不是游戏回合）
            if msg.type == MessageType.SYSTEM_CMD and msg.text.strip().lower().startswith("/ingest"):
                out = await self.handle(msg)
                await self.send(out)
                print()
                continue

            # 处理 /rollback — 回滚到指定事件版本
            if msg.type == MessageType.SYSTEM_CMD and msg.text.strip().lower().startswith("/rollback"):
                out = await self._handle_rollback_cmd(msg.text.strip(), self.session_id)
                await self.send(out)
                print()
                continue

            # 处理 /worlds — 列出所有世界（不是游戏回合）
            if msg.type == MessageType.SYSTEM_CMD and msg.text.strip().lower() in ("/worlds",):
                out = await self._handle_worlds_cmd(self.session_id)
                await self.send(out)
                print()
                continue

            # 处理 /start /modules（不是游戏回合）
            lower = msg.text.strip().lower()
            if msg.type == MessageType.SYSTEM_CMD and (
                lower.startswith("/start") or lower in ("/modules",)
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

            # 检测 state 中是否有 pending_dice，进入交互式掷骰子循环
            await self._resolve_pending_dice_interactive(session_id=self.session_id)

            # 每轮交互后触发记忆固化
            await self._trigger_memorizer(session_id=self.session_id)

            print()

        # Workers 优雅退出
        await self._stop_workers()
        print(_color("  后台任务已停止", _DIM))
        print(_color("\n感谢使用 GlyphKeeper！", _CYAN))

    # ── 工具方法 ──

    # ================================================================
    # 游戏生命周期
    # ================================================================

    async def _handle_start_cmd(self, cmd: str, session_id: str, msg: Optional[InboundMessage] = None) -> OutboundMessage:
        """处理 /start [模组名] — 创建角色 + 加载模组开始游戏。

        覆盖 base._handle_start_cmd，在加载模组之前先创建/选择角色。
        """
        # ── 角色创建/选择 ──
        exists = await self._player_loader.exists(session_id) if self._player_loader else False
        if exists:
            loaded = await self._player_loader.load(session_id)
            if loaded:
                print(_color(f"\n  检测到已有角色: {loaded.name} ({loaded.occupation})", _DIM))
                raw = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: input(f"  {_color('使用此角色(v) / 新建(r)', _GREEN)} > ")
                )
                if raw.strip().lower() in ("r", "重建", "新建"):
                    self._character = await self._character_creation_wizard()
                    if self._player_loader:
                        await self._player_loader.save(session_id, self._character)
                else:
                    self._character = loaded
        else:
            self._character = await self._character_creation_wizard()
            if self._player_loader:
                await self._player_loader.save(session_id, self._character)

        # ── 加载模组（委托给 base 类） ──
        result = await super()._handle_start_cmd(cmd, session_id, msg)

        # ── 确认模组已加载（scheduler 中有该会话） ──
        session_exists = (
            self._scheduler is not None
            and self._scheduler.get_session(session_id) is not None
        )
        if not session_exists:
            return result

        # ── 将角色注入 GameState ──
        if self._character and self._scheduler:
            from dataclasses import asdict
            slot = self._scheduler.get_session(session_id)
            if slot:
                slot.state["character"] = asdict(self._character)

        self._game_started = True
        return result

    # ================================================================
    # 世界管理命令
    # ================================================================

    async def _handle_worlds_cmd(self, session_id: str) -> OutboundMessage:
        """处理 /worlds — 列出所有世界和当前活跃世界"""
        from src.tools.world_manager import list_worlds
        from src.tools import get_settings

        worlds = list_worlds()
        if not worlds:
            return OutboundMessage.system_msg(
                "没有找到任何世界。使用 /start <模组名> 创建新世界。",
                session_id=session_id,
            )

        active = get_settings().project.active_world
        lines = [f"可用世界 ({len(worlds)} 个):"]
        for w in worlds:
            mark = _color("→", _GREEN) if w == active else " "
            lines.append(f"  {mark} {w}")
        lines.append(f"\n当前世界: {_color(active, _CYAN)}")
        lines.append("每局 /start 自动创建新世界，数据完全隔离。")
        return OutboundMessage.system_msg("\n".join(lines), session_id=session_id)

    # ================================================================
    # 增强型掷骰系统
    # ================================================================

    async def _handle_roll_cmd(self, cmd: str, session_id: str) -> OutboundMessage:
        """
        处理 /roll 命令。

        支持:
          /roll              — D100 测试（参考多个技能等级）
          /roll <技能名>     — 用角色的技能值检定
          /roll <数值>       — 用指定数值检定
          /roll <表达式>     — 任意骰子表达式（3D6, 1D3+1D4 等）
          /roll help         — 显示用法
        """
        from src.tools.dice import roll_d100, roll_dice as roll_expression
        from src.domain.coc_rules import determine_success_level

        parts = cmd.split(maxsplit=1)
        arg = parts[1].strip().lower() if len(parts) > 1 else ""

        if arg in ("help", "-h", "--help"):
            return OutboundMessage.system_msg(
                "/roll 用法:\n"
                "  /roll               — D100 参考检定（显示多个技能等级的判定）\n"
                "  /roll <技能名>       — 用角色技能值检定（如: /roll 侦查）\n"
                "  /roll <数值>         — 用指定阈值检定（如: /roll 50）\n"
                "  /roll <表达式>       — 任意骰子（如: /roll 3D6, /roll 1D3+1D4）\n"
                "  /r <技能名>          — 同上（快捷方式）",
                session_id=session_id,
            )

        # ── 骰子表达式模式 — 包含 D/d 字符 ──
        if "d" in arg or "D" in arg:
            try:
                total = roll_expression(arg.upper())
                return OutboundMessage.system_msg(
                    f"🎲 {arg.upper()} = {_color(str(total), _YELLOW)}",
                    session_id=session_id,
                )
            except Exception as e:
                return OutboundMessage.system_msg(
                    f"无效的骰子表达式: {arg} ({e})", level="error", session_id=session_id
                )

        # ── 数值模式 — 纯数字 ──
        try:
            threshold = int(arg)
            tens, ones, value = roll_d100()
            from src.domain.coc_rules import determine_success_level
            level = determine_success_level(threshold, value)
            color = _GREEN if level.value in ("CRITICAL", "EXTREME", "HARD", "REGULAR") else _RED
            return OutboundMessage.system_msg(
                f"🎲 D100 = {_color(str(value), _YELLOW)}  "
                f"阈值 [{threshold}] → {_color(level.value, color)}"
                f"{' (大成功!)' if level.value == 'CRITICAL' else ''}"
                f"{' (大失败!)' if level.value == 'FUMBLE' else ''}",
                session_id=session_id,
            )
        except ValueError:
            pass

        # ── 技能名模式 — 从角色数据查找技能值 ──
        skill_name = arg
        skill_value = 0

        # 先查角色技能
        if self._character:
            skill_value = self._character.skills.get(skill_name, 0)

        # 查不到则查基础技能表
        if not skill_value:
            base_skills = _get_base_skill_values(
                self._character.stats if self._character else None
            ) if hasattr(self, '_character') and self._character else {}
            skill_value = base_skills.get(skill_name, 0)

        # 仍查不到则查 CoC 属性快捷名
        if not skill_value and self._character:
            stat_map = {
                "str": self._character.stats.strength,
                "con": self._character.stats.constitution,
                "siz": self._character.stats.size,
                "dex": self._character.stats.dexterity,
                "app": self._character.stats.appearance,
                "int": self._character.stats.intelligence,
                "pow": self._character.stats.power,
                "edu": self._character.stats.education,
            }
            if skill_name.upper() in stat_map:
                skill_value = stat_map[skill_name.upper()]
                skill_name = skill_name.upper()
                # 属性检定 ×5
                threshold_display = f"{skill_value} (属性×5={skill_value * 5})"
                skill_value = skill_value * 5

        if not skill_value:
            # 无对应技能 / 属性 — 降级为纯 D100 参考检定
            tens, ones, value = roll_d100()
            lines = [f"🎲 D100 = {_color(str(value), _YELLOW)} ({tens * 10}+{ones})"]
            for ref in [10, 30, 50, 70, 90]:
                level = determine_success_level(ref, value)
                marker = " ◀" if level.value in ("CRITICAL", "FUMBLE") else ""
                lines.append(f"  技能 {ref:>3}: {_color(f'{level.value:>10}', _GREEN)}{marker}")
            return OutboundMessage.system_msg(
                "\n".join(lines) + f"\n(未找到技能 '{skill_name}'，显示参考检定)",
                session_id=session_id,
            )

        # 正常技能检定
        tens, ones, value = roll_d100()
        level = determine_success_level(skill_value, value)
        is_success = level.value in ("CRITICAL", "EXTREME", "HARD", "REGULAR")
        color = _GREEN if is_success else _RED
        emoji = "🎲"
        extra = ""
        if level.value == "CRITICAL":
            extra = " 大成功!! 🎉"
            emoji = "🌟"
        elif level.value == "FUMBLE":
            extra = " 大失败!! 💀"

        return OutboundMessage.system_msg(
            f"{emoji} [{skill_name}] D100 = {_color(str(value), _YELLOW)}  "
            f"技能 {skill_value} → {_color(level.value, color)}{extra}",
            session_id=session_id,
        )
    
    _DICE_TIMEOUT = 60.0
    """等待玩家掷骰输入的超时秒数，超时后自动掷骰（状态：超时保护）"""

    async def _resolve_pending_dice_interactive(self, session_id: str):
        """
        检测 state 中是否有 pending_dice，进入交互式掷骰子循环。

        流程：先检测 pending_dice 并显示请求信息，再等待玩家输入掷骰值。
        超时后自动掷骰，无效输入走自动掷骰，最后注入 roll_value 并重提交引擎。
        """
        state = self._scheduler.get_session_state(session_id) if self._scheduler else None
        if not state:
            return

        pending = state.get("pending_dice")
        if not pending:
            return

        # 已有 roll_value（来自上一轮注入），跳过
        if pending.get("roll_value") is not None:
            return

        reason = pending.get("reason", "掷骰检定")
        skill_name = pending.get("skill_name", "")
        difficulty = pending.get("difficulty", "REGULAR")
        bonus_dice = pending.get("bonus_dice", 0)
        penalty_dice = pending.get("penalty_dice", 0)

        # 显示掷骰请求
        print()
        print(_color("=" * 50, _YELLOW))
        print(_color(f"  [掷骰] 需要检定!", _BOLD))
        print(_color(f"  原因: {reason}", _YELLOW))
        if skill_name:
            skill_val = 0
            if self._character:
                skill_val = self._character.skills.get(skill_name, 0)
            if skill_val:
                print(f"  技能: {skill_name} ({skill_val})")
            else:
                print(f"  技能: {skill_name}")
        print(f"  难度: {difficulty}")
        if bonus_dice:
            print(f"  奖励骰: +{bonus_dice}")
        if penalty_dice:
            print(f"  惩罚骰: +{penalty_dice}")
        print(_color("=" * 50, _YELLOW))
        print(_color(f"  输入掷骰值 (1-100)，{int(self._DICE_TIMEOUT)}s 无操作自动掷骰", _DIM))
        print()

        # 等待玩家输入（带超时）
        raw = ""
        timed_out = False
        try:
            raw = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: input(f"{_color('掷骰值', _GREEN)} > "),
                ),
                timeout=self._DICE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            print(_color(f"  [超时] 无响应，自动掷骰", _YELLOW))
            timed_out = True
        except (KeyboardInterrupt, EOFError):
            print()
            return

        # 确定掷骰值
        if timed_out or not raw.strip():
            from src.tools.dice import roll_d100
            _, _, roll_value = roll_d100()
            print(_color(f"  自动掷骰: {roll_value}", _DIM))
        else:
            try:
                roll_value = int(raw.strip())
                if not (1 <= roll_value <= 100):
                    print(_color(f"  掷骰值越界 (1-100)，使用 50 替代", _YELLOW))
                    roll_value = 50
            except ValueError:
                print(_color(f"  无效数值，自动掷骰", _YELLOW))
                from src.tools.dice import roll_d100
                _, _, roll_value = roll_d100()

        # 注入 roll_value 到 pending_dice
        pending["roll_value"] = roll_value

        # 重新提交到引擎（带 roll_value）
        try:
            self._turn_count += 1
            replay_msg = InboundMessage(
                type=MessageType.PLAYER_INPUT,
                text=state.get("player_input", ""),
                session_id=session_id,
                platform="cli",
                world_id=state.get("world_id", ""),
            )
            narrative = await self._scheduler.submit(replay_msg)
            if narrative:
                print()
                print(_color("━" * 50, _DIM))
                print(_color(narrative, _CYAN))
                print(_color("━" * 50, _DIM))

            # 显示检定结果
            new_state = self._scheduler.get_session_state(session_id)
            if new_state:
                resolution = new_state.get("resolution") or {}
                roll_val = resolution.get("roll_value", roll_value)
                succ_level = resolution.get("success_level", "")
                if succ_level:
                    level_color = _GREEN if succ_level in ("CRITICAL", "EXTREME", "HARD", "REGULAR") else _RED
                    print(
                        f"  🎲 检定: {_color(str(roll_val), _YELLOW)} "
                        f"→ {_color(succ_level, level_color)}"
                    )

        except Exception as e:
            logger.error(f"掷骰结果提交失败: {e}")
            print(_color(f"掷骰结果提交失败: {e}", _RED))

        print()

    async def _handle_rollback_cmd(self, cmd: str, session_id: str) -> OutboundMessage:
        """处理 /rollback 命令 — 回滚到指定事件版本

        用法:
          /rollback                — 列出最近事件，引导交互
          /rollback <version>      — 直接回滚到指定版本

        流程：从 EventStore 读取事件列表 → 确认回滚目标 →
        通过 Scheduler.rollback_session 替换会话状态。
        """
        if self._scheduler is None or self._scheduler.engine is None:
            return OutboundMessage.system_msg("引擎不可用", level="error", session_id=session_id)

        engine = self._scheduler.engine
        event_log = engine.event_log
        if event_log is None:
            return OutboundMessage.system_msg(
                "EventLog 未启用，无法回滚", level="error", session_id=session_id,
            )

        parts = cmd.split(maxsplit=1)
        target_version = int(parts[1].strip()) if len(parts) > 1 else None

        # 获取当前版本信息
        latest_ver = await event_log.get_latest_version(session_id)
        if latest_ver == 0:
            return OutboundMessage.system_msg(
                "当前会话无事件记录，无法回滚", level="warn", session_id=session_id,
            )

        # 未指定版本 → 列出最近事件供选择
        if target_version is None:
            events = await event_log.get_events(session_id, since_version=0)
            total = len(events)
            show = events[-10:]  # 最多显示最近 10 条
            # 过滤掉 PlayerInput（回滚无意义）
            show = [e for e in show if e.get("type") != "PlayerInput"]
            lines = [
                f"当前最新版本: {latest_ver}（共 {total} 条事件）",
                "最近事件如下：",
            ]
            for evt in show:
                v = evt.get("version", "?")
                t = evt.get("type", "?")
                ts = (evt.get("timestamp") or "")[11:19]
                # 从 data 中取简短的描述文本
                data = evt.get("data", {})
                snippet = ""
                if isinstance(data, dict):
                    # NarrativeOutput → 优先取 patch.narrative
                    if t in ("NarrativeOutput",):
                        patch = data.get("patch", {})
                        narrative = patch.get("narrative", "")
                        if narrative:
                            snippet = narrative[:60]
                    # PlayerInput → 取 text
                    elif t in ("PlayerInput",):
                        text = data.get("text", "")
                        if text:
                            snippet = text[:40]
                    # WorldInitialized → 显示模块名
                    elif t in ("WorldInitialized",):
                        module = data.get("module_name", "")
                        if module:
                            snippet = module[:40]
                snippet_str = f"  {snippet}" if snippet else ""
                lines.append(f"  #{v} [{t}] {ts}{snippet_str}")
            lines.append("")
            lines.append("使用 /rollback <版本号> 回滚到指定版本。")
            return OutboundMessage.system_msg("\n".join(lines), session_id=session_id)

        # 执行回滚
        if target_version < 0 or target_version > latest_ver:
            return OutboundMessage.system_msg(
                f"版本 {target_version} 越界（当前范围 0-{latest_ver}）",
                level="error", session_id=session_id,
            )

        # 如果目标是 PlayerInput 事件，自动跳到下一版（指向其后紧随的 NarrativeOutput）
        events_at_target = await event_log.get_events(session_id, since_version=target_version - 1)
        if events_at_target and events_at_target[0].get("type") == "PlayerInput":
            adjusted = target_version + 1
            if adjusted <= latest_ver:
                logger.info(
                    f"rollback: 跳过 PlayerInput #{target_version}，使用 #{adjusted}"
                )
                target_version = adjusted

        success = await self._scheduler.rollback_session(session_id, target_version)
        if not success:
            return OutboundMessage.system_msg(
                f"回滚到版本 {target_version} 失败", level="error", session_id=session_id,
            )

        # 从事件流中重建 session_knowledge_state：提取 target_version 之前的 ClueDiscovered 事件
        try:
            all_events = await event_log.get_events(session_id, since_version=0)
            clue_ids = []
            for evt in all_events:
                ver = evt.get("version", 0)
                if ver <= target_version and evt.get("type") == "ClueDiscovered":
                    kid = evt.get("data", {}).get("knowledge_id", "")
                    if kid:
                        clue_ids.append(kid)
            from src.state.session_state import SessionKnowledgeState
            sks = SessionKnowledgeState()
            char_name = self._character.name if self._character else ""
            await sks.restore_from_ids(session_id, clue_ids, character_name=char_name)
        except Exception as e:
            logger.warning(f"rollback: 重建知识状态失败: {e}")

        # 获取回滚后的 state 信息
        new_state = self._scheduler.get_session_state(session_id)
        location = new_state.get("current_location", "") if new_state else ""
        phase = new_state.get("game_phase", "") if new_state else ""
        info = f"  当前位置: {location}" if location else ""
        if phase:
            info += f"  阶段: {phase}"
        return OutboundMessage.system_msg(
            f"已回滚到版本 {target_version}（最新 {latest_ver}）\n{info}",
            level="info", session_id=session_id,
        )

    # ================================================================
    # 存档系统
    # ================================================================

    async def _handle_save_cmd(self, cmd: str, session_id: str) -> OutboundMessage:
        """处理 /save [存档名] — 创建游戏快照。

        先获取当前会话的 GameState，再用 SnapshotManager 创建快照。
        存档名缺省时使用时间戳标签。保留策略由 SnapshotManager 内部
        的 MAX_SNAPSHOTS_PER_SESSION 控制（默认 20 个）。
        """
        parts = cmd.split(maxsplit=1)
        label = parts[1].strip() if len(parts) > 1 else ""

        state = self._scheduler.get_session_state(session_id) if self._scheduler else None
        if state is None:
            return OutboundMessage.system_msg("当前无活跃会话，无法存档", level="warn", session_id=session_id)

        from datetime import datetime
        snap_label = label or f"auto_{datetime.now().strftime('%m%d_%H%M')}"

        try:
            snap_id = await self._snapshot_mgr.create(state, label=snap_label)

            # 全量备份 LightRAG 数据到快照关联目录
            try:
                from src.memory.vector_store import VectorStore
                vs = await VectorStore.get_instance("world")
                lightrag_dir = _ensure_lightrag_backup_dir(snap_id)
                await vs.backup_to(lightrag_dir)
            except Exception as e:
                logger.warning(f"存档: LightRAG 备份失败（不影响存档）: {e}")

            return OutboundMessage.system_msg(
                f"存档完成: [{snap_label}] (ID: {snap_id[:8]}...)",
                session_id=session_id,
            )
        except Exception as e:
            logger.error(f"存档失败: {e}")
            return OutboundMessage.system_msg(f"存档失败: {e}", level="error", session_id=session_id)

    async def _handle_load_cmd(self, cmd: str, session_id: str, msg: Optional[InboundMessage] = None) -> OutboundMessage:
        """处理 /load [存档名] — 读取游戏快照。

        先按标签名查找快照，匹配到后调用 SnapshotManager.restore()
        恢复状态并写入 scheduler 会话。若缺省存档名则加载最新快照。
        """
        parts = cmd.split(maxsplit=1)
        label = parts[1].strip() if len(parts) > 1 else ""

        try:
            snapshots = await self._snapshot_mgr.list_snapshots(session_id)

            if not snapshots:
                return OutboundMessage.system_msg("没有找到存档", level="warn", session_id=session_id)

            target_id = ""
            if label:
                # 按标签匹配
                matches = [s for s in snapshots if s.get("label", "") == label]
                if not matches:
                    available = ", ".join(s.get("label", "?") for s in snapshots[:10])
                    return OutboundMessage.system_msg(
                        f"未找到存档 [{label}]。可用存档: {available}",
                        level="warn", session_id=session_id,
                    )
                target_id = matches[0]["id"]
            else:
                # 取最新
                target_id = snapshots[0]["id"]

            restored = await self._snapshot_mgr.restore(target_id)
            if restored is None:
                return OutboundMessage.system_msg("存档数据损坏，无法读取", level="error", session_id=session_id)

            restored_state = restored.get("state", restored)
            known_ids = restored.get("known_knowledge_ids", [])

            # 写入 scheduler 当前会话
            if self._scheduler:
                # 若存档中无角色数据（旧存档），用当前角色兜底
                if not restored_state.get("character") and self._character:
                    from dataclasses import asdict
                    restored_state["character"] = asdict(self._character)
                # 使用 msg 中的 routing 信息，确保 key 与玩家当前会话一致
                platform = msg.platform if msg else "cli"
                channel_id = msg.channel_id if msg else ""
                world_id = msg.world_id if msg else ""
                await self._scheduler.restore_session_state(
                    session_id, restored_state,
                    platform=platform,
                    channel_id=channel_id,
                    world_id=world_id,
                )
                # 恢复角色引用
                char_data = restored_state.get("character")
                if char_data and self._player_loader:
                    from src.state.player_state import _dict_to_character
                    loaded_char = _dict_to_character(char_data)
                    if loaded_char:
                        self._character = loaded_char

            # 恢复 session_knowledge_state 至存档时的状态
            try:
                from src.state.session_state import SessionKnowledgeState
                sks = SessionKnowledgeState()
                char_name = self._character.name if self._character else ""
                await sks.restore_from_ids(session_id, known_ids, character_name=char_name)
            except Exception as e:
                logger.warning(f"读档: 恢复知识状态失败: {e}")

            # 恢复 LightRAG 数据
            try:
                from src.memory.vector_store import VectorStore
                vs = await VectorStore.get_instance("world", force_reinit=False)
                lightrag_dir = _ensure_lightrag_backup_dir(target_id)
                if lightrag_dir.exists():
                    await vs.restore_from(lightrag_dir)
                    # 重新初始化 VectorStore（读档后首次使用会懒加载）
                    await VectorStore.get_instance("world", force_reinit=True)
            except Exception as e:
                logger.warning(f"读档: LightRAG 恢复失败（可继续游戏）: {e}")

            self._game_started = True

            return OutboundMessage.system_msg(
                f"读档完成: [{label or snapshots[0].get('label', 'latest')}] "
                f"(轮次 {restored_state.get('beat_counter', 0)})",
                session_id=session_id,
            )

        except Exception as e:
            logger.error(f"读档失败: {e}")
            return OutboundMessage.system_msg(f"读档失败: {e}", level="error", session_id=session_id)

    async def _handle_list_saves_cmd(self, session_id: str) -> OutboundMessage:
        """处理 /list — 列出当前会话的所有存档。"""
        try:
            snapshots = await self._snapshot_mgr.list_snapshots(session_id)
            if not snapshots:
                return OutboundMessage.system_msg(
                    "没有存档。使用 /save <存档名> 创建存档。",
                    session_id=session_id,
                )

            lines = [f"存档列表 ({len(snapshots)} 个):"]
            for s in snapshots:
                lbl = s.get("label", "")
                ver = s.get("version", 0)
                ts = str(s.get("created_at", ""))[:19]  # ISO 前19字符
                lines.append(f"  [{lbl}]  v{ver}  ({ts})")
            lines.append("使用 /load <存档名> 读取存档。")
            return OutboundMessage.system_msg("\n".join(lines), session_id=session_id)

        except Exception as e:
            logger.error(f"列出存档失败: {e}")
            return OutboundMessage.system_msg(f"列出存档失败: {e}", level="error", session_id=session_id)

    # ================================================================
    # RAG 搜索调试命令
    # ================================================================

    async def _handle_rag_cmd(self, cmd: str) -> OutboundMessage:
        """处理 /rag — RAG 语义搜索调试

        支持:
          /rag <查询内容>         — 搜索世界知识 RAG（默认 hybrid 模式）
          /rag local <查询内容>   — local 模式搜索（仅图内关联）
          /rag global <查询内容>  — global 模式搜索（全局摘要）
          /rag naive <查询内容>   — naive 模式搜索（纯向量）
          /rag rules <查询内容>   — 搜索规则知识 RAG

        示例:
          /rag 旧图书馆的秘密
          /rag rules 技能检定规则
        """
        session_id = self.session_id

        # 解析命令参数：/rag [mode] <内容...>
        rest = cmd[len("/rag"):].strip()
        if not rest:
            return OutboundMessage.system_msg(
                "用法: /rag <查询内容>\n"
                "      /rag local/global/naive <查询内容>\n"
                "      /rag rules <查询内容>\n"
                "从 LightRAG 向量/图存储中搜索相关内容。",
                session_id=session_id,
            )

        # 解析可选的 mode 前缀
        mode = "hybrid"  # 默认模式
        query = rest
        domain = "world"

        first_word = rest.split(maxsplit=1)[0].lower() if " " in rest else ""
        known_modes = {"local", "global", "naive", "hybrid", "rules"}

        if first_word in known_modes:
            mode_or_domain = first_word
            remaining = rest.split(maxsplit=1)[1] if len(rest.split(maxsplit=1)) > 1 else ""
            if not remaining:
                return OutboundMessage.system_msg(
                    f"请指定查询内容，例如: /rag {mode_or_domain} 查询内容",
                    level="warn", session_id=session_id,
                )
            query = remaining
            if mode_or_domain == "rules":
                domain = "rules"
                mode = "hybrid"
            else:
                domain = "world"
                mode = mode_or_domain

        try:
            from src.memory.vector_store import VectorStore
            vs = await VectorStore.get_instance(domain=domain)

            # 显示搜索提示
            print()
            print(_color(f"  🔍 RAG 搜索: domain={domain} mode={mode}", _DIM))
            print(_color(f"     查询: {query[:80]}", _DIM))
            print()

            result = await vs.query(question=query, mode=mode, top_k=60)

            if not result or not result.strip():
                return OutboundMessage.system_msg(
                    "RAG 未返回结果（知识库可能为空或查询无匹配）",
                    level="warn", session_id=session_id,
                )

            # 格式化输出，截断过长结果
            MAX_LEN = 5000
            text = result.strip()
            if len(text) > MAX_LEN:
                text = text[:MAX_LEN] + "\n\n  ... (结果过长，截断显示)"

            lines = result.strip().split("\n")
            summary = f"RAG 搜索结果: {len(lines)} 行, {len(result)} 字符"

            return OutboundMessage.system_msg(
                f"{summary}\n\n{text}",
                session_id=session_id,
            )

        except ImportError as e:
            return OutboundMessage.system_msg(
                f"LightRAG 库未安装: {e}",
                level="error", session_id=session_id,
            )
        except Exception as e:
            logger.error(f"RAG 查询失败: {e}")
            return OutboundMessage.system_msg(
                f"RAG 查询失败: {type(e).__name__}: {e}",
                level="error", session_id=session_id,
            )

    # ================================================================
    # 信息查询命令
    # ================================================================

    async def _handle_inventory_cmd(self, session_id: str) -> OutboundMessage:
        """处理 /inventory — 查看背包物品。

        从 state 或角色数据中读取 inventory 字段。若角色数据中
        无物品信息，返回空背包提示。
        """
        state = self._scheduler.get_session_state(session_id) if self._scheduler else None
        items = []

        if state:
            char = state.get("character") or {}
            items = char.get("inventory", [])

        if not items:
            return OutboundMessage.system_msg(
                "背包是空的。\n(物品系统待完善 — 当前仅记录角色创建后的基础物品)",
                session_id=session_id,
            )

        lines = [f"背包 ({len(items)} 件):"]
        for it in items:
            name = it if isinstance(it, str) else it.get("name", str(it))
            qty = ""
            if isinstance(it, dict):
                qty = f" x{it.get('quantity', 1)}" if it.get("quantity", 1) > 1 else ""
            lines.append(f"  - {name}{qty}")
        return OutboundMessage.system_msg("\n".join(lines), session_id=session_id)

    async def _handle_scene_cmd(self, session_id: str) -> OutboundMessage:
        """处理 /scene — 查看当前场景的实体、物品、出口。

        调试用命令，从 PG 读模型表查询当前所在场景的结构化数据。
        """
        state = self._scheduler.get_session_state(session_id) if self._scheduler else None
        if state is None:
            return OutboundMessage.system_msg("当前无活跃会话", level="warn", session_id=session_id)

        current_loc = state.get("current_location", "")
        if not current_loc:
            return OutboundMessage.system_msg(
                "尚未设置初始地点（未开始游戏或模组未加载）。",
                level="warn", session_id=session_id,
            )

        try:
            from src.state.read_models import StaticReadStore
            store = StaticReadStore()

            # 查场景基本信息
            loc = await store.get_location(current_loc)
            if not loc:
                return OutboundMessage.system_msg(
                    f"场景 '{current_loc}' 未在数据库中找到（模组可能尚未摄入）。",
                    level="warn", session_id=session_id,
                )

            loc_name = loc.get("name", current_loc)
            loc_desc = loc.get("base_desc", "")
            loc_tags = loc.get("tags", [])
            raw_exits = loc.get("exits_json", {})
            if isinstance(raw_exits, str):
                import json
                raw_exits = json.loads(raw_exits) if raw_exits else {}

            # 查物品
            items = await store.get_interactables_by_location(current_loc)

            # 查 NPC 实体
            entities = await store.get_entities_by_location(current_loc)

            lines = [
                f"📍 场景: {loc_name}  ({current_loc})",
                f"   描述: {loc_desc[:120]}{'...' if len(loc_desc) > 120 else ''}",
            ]

            if loc_tags:
                lines.append(f"   标签: {', '.join(loc_tags)}")

            # 出口
            if raw_exits:
                exit_lines = [f"   出口 ({len(raw_exits)}):"]
                for direction, target_key in raw_exits.items():
                    exit_lines.append(f"     {direction} → {target_key}")
                lines.append("\n".join(exit_lines))
            else:
                lines.append("   出口: 无")

            # 物品
            if items:
                lines.append(f"\n  🪑 物品 ({len(items)} 件):")
                for item in items:
                    item_name = item.get("name", item.get("key", "?"))
                    item_key = item.get("key", "")
                    item_tags = item.get("tags", [])
                    tag_str = f" [{', '.join(item_tags)}]" if item_tags else ""
                    lines.append(f"    - {item_name}  ({item_key}){tag_str}")
            else:
                lines.append(f"\n  🪑 物品: 无")

            # NPC 实体
            if entities:
                lines.append(f"\n  🧑 NPC ({len(entities)} 位):")
                for ent in entities:
                    ent_name = ent.get("name", ent.get("key", "?"))
                    ent_key = ent.get("key", "")
                    ent_tags = ent.get("tags", [])
                    tag_str = f" [{', '.join(ent_tags)}]" if ent_tags else ""
                    lines.append(f"    - {ent_name}  ({ent_key}){tag_str}")
            else:
                lines.append(f"\n  🧑 NPC: 无")

            # GameState 中的额外运行时信息
            scene_npcs = state.get("scene_npcs") or []
            entity_name_map = state.get("entity_name_map") or {}
            if entity_name_map:
                lines.append(f"\n  📋 entity_name_map ({len(entity_name_map)} 条):")
                for ek, en in entity_name_map.items():
                    lines.append(f"    {ek} → {en}")

            return OutboundMessage.system_msg(
                "\n".join(lines),
                session_id=session_id,
            )

        except ImportError as e:
            return OutboundMessage.system_msg(
                f"数据库模块不可用: {e}",
                level="error", session_id=session_id,
            )
        except Exception as e:
            logger.error(f"场景查询失败: {e}")
            return OutboundMessage.system_msg(
                f"场景查询失败: {type(e).__name__}: {e}",
                level="error", session_id=session_id,
            )

    async def _handle_time_cmd(self, session_id: str) -> OutboundMessage:
        """处理 /time — 显示游戏内时间。"""
        state = self._scheduler.get_session_state(session_id) if self._scheduler else None
        if state is None:
            return OutboundMessage.system_msg("当前无活跃会话", level="warn", session_id=session_id)

        from src.tools.time import TimeSlot, get_time_description
        slot_str = state.get("time_slot", "MORNING")
        slot = TimeSlot(slot_str) if slot_str in TimeSlot._value2member_map_ else TimeSlot.MORNING
        desc = get_time_description(slot)
        beat = state.get("beat_counter", 0)
        phase = state.get("game_phase", "exploration")
        scenario = state.get("scenario_name", "") or "未命名模组"

        return OutboundMessage.system_msg(
            f"游戏时间:\n"
            f"  模组:    {scenario}\n"
            f"  时段:    {slot_str} ({desc})\n"
            f"  阶段:    {phase}\n"
            f"  节拍:    第 {beat} 轮",
            session_id=session_id,
        )

    async def _handle_debug_cmd(self, session_id: str) -> OutboundMessage:
        """处理 /debug — 显示完整 GameState 原始 JSON。

        调试用命令，输出当前会话状态的所有字段。
        """
        state = self._scheduler.get_session_state(session_id) if self._scheduler else None
        if state is None:
            return OutboundMessage.system_msg("当前无活跃会话", level="warn", session_id=session_id)

        import json
        # 移除 node_trace（太长）和 character 技能详情
        debug_state = dict(state)
        debug_state.pop("node_trace", None)
        char = debug_state.get("character")
        if char and isinstance(char, dict):
            char_copy = dict(char)
            if "skills" in char_copy:
                # 只显示技能数量而非全部列表
                char_copy["skills"] = f"<{len(char_copy['skills'])} 项技能>"
            debug_state["character"] = char_copy

        text = json.dumps(debug_state, ensure_ascii=False, indent=2, default=str)
        # 截断过长输出
        if len(text) > 3000:
            text = text[:3000] + "\n  ... (截断)"
        return OutboundMessage.system_msg(f"GameState:\n{text}", session_id=session_id)

    # ================================================================
    # Workers 生命周期管理
    # ================================================================

    async def _ensure_workers(self):
        """初始化三个后台 Worker 并启动它们的后台任务。

        先通过 EventStore 和 VectorStore 构建存储层实例，
        再创建 MemorizerWorker、WorldSummarizer、BackgroundSync，
        最后用 asyncio.create_task 启动。各 Worker 内部已处理
        存储层为 None 的降级逻辑，初始化失败不影响游戏主循环。
        """
        if self._memorizer is not None:
            return

        try:
            from src.memory.event_store import create_event_store
            from src.memory.vector_store import VectorStore

            event_store = await create_event_store()
            vector_store = await VectorStore.get_instance(
                domain="world", llm_tier="standard",
            )

            self._memorizer = MemorizerWorker(
                event_store=event_store, vector_store=vector_store, interval=300,
            )
            self._summarizer = WorldSummarizer(
                event_store=event_store, vector_store=vector_store, interval=600,
            )
            self._sync = BackgroundSync(
                event_store=event_store, vector_store=vector_store,
            )

            self._worker_tasks = [
                asyncio.create_task(self._memorizer.start(), name="memorizer"),
                asyncio.create_task(self._summarizer.start(), name="summarizer"),
                asyncio.create_task(self._sync.start(), name="sync"),
            ]
            logger.info("三个后台 Worker 已启动")
        except Exception as e:
            logger.warning(f"Worker 初始化跳过 ({e})")

    async def _stop_workers(self):
        """依次停止所有 Worker，等待各自任务结束。"""
        for w, name in [
            (self._memorizer, "memorizer"),
            (self._summarizer, "summarizer"),
            (self._sync, "sync"),
        ]:
            if w is not None:
                try:
                    await w.stop()
                except Exception as e:
                    logger.debug(f"{name} stop 异常 ({e})")

        for t in self._worker_tasks:
            if not t.done():
                t.cancel()
        if self._worker_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*self._worker_tasks, return_exceptions=True),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                logger.warning("Worker 停止超时，强制放弃")
                for t in self._worker_tasks:
                    if not t.done():
                        t.cancel()
            self._worker_tasks.clear()
            logger.info("所有 Worker 已停止")

    async def _trigger_memorizer(self, session_id: str):
        """每轮交互后触发 MemorizerWorker 固化当前轮叙事。

        若 EventStore 中有待固化的事件，则调用 consolidate_session
        写入 VectorStore。实现采用 try-and-ignore 模式，失败不影响
        游戏主循环。
        """
        if self._memorizer is None:
            return
        try:
            if self._memorizer._event_store is not None:
                events = await self._memorizer._event_store.get_events(
                    session_id, since_version=0,
                )
                if events:
                    await self._memorizer.consolidate_session(session_id, events)
                    await self._memorizer.trigger_now()
        except Exception as e:
            logger.debug(f"记忆触发跳过 ({e})")


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
