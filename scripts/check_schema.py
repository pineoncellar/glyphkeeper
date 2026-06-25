#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@File     :   check_schema.py
@Desc     :   模组 intermediate JSON 格式校验器
@Note     :   校验 data/intermediate/*.json 是否符合 ingestion 期望的 schema

使用方式:
    uv run python scripts/check_schema.py                # 校验所有模组
    uv run python scripts/check_schema.py --name book    # 校验指定模组
"""

import json
import sys
from pathlib import Path

# ── 项目根 ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent


# ====================================================================
# Schema 定义
# ====================================================================

# 字段规则: (字段名, 是否必需, 期望类型)
FIELD_RULES = {
    "meta": {
        "module_name": (True, str),
        "description": (True, str),
        "original_source": (False, str),
        "author": (False, str),
        "version": (False, str),
    },
    "opening": {
        "start_location_key": (True, str),
        "intro_text_template": (True, str),
        "required_tags": (False, list),
        "start_time_slot": (False, str),
    },
    "global_knowledge_item": {
        "key": (True, str),
        "rag_content": (True, str),
        "tags_granted": (False, list),
    },
    "entity": {
        "key": (True, str),
        "name": (True, str),
        "stats": (False, dict),
        "attacks": (False, list),
        "tags": (False, list),
        "dialogue_clues": (False, list),
    },
    "interactable": {
        "key": (True, str),
        "name": (True, str),
        "state": (False, str),
        "tags": (False, list),
        "clues": (False, list),
    },
    "clue": {
        "trigger": (True, str),
        "flavor_text": (True, str),
        "required_check": (False, (dict, type(None))),
        "target_knowledge": (False, (str, type(None))),
    },
    "location": {
        "key": (True, str),
        "name": (True, str),
        "base_desc": (True, str),
        "tags": (False, list),
        "exits": (False, dict),
        "entities": (False, list),
        "interactables": (False, list),
    },
}

VALID_DIFFICULTIES = {"REGULAR", "HARD", "EXTREME"}


# ====================================================================
# 校验逻辑
# ====================================================================


def _check_fields(data: dict, rules: dict, path: str) -> list[str]:
    """校验 dict 中的字段是否符合规则"""
    errors = []
    for field, (required, expected_type) in rules.items():
        full_path = f"{path}.{field}"
        if field not in data:
            if required:
                errors.append(f"  [MISS] {full_path}: 必需字段缺失")
            continue
        value = data[field]
        if expected_type is not None and not isinstance(value, expected_type):
            errors.append(
                f"  [TYPE] {full_path}: 期望 {expected_type.__name__}, "
                f"实际 {type(value).__name__}"
            )
    return errors


def validate_module(data: dict, name: str = "") -> list[str]:
    """校验完整模组数据，返回错误列表"""
    all_errors = []

    # ── 顶层结构 ──
    for top_key in ("meta", "opening", "global_knowledge", "locations"):
        if top_key not in data:
            all_errors.append(f"  [MISS] {top_key}: 顶层必需字段缺失")

    if not all_errors and data.get("global_knowledge") is not None:
        if not isinstance(data["global_knowledge"], list):
            all_errors.append("  [TYPE] global_knowledge: 期望 list")

    # ── meta ──
    meta = data.get("meta", {})
    all_errors.extend(_check_fields(meta, FIELD_RULES["meta"], "meta"))
    module_name = meta.get("module_name", name)
    if name and module_name != name:
        all_errors.append(f"  [VALUE] meta.module_name: 期望 '{name}', 实际 '{module_name}'")

    # ── opening ──
    opening = data.get("opening", {})
    all_errors.extend(_check_fields(opening, FIELD_RULES["opening"], "opening"))

    # ── global_knowledge ──
    for i, item in enumerate(data.get("global_knowledge", [])):
        all_errors.extend(
            _check_fields(item, FIELD_RULES["global_knowledge_item"], f"global_knowledge[{i}]")
        )

    # ── locations（两遍扫描: 先收集所有 key，再逐场景校验）──
    loc_keys = set()
    for loc in data.get("locations", []):
        k = loc.get("key", "")
        if k:
            if k in loc_keys:
                all_errors.append(f"  [DUP] locations: 重复的场景 key '{k}'")
            loc_keys.add(k)

    for j, loc in enumerate(data.get("locations", [])):
        prefix = f"locations[{j}]"
        all_errors.extend(_check_fields(loc, FIELD_RULES["location"], prefix))

        # 检查 exits 引用（允许引用后面的场景）
        exits = loc.get("exits", {})
        for exit_name, target_key in exits.items():
            if target_key and target_key not in loc_keys:
                all_errors.append(
                    f"  [REF] {prefix}.exits.{exit_name}: "
                    f"引用的场景 '{target_key}' 不存在"
                )

        # 校验 entities
        for k, ent in enumerate(loc.get("entities", [])):
            ent_prefix = f"{prefix}.entities[{k}]"
            all_errors.extend(_check_fields(ent, FIELD_RULES["entity"], ent_prefix))
            for m, clue in enumerate(ent.get("dialogue_clues", [])):
                clue_prefix = f"{ent_prefix}.dialogue_clues[{m}]"
                all_errors.extend(_check_fields(clue, FIELD_RULES["clue"], clue_prefix))
                _check_required_check(clue, clue_prefix, all_errors)

        # 校验 interactables
        for k, item in enumerate(loc.get("interactables", [])):
            item_prefix = f"{prefix}.interactables[{k}]"
            all_errors.extend(_check_fields(item, FIELD_RULES["interactable"], item_prefix))
            for m, clue in enumerate(item.get("clues", [])):
                clue_prefix = f"{item_prefix}.clues[{m}]"
                all_errors.extend(_check_fields(clue, FIELD_RULES["clue"], clue_prefix))
                _check_required_check(clue, clue_prefix, all_errors)

    # 检查 opening.start_location_key 的引用
    start_loc = opening.get("start_location_key", "")
    if start_loc and start_loc not in loc_keys:
        all_errors.append(
            f"  [REF] opening.start_location_key: "
            f"引用的场景 '{start_loc}' 在 locations 中不存在"
        )

    return all_errors


def _check_required_check(clue: dict, prefix: str, errors: list[str]):
    """校验 required_check 内部字段"""
    rc = clue.get("required_check")
    if rc and isinstance(rc, dict):
        skill = rc.get("skill")
        difficulty = rc.get("difficulty")
        if not skill:
            errors.append(f"  [MISS] {prefix}.required_check.skill: 必需字段缺失")
        if not difficulty:
            errors.append(f"  [MISS] {prefix}.required_check.difficulty: 必需字段缺失")
        elif difficulty.upper() not in VALID_DIFFICULTIES:
            errors.append(
                f"  [VALUE] {prefix}.required_check.difficulty: "
                f"'{difficulty}' 不在有效值 {VALID_DIFFICULTIES} 中"
            )


# ====================================================================
# CLI
# ====================================================================


def main():
    import argparse

    parser = argparse.ArgumentParser(description="校验模组 intermediate JSON 格式")
    parser.add_argument("--name", "-n", type=str, help="指定模组名称（如 book）")
    args = parser.parse_args()

    search_dir = PROJECT_ROOT / "data" / "intermediate"

    if args.name:
        files = [search_dir / f"{args.name}.json"]
    else:
        files = sorted(search_dir.glob("*.json"))

    if not files:
        print(f"[FAIL] data/intermediate/ 目录下没有 JSON 文件")
        sys.exit(1)

    total_errors = 0

    for fpath in files:
        if not fpath.exists():
            print(f"\n{'=' * 50}")
            print(f"[SKIP] {fpath.name}: 文件不存在")
            continue

        print(f"\n{'=' * 50}")
        print(f"[FILE] {fpath.name}")

        try:
            with open(fpath, "r", encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            print(f"  [FAIL] JSON 解析错误: {e}")
            total_errors += 1
            continue
        except Exception as e:
            print(f"  [FAIL] 读取失败: {e}")
            total_errors += 1
            continue

        meta = data.get("meta", {})
        name = meta.get("module_name", fpath.stem)
        desc = meta.get("description", "")
        print(f"  模组: {name}")
        if desc:
            print(f"  描述: {desc[:80]}")

        errors = validate_module(data, name=fpath.stem)

        if errors:
            print(f"  结果: [FAIL] ({len(errors)} 个问题)")
            for err in errors:
                print(err)
            total_errors += len(errors)
        else:
            loc_count = len(data.get("locations", []))
            knowledge_count = len(data.get("global_knowledge", []))
            print(f"  结果: [OK] ({loc_count} 场景, {knowledge_count} 条知识)")

    print(f"\n{'=' * 50}")
    if total_errors == 0:
        print("[OK] 所有模组校验通过!")
    else:
        print(f"[FAIL] 共发现 {total_errors} 个问题")
        sys.exit(1)


if __name__ == "__main__":
    main()
