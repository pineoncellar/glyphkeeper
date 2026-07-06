# -*- coding: utf-8 -*-
"""
@File     :   ingestion.py
@Desc     :   模组数据摄入 — 双脑分流管线
@Note     :   左脑: locations/entities/interactables/clues → 直接写 PG 读模型表（跳过 EventStore）
              右脑: global_knowledge + NPC 深度人设 → 合并降噪 → LightRAG (gleaning 关闭)
              模组元数据写入 module_meta 表，不再使用 TEMPLATE_SESSION_ID。
使用方式:
    uv run python -m src.tools.ingestion --name book
    uv run python -m src.tools.ingestion --list
"""

from __future__ import annotations

import asyncio
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.tools import get_logger, get_settings, PROJECT_ROOT

logger = get_logger(__name__)


# ====================================================================
# ModuleIngestor — 跳过 EventStore，直接写读模型表
# ====================================================================


class ModuleIngestor:
    """模组数据摄入器 — 双脑分流

    左脑管线直接写入 StaticReadStore 读模型表（skipping EventStore 中间层）。
    右脑管线合并叙事文本后集中写入 LightRAG 种子工作区。
    """

    def __init__(self, vector_store=None):
        self._vector_store = vector_store

    # ── 属性（延迟导入） ──

    @property
    async def vector_store(self):
        if self._vector_store is None:
            from src.memory.vector_store import VectorStore
            self._vector_store = await VectorStore.get_instance(knowledge_space="world")
        return self._vector_store

    # ── 主入口 ──

    async def ingest(self, json_data: dict) -> bool:
        """全流程摄入 — 先左脑（结构化）再右脑（叙事）

        左脑: 知识注册表 + 场景/物品/NPC/线索 → 直接写 PG 读模型表
              写完就返回，不需要等右脑。
        右脑: 将 global_knowledge 和大段风味文本合并为大文档 → LightRAG
              关闭 gleaning 减少 LLM 调用。
        """
        meta = json_data.get("meta", {})
        module_name = meta.get("module_name", "Unknown Module")
        logger.info(f"═" * 50)
        logger.info(f"开始摄入模组: {module_name}")
        logger.info(f"  描述: {meta.get('description', '')}")

        from src.memory.vector_store import VectorStore
        seed_ws = VectorStore.seed_workspace_name(module_name)
        import uuid as _uuid

        # ── 左脑管线：结构化数据 → 直接写 PG 读模型表 ──
        left_ok = True
        knowledge_registry = self._build_knowledge_registry(json_data)
        locations, raw_locations = [], []

        for loc_data in json_data.get("locations", []):
            loc_key = loc_data.get("key", "unknown")
            loc_id = str(_uuid.uuid4())

            raw_interactables = []
            for item in loc_data.get("interactables", []):
                item_copy = dict(item)
                item_copy["id"] = str(_uuid.uuid4())
                raw_interactables.append(item_copy)

            raw_locations.append({
                "id": loc_id, "key": loc_key,
                "name": loc_data.get("name", ""),
                "base_desc": loc_data.get("base_desc", ""),
                "tags": loc_data.get("tags", []),
                "exits": loc_data.get("exits", {}),
                "entities": loc_data.get("entities", []),
                "interactables": raw_interactables,
            })

            locations.append({
                "id": loc_id, "key": loc_key,
                "name": loc_data.get("name", ""),
                "base_desc": loc_data.get("base_desc", ""),
                "tags": loc_data.get("tags", []),
                "exits": loc_data.get("exits", {}),
                "entities": [e.get("key", e.get("name", ""))
                             for e in loc_data.get("entities", [])],
                "interactables": [i.get("key", i.get("name", ""))
                                  for i in loc_data.get("interactables", [])],
            })

        # 直接调用 StaticReadStore 写入（跳过 EventStore + Projector 中间层）
        from src.state.read_models import StaticReadStore
        store = StaticReadStore()
        conn = await store._get_conn()

        try:
            # 写知识注册表
            if knowledge_registry:
                await store.bulk_insert_knowledge(knowledge_registry, world_id=seed_ws)

            # 写场景和物品 NPC
            loc_id_map = await store.bulk_insert_locations(locations, world_id=seed_ws)

            all_interactables, all_entities, all_clues = [], [], []
            for loc_data in raw_locations:
                loc_key = loc_data.get("key", "")
                location_id = loc_id_map.get(loc_key)

                for entity_data in loc_data.get("entities", []):
                    all_entities.append({
                        "id": entity_data.get("id", str(_uuid.uuid4())),
                        "key": entity_data.get("key", ""),
                        "name": entity_data.get("name", ""),
                        "location_id": location_id,
                        "tags": entity_data.get("tags", []),
                        "stats": entity_data.get("stats", {}),
                    })

                for item_data in loc_data.get("interactables", []):
                    item_id = item_data.get("id", "")
                    all_interactables.append({
                        "id": item_id,
                        "key": item_data.get("key", ""),
                        "name": item_data.get("name", ""),
                        "location_id": location_id,
                        "tags": item_data.get("tags", []),
                        "state": item_data.get("state", ""),
                    })

                    for clue in item_data.get("clues", []):
                        target_knowledge = clue.get("target_knowledge")
                        knowledge_id = None
                        if target_knowledge:
                            for kr in knowledge_registry:
                                if kr["knowledge_id"] == target_knowledge:
                                    knowledge_id = kr["id"]
                                    break
                        all_clues.append({
                            "interactable_id": item_id,
                            "entity_key": None,
                            "knowledge_id": knowledge_id,
                            "required_check": clue.get("required_check", {}),
                            "flavor_text": clue.get("flavor_text", ""),
                            "loot_items": clue.get("loot_items", []),
                            "required_item": clue.get("required_item", ""),
                            "deterministic_changes": clue.get("deterministic_changes", {}),
                        })

                for entity_data in loc_data.get("entities", []):
                    entity_key = entity_data.get("key", "")
                    for clue in entity_data.get("dialogue_clues", []):
                        target_knowledge = clue.get("target_knowledge")
                        knowledge_id = None
                        if target_knowledge:
                            for kr in knowledge_registry:
                                if kr["knowledge_id"] == target_knowledge:
                                    knowledge_id = kr["id"]
                                    break
                        all_clues.append({
                            "interactable_id": None,
                            "entity_key": entity_key,
                            "knowledge_id": knowledge_id,
                            "required_check": clue.get("required_check", {}),
                            "flavor_text": clue.get("flavor_text", ""),
                            "loot_items": clue.get("loot_items", []),
                        })

            if all_interactables:
                await store.bulk_insert_interactables(all_interactables, world_id=seed_ws)
            if all_entities:
                await store.bulk_insert_entities(all_entities, world_id=seed_ws)
            if all_clues:
                await store.bulk_insert_clues(all_clues, world_id=seed_ws)

            # 写触发器
            raw_triggers = json_data.get("static_triggers", [])
            normalized_triggers = self._normalize_triggers(raw_triggers, module_name)
            if normalized_triggers:
                await store.bulk_insert_triggers(normalized_triggers, world_id=seed_ws)

            # 写模组元数据（含开场配置）
            opening_data = json_data.get("opening", {})
            await store.insert_module_meta(
                module_name=module_name,
                world_id=seed_ws,
                description=meta.get("description", ""),
                opening=opening_data,
            )

            logger.info(f"  [OK] 左脑管线完成: {len(knowledge_registry)} 条知识, "
                        f"{len(json_data.get('locations', []))} 个场景")
            left_ok = True
        except Exception as e:
            logger.error(f"  [FAIL] 左脑管线写入失败: {e}")
            left_ok = False
        finally:
            await store.close()

        # ── 右脑管线：叙事文本 → 合并降噪 → LightRAG ──
        docs = self._build_right_brain_docs(json_data, module_name)
        right_ok = False

        if docs:
            try:
                seed_vs = await VectorStore.get_instance(
                    knowledge_space="world", world_id=seed_ws,
                )
                await seed_vs.insert(docs, source_type="narrative_seed")
                logger.info(f"  [OK] 种子工作区 '{seed_ws}' 已写入 ({len(docs)} 篇)")
                right_ok = True
            except Exception as e:
                logger.error(f"  [FAIL] 种子工作区写入失败: {e}")
        else:
            logger.info(f"  [SKIP] 无非叙事内容需写入 LightRAG")
            right_ok = True

        if left_ok and right_ok:
            logger.info(f"[OK] 模组 '{module_name}' 摄入完成")
        else:
            part = "左脑" if not left_ok else "右脑"
            logger.warning(f"[WARN] 模组 '{module_name}' {part}管线失败")

        logger.info(f"═" * 50)
        return left_ok and right_ok

    # ── 左脑管线：世界初始化事件 + 投影 ──

    @staticmethod
    def _build_knowledge_registry(json_data: dict) -> list[dict]:
        """从模组数据构建知识注册表"""
        import uuid

        registry_map: dict[str, dict] = {}

        for k in json_data.get("global_knowledge", []):
            kid = k.get("key", "")
            if kid:
                registry_map[kid] = {
                    "id": str(uuid.uuid4()),
                    "knowledge_id": kid,
                    "rag_key": kid,
                    "description": k.get("rag_content", "")[:200],
                    "tags_granted": k.get("tags_granted", []),
                }

        for loc_data in json_data.get("locations", []):
            for item in loc_data.get("interactables", []):
                for clue in item.get("clues", []):
                    target = clue.get("target_knowledge")
                    if target and target not in registry_map:
                        registry_map[target] = {
                            "id": str(uuid.uuid4()),
                            "knowledge_id": target,
                            "rag_key": target,
                            "description": clue.get("flavor_text", "")[:200],
                            "tags_granted": [],
                        }
            for entity in loc_data.get("entities", []):
                for clue in entity.get("dialogue_clues", []):
                    target = clue.get("target_knowledge")
                    if target and target not in registry_map:
                        registry_map[target] = {
                            "id": str(uuid.uuid4()),
                            "knowledge_id": target,
                            "rag_key": target,
                            "description": clue.get("flavor_text", "")[:200],
                            "tags_granted": [],
                        }

        return list(registry_map.values())

    # _record_world_initialized 已合并到 ingest() 中直接写读模型表
    # 不再经过 EventStore + StateProjector 中间层

    # ── 右脑管线：叙事文本合并 → LightRAG ──

    @staticmethod
    def _build_right_brain_docs(json_data: dict, module_name: str) -> list[str]:
        """将模组叙事文本合并为大文档列表（纯 Python，无 IO）

        结构化元数据（exits/tags/keys/stats）不写入 RAG，
        只写入需要语义理解的叙事内容。
        """
        docs: list[str] = []

        # 合并 global_knowledge
        gk = json_data.get("global_knowledge", [])
        if gk:
            lore_parts = [
                f"[知识: {k.get('key', '?')}]\n{k.get('rag_content', '')}"
                for k in gk
            ]
            docs.append(
                f"# {module_name} — 世界观知识\n\n"
                + "\n\n---\n\n".join(lore_parts)
            )

        # 合并 NPC 深度人设
        npc_parts = []
        for loc_data in json_data.get("locations", []):
            for entity_data in loc_data.get("entities", []):
                dialogues = entity_data.get("dialogue_clues", [])
                if not dialogues:
                    continue
                lines = []
                for d in dialogues:
                    ft = d.get("flavor_text", "")
                    trigger = d.get("trigger", "talk")
                    if ft:
                        lines.append(f"  [{trigger}]: {ft}")
                if lines:
                    npc_parts.append(
                        f"[NPC: {entity_data.get('name', '?')}]\n"
                        + f"位置: {loc_data.get('key', '?')}\n"
                        + f"标签: {', '.join(entity_data.get('tags', []))}\n"
                        + "对话示例:\n" + "\n".join(lines)
                    )
        if npc_parts:
            docs.append(
                f"# {module_name} — NPC 深度人设\n\n"
                + "\n\n---\n\n".join(npc_parts)
            )

        # 合并场景氛围描述
        scene_parts = []
        for loc_data in json_data.get("locations", []):
            desc = loc_data.get("base_desc", "")
            if desc:
                scene_parts.append(
                    f"[场景: {loc_data.get('name', '?')}]\n{desc}"
                )
        if scene_parts:
            docs.append(
                f"# {module_name} — 场景氛围\n\n"
                + "\n\n---\n\n".join(scene_parts)
            )

        # 实体名称索引
        entity_index_parts = []
        for loc_data in json_data.get("locations", []):
            loc_name = loc_data.get("name", "?")
            loc_key = loc_data.get("key", "?")

            for entity_data in loc_data.get("entities", []):
                ent_name = entity_data.get("name", "")
                ent_key = entity_data.get("key", "")
                if ent_name and ent_key:
                    entity_index_parts.append(
                        f"[实体: {ent_name}]\n"
                        + f"  系统ID: {ent_key}\n"
                        + f"  类型: npc\n"
                        + f"  场景: {loc_name} ({loc_key})\n"
                        + f"  标签: {', '.join(entity_data.get('tags', []))}"
                    )

            for item_data in loc_data.get("interactables", []):
                item_name = item_data.get("name", "")
                item_key = item_data.get("key", "")
                if item_name and item_key:
                    entity_index_parts.append(
                        f"[物品: {item_name}]\n"
                        + f"  系统ID: {item_key}\n"
                        + f"  类型: interactable\n"
                        + f"  场景: {loc_name} ({loc_key})\n"
                        + f"  标签: {', '.join(item_data.get('tags', []))}"
                    )

            loc_desc = loc_data.get("base_desc", "")
            entity_index_parts.append(
                f"[场景: {loc_name}]\n"
                + f"  系统ID: {loc_key}\n"
                + f"  类型: location\n"
                + f"  描述: {loc_desc[:100]}"
            )

        if entity_index_parts:
            docs.append(
                f"# {module_name} — 实体名称索引\n\n"
                + "\n\n---\n\n".join(entity_index_parts)
            )

        return docs

    @staticmethod
    def _normalize_triggers(raw_triggers: list[dict], module_name: str) -> list[dict]:
        """将 JSON 中的触发器格式规范化为内部 DSL 格式

        支持两种输入格式：
          1. 标准化格式 — conditions_json / actions_json 已是内部 DSL 结构
          2. 兼容格式（mttest.json 风格）— 含 logic/expressions 和 SILENT_ALTER 类型

        转换逻辑：
          {logic:"AND", expressions:[...]} → {"AND": [...]}
          CURRENT_LOCATION(location_id)   → AT_LOCATION(params.key)
          STATE_MATCH(key, value)          → GLOBAL_FLAG(params.key) 或 SANITY_BELOW
          SILENT_ALTER(state_patch, echo)  → APPEND_ECHO + 平铺 state_patch 字段
          TRIGGER_ENDING(payload)          → TRIGGER_ENDING(params.ending_id)
        """
        normalized = []
        for t in raw_triggers:
            tid = t.get("trigger_id", "")
            if not tid:
                continue

            conditions = t.get("conditions_json", {})
            actions = t.get("actions_json", [])

            # ── 条件格式转换：logic/expressions → 内部 AND/OR/NOT ──
            if "logic" in conditions and "expressions" in conditions:
                logic = conditions["logic"]
                exprs = conditions["expressions"]
                converted = []
                for expr in exprs:
                    etype = expr.get("type", "")
                    if etype == "CURRENT_LOCATION":
                        converted.append({
                            "type": "AT_LOCATION",
                            "params": {"key": expr.get("location_id", "")},
                        })
                    elif etype == "STATE_MATCH":
                        key = expr.get("key", "")
                        val = expr.get("value")
                        if key in ("sanity", "san"):
                            converted.append({
                                "type": "SANITY_BELOW",
                                "params": {"threshold": val, "mode": "absolute"},
                            })
                        elif val is False:
                            # value=false → NOT GLOBAL_FLAG
                            converted.append({
                                "NOT": {"type": "GLOBAL_FLAG", "params": {"key": key}},
                            })
                        else:
                            converted.append({
                                "type": "GLOBAL_FLAG",
                                "params": {"key": key},
                            })
                    elif etype in ("HAS_ITEM", "AT_LOCATION", "TAG_ACTIVE",
                                   "KNOWLEDGE_STATE", "SANITY_BELOW", "GLOBAL_FLAG"):
                        converted.append({"type": etype, "params": expr.get("params", expr)})
                    else:
                        # 未知类型原样保留
                        converted.append(expr)

                conditions = {logic: converted}

            # ── 动作格式转换 ──
            converted_actions = []
            for act in actions:
                atype = act.get("type", "")

                if atype == "SILENT_ALTER":
                    state_patch = act.get("state_patch", {})
                    echo_text = act.get("echo_text")

                    # echo_text 转 APPEND_ECHO
                    if echo_text:
                        converted_actions.append({
                            "type": "APPEND_ECHO",
                            "params": {"text": echo_text},
                        })
                    # state_patch 中的特殊 key 转为标准 action
                    for sk, sv in state_patch.items():
                        if sk.startswith("_inventory_append"):
                            converted_actions.append({
                                "type": "SPAWN_ITEM",
                                "params": {"item_key": sv},
                            })
                        elif sk.startswith("_inventory_remove"):
                            converted_actions.append({
                                "type": "REMOVE_ITEM",
                                "params": {"key": sv},
                            })
                        elif sk == "front_door_locked":
                            converted_actions.append({
                                "type": "SET_GLOBAL_FLAG",
                                "params": {"key": sk},
                            })
                        elif sk == "door_mechanism_jammed":
                            converted_actions.append({
                                "type": "SET_GLOBAL_FLAG",
                                "params": {"key": sk},
                            })
                        elif sk == "statue_sealed":
                            # boolean 值转换为 GLOBAL_FLAG
                            converted_actions.append({
                                "type": "SET_GLOBAL_FLAG",
                                "params": {"key": sk},
                            })
                        elif sk.startswith("_"):
                            # 内部字段直接透传
                            converted_actions.append({
                                "type": "APPEND_ECHO",
                                "params": {"text": f"[系统: {sk} = {sv}]"},
                            })
                        else:
                            # 其余未知 key 作为 GLOBAL_FLAG
                            converted_actions.append({
                                "type": "SET_GLOBAL_FLAG",
                                "params": {"key": sk},
                            })

                elif atype == "TRIGGER_ENDING":
                    payload = act.get("payload", {})
                    converted_actions.append({
                        "type": "TRIGGER_ENDING",
                        "params": {"ending_id": payload.get("ending_id", tid)},
                    })

                elif atype in ("APPEND_ECHO", "MODIFY_LOCATION_DESC", "SPAWN_ITEM",
                               "GRANT_TAG", "SET_GLOBAL_FLAG", "REMOVE_ITEM",
                               "GRANT_KNOWLEDGE"):
                    # 已是标准格式
                    converted_actions.append(act)

                else:
                    # 未知动作类型原样保留
                    converted_actions.append(act)

            normalized.append({
                "trigger_id": tid,
                "module_name": module_name,
                "description": t.get("description", ""),
                "priority": t.get("priority", 0),
                "is_one_off": t.get("is_one_off", True),
                "conditions_json": conditions,
                "actions_json": converted_actions,
            })

        return normalized

    async def _ingest_right_brain(self, json_data: dict, module_name: str) -> bool:
        """构建文档并写入当前 VectorStore"""
        vs = await self.vector_store
        docs = self._build_right_brain_docs(json_data, module_name)

        if not docs:
            logger.info(f"  [SKIP] 无非叙事内容需写入 LightRAG")
            return True

        total_chars = sum(len(d) for d in docs)
        logger.info(f"  右脑: {len(docs)} 篇合并文档, 共 {total_chars} 字符")

        try:
            await vs.insert(docs, source_type="narrative")
            logger.info(f"  [OK] 右脑 {len(docs)} 篇文档已批量写入")
            return True
        except Exception as e:
            logger.error(f"  [FAIL] 右脑批量写入失败: {e}")
            return False


