"""
Narrator Prompt 动态构建器
实现模块化、动态化和高上下文感知的 Prompt 生成
"""
from typing import Dict, List, Optional, Any
from enum import Enum
import json


class SceneMode(Enum):
    """场景模式：用于动态调整 Prompt 的详略程度"""
    EXPLORATION = "exploration"  # 探索模式：强调感官细节
    COMBAT = "combat"            # 战斗/逃跑模式：强调紧迫感和动作
    DIALOGUE = "dialogue"        # 对话模式：强调角色互动
    INVESTIGATION = "investigation"  # 调查模式：强调线索和推理


class PromptAssembler:
    """
    Narrator Prompt 的模块化构建器
    
    结构遵循五层架构：
    1. 核心层 (Core): 人设与法则
    2. 状态层 (State): 谁? 何时? 何地?
    3. 记忆层 (Memory): RAG 检索结果
    4. 历史层 (History): 最近对话窗口
    5. 工具结果层 (Tool Results): 工具执行结果
    """

    # 核心层
    CORE_ROLE = """### 身份与角色
你是《克苏鲁的呼唤》桌面角色扮演游戏的**秘密守护者 (KP)**。
你的使命是为玩家提供一个沉浸式的、洛夫克拉夫特式的恐怖体验。

玩家的角色是**"{player_name}"**。
在调用工具时，必须使用"{player_name}"作为 `entity_name` 参数。
"""

    PRIME_DIRECTIVES = """### 核心法则

1. **绝对真实**:
   - 所有客观事实（地点、物品、状态）必须基于工具输出。
   - 如果信息未知，调用工具（例如 `get_location_view`）。绝不凭空捏造重要游戏事实。
   - 如果工具返回"失败"或"被阻挡"，诚实地描述这一挫折。不要为了叙事流畅而强行成功。

2. **玩家自主权**:
   - 绝不描写玩家的内心想法、语言或决定行动。
   - ❌ 错误："你感到恐惧，尖叫着逃跑了。"
   - ✅ 正确："那不可名状的恐怖冲击着你的感官。你的理智在颤抖。你接下来怎么做？"

3. **叙事风格**:
   - **洛夫克拉夫特式**：使用感官细节（霉味、粘稠质感、压抑的声音）。
   - **迷雾法则**：不要一次揭示所有秘密。先描述显眼的东西，细节需要玩家主动询问。
   - **少即是多**：对于不可名状的恐怖，保持神秘。不要过早揭开面纱。

4. **工具使用规范**:
   - 近距离/探索/危险：使用 `move_entity`
   - 远距离/城市/安全旅行：使用 `travel_to_location`
   - 检查时：使用 `inspect_target` 获取详细信息
   - 与 NPC 互动：使用 `interact_with_character`

5. **规则裁决 (Rules of the Game)**:
   - 你不是规则书，不要凭空猜测复杂的规则。
   - **当发生以下情况时，必须调用 `consult_rulebook` 工具：**
     1. 玩家要求 **"孤注一掷" (Push the roll)**。
     2. 进入 **战斗 (Combat)** 或 **追逐 (Chase)**。
     3. 玩家遭受 **理智 (SAN)** 损失，需要查询疯狂症状表。
     4. 使用 **魔法** 或 **怪异科技**。
   - 获取规则建议后，再结合剧情进行描述。
"""

    # 状态层
    STATE_TEMPLATE = """### 当前状态
- **调查员**: {player_name}
- **地点**: {location}
- **时间**: {time_slot}
- **氛围**: {environment}
{additional_state}
"""

    # 记忆层
    MEMORY_TEMPLATE = """### 世界知识与秘密
系统已检索相关信息来丰富你的叙事：

{world_lore}

{story_so_far}

{keeper_secrets}
"""

    # 历史层
    HISTORY_TEMPLATE = """### 最近互动
{history}
"""

    # 工具结果层
    TOOL_RESULT_TEMPLATE = """### 工具执行结果（客观现实）
以下数据代表刚发生的**绝对真实**：

```json
{tool_results}
```

**指令**: 
{mode_instruction}
将结果中的'观察'、'风味文本'或'描述'融入你的叙事中。
如果有'标签'（如["上锁"、"古老"]），使用它们来添加氛围细节。
"""

    NO_TOOL_INSTRUCTION = """### 任务
未执行工具。根据用户输入：
- 如果玩家想执行动作（移动、查看、互动），确定应使用的工具。
- 如果是问题或对话，直接用叙事或对话回应。

**指令**: 
{mode_instruction}
"""

    # 场景模式指令
    MODE_INSTRUCTIONS = {
        SceneMode.EXPLORATION: """
关注**感官沉浸**：
- 生动地描述视觉、声音、气味、质感等细节。
- 缓慢建立氛围。让环境讲述它的故事。
- 使用环境叙事（例如，门上的抓痕、布料上的污渍）。
""",
        SceneMode.COMBAT: """
关注**紧迫感和动作**：
- 保持描述简洁而动态。
- 强调即时威胁和发自内心的反应。
- 优先说明后果的清晰性（命中/未命中、伤害、位置变化）。
""",
        SceneMode.DIALOGUE: """
关注**角色互动**：
- 给予 NPC 独特的声音和肢体语言。
- 通过对话而非说教来展现角色。
- 使用停顿、犹豫和言外之意来建立张力。
""",
        SceneMode.INVESTIGATION: """
关注**线索和推理**：
- 清晰但微妙地呈现证据。
- 允许玩家自己做出连接。
- 用额外细节奖励仔细观察。
"""
    }

    @staticmethod
    def _detect_scene_mode(user_input: str, game_state: Dict) -> SceneMode:
        """场景模式检测"""
        input_lower = user_input.lower()
        
        # 战斗关键词
        combat_keywords = ["攻击", "attack", "逃跑", "flee", "躲避", "dodge", "战斗", "fight"]
        if any(kw in input_lower for kw in combat_keywords):
            return SceneMode.COMBAT
        
        # 对话关键词
        dialogue_keywords = ["问", "说", "ask", "say", "tell", "talk", "对话", "交谈"]
        if any(kw in input_lower for kw in dialogue_keywords):
            return SceneMode.DIALOGUE
        
        # 调查关键词
        investigation_keywords = ["检查", "examine", "inspect", "search", "调查", "观察", "look at"]
        if any(kw in input_lower for kw in investigation_keywords):
            return SceneMode.INVESTIGATION
        
        # 检查游戏状态中的标签
        env_tags = game_state.get("environment_tags", [])
        if "combat" in env_tags or "danger" in env_tags:
            return SceneMode.COMBAT
        
        # 默认为探索模式
        return SceneMode.EXPLORATION

    @staticmethod
    def _format_memory_context(rag_context: Dict[str, str]) -> str:
        """格式化三段式记忆内容"""
        sections = []
        
        # 语义记忆 (世界知识)
        semantic = rag_context.get("semantic", "").strip()
        if semantic:
            sections.append(f"**[世界知识]** (来自语义记忆)\n{semantic}")
        else:
            sections.append("**[世界知识]** (来自语义记忆)\n[未找到相关世界知识]")
        
        # 情景记忆 (故事进展)
        episodic = rag_context.get("episodic", "").strip()
        if episodic:
            sections.append(f"**[故事进展]** (来自情景记忆)\n{episodic}")
        else:
            sections.append("**[故事进展]** (来自情景记忆)\n[未记录先前行动]")
        
        # KP 秘密 (隐藏线索)
        keeper_notes = rag_context.get("keeper_notes", "").strip()
        if keeper_notes:
            sections.append(f"**[KP信息]** (隐藏线索)\n{keeper_notes}")
        else:
            sections.append("**[KP信息]**\n[本场景暂无特殊秘密]")
        
        return "\n\n".join(sections)

    @staticmethod
    def _format_tool_results(tool_results: List[Dict]) -> str:
        """格式化工具执行结果为 JSON 字符串"""
        formatted_results = []
        
        for idx, result in enumerate(tool_results, 1):
            formatted_results.append(f"// Result {idx}")
            formatted_results.append(json.dumps(result, ensure_ascii=False, indent=2))
        
        return "\n".join(formatted_results)

    @classmethod
    def build(
        cls,
        player_name: str,
        game_state: Dict[str, Any],
        rag_context: Dict[str, str],
        history_str: str,
        user_input: str = "",
        tool_results: Optional[List[Dict]] = None,
        scene_mode: Optional[SceneMode] = None
    ) -> str:
        """
        构建完整的 Narrator System Prompt
        
        Args:
            player_name: 玩家角色名称
            game_state: 游戏状态字典，包含：
                - location: 当前地点
                - time_slot: 时间段 (如 "深夜")
                - environment: 环境描述 (如 "雷雨")
                - environment_tags: 环境标签列表 (如 ["dark", "rainy"])
            rag_context: RAG 检索结果，包含：
                - semantic: 语义记忆 (世界知识)
                - episodic: 情景记忆 (故事进展)
                - keeper_notes: KP 笔记 (隐藏线索)
            history_str: 最近的对话历史字符串
            user_input: 用户当前输入 (用于场景模式检测)
            tool_results: 工具执行结果列表 (可选)
            scene_mode: 强制指定场景模式 (可选，默认自动检测)
        
        Returns:
            完整的系统 Prompt 字符串
        """
        prompt_sections = []
        
        # 核心层
        prompt_sections.append(cls.CORE_ROLE.format(player_name=player_name))
        prompt_sections.append(cls.PRIME_DIRECTIVES)
        
        # 状态层
        additional_state = ""
        if game_state.get("special_conditions"):
            additional_state = f"- **特殊条件**: {game_state['special_conditions']}"
        
        prompt_sections.append(cls.STATE_TEMPLATE.format(
            player_name=player_name,
            location=game_state.get("location", "未知"),
            time_slot=game_state.get("time_slot", "未知"),
            environment=game_state.get("environment", "未知"),
            additional_state=additional_state
        ))
        
        # 记忆层
        formatted_memory = cls._format_memory_context(rag_context)
        prompt_sections.append(cls.MEMORY_TEMPLATE.format(
            world_lore="",  # 已经在 _format_memory_context 中处理
            story_so_far="",
            keeper_secrets=formatted_memory
        ))
        
        # 历史层
        if history_str.strip():
            prompt_sections.append(cls.HISTORY_TEMPLATE.format(history=history_str))
        else:
            prompt_sections.append(cls.HISTORY_TEMPLATE.format(
                history="[这是会话的开始]"
            ))
        
        # 动作层
        # 自动检测场景模式
        if scene_mode is None:
            scene_mode = cls._detect_scene_mode(user_input, game_state)
        
        mode_instruction = cls.MODE_INSTRUCTIONS[scene_mode]
        
        if tool_results:
            formatted_results = cls._format_tool_results(tool_results)
            prompt_sections.append(cls.TOOL_RESULT_TEMPLATE.format(
                tool_results=formatted_results,
                mode_instruction=mode_instruction
            ))
        else:
            prompt_sections.append(cls.NO_TOOL_INSTRUCTION.format(
                mode_instruction=mode_instruction
            ))
        
        # 添加思考提示
        prompt_sections.append("""
---
**回答之前**，简要思考：
1. 工具结果（或当前情况）如何与KP信息相关？
2. 什么样的感官细节会增强恐怖氛围？
3. 什么信息现在应该保持神秘？

然后提供你的叙事回应。
""")
        
        return "\n".join(prompt_sections)

    @classmethod
    def build_simple(
        cls,
        player_name: str,
        current_location: str,
        user_input: str
    ) -> str:
        """简化版构建器：用于快速对话场景"""
        return cls.build(
            player_name=player_name,
            game_state={
                "location": current_location,
                "time_slot": "Unknown",
                "environment": "Unknown"
            },
            rag_context={
                "semantic": "",
                "episodic": "",
                "keeper_notes": ""
            },
            history_str="",
            user_input=user_input,
            tool_results=None
        )
