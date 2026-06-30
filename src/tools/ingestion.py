# -*- coding: utf-8 -*-
"""
@File     :   ingestion.py
@Desc     :   模组数据摄入 — 双脑分流管线
@Note     :   左脑: locations/entities/interactables/clues → EventStore + CQRS 投影 → PG
              右脑: global_knowledge + NPC 深度人设 → 合并降噪 → LightRAG (gleaning 关闭)
@TODO     :   实现断点续传、优化rag输入的拼接（？）逻辑
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

# EventStore / VectorStore 延迟导入避免循环依赖

logger = get_logger(__name__)


# ====================================================================
# 常量
# ====================================================================

TEMPLATE_SESSION_ID = "00000000-0000-0000-0000-000000000000"


# ====================================================================
# ModuleIngestor
# ====================================================================


class ModuleIngestor:
    """模组数据摄入器 — 双脑分流

    左脑管线写入 EventStore + CQRS 读模型表（结构化数据不掉 LLM）。
    右脑管线合并叙事文本后集中写入 LightRAG（关闭 gleaning 减少 API 调用）。
    """

    def __init__(self, vector_store=None, event_store=None):
        self._vector_store = vector_store
        self._event_store = event_store

    # ── 属性（延迟导入） ──

    @property
    async def vector_store(self):
        if self._vector_store is None:
            from src.memory.vector_store import VectorStore
            self._vector_store = await VectorStore.get_instance(domain="world")
        return self._vector_store

    @property
    async def event_store(self):
        if self._event_store is None:
            from src.memory.event_store import create_event_store
            self._event_store = await create_event_store()
        return self._event_store

    # ── 主入口 ──

    async def ingest(self, json_data: dict) -> bool:
        """全流程摄入 — 先左脑（结构化）再右脑（叙事）

        左脑: 知识注册表 + 场景/物品/NPC/线索 → EventStore + 投影到 PG
              写完就返回，不需要等右脑。
        右脑: 将 global_knowledge 和大段风味文本合并为大文档 → LightRAG
              关闭 gleaning 减少 LLM 调用。
        """
        meta = json_data.get("meta", {})
        module_name = meta.get("module_name", "Unknown Module")
        logger.info(f"═" * 50)
        logger.info(f"开始摄入模组: {module_name}")
        logger.info(f"  描述: {meta.get('description', '')}")

        # ── 左脑管线：结构化数据 → EventStore + PG 读模型 ──
        left_ok = True
        knowledge_registry = self._build_knowledge_registry(json_data)

        if "opening" in json_data:
            ok = await self._ingest_opening(module_name, json_data["opening"])
            left_ok = left_ok and ok

        ok = await self._record_world_initialized(
            module_name, json_data, knowledge_registry,
        )
        left_ok = left_ok and ok

        if left_ok:
            logger.info(f"  [OK] 左脑管线完成: {len(knowledge_registry)} 条知识, "
                        f"{len(json_data.get('locations', []))} 个场景")

        # ── 右脑管线：叙事文本 → 合并降噪 → LightRAG ──
        right_ok = await self._ingest_right_brain(json_data, module_name)

        if left_ok and right_ok:
            logger.info(f"[OK] 模组 '{module_name}' 摄入完成")
        else:
            part = "左脑" if not left_ok else "右脑"
            logger.warning(f"[WARN] 模组 '{module_name}' {part}管线失败")

        logger.info(f"═" * 50)
        return left_ok and right_ok

    # ── 左脑管线：开场 ──

    async def _ingest_opening(self, module_name: str, opening_data: dict) -> bool:
        """开场配置写入 EventStore"""
        es = await self.event_store
        try:
            await es.append(
                session_id=TEMPLATE_SESSION_ID,
                event_type="OpeningTemplateSet",
                data={
                    "module_name": module_name,
                    "opening": opening_data,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                source_node="ingestion",
            )
            logger.info(f"  [OK] 开场配置已写入 EventStore")
            return True
        except Exception as e:
            logger.error(f"  [FAIL] 开场配置写入失败: {e}")
            return False

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

    async def _record_world_initialized(
        self, module_name: str, json_data: dict, knowledge_registry: list[dict],
    ) -> bool:
        """写入 WorldInitialized 事件 → 投影到 PG 读模型表"""
        es = await self.event_store
        import uuid as _uuid

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

        event_data = {
            "module_name": module_name,
            "locations": locations,
            "raw_locations": raw_locations,
            "knowledge_registry": knowledge_registry,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            event = await es.append(
                session_id=TEMPLATE_SESSION_ID,
                event_type="WorldInitialized",
                data=event_data, source_node="ingestion",
            )
            logger.info(f"  [OK] WorldInitialized 已记录 ({len(locations)} 场景)")

            from src.state.projector import StateProjector
            projector = StateProjector()
            await projector.handle(event)
            logger.info(f"  [OK] 读模型投影完成")
            return True
        except Exception as e:
            logger.error(f"  [FAIL] WorldInitialized 记录/投影失败: {e}")
            return False

    # ── 右脑管线：叙事文本合并 → LightRAG ──

    async def _ingest_right_brain(self, json_data: dict, module_name: str) -> bool:
        """将散碎的叙事文本合并为大文档，集中写入 LightRAG

        设计原则:
          结构化元数据（exits/tags/keys/stats）不写入 RAG，
          只写入需要语义理解的叙事内容: global_knowledge 原文、
          NPC 深度对话文本、场景氛围描述。
        """
        vs = await self.vector_store
        docs: list[str] = []

        # 合并 global_knowledge — 每条知识的内容接在一起形成 lore 文档
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

        # 合并 NPC 深度人设 — 只取 dialogue_clues 中的风味文本
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

        # 合并场景氛围描述 — 只取 base_desc 不要 exits/tags 等结构化字段
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

        # ── 实体名称索引 — 供 disambiguation_node 消歧向量匹配使用 ──
        # 将 NPC/物品/场景的显示名称 + 系统 ID 嵌入 LightRAG，
        # 使玩家自然语言称呼（如"托马斯"）能与实体名称在向量空间中匹配。
        entity_index_parts = []
        for loc_data in json_data.get("locations", []):
            loc_name = loc_data.get("name", "?")
            loc_key = loc_data.get("key", "?")

            # NPC 实体名称
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

            # 物品名称
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

            # 场景名称
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
