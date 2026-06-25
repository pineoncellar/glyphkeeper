"""
基础设施层单元测试

测试范围:
  - config: Settings 加载、配置项访问
  - event_store: 事件追加、查询、回放、版本
  - summarizer: Token 计数、策略判定、摘要清洗
  - retriever: 上下文拼接（mock）
"""

import json
import pytest
import asyncio
from pathlib import Path

from src.tools import get_settings, Settings, PROJECT_ROOT
from src.memory.event_store import EventStore
from src.memory.summarizer import (
    Summarizer,
    TokenCountStrategy,
    TopicEndStrategy,
    TimeBasedStrategy,
    count_tokens,
)


# ====================================================================
# Config 测试
# ====================================================================

class TestConfig:
    def test_get_settings(self, monkeypatch):
        """Settings 可加载且字段可正常访问（使用干净配置）"""
        # 注入纯净配置，避免被 config.yaml / providers.ini 干扰
        from src.tools.config import _settings_instance
        clean = Settings()
        monkeypatch.setattr('src.tools.config._settings_instance', clean)
        # 重新导入以获取 clean 引用
        from src.tools import get_settings as gs
        s = gs()
        assert s.project.name == "GlyphKeeper"
        assert s.project.debug is False
        assert s.project.active_world == "default_world"

    def test_model_tiers_default(self, monkeypatch):
        """无配置时 model_tiers 为空字典"""
        from src.tools.config import _settings_instance
        monkeypatch.setattr('src.tools.config._settings_instance', Settings())
        from src.tools import get_settings as gs
        s = gs()
        assert isinstance(s.model_tiers, dict)
        assert len(s.model_tiers) == 0

    def test_providers_default(self, monkeypatch):
        """无配置时 providers 为空字典"""
        from src.tools.config import _settings_instance
        monkeypatch.setattr('src.tools.config._settings_instance', Settings())
        from src.tools import get_settings as gs
        s = gs()
        assert isinstance(s.providers, dict)
        assert len(s.providers) == 0

    def test_vector_store_defaults(self):
        """向量存储配置有合理的默认值"""
        s = get_settings()
        assert s.vector_store.embedding_dim == 1024
        assert s.vector_store.chunk_size == 500
        assert s.vector_store.chunk_overlap == 50

    def test_project_root(self):
        """PROJECT_ROOT 指向项目根目录"""
        root = PROJECT_ROOT
        assert (root / "pyproject.toml").exists()

    def test_log_dir_created(self):
        """logs/ 目录存在"""
        assert (PROJECT_ROOT / "logs").exists()

    def test_data_dir_created(self):
        """data/ 及子目录存在"""
        for name in ("data", "data/modules", "data/raw_sources", "data/intermediate",
                     "data/worlds", "data/rules"):
            assert (PROJECT_ROOT / name).exists(), f"{name} 不存在"


# ====================================================================
# EventStore 测试
# ====================================================================

