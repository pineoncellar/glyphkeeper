# GlyphKeeper

### 基于多代理与持久化状态的 AI COC7版规则跑团守密人系统

> **核心理念**：解决LLM在长程叙事中的灾难性上下文遗忘与冲突角色悖论，实现忠于规则书的绝对中立AI守密人

##  项目简介

**GlyphKeeper** 是一个混合代理 AI 系统，旨在主持复杂的桌面角色扮演游戏。当前正在开发中。

传统的"提示词工程"式 AI 往往强迫单个 LLM 同时扮演"创造性的叙事者"和"严谨的数据库"两个角色，导致逻辑冲突和记忆错乱。本项目受 **Google DeepMind Concordia** 框架与 **ChatRPG v2** 论文启发，通过**关注点分离**，将 GM 的职责拆分为多个协同工作的专业代理，并基于单一事实来源的持久化数据库构建。

##  核心痛点与解决方案

| 痛点 | 传统 LLM 方案 | GlyphKeeper 解决方案 |
| --- | --- | --- |
| **记忆错乱** | 依赖有限的 Context Window，随对话变长必然遗忘 | **持久化数据库**：所有状态存储在 PostgreSQL 中，而非 LLM 上下文 |
| **角色悖论** | 试图让同一个模型既"瞎编"又"严谨" | **代理拆分**：叙事者负责创造，档案员负责维护 |
| **节奏失控** | 对玩家的每一句闲聊都进行冗长回复 | **协调器路由**：识别意图，过滤无效交互，节省 Token |
| **模组僵化** | 无法灵活处理玩家偏离预设路线的行为 | **动态逻辑**：基于 ReAct 框架，动态调整剧情 |

##  系统架构：多代理协作

本系统由多个核心代理组成，它们通过 **ReAct** 循环协同工作：

### 1.  协调器 (Orchestrator)  智能路由

* **职责**：接收用户输入，分析意图，路由到合适的子代理
* **路由策略**：
  * 游戏叙事请求  Narrator（故事推进、场景描述）
  * 数据查询请求  Archivist（世界状态、规则检索）
  * 知识问答请求  Search Agent（RAG 检索）
  * 混合请求  多代理协作

### 2.  叙事者 (Narrator)  创造性大脑

* **职责**：生成沉浸式的环境描述、NPC 对话与剧情推进
* **特性**：
  * 基于 ReAct 框架思考与行动
  * 通过函数调用向档案员查询或更新世界状态
  * 使用分级模型配置（fast/standard/smart）优化成本

### 3.  档案员 (Archivist)  逻辑核心

* **职责**：维护游戏世界状态，提供数据 CRUD 接口
* **核心能力**：
  * 管理位置、实体、线索、关系等游戏数据
  * 确保逻辑一致性（如：搜索过的抽屉不会重复掉落物品）
  * 提供结构化 API 供其他代理调用

### 4.  搜索代理 (Search Agent)  知识检索

* **职责**：基于 RAG 检索游戏规则、背景知识、模组信息
* **技术栈**：
  * LightRAG-HKU 实现的高级 RAG 功能
  * pgvector 向量数据库存储
  * 支持混合检索（向量 + 关键词）

---

##  数据层：单一事实来源

为了彻底解决一致性问题，系统采用 **混合数据存储** 策略。

### 1. 结构化世界状态 (PostgreSQL)

通过 SQLAlchemy ORM 管理游戏世界状态。

**核心数据表**：
* **Location**：游戏地点（名称、描述、状态、连接关系）
* **Entity**：玩家/NPC（属性、位置、关系、状态）
* **Clue**：线索/物品（描述、发现条件、关联实体）
* **Relationship**：实体间关系（好感度、敌对度）
* **GameSession**：游戏会话（进度、时间线、状态）

**数据访问层**：
* [src/memory/database.py](src/memory/database.py)：异步数据库连接管理
* [src/memory/repositories/](src/memory/repositories/)：Repository 模式封装的数据访问接口

### 2. 向量知识库 (pgvector + LightRAG)

通过 [src/memory/vector_store.py](src/memory/vector_store.py) 和 [src/memory/RAG_engine.py](src/memory/RAG_engine.py) 实现。

**功能**：
* 存储模组的风味文本、氛围描述、背景知识
* 支持图谱增强的 RAG 检索（LightRAG-HKU）
* 通过向量相似度辅助叙事者生成描写

---

##  工作流示例

### 场景 A：灵活的线索 (The Flexible Diary)

> **目标**：玩家搜索书桌，但关键道具"日记"原定在床垫下。为了奖励玩家合理的探索，系统动态调整剧情。

1. **玩家**："我仔细翻找书桌。"
2. **协调器**：识别为 PLAYER_ACTION_ENV  路由给叙事者
3. **叙事者 (ReAct)**：
   * *Think*: 玩家在搜书桌。书桌状态如何？日记在哪？
   * *Act*: 调用 Archivist.query_world_state('desk') 和 Archivist.query_clue('diary')
4. **档案员**：返回 desk: {searched: false}，diary: {location: mattress, status: UNDISCOVERED}
5. **叙事者 (ReAct)**：
   * *Think*: 书桌没搜过。为了剧情流畅度，我决定把日记移到这里
   * *Act*: 调用 Archivist.move_clue('diary', 'desk')
6. **档案员**：更新数据库
7. **叙事者**：输出 "你在书桌夹层发现了一本日记。"

