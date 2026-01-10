# 🏗️ Agents 新架构蓝图

> **核心范式**：Deep-Think Pipeline (深思流水线) with Hybrid Resolver (混合裁决)
> **关键特性**：延迟容忍、绝对数据一致性、高自由度即兴判定

---

## 1. 宏观架构全景图 (The Big Picture)

系统不再是单一的 Agent 对话，而是一个由 **Python 引擎** 驱动的严密工业流水线。

```mermaid
graph TD
    User((👤 玩家)) <--> Engine[⚙️ 中央引擎 GameEngine<br/>(FSM 状态机)]

    subgraph "Stage 1: 感知与翻译 (Perception)"
        Engine --> Analyzer[🧠 意图分析师 Analyzer<br/>(LLM: DeepSeek)]
        Analyzer -->|1. 意图 JSON| Engine
    end

    subgraph "Stage 2: 规则与裁决 (Resolution)"
        Engine --> Resolver[⚖️ 规则总线 Resolver<br/>(Python Facade)]
        
        Resolver --> Check{硬编码逻辑?}
        Check -->|Yes| PyComp[🧩 Python 组件<br/>Combat/Sanity/Skill]
        Check -->|No| Adjudicator[👨‍⚖️ 裁决者 Adjudicator<br/>(LLM: 规则翻译官)]
        
        Adjudicator -->|规则参数| PyComp
        PyComp <--> DB[(PostgreSQL)]
        
        Resolver -->|2. 结果 JSON| Engine
    end

    subgraph "Stage 3: 表达与叙事 (Expression)"
        Engine --> Writer[✍️ 叙事作家 Writer<br/>(LLM: DeepSeek)]
        Writer -->|3. 叙事文本| Engine
    end

    subgraph "Stage 4: 记忆固化 (Consolidation)"
        Writer -.-> Memorizer[🦉 记忆员 Memorizer<br/>(后台任务)]
        Memorizer -->|提取 Facts| RAG[(向量库)]
        RAG -.->|Context| Analyzer
    end

```

---

## 2. 目录结构规范 (Directory Structure)

我们将系统划分为清晰的 **四层架构**：

```text
src/
├── core/                    # [中枢层] 调度与状态定义
│   ├── __init__.py
│   ├── game_engine.py       # 主循环、流水线编排
│   ├── fsm.py               # 状态机定义 (EXPLORATION, COMBAT...)
│   └── events.py            # 定义信号 (Signal) 与 数据类 (ResolutionResult)
│
├── agents/                  # [感知/表达层] LLM 接口 (只负责自然语言处理)
│   ├── __init__.py
│   ├── analyzer.py          # Stage 1: Player Input -> Intent JSON
│   ├── writer.py            # Stage 3: Result JSON -> Narrative Text
│   ├── adjudicator.py       # Stage 2 Helper: 即兴行为 -> 规则参数
│   ├── memorizer.py         # Stage 4: Narrative -> Facts
│   └── prompts/             # Prompt 模板库
│       ├── analyzer_prompts.py
│       ├── writer_prompts.py
│       └── ...
│
├── components/              # [逻辑层] 纯 Python 业务内核 (Resolver + Systems)
│   ├── __init__.py
│   ├── resolver.py          # 规则总线 (Facade) - 唯一入口
│   ├── base.py              # Component 基类
│   ├── physical.py          # 物理交互 (搜查、物品使用)
│   ├── social.py            # 社交交互 (话术、心理学)
│   ├── combat.py            # 战斗系统 (伤害计算、轮次)
│   ├── sanity.py            # 理智系统 (固化、疯狂)
│   ├── navigation.py        # 移动逻辑 (地图、寻路)
│   └── dice.py              # 基础掷骰引擎 (D100)
│
├── memory/                  # [数据层] 纯粹的 CRUD
│   └── 保持不变...
│
└── main.py                  # 启动入口

```

---

## 3. 核心流水线详解 (The Pipeline)

### Stage 1: Analyzer (意图分析)