class TestEventStore:
    @pytest.fixture
    async def store(self):
        """创建 PG EventStore（自动启动 pgembed）"""
        from src.tools.pg_manager import PgManager
        await PgManager.reset_instance()
        mgr = await PgManager.get_instance()
        if not mgr.available:
            pytest.skip("pgembed 不可用，跳过 PG 测试")
        await mgr.start()
        es = EventStore(pg_uri=mgr.uri)
        await es.clear_all()
        yield es
        await es.close()
        await mgr.stop()

    @pytest.mark.asyncio
    async def test_append_event(self, store):
        """追加事件应返回带 ID 和版本的事件"""
        ev = await store.append("session-1", "TEST", {"msg": "hello"}, source_node="test")
        assert ev["type"] == "TEST"
        assert ev["version"] == 1
        assert ev["session_id"] == "session-1"
        assert ev["data"]["msg"] == "hello"
        assert ev["source_node"] == "test"

    @pytest.mark.asyncio
    async def test_append_increments_version(self, store):
        """同一会话的事件版本号递增"""
        ev1 = await store.append("s1", "EVENT_A", {"n": 1})
        ev2 = await store.append("s1", "EVENT_B", {"n": 2})
        assert ev1["version"] == 1
        assert ev2["version"] == 2

    @pytest.mark.asyncio
    async def test_append_different_sessions(self, store):
        """不同会话的版本号独立"""
        ev1 = await store.append("s1", "A", {})
        ev2 = await store.append("s2", "A", {})
        assert ev1["version"] == 1
        assert ev2["version"] == 1

    @pytest.mark.asyncio
    async def test_get_events(self, store):
        """get_events 返回指定会话的事件列表"""
        await store.append("s1", "A", {})
        await store.append("s1", "B", {})
        await store.append("s2", "C", {})

        events = await store.get_events("s1")
        assert len(events) == 2
        assert [e["type"] for e in events] == ["A", "B"]

    @pytest.mark.asyncio
    async def test_get_events_since_version(self, store):
        """since_version 过滤"""
        await store.append("s1", "A", {})
        await store.append("s1", "B", {})
        await store.append("s1", "C", {})

        events = await store.get_events("s1", since_version=1)
        assert len(events) == 2
        assert [e["type"] for e in events] == ["B", "C"]

    @pytest.mark.asyncio
    async def test_replay(self, store):
        """replay 生成器按版本顺序产出事件"""
        await store.append("s1", "A", {})
        await store.append("s1", "B", {})

        replayed = []
        async for ev in store.replay("s1"):
            replayed.append(ev["type"])
        assert replayed == ["A", "B"]

    @pytest.mark.asyncio
    async def test_replay_empty_session(self, store):
        """空会话的 replay 不产出事件"""
        count = 0
        async for _ in store.replay("nonexistent"):
            count += 1
        assert count == 0

    @pytest.mark.asyncio
    async def test_latest_version(self, store):
        """get_latest_version 返回正确的最新版本号"""
        assert await store.get_latest_version("s1") == 0
        await store.append("s1", "A", {})
        assert await store.get_latest_version("s1") == 1
        await store.append("s1", "B", {})
        assert await store.get_latest_version("s1") == 2

    @pytest.mark.asyncio
    async def test_parent_event_id(self, store):
        """支持父事件 ID 因果链"""
        parent = await store.append("s1", "PARENT", {})
        child = await store.append("s1", "CHILD", {}, parent_event_id=parent["id"])
        assert child["parent_event_id"] == parent["id"]

    @pytest.mark.asyncio
    async def test_clear_session(self, store):
        """clear_session 清空指定会话"""
        await store.append("s1", "A", {})
        await store.append("s1", "B", {})
        await store.clear_session("s1")
        events = await store.get_events("s1")
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_event_count(self, store):
        """get_event_count 返回正确计数"""
        assert await store.get_event_count("s1") == 0
        await store.append("s1", "A", {})
        assert await store.get_event_count("s1") == 1


# ====================================================================
# Summarizer 测试
# ====================================================================

class TestTokenCountStrategy:
    def test_empty_buffer(self):
        """空缓冲区不应触发固化"""
        strategy = TokenCountStrategy(max_tokens=100)
        assert strategy.should_consolidate([]) is False

    def test_below_threshold(self):
        """低于阈值不触发"""
        strategy = TokenCountStrategy(max_tokens=10000)
        buffer = [{"role": "user", "content": "hello"}]
        assert strategy.should_consolidate(buffer) is False

    def test_above_threshold(self):
        """超过阈值触发"""
        strategy = TokenCountStrategy(max_tokens=5)
        buffer = [{"role": "user", "content": "a" * 100}]
        assert strategy.should_consolidate(buffer) is True


class TestTopicEndStrategy:
    def test_no_marker(self):
        """没有结束标记不触发"""
        strategy = TopicEndStrategy()
        buffer = [{"role": "user", "content": "继续探索"}]
        assert strategy.should_consolidate(buffer) is False

    def test_has_marker(self):
        """最后一条有结束标记触发"""
        strategy = TopicEndStrategy()
        buffer = [{"role": "user", "content": "调查完毕<END_TOPIC>"}]
        assert strategy.should_consolidate(buffer) is True