### 场景 B：外部技能检定

> **目标**：玩家试图踹门，系统需进行规则判定，而非让 LLM 瞎编结果。

1. **玩家**："我要踹开这扇门！"
2. **叙事者 (ReAct)**：
   * *Think*: 需要进行力量检定 (Strength Check)
   * *Act*: 调用 call_external_skill_check(player='A', skill='Str', dc=15)
3. **系统/DiceBot**：在聊天频道投骰子  结果：失败 (7 < 15)
4. **叙事者 (ReAct)**：
   * *Think*: 检定失败，依据规则应给予惩罚
   * *Act*: 调用 Archivist.update_entity_state('A', {status: 'pained'})
5. **叙事者**：输出 "你狠狠踹在门上，门纹丝不动，你的脚踝一阵剧痛。"

---

##  技术栈

### 核心框架
* **Python 3.12+**：利用现代异步特性实现高并发处理
* **模块化架构**：严格分离代理层、数据层、接口层，确保系统可维护性和扩展性

### LLM 集成策略
* **分级模型体系**：根据任务复杂度使用三级模型
  * Fast 模型：快速意图识别、简单对话响应
  * Standard 模型：常规叙事生成、场景描述
  * Smart 模型：复杂推理、关键剧情决策
* **多提供商架构**：支持灵活切换不同 LLM 提供商
* **工厂模式**：统一管理 LLM 实例创建与配置，简化模型调用逻辑
* **成本优化**：通过合理的模型分级与 Token 统计，平衡性能与成本

### 数据存储架构
* **PostgreSQL + pgvector**：结合关系型数据库的事务特性与向量检索能力
* **SQLAlchemy 2.0**：异步 ORM 实现，提供类型安全的数据访问
* **LightRAG 引擎**：图谱增强的 RAG 系统，支持复杂知识关系检索
* **Repository 模式**：封装数据访问逻辑，提高代码可测试性

### 模组消化策略
采用**双管道架构**并行处理：

1. **结构化管道**：
   * PDF 解析提取文本内容
   * LLM 驱动的结构化信息抽取（实体、位置、关系）
   * 填充关系型数据库，构建游戏世界图谱

2. **语义管道**：
   * 提取风味文本、氛围描述、背景故事
   * 生成文本嵌入向量
   * 存入向量数据库，支持语义检索

### 接口层设计
* **REST API**：标准 HTTP 接口，支持 Web 集成
* **CLI 交互**：命令行界面，便于开发调试与自动化测试
* **可扩展性**：预留 WebSocket 支持，未来可接入实时聊天系统

### 系统辅助能力
* **统一配置管理**：集中管理模型参数、数据库连接、系统设置
* **结构化日志**：完整记录系统运行状态，便于问题追踪与性能分析
* **Token 使用统计**：实时监控各模型 Token 消耗，支持成本核算

---

##  快速开始

### 1. 环境准备

```bash
# 安装 uv（Python 包管理器）
pip install uv

# 安装依赖
uv sync

# 配置文件
cp template/config.yaml.template config.yaml
cp template/providers.ini.template providers.ini

# 编辑配置文件，填入你的 LLM API 密钥和数据库连接信息
```

### 2. 初始化数据库

```bash
#### 运行数据库初始化脚本
uv run python scripts/init_db.py
```

### 3. 运行系统

```bash
# CLI 模式
uv run python src/interfaces/cli_runner.py

# API 服务模式
uv run python src/interfaces/api_server.py
```

---

##  项目结构

```
GlyphKeeper/
 config.yaml              # 主配置文件（分级模型、向量存储）
 providers.ini            # LLM 提供商配置
 pyproject.toml          # 项目依赖管理
 src/
    agents/             # 核心代理实现
       orchestrator.py # 路由协调器
       narrator.py     # 叙事生成器
       archivist.py    # 数据管理器
       search.py       # RAG 检索代理
       tools/          # 代理工具（骰子、数据库操作）
    memory/             # 数据持久化层
       database.py     # 数据库连接
       models.py       # ORM 模型定义
       RAG_engine.py   # RAG 引擎
       vector_store.py # 向量存储
       repositories/   # 数据访问层
    llm/                # LLM 抽象层
       llm_factory.py  # LLM 工厂
       llm_openai.py   # OpenAI 实现
       llm_lightrag.py # LightRAG 实现
    ingestion/          # 模组消化管道
       pdf_parser.py   # PDF 解析
       structure_extractor.py  # 结构提取
       loader.py       # 数据加载
    interfaces/         # 用户接口层
       api_server.py   # REST API
       cli_runner.py   # CLI 交互
    core/               # 核心功能
       config.py       # 配置管理
       logger.py       # 日志系统
    utils/              # 工具函数
        token_tracker.py # Token 统计
 scripts/
    init_db.py          # 数据库初始化脚本
 tests/                  # 单元测试
 data/                   # 数据文件（图谱、日志）
```

---

##  参考项目

1. [**Google DeepMind Concordia**: Generative Social Simulation](https://github.com/google-deepmind/concordia)
2. [**ChatRPG v2**: Conflict Resolution in RPGs](https://github.com/KarmaKamikaze/ChatRPG)
3. [**LightRAG**: Graph-Enhanced RAG](https://github.com/HKUDS/LightRAG)

---

**License**: 还没定

**Status**:  开发中