# ====================================================================
# CLI 工具函数
# ====================================================================


def find_module_files() -> list[Path]:
    """扫描所有可用的 intermediate JSON 文件"""
    search_paths = [
        PROJECT_ROOT / "data" / "intermediate"
    ]
    files: list[Path] = []
    for sp in search_paths:
        if sp.exists():
            files.extend(sorted(sp.glob("*.json")))
    return files


def list_available_modules():
    """打印所有可用模组"""
    files = find_module_files()
    print(f"\n{'=' * 50}")
    print(f"[MODULE] 可用模组文件 ({len(files)} 个)")
    print(f"{'=' * 50}")
    for f in files:
        # 尝试读取 meta 信息
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            meta = data.get("meta", {})
            name = meta.get("module_name", f.stem)
            desc = meta.get("description", "")
            print(f"  [FILE] {name}")
            print(f"     路径: {f.relative_to(PROJECT_ROOT)}")
            if desc:
                print(f"     描述: {desc[:80]}")
            print()
        except Exception:
            print(f"  [FILE] {f.name} (无法解析)")


def load_json(file_path: Path) -> Optional[dict]:
    """读取 JSON 文件"""
    if not file_path.exists():
        logger.error(f"文件不存在: {file_path}")
        return None
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        logger.error(f"JSON 解析失败: {e}")
        return None
    except Exception as e:
        logger.error(f"读取文件失败: {e}")
        return None