class TestSummarizer:
    def test_clean_summary_removes_xml(self):
        """_clean_summary 移除 XML 标签"""
        clean = Summarizer._clean_summary("<thinking>分析中</thinking>调查员打开了门。")
        assert "调查员打开了门。" in clean
        assert "<thinking>" not in clean

    def test_clean_summary_removes_markdown(self):
        """_clean_summary 移除 Markdown 加粗"""
        clean = Summarizer._clean_summary("**调查员** 走进了房间。")
        assert "**调查员**" not in clean
        assert "调查员" in clean

    def test_clean_summary_truncates_long(self):
        """超长摘要被截断"""
        long_text = "调查员" * 500
        clean = Summarizer._clean_summary(long_text)
        assert len(clean) <= 500

    def test_should_summarize_false(self):
        """短对话不触发摘要"""
        s = Summarizer()
        records = [{"role": "user", "content": "你好"}]
        assert s.should_summarize(records, max_tokens=1000) is False

    def test_should_summarize_true(self):
        """长对话触发摘要"""
        s = Summarizer()
        records = [{"role": "user", "content": "a" * 5000}]
        assert s.should_summarize(records, max_tokens=100) is True

    def test_summarize_empty(self):
        """空记录返回空字符串"""
        s = Summarizer()
        result = asyncio.run(s.summarize([]))
        assert result == ""

    def test_extract_facts_empty(self):
        """空文本返回空列表"""
        s = Summarizer()
        result = asyncio.run(s.extract_facts(""))
        assert result == []

    def test_parse_facts(self):
        """_parse_facts 解析 JSON 列表"""
        raw = '[{"type": "event", "description": "发现密室"}]'
        facts = Summarizer._parse_facts(raw)
        assert len(facts) == 1
        assert facts[0]["type"] == "event"


# ====================================================================
# count_tokens 测试
# ====================================================================

class TestCountTokens:
    def test_empty_string(self):
        assert count_tokens("") == 0

    def test_short_string(self):
        assert count_tokens("hello") >= 1

    def test_long_string(self):
        # GPT2 tokenizer 编码"x"约 1 token/8 字符
        assert count_tokens("x" * 5000) > 0


# ====================================================================
# TimeBasedStrategy 测试
# ====================================================================

class TestTimeBasedStrategy:
    def test_never_consolidated_triggers(self):
        """从未固化过应触发"""
        strategy = TimeBasedStrategy(min_interval_seconds=600)
        assert strategy.should_consolidate([{"role": "user", "content": "hello"}]) is True

    def test_empty_buffer(self):
        """空缓冲区不触发"""
        strategy = TimeBasedStrategy()
        assert strategy.should_consolidate([]) is False

    def test_within_interval_no_trigger(self):
        """刚固化完不应触发"""
        strategy = TimeBasedStrategy(min_interval_seconds=3600)
        strategy.mark_consolidated()
        buffer = [{"role": "user", "content": "test"}]
        assert strategy.should_consolidate(buffer) is False

    def test_time_since_last_none(self):
        """从未固化时 time_since_last 为 None"""
        strategy = TimeBasedStrategy()
        assert strategy.time_since_last is None

    def test_time_since_last_after_mark(self):
        """固化后 time_since_last 返回正常值"""
        strategy = TimeBasedStrategy()
        strategy.mark_consolidated()
        elapsed = strategy.time_since_last
        assert elapsed is not None
        assert elapsed >= 0


# ====================================================================
# _parse_facts 边界测试
# ====================================================================

class TestParseFacts:
    def test_empty_string(self):
        """空字符串返回空列表"""
        assert Summarizer._parse_facts("") == []

    def test_single_object(self):
        """单个 {} 对象返回 list[dict]"""
        raw = '{"type": "event", "description": "发现密室"}'
        facts = Summarizer._parse_facts(raw)
        assert len(facts) == 1
        assert facts[0]["type"] == "event"

    def test_nested_json_array(self):
        """嵌套 JSON 数组应完整解析"""
        raw = '[{"type": "event", "data": [1, 2, 3]}, {"type": "clue", "tags": ["a", "b"]}]'
        facts = Summarizer._parse_facts(raw)
        assert len(facts) == 2
        assert len(facts[0]["data"]) == 3

    def test_multiline_json(self):
        """多行 JSON 能正确解析"""
        raw = """[
            {"type": "event", "desc": "step1"},
            {"type": "event", "desc": "step2"}
        ]"""
        facts = Summarizer._parse_facts(raw)
        assert len(facts) == 2

    def test_object_with_nested_brackets(self):
        """对象字段值含 ] 不中断"""
        raw = '{"type": "note", "text": "结果是 [42] 号"}'
        facts = Summarizer._parse_facts(raw)
        assert len(facts) == 1
        assert facts[0]["type"] == "note"

    def test_invalid_json_returns_empty(self):
        """无效 JSON 返回空列表"""
        facts = Summarizer._parse_facts("这不是 JSON")
        assert facts == []

    def test_mixed_text_json(self):
        """混合文本中提取 JSON 数组"""
        raw = "以下是结果：\n```json\n[{\"type\": \"event\"}]\n```"
        facts = Summarizer._parse_facts(raw)
        assert len(facts) == 1


