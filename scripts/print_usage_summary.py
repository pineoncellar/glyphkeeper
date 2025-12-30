"""Summarize LLM usage log (JSONL).

Reads the per-call records in logs/llm_usage.jsonl and prints:
- overall totals
- totals grouped by model

Usage:
  python scripts/print_usage_summary.py
  python scripts/print_usage_summary.py --path logs/llm_usage.jsonl
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import sys

# 添加项目根目录到 python path
sys.path.append(str(Path(__file__).parent.parent))
from src.core.config import get_settings


@dataclass
class Agg:
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_cny: float = 0.0


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _print_block(title: str, agg: Agg) -> None:
    print(f"\n{title}")
    print("-" * len(title))
    print(f"calls            : {agg.calls}")
    print(f"prompt_tokens    : {agg.prompt_tokens:,}")
    print(f"completion_tokens: {agg.completion_tokens:,}")
    print(f"total_tokens     : {agg.total_tokens:,}")
    print(f"cost_cny         : {agg.cost_cny:.6f}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--path",
        default="",
        help="Path to usage jsonl (default: from config project.model_usage_log_path)",
    )
    args = parser.parse_args()

    settings = get_settings()
    if args.path:
        log_path = Path(args.path)
        if not log_path.is_absolute():
            log_path = settings.get_absolute_path(args.path)
    else:
        log_path = settings.get_absolute_path(settings.project.model_usage_log_path)

    if not log_path.exists():
        raise SystemExit(f"usage log not found: {log_path}")

    overall = Agg()
    by_model: dict[str, Agg] = defaultdict(Agg)

    bad_lines = 0
    total_lines = 0

    with open(log_path, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            total_lines += 1
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                bad_lines += 1
                continue

            model = str(rec.get("model") or "unknown")
            pt = _to_int(rec.get("prompt_tokens"))
            ct = _to_int(rec.get("completion_tokens"))
            tt = _to_int(rec.get("total_tokens"))
            cost = _to_float(rec.get("cost_cny"))

            overall.calls += 1
            overall.prompt_tokens += pt
            overall.completion_tokens += ct
            overall.total_tokens += tt
            overall.cost_cny += cost

            agg = by_model[model]
            agg.calls += 1
            agg.prompt_tokens += pt
            agg.completion_tokens += ct
            agg.total_tokens += tt
            agg.cost_cny += cost

    print(f"usage log: {log_path}")
    if bad_lines:
        print(f"warning: {bad_lines} bad line(s) skipped (out of {total_lines})")

    _print_block("OVERALL", overall)

    print("\nBY MODEL")
    print("--------")
    for model in sorted(by_model.keys()):
        agg = by_model[model]
        print(
            f"{model}: calls={agg.calls}, "
            f"tokens={agg.total_tokens:,} (in={agg.prompt_tokens:,}, out={agg.completion_tokens:,}), "
            f"cost_cny={agg.cost_cny:.6f}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
