# -*- coding: utf-8 -*-
"""
@File     :   ingestion.py
@Desc     :   模组数据摄入模块 — 将 intermediate JSON 摄入到 VectorStore + EventStore
@Note     :   使用方式:
              uv run python -m src.tools.ingestion --name book
              uv run python -m src.tools.ingestion --list        # 列出可用模组

流程:
    intermediate/*.json
        │
        ▼  ModuleIngestor.ingest()
        │
        ├──► VectorStore (LightRAG)   ← 语义检索
        │     global_knowledge / locations / entities
        │
        └──► EventStore (SQLite)      ← 事件溯源
              WorldInitialized 事件 (重建世界状态)
"""

from __future__ import annotations

import asyncio
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.tools import get_logger, get_settings, PROJECT_ROOT

# 注意: EventStore / VectorStore 使用延迟导入（lazy import）
# 避免循环依赖: tools → ingestion → memory/event_store → tools → ...

logger = get_logger(__name__)


# ====================================================================
# 常量
# ====================================================================

# 模组模板会话 ID（固定，用于存储场景开场配置）
TEMPLATE_SESSION_ID = "00000000-0000-0000-0000-000000000000"


# ====================================================================
# ModuleIngestor
# ====================================================================


class ModuleIngestor:
    """模组数据摄入器

    职责:
      - 读取 intermediate JSON 模组文件
      - 将 global_knowledge → LightRAG（语义检索用）
      - 将 locations/entities/interactables → LightRAG + EventStore
      - 将 opening 配置 → EventStore（模板会话事件）
      - 记录 WorldInitialized 事件（供 WorldManager 重建世界）
    """

    def __init__(
        self,
        vector_store=None,
        event_store=None,
    ):
        self._vector_store = vector_store
        self._event_store = event_store

    # ── 属性（懒加载 + 延迟导入） ──

    @property
    async def vector_store(self):
        """获取 VectorStore 实例（延迟导入避免循环依赖）"""
        if self._vector_store is None:
            from src.memory.vector_store import VectorStore
            self._vector_store = await VectorStore.get_instance(
                domain="world",
                llm_tier="standard",

            )
        return self._vector_store

    @property
    async def event_store(self):
        """获取 EventStore 实例（延迟导入避免循环依赖）"""
        if self._event_store is None:
            from src.memory.event_store import EventStore
            self._event_store = EventStore()
        return self._event_store

    # ── 主入口 ──

    async def ingest(self, json_data: dict) -> bool:
        """全流程摄入一个模组

        参数:
            json_data: 符合 intermediate JSON 格式的完整模组数据

        返回:
            是否全部成功
        """
        meta = json_data.get("meta", {})
        module_name = meta.get("module_name", "Unknown Module")
        logger.info(f"═" * 50)
        logger.info(f"开始摄入模组: {module_name}")
        logger.info(f"  描述: {meta.get('description', '')}")
        logger.info(f"  版本: {meta.get('version', '')}")

        success = True

        # 1. 摄入全局知识 → LightRAG
        if "global_knowledge" in json_data:
            ok = await self._ingest_knowledge(json_data["global_knowledge"])
            success = success and ok

        # 2. 摄入场景/实体/物品 → LightRAG + EventStore
        if "locations" in json_data:
            for loc_data in json_data["locations"]:
                ok = await self._ingest_location(loc_data, module_name)
                success = success and ok

        # 3. 摄入开场配置 → EventStore
        if "opening" in json_data:
            ok = await self._ingest_opening(module_name, json_data["opening"])
            success = success and ok

        # 4. 写入 WorldInitialized 事件（标记模组已加载）
        await self._record_world_initialized(module_name, json_data)

        if success:
            logger.info(f"[OK] 模组 '{module_name}' 摄入完成")
        else:
            logger.warning(f"[WARN] 模组 '{module_name}' 摄入部分失败，请查看日志")

        logger.info(f"═" * 50)
        return success

    # ── 知识摄入 ──

    async def _ingest_knowledge(self, knowledge_list: list[dict]) -> bool:
        """将 global_knowledge 列表摄入到 LightRAG

        每条知识以结构化文本形式插入，包含:
          - 知识 key（用于 ClueDiscovery 关联）
          - 具体内容
          - 授予的标签
        """
        vs = await self.vector_store
        all_ok = True

        for k in knowledge_list:
            rag_key = k.get("key", "unknown")
            rag_content = k.get("rag_content", "")
            tags = k.get("tags_granted", [])

            doc_text = (
                f"[Knowledge: {rag_key}]\n"
                f"Content: {rag_content}\n"
                f"Related Tags: {', '.join(tags)}"
            )

            try:
                await vs.insert(doc_text, source_type="knowledge")
                logger.info(f"  [OK] 知识已插入: {rag_key}")
            except Exception as e:
                logger.error(f"  [FAIL] 知识插入失败 ({rag_key}): {e}")
                all_ok = False

        return all_ok

    # ── 场景摄入 ──

    async def _ingest_location(
        self, loc_data: dict, module_name: str
    ) -> bool:
        """摄入单个场景及其子实体/物品

        1. 场景描述 → LightRAG（供语义检索）
        2. 子实体 NPC → LightRAG + EventStore
        3. 子物品 → LightRAG + EventStore
        4. 线索关联 → EventStore
        """
        vs = await self.vector_store
        es = await self.event_store
        loc_key = loc_data.get("key", "unknown")
        loc_name = loc_data.get("name", loc_key)
        all_ok = True

        # ── 场景 → LightRAG ──
        interactables_summary = self._summarize_interactables(
            loc_data.get("interactables", [])
        )
        rag_text = (
            f"[Location: {loc_name}]\n"
            f"Key: {loc_key}\n"
            f"Description: {loc_data.get('base_desc', '')}\n"
            f"Atmosphere Tags: {', '.join(loc_data.get('tags', []))}\n"
            f"Exits: {json.dumps(loc_data.get('exits', {}), ensure_ascii=False)}\n"
            f"Possible Interactions: {interactables_summary}"
        )
        try:
            await vs.insert(rag_text, source_type="location")
            logger.info(f"  [OK] 场景已插入 LightRAG: {loc_name}")
        except Exception as e:
            logger.error(f"  [FAIL] 场景 LightRAG 插入失败 ({loc_name}): {e}")
            all_ok = False

        # ── 场景 → EventStore（WorldInitialized 事件的 locations 部分） ──
        # 单个场景的初始化事件暂存，在 _record_world_initialized 中统一写入
        # 这里只处理 LightRAG 部分

        # ── 处理子实体 (NPC) ──
        for entity_data in loc_data.get("entities", []):
            ok = await self._ingest_entity(entity_data, loc_key, module_name)
            all_ok = all_ok and ok

        # ── 处理子物品 ──
        for item_data in loc_data.get("interactables", []):
            ok = await self._ingest_interactable(item_data, loc_key, module_name)
            all_ok = all_ok and ok

        return all_ok

    # ── 实体 NPC 摄入 ──

    async def _ingest_entity(
        self, entity_data: dict, loc_key: str, module_name: str
    ) -> bool:
        """摄入 NPC 实体到 LightRAG"""
        vs = await self.vector_store
        name = entity_data.get("name", "unknown")
        key = entity_data.get("key", name)

        # 构造人设描述
        dialogues = entity_data.get("dialogue_clues", [])
        dialogue_text = ""
        if dialogues:
            lines = []
            for d in dialogues:
                lines.append(f"  - [{d.get('trigger', 'talk')}]: {d.get('flavor_text', '')}")
            dialogue_text = "\nDialogue Examples:\n" + "\n".join(lines)

        rag_text = (
            f"[NPC: {name}]\n"
            f"Key: {key}\n"
            f"Location: {loc_key}\n"
            f"Tags: {', '.join(entity_data.get('tags', []))}\n"
            f"Stats: {json.dumps(entity_data.get('stats', {}), ensure_ascii=False)}{dialogue_text}"
        )

        try:
            await vs.insert(rag_text, source_type="entity")
            logger.info(f"  [OK] NPC 已插入 LightRAG: {name}")
            return True
        except Exception as e:
            logger.error(f"  [FAIL] NPC LightRAG 插入失败 ({name}): {e}")
            return False

    # ── 物品摄入 ──

    async def _ingest_interactable(
        self, item_data: dict, loc_key: str, module_name: str
    ) -> bool:
        """摄入交互物品到 LightRAG"""
        vs = await self.vector_store
        name = item_data.get("name", "unknown")
        key = item_data.get("key", name)

        rag_text = (
            f"[Interactable: {name}]\n"
            f"Key: {key}\n"
            f"Location: {loc_key}\n"
            f"State: {item_data.get('state', 'default')}\n"
            f"Tags: {', '.join(item_data.get('tags', []))}"
        )

        try:
            await vs.insert(rag_text, source_type="interactable")
            logger.info(f"  [OK] 物品已插入 LightRAG: {name}")
            return True
        except Exception as e:
            logger.error(f"  [FAIL] 物品 LightRAG 插入失败 ({name}): {e}")
            return False

    # ── 开场配置摄入 ──

    async def _ingest_opening(self, module_name: str, opening_data: dict) -> bool:
        """将开场配置写入 EventStore（模板会话）"""
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

    # ── 世界初始化事件 ──

    async def _record_world_initialized(self, module_name: str, json_data: dict):
        """记录 WorldInitialized 事件，供 WorldManager 重建世界

        事件包含所有场景/实体/物品的完整结构快照。
        """
        es = await self.event_store

        # 构建 locations 快照
        locations = {}
        for loc_data in json_data.get("locations", []):
            loc_key = loc_data.get("key", "unknown")
            locations[loc_key] = {
                "key": loc_key,
                "name": loc_data.get("name", ""),
                "base_desc": loc_data.get("base_desc", ""),
                "tags": loc_data.get("tags", []),
                "exits": loc_data.get("exits", {}),
                "entities": [e.get("key", e.get("name", ""))
                             for e in loc_data.get("entities", [])],
                "interactables": [i.get("key", i.get("name", ""))
                                  for i in loc_data.get("interactables", [])],
            }

        try:
            await es.append(
                session_id=TEMPLATE_SESSION_ID,
                event_type="WorldInitialized",
                data={
                    "module_name": module_name,
                    "locations": locations,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                source_node="ingestion",
            )
            logger.info(f"  [OK] WorldInitialized 事件已记录 ({len(locations)} 个场景)")
        except Exception as e:
            logger.error(f"  [FAIL] WorldInitialized 事件记录失败: {e}")

    # ── 辅助方法 ──

    @staticmethod
    def _summarize_interactables(interactables: list[dict]) -> str:
        """生成交互物摘要（用于 RAG 文本）"""
        if not interactables:
            return "None"
        return ", ".join(
            f"{item.get('name', '?')} ({item.get('state', 'default')})"
            for item in interactables
        )


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