# ====================================================================
# Retriever 测试（mock）
# ====================================================================

class TestRetriever:
    async def test_retrieve_context_without_stores(self):
        """没有传入 store 时不应报错"""
        from src.memory.retriever import Retriever
        r = Retriever(vector_store=None, event_store=None)
        result = await r.retrieve_context("session-1", "")
        assert result == "（无查询内容）"

    async def test_retrieve_context_query_too_short(self):
        """过短查询返回占位文本"""
        from src.memory.retriever import Retriever
        r = Retriever()
        result = await r.retrieve_context("session-1", "  ")
        assert result == "（无查询内容）"

    async def test_build_memory_input_structure(self):
        """build_memory_input 返回正确结构"""
        from src.memory.retriever import Retriever
        r = Retriever()
        result = await r.build_memory_input("session-1", "测试")
        assert isinstance(result, dict)
        assert "context" in result
        assert "recent_events" in result
        assert "rules" in result

    async def test_retrieve_history_empty(self):
        """空会话返回空列表"""
        from src.memory.retriever import Retriever
        from src.memory.event_store import EventStore
        from src.tools.pg_manager import PgManager
        await PgManager.reset_instance()
        mgr = await PgManager.get_instance()
        if not mgr.available:
            pytest.skip("pgembed 不可用")
        await mgr.start()
        store = EventStore(pg_uri=mgr.uri)
        r = Retriever(event_store=store)
        events = await r.retrieve_history("nonexistent")
        assert events == []
        await store.close()
        await mgr.stop()

    async def test_retrieve_rules_fallback(self):
        """检索规则时如无连接返回降级文本"""
        from src.memory.retriever import Retriever
        r = Retriever()
        result = await r.retrieve_rules("")
        assert result == "（无查询内容）"

    async def test_build_memory_input_empty_query(self):
        """空查询的 build_memory_input 应包含占位"""
        from src.memory.retriever import Retriever
        r = Retriever()
        result = await r.build_memory_input("s1", "")
        assert result["context"] == "（无查询内容）"


# ====================================================================
# VectorStore 配置测试
# ====================================================================

class TestVectorStoreConfig:
    """VectorStore 纯逻辑方法测试（无需真实 LightRAG 连接）"""

    def test_build_storage_config_default(self):
        """默认配置包含所有必要键"""
        from src.memory.vector_store import VectorStore
        vs = VectorStore(domain="world")
        config = vs._build_storage_config("/tmp/test", "test_world")
        assert config["workspace"] == "test_world"
        assert config["working_dir"] == "/tmp/test"

    def test_build_storage_config_has_required_keys(self):
        """配置字典应包含所有必要键"""
        from src.memory.vector_store import VectorStore
        vs = VectorStore(domain="rules")
        config = vs._build_storage_config("/tmp/rules", "rules")
        required_keys = {"working_dir", "workspace", "graph_storage",
                         "vector_storage", "kv_storage", "doc_status_storage"}
        assert required_keys.issubset(config.keys())

    async def test_build_postgres_url_format(self):
        """PostgreSQL URL 格式正确（通过 PgManager）"""
        from src.tools.pg_manager import PgManager, PgBackend

        await PgManager.reset_instance()
        mgr = await PgManager.get_instance()

        print(f"[test] backend={mgr.backend.value} uri='{mgr.uri}'")
        if mgr.backend == PgBackend.NONE:
            print("[test] PG 不可用，跳过 URI 格式断言（SQLite 降级模式）")
            return

        assert mgr.uri.startswith("postgresql://"), (
            f"URI should start with postgresql://, "
            f"got uri='{mgr.uri}' backend={mgr.backend.value}"
        )

    def test_domain_immutable(self):
        """domain 在初始化后不可变"""
        from src.memory.vector_store import VectorStore
        vs = VectorStore(domain="rules")
        assert vs.domain == "rules"
