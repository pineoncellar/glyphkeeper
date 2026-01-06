"""
Narrator Prompt 动态构建器
采用 XML 结构化上下文与强制思维链 (CoT)，专为中文 TRPG 体验优化
"""
from typing import Dict, List, Optional, Any
from enum import Enum
import json

class SceneMode(Enum):
    """场景模式：用于动态调整 Prompt 的详略程度"""
    EXPLORATION = "exploration"      # 探索
    COMBAT = "combat"                # 战斗
    DIALOGUE = "dialogue"            # 对话
    INVESTIGATION = "investigation"  # 调查

class PromptAssembler:
    # ------------------------------------------------------------------
    # 1. 系统指令层 (System Header) - 确立 KP 身份与核心法则
    # ------------------------------------------------------------------
    SYSTEM_HEADER = """
你现在是《克苏鲁的呼唤》(Call of Cthulhu) 桌面角色扮演游戏的**守密人 (Keeper/KP)**。
你的目标是为玩家 **"{player_name}"** 创造一个沉浸式、充满洛夫克拉夫特风格的恐怖叙事体验。

### 核心法则 (Prime Directives)
1. **绝对真实**:
   - `<world_state>` 和 `<observation>` 中的数据是**绝对的物理现实**。
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

5. **规则裁决**:
   - 你不是规则书，不要凭空猜测复杂的规则。
   - **当发生以下情况时，必须调用 `consult_rulebook` 工具：**
     1. 玩家要求 **"孤注一掷" (Push the roll)**。
     2. 进入 **战斗 (Combat)** 或 **追逐 (Chase)**。
     3. 玩家遭受 **理智 (SAN)** 损失，需要查询疯狂症状表。
     4. 使用 **魔法** 或 **怪异科技**。
   - 获取规则建议后，再结合剧情进行描述。

6. **内容安全限制**:
   - **严禁生成以下内容**：
     * 种族、性别歧视或其他歧视性观点
     * 针对儿童的犯罪暴力的耸人听闻或令人厌恶的描绘
     * 性暴力或过度血腥的明确描写
     * 宣扬仇恨或极端主义意识形态的内容
   - **恐怖的界限**：
     * ✅ 允许：宇宙恐怖、心理惊悚、超自然威胁、成年角色面临危险
     * ❌ 禁止：虐待儿童、性侵犯、种族仇恨言论、极端暴力细节
   - **当遇到违规请求时**：
     * 礼貌地拒绝："抱歉，这类内容超出了我作为 KP 的职责范围。让我们换个方向继续故事。"
     * 提供替代方案，保持叙事流畅性。
   - **记住**：洛夫克拉夫特式恐怖的核心在于**未知的恐惧**和**宇宙冷漠**，而非明确的暴力或仇恨。
"""

    # ------------------------------------------------------------------
    # 2. 上下文层 (Context Layers) - 清晰隔离左右脑
    # ------------------------------------------------------------------
    
    # 左脑：结构化客观事实
    STATE_SECTION = """
<world_state>
    <time_and_beat>时间: {time_slot} | 节拍数: {beat_counter}</time_and_beat>
    <location_data>
{location_stat}

    *重要提示*: 
    请仔细检查上述 `interactables` (物品) 和 `entities` (实体) 中的 `tags` 列表。
    - 如果带有 `"hidden"`、`"secret"` 或 `"locked"` 等标签，**严禁**直接告诉玩家它们的存在，除非玩家主动执行了调查动作。
    - 对于非隐藏的、带有线索的可互动物品，可以在环境描写中自然地暗示其存在感（例如反光、异味、突兀的轮廓），但不要像游戏列表一样罗列。
    </location_data>
    <player_status>
        调查员: {player_name}
        状态: {player_condition}
    </player_status>
    <active_tags>{active_global_tags}</active_tags>
</world_state>
"""

    # 右脑：非结构化记忆与知识
    MEMORY_SECTION = """
<knowledge_base>
    <lore>
{semantic_memory}
    </lore>
    
    <story_context>
{episodic_memory}
    </story_context>

    <secret_notes>
{keeper_secrets}
    </secret_notes>
</knowledge_base>
"""

    # ------------------------------------------------------------------
    # 3. 行动层 (Action Layer) - 历史与观察
    # ------------------------------------------------------------------
    
    HISTORY_SECTION = """
<chat_history>
{history_str}
</chat_history>
"""

    # 工具执行结果：这是 ReAct 循环中的 "Observation"
    TOOL_RESULT_SECTION = """
<observation>
基于玩家的上一步意图，系统执行了工具。
以下数据是**刚发生的客观事实** (JSON 格式):

{tool_outputs_json}

(如果结果显示 "failure" 或 "empty"，请务必在叙述中体现这种挫折感)
</observation>
"""

    # ------------------------------------------------------------------
    # 4. 指令层 (Instruction Layer) - 强制 CoT 与 模式指导
    # ------------------------------------------------------------------
    
    FINAL_INSTRUCTION = """
<task>
请分析 `<observation>` (如果有) 和 `<world_state>`，然后生成对玩家的回应。

当前场景模式: **{mode_name}**
模式指导: {mode_guidance}

**必须执行的思考步骤 (Thinking Process)**:
在生成叙事之前，你必须先在 `<thinking>` 标签中进行逻辑推演：
1. **事实核对**: 工具执行成功了吗？如果不成功，怎么描述这种阻碍？
2. **冲突检测**: `<world_state>` (事实) 和 `<knowledge_base>` (传说/记忆) 有冲突吗？如果有，以事实为准，可以把传说描述为“错误的传言”。
3. **氛围定调**: 这是一个安全的探索时刻，还是紧迫的战斗时刻？
4. **秘密判定**: 玩家的行为是否触发了 `<secret_notes>` 中的线索？

**输出格式要求**:
请严格按照以下格式输出：

<thinking>
在此处写下你的推理过程...
1. 工具结果分析: ...
2. 状态检查: ...
3. 叙事策略: ...
</thinking>

<narrative>
在此处输出给玩家的最终剧情描述...
</narrative>
</task>
"""

    # 针对中文语境优化的模式指导
    MODE_GUIDANCE = {
        SceneMode.EXPLORATION: "重点描写环境氛围（光影、声音、气味）。节奏缓慢，建立悬疑感。",
        SceneMode.COMBAT: "节奏紧凑。短句为主。重点描写动作的后果和肉体的痛楚。",
        SceneMode.DIALOGUE: "通过语言展现 NPC 的性格。不要过度热心，NPC 应该有自己的动机和隐瞒。",
        SceneMode.INVESTIGATION: "强调细节。根据 `<observation>` 中的数据，精确地描述线索，但不要直接给出结论，让玩家自己推理。"
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
        active_tags = game_state.get("active_global_tags", [])
        if "combat" in active_tags or "danger" in active_tags:
            return SceneMode.COMBAT
        
        # 默认为探索模式
        return SceneMode.EXPLORATION

    @staticmethod
    def _format_dict_to_yaml(data: Any, indent: int = 4) -> str:
        """辅助函数：将字典格式化为对 LLM 友好的伪 YAML 字符串"""
        if isinstance(data, str):
            try:
                parsed = json.loads(data)
                return json.dumps(parsed, ensure_ascii=False, indent=2)
            except:
                return " " * indent + data
        return json.dumps(data, ensure_ascii=False, indent=2)

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
        
        # 1. 默认模式处理
        if scene_mode is None:
            scene_mode = cls._detect_scene_mode(user_input, game_state)
        
        parts = []

        # 2. 组装 Header
        parts.append(cls.SYSTEM_HEADER.format(player_name=player_name))

        # 3. 组装左脑 (State)
        loc_data = game_state.get("location_stat", {})
        parts.append(cls.STATE_SECTION.format(
            time_slot=game_state.get("time_slot", "未知"),
            beat_counter=game_state.get("beat_counter", 0),
            location_stat=cls._format_dict_to_yaml(loc_data),
            player_name=player_name,
            player_condition=str(game_state.get("player_condition", "健康")), 
            active_global_tags=", ".join(game_state.get("active_global_tags", []))
        ))

        # 4. 组装右脑 (Memory)
        parts.append(cls.MEMORY_SECTION.format(
            semantic_memory=rag_context.get("semantic", "暂无相关传说。"),
            episodic_memory=rag_context.get("episodic", "暂无近期记忆。"),
            keeper_secrets=rag_context.get("keeper_notes", "此处无特殊秘密。")
        ))

        # 5. 组装历史
        parts.append(cls.HISTORY_SECTION.format(
            history_str=history_str if history_str else "[会话开始]"
        ))

        # 6. 组装工具结果 (Observation)
        if tool_results:
            formatted_tools = json.dumps(tool_results, ensure_ascii=False, indent=2)
            parts.append(cls.TOOL_RESULT_SECTION.format(tool_outputs_json=formatted_tools))

        # 7. 组装最终指令
        parts.append(cls.FINAL_INSTRUCTION.format(
            mode_name=scene_mode.value,
            mode_guidance=cls.MODE_GUIDANCE[scene_mode]
        ))

        return "\n".join(parts)

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
                "location_stat": {"description": current_location},
                "time_slot": "Unknown",
                "active_global_tags": [],
                "beat_counter": 0,
                "player_condition": "Unknown"
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
