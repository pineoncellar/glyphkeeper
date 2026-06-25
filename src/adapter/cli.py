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
            print(f"  Session ID:  {message.session_id}")
            print(f"  轮次:         {self._turn_count}")
            print(f"  游戏阶段:     {d.get('game_phase', 'unknown')}")
            print(f"  战斗进行中:   {'是' if d.get('combat_active') else '否'}")
            print(f"  激活标签:     {d.get('active_tags', [])}")
            if d.get("pending_dice"):
                print(f"  等待掷骰:     {d['pending_dice']}")
            print(_color("──────────────────────────────────", _DIM))

        elif message.type == MessageType.DICE_REQUEST:
            print(_color(f"\n🎲 请掷骰: {message.text}", _YELLOW))

    # ── 主循环 ──

    async def run_impl(self):
        """CLI 交互主循环"""
        print(_BANNER)
        print(_color("💡 输入 /help 查看命令，直接输入文本开始游戏", _DIM))
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

            # 特殊处理 /quit（base 里不处理）
            if msg.type == MessageType.SYSTEM_CMD and msg.text.strip().lower() in ("/quit", "/q"):
                await self.send(OutboundMessage.system_msg("正在退出..."))
                break

            # 特殊处理 /roll（不涉及引擎）
            if msg.type == MessageType.SYSTEM_CMD and msg.text.strip().lower() == "/roll":
                await self._manual_roll()
                continue

            # 特殊处理 /ingest（不是游戏回合）
            if msg.type == MessageType.SYSTEM_CMD and msg.text.strip().lower().startswith("/ingest"):
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

    async def _manual_roll(self):
        """手动掷 D100 测试"""
        from src.tools.dice import roll_d100
        from src.domain.coc_rules import determine_success_level

        tens, ones, value = roll_d100()
        print()
        print(_color("─ 掷骰测试 ───────────────────────", _BOLD))
        print(f"  掷骰结果:     {_color(str(value), _YELLOW)} ({tens * 10} + {ones})")
        for skill in [10, 30, 50, 70, 90]:
            level = determine_success_level(skill, value)
            marker = " ◀" if level.value in ("CRITICAL", "FUMBLE") else ""
            colored = _color(f"{level.value:>10}", _GREEN)
            print(f"  技能 {skill:>3}: {colored}{marker}")
        print(_color("──────────────────────────────────", _DIM))
        print()


# ── 独立入口 ──

async def main_async():
    adapter = CliAdapter()
    await adapter.run()


def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\n👋 再见！")


if __name__ == "__main__":
    main()