async def ingest_by_name(name: str) -> bool:
    """按模组名称摄入（从 data/intermediate/{name}.json 读取）"""
    path = PROJECT_ROOT / "data" / "intermediate" / f"{name}.json"
    if path.exists():
        return await ingest_by_path(path)

    logger.error(f"未找到模组 '{name}'。可使用 --list 查看所有可用模组。")
    return False


async def ingest_by_path(file_path: Path) -> bool:
    """按文件路径摄入"""
    data = load_json(file_path)
    if data is None:
        return False

    logger.info(f"读取模组文件: {file_path}")

    ingestor = ModuleIngestor()
    return await ingestor.ingest(data)


# ====================================================================
# 独立 CLI 入口
# ====================================================================


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """解析 CLI 参数"""
    parser = argparse.ArgumentParser(
        description="GlyphKeeper 模组数据摄入工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "示例:\n"
            "  uv run python -m src.tools.ingestion --name book\n"
            "  uv run python -m src.tools.ingestion --list\n"
        ),
    )

    # 互斥参数组
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--name", "-n",
        type=str,
        help="模组名称（从 data/intermediate/{name}.json 读取）",
    )
    group.add_argument(
        "--file", "-f",
        type=str,
        help="JSON 文件路径（直接指定）",
    )
    group.add_argument(
        "--list", "-l",
        action="store_true",
        help="列出所有可用模组",
    )

    return parser.parse_args(argv)


async def main_async():
    """异步主入口"""
    args = parse_args()

    if args.list:
        list_available_modules()
        return

    if args.file:
        file_path = Path(args.file)
        if not file_path.is_absolute():
            file_path = PROJECT_ROOT / file_path
        success = await ingest_by_path(file_path)
    elif args.name:
        success = await ingest_by_name(args.name)
    else:
        print("请指定模组名称 (--name) 或文件路径 (--file)。使用 --list 查看可用模组。")
        return

    if success:
        print("\n[OK] 摄入成功！")
    else:
        print("\n[FAIL] 摄入失败，请查看日志。")
        import sys
        sys.exit(1)


def main():
    """同步入口（供 CLI 调用）"""
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\n操作已取消。")


if __name__ == "__main__":
    main()
