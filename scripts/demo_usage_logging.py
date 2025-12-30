"""Demo: verify LLM token/quota usage logging.

Runs one streaming chat call via the existing LLMFactory, then prints the last
record appended to the JSONL usage log.

Usage:
  python scripts/demo_usage_logging.py --tier fast

Notes:
- Requires providers.ini + config.yaml to be configured for the chosen tier.
- Will write to the configured log path (default: logs/llm_usage.jsonl).
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

# 添加项目根目录到 python path
sys.path.append(str(Path(__file__).parent.parent))
from src.core.config import get_settings
from src.llm.llm_factory import LLMFactory


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tier", default="fast", help="LLM tier name in config.yaml (default: fast)")
    parser.add_argument(
        "--prompt",
        default="用一句话介绍一下GlyphKeeper。",
        help="User prompt to send",
    )
    args = parser.parse_args()

    settings = get_settings()
    log_path = settings.get_absolute_path(settings.project.model_usage_log_path)

    llm = LLMFactory.get_llm(args.tier)

    messages = [
        {"role": "system", "content": "你是一个简洁的助手。"},
        {"role": "user", "content": args.prompt},
    ]

    print(f"[demo] calling tier={args.tier}, model={llm.model_name}")

    out_parts: list[str] = []
    async for chunk in llm.chat(messages):
        out_parts.append(chunk)

    text = "".join(out_parts).strip()
    print("\n[demo] model output:\n")
    print(text)

    if not log_path.exists():
        raise RuntimeError(f"usage log file not found: {log_path}")

    # Read last non-empty line
    last_line = ""
    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                last_line = line

    if not last_line:
        raise RuntimeError(f"usage log file is empty: {log_path}")

    record = json.loads(last_line)
    print("\n[demo] last usage record (from log):\n")
    print(json.dumps(record, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