* **职责**：将玩家的自然语言“降维”为系统可理解的**原语**。
* **输入**：`Player Input` + `FSM State` + `History Summary`.
* **Prompt 策略**：根据 FSM 状态注入限制（如战斗中屏蔽闲聊）。
* **输出 (JSON)**：
* `type`: `PHYSICAL_INTERACT` | `SOCIAL_INTERACT` | `COMBAT_ACTION` | `MOVE` | `META`
* `target`: 目标实体名
* `action_verb`: 具体动作 (如 "throw", "search")
* `params`: 附加参数 (如 `attempt_push: true`)



### Stage 2: Resolver (规则裁决) —— 最核心的大脑

* **职责**：维护游戏世界的物理法则和规则逻辑。
* **双路处理机制**：
1. **Fast Path (Python)**: 针对常见动作（战斗、技能检定、移动）。
* 直接调用 `components` 进行数值计算。


2. **Slow Path (Adjudicator LLM)**: 针对即兴行为（如“用面粉撒地”）。
* 调用 `Adjudicator` 将行为翻译为规则参数（如“给隐形怪挂上 Tag”）。
* 再交回 Python 执行数据库修改。


3. **Improvisation (常识补全)**:
* 针对“找灭火器”等合理但不存在的物品，调用常识判断并动态生成。




* **输出 (ResolutionResult)**：
* `success`: bool
* `outcome_desc`: 技术性描述 (如 "HP -1, Status: Prone")
* `signal`: 系统信号 (如 `COMBAT_START`)



### Stage 3: Writer (叙事生成)

* **职责**：将枯燥的技术结果“升维”为文学描写。
* **铁律**：

- 不可知论：看不到数据库，只能看到 Resolver 给的结果。

- 复读机：如果 Resolver 说失败，Writer 必须写失败。

- 润色者：负责补充细节（颜色、气味、痛感）。



### Stage 4: Memorizer (记忆固化)

* **职责**：事后一致性维护。
* **流程**：
- 监听 Writer 的输出。
- 提取 Writer 编造的细节（如“白色的床单”）。
- 存入 **右脑 (RAG)** 作为事实 (Fact)。
- 下一轮检索时，确保床单依然是白色的。



---

## 4. 关键交互流程示例 (Sequence Flow)

### 场景：玩家试图过肩摔黑山羊幼崽 (规则拦截)

1. **User**: "我要给它个过肩摔！"
2. **Analyzer**: `{"type": "COMBAT_ACTION", "action": "throw", "target": "Dark Young"}`
3. **Resolver**:
* 调用 `CombatComponent`.
* 检查属性：Player SIZ 50 vs Target SIZ 400.
* **逻辑拦截**: `if target.siz > attacker.siz + 20: return IMPOSSIBLE`
* 返回: `{"success": False, "reason": "physically_impossible"}`


4. **Writer**: "你试图撼动那座肉山，但它纹丝不动，反倒震得你手臂发麻。"

### 场景：玩家寻找灭火器 (即兴生成)

1. **User**: "快找找有没有灭火器！"
2. **Analyzer**: `{"type": "PHYSICAL_INTERACT", "target": "fire_extinguisher", "action": "search"}`
3. **Resolver**:
* `PhysicalComponent` 查库 -> 无。
* 调用 **常识判断** -> "现代楼道应该有"。
* 执行 **幸运检定** -> 成功。
* **写入数据库**: `INSERT INTO items (name="灭火器")...`
* 返回: `{"success": True, "item": "Fire Extinguisher"}`


4. **Writer**: "你在墙角发现了一个落满灰尘的灭火器箱。"

---

## 5. 开发起步建议

1. **Phase 1: 骨架搭建**
* 建立目录结构。
* 定义 `core/events.py` 中的 `IntentType` 和 `ResolutionResult` 数据类。这是各层通信的“普通话”。


2. **Phase 2: 移植逻辑层**
* 将原 `Archivist` 的逻辑拆解放入 `components/`。
* 编写 `dice.py`，确保所有的随机数都来自 Python。


3. **Phase 3: 接入 LLM**
* 编写 `Analyzer` 和 `Writer`，接入 DeepSeek API。
* 跑通 "Echo Loop"：玩家说话 -> 分析 -> 直接返回意图 JSON -> 玩家。


4. **Phase 4: 注入灵魂**
* 实现 `Resolver` 中的 `Adjudicator` 兜底逻辑。
* 实现 `Memorizer` 后台任务。
