# InkOctoBot 全功能详细文档

> 本文档记录 InkOctoBot 所有功能的实现方式，包括文件 pipeline、每个文件的输入输出（IO）、每个功能的实现逻辑。

---

## 目录

1. [项目总览](#一项目总览)
2. [系统架构 Pipeline](#二系统架构-pipeline总流程)
3. [章节生成 Pipeline（核心功能）](#功能-1章节生成-pipeline核心功能)
4. [Agent-Skill 框架](#功能-2agent-skill-框架)
5. [四层记忆系统（RAG Memory）](#功能-3四层记忆系统rag-memory)
6. [约束系统](#功能-4约束系统constraint-system)
7. [Model Provider 层](#功能-5model-provider-层llm-调用)
8. [数据库系统](#功能-6数据库系统)
9. [评估子系统](#功能-7评估子系统)
10. [市场分析子系统](#功能-8分析子系统市场分析)
11. [预处理子系统](#功能-9预处理子系统)
12. [安全子系统](#功能-10安全子系统)
13. [事件系统](#功能-11事件系统)
14. [学习系统](#功能-12学习系统)
15. [Web UI API 路由表](#四web-ui-api-完整路由表)
16. [CLI 命令表](#五cli-命令完整表)
17. [配置文件一览](#六配置文件一览)
18. [前端页面结构](#七前端页面结构)

---

## 一、项目总览

InkOctoBot 是一个 **AI 驱动的网文（Web Novel）创作系统**，采用"电影制片"隐喻：
- **用户 = 导演 + 编剧**
- **AI = 演员 + 编辑 + 摄影 + 评审团队**

### 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | React 18 + TypeScript + TailwindCSS + TipTap 编辑器 |
| 后端 | FastAPI + Uvicorn + WebSocket |
| 桌面 | PyWebView (跨平台) |
| 存储 | SQLite (应用 + 市场数据) + ChromaDB (向量) + YAML/JSON |
| LLM | OpenAI, Anthropic, DeepSeek, Gemini, Ollama, vLLM, LoRA |
| NLP | jieba, SnowNLP, text2vec-large-chinese |
| 分析 | Pandas, NumPy, scikit-learn, Matplotlib |
| CLI | Typer |
| 测试 | pytest + pytest-asyncio |

### 入口文件

| 文件 | 作用 |
|------|------|
| `cli.py` | Typer CLI 入口，提供 `ink` 命令组 |
| `launcher.py` | PyWebView 桌面应用启动器，启动 FastAPI (port 8713) |
| `main.py` | 旧版爬虫集成入口 |
| `config.py` | 配置加载器，读取 YAML 文件 |
| `ui/backend/app/main.py` | FastAPI Web 后端主入口 |

---

## 二、系统架构 Pipeline（总流程）

```
用户输入（大纲/指令）
    ↓
┌─────────────────────────────────┐
│  入口层 (CLI / Web UI / 桌面)     │
│  cli.py / ui/backend/app/main.py│
└──────────┬──────────────────────┘
           ↓
┌─────────────────────────────────┐
│  API 路由层 (FastAPI Routers)    │
│  generation_api / planner_api   │
│  editor_api / eval_api 等       │
└──────────┬──────────────────────┘
           ↓
┌─────────────────────────────────┐
│  Agent 编排层                    │
│  SceneDirector → SceneSimulator │
│  → EditorWriter → Evaluator     │
└──────────┬──────────────────────┘
           ↓
┌─────────────────────────────────┐
│  Skill 执行层                    │
│  BaseSkill.execute()            │
│  input → build_prompt → LLM    │
│  → parse_output → output       │
└──────────┬──────────────────────┘
           ↓
┌─────────────────────────────────┐
│  Model Provider 层              │
│  ModelRouter → Provider         │
│  (OpenAI/Anthropic/Ollama/...) │
└──────────┬──────────────────────┘
           ↓
┌─────────────────────────────────┐
│  数据层                          │
│  SQLite + ChromaDB + YAML       │
│  RAG Memory System              │
└─────────────────────────────────┘
```

---

## 三、核心功能模块详解

### 功能 1：章节生成 Pipeline（核心功能）

**触发方式**：`POST /api/generation/start` 或 `ink generate chapter`

**Pipeline 四步流程**：

```
SceneDirector → SceneSimulator → EditorWriter → Evaluator
  (规划场景)    (多角色演出)      (编辑整合)     (质量评估)
```

---

#### Step 1: SceneDirector —— 场景规划

- **文件**: `agents/production/scene_director.py`
- **Skill**: `agents/production/skills/scene_direct/skill.py`
- **Prompt 模板**: `config/prompts/scene_director.yaml`

**输入 (Input)**:

| 参数 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `chapter_outline` | `str` | 用户大纲 | 章节大纲文本 |
| `chapter_num` | `int` | 用户指定 | 章节号 |
| `memory_context` | `dict` | MemoryManager | 从 RAG 四层加载的记忆上下文 |
| `world_rules` | `list[str]` | WorldBook DB | 世界观规则列表 |
| `character_cards` | `list[dict]` | CharacterCards DB | 参与角色的卡片数据 |
| `constraints` | `list` | ConstraintStore | 约束条件列表 |
| `unresolved_foreshadowing` | `list` | EpisodicTimeline | 未解决的伏笔列表 |

**处理逻辑**:
1. 加载 `config/prompts/scene_director.yaml` 中的 system prompt
2. 组装 LLM 消息：system prompt + 世界规则 + 角色卡 + 大纲 + 记忆上下文
3. 调用 `ModelRouter.generate()`（role=`scene_director`, temperature=0.6, max_tokens=4000）
4. 解析 JSON 输出为场景计划

**输出 (Output)**:

```json
{
  "scenes": [
    {
      "scene_index": 1,
      "location": "地点描述",
      "time": "时间描述",
      "characters": ["角色A", "角色B"],
      "character_instructions": {
        "角色A": {
          "emotional_state": "当前情绪",
          "secret_goal": "隐藏目标",
          "knowledge_boundary": "知识边界描述"
        }
      },
      "beats": ["节拍1: ...", "节拍2: ..."],
      "narrator_instructions": "旁白风格指导",
      "pov": "third_limited"
    }
  ],
  "chapter_arc": "章节弧线描述"
}
```

**降级策略**: 如果 LLM 调用失败，生成最小默认 scene plan（1个场景包含所有角色）

---

#### Step 2: SceneSimulator —— 场景模拟（多角色演出）

- **文件**: `agents/production/scene_simulator.py`
- **子 Agent**:
  - `agents/production/actor_agent.py` — 角色表演
  - `agents/production/narrator_agent.py` — 旁白叙述

**输入 (Input)**:

| 参数 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `scene_plan` | `dict` | Step 1 输出 | 场景规划结果 |
| `character_cards` | `list[dict]` | CharacterCards DB | 角色卡片（A层定性+B层决策模型） |
| `world_book_entries` | `list` | WorldBook DB | 世界书条目 |
| `memory_context` | `dict` | MemoryManager | 记忆上下文 |

**处理逻辑**:
1. 为每个场景中的每个角色创建 `ActorAgent` 实例
2. 通过 **Knowledge Isolation Engine** (`rag/memory/knowledge_isolation.py`) 过滤每个角色的知识视图
3. 通过 **Decision Engine** (`rag/decision_engine.py`) 获取角色行为的量化指导
4. 模式选择：`parallel`（所有演员同时演出）或 `turn_based`（轮流演出）
5. 调用每个 `ActorAgent.perform()`
6. 调用 `NarratorAgent.narrate()` 生成旁白
7. 合并所有演出记录

**ActorAgent.perform() 子流程**:

| 项目 | 详情 |
|------|------|
| **文件** | `agents/production/actor_agent.py` |
| **Skill** | `agents/production/skills/actor_perform/skill.py` |
| **Prompt** | `config/prompts/actor_agent.yaml` |
| **输入** | character_card, scene_plan, knowledge_view, constraints, decision_guidance |
| **输出** | 半结构化表演记录（对话 + 动作 + 内心独白） |
| **参数** | temperature=0.8, max_tokens=3000 |

**NarratorAgent.narrate() 子流程**:

| 项目 | 详情 |
|------|------|
| **文件** | `agents/production/narrator_agent.py` |
| **Prompt** | `config/prompts/narrator_agent.yaml` |
| **输入** | scene_plan, narrator_instructions, style_profile |
| **输出** | 场景描写文本（环境、氛围、叙述性描写） |

**输出 (Output)**:

```json
{
  "performances": {
    "角色A": "角色A的表演文本（对话+动作+内心）...",
    "角色B": "角色B的表演文本..."
  },
  "narrator": "旁白叙述文本...",
  "combined": "合并的场景文本..."
}
```

---

#### Step 3: EditorWriter —— 编辑整合

- **文件**: `agents/production/editor_writer.py`
- **Skill**: `agents/production/skills/editor_write/skill.py`
- **Prompt 模板**: `config/prompts/editor_writer.yaml`

**输入 (Input)**:

| 参数 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `performance_records` | `dict` | Step 2 输出 | 所有角色的表演记录 |
| `narrator_text` | `str` | Step 2 输出 | 旁白文本 |
| `narrative_instructions` | `dict` | SceneDirector | POV、节奏、情感弧线指令 |
| `style_requirements` | `dict` | 风格配置 | 文风要求（来自 `config/style_profiles/`） |
| `user_preferences` | `dict` | EditAnalyzer | 从用户编辑历史学习到的偏好 |
| `memory_context` | `dict` | MemoryManager | 记忆上下文 |

**处理逻辑**:
1. 合并所有表演记录和旁白
2. 注入用户风格偏好（从 `agents/evaluation/edit_analyzer.py` 学习的历史）
3. 注入参考作品风格特征（从 `preprocessing/style_extractor.py` 提取）
4. 组装 system prompt + 所有素材
5. 调用 LLM（temperature=0.7, max_tokens=8000）
6. 输出最终章节正文

**输出 (Output)**: `str` — 最终打磨的章节文本（可直接发布的完整章节）

---

#### Step 4: Evaluator —— 质量评估

- **文件**: `agents/evaluation/evaluator.py`

**输入 (Input)**:

| 参数 | 类型 | 来源 | 说明 |
|------|------|------|------|
| `chapter_text` | `str` | Step 3 输出 | 待评估的章节文本 |
| `chapter_num` | `int` | 用户指定 | 章节号 |
| `scene_plan` | `dict` | Step 1 输出 | 用于验证场景执行情况 |
| `character_cards` | `list[dict]` | DB | 角色卡片 |
| `world_rules` | `list[str]` | DB | 世界规则 |
| `constraints` | `list` | ConstraintStore | 约束列表 |

**处理逻辑**（调用多个子评估器）:
1. **约束满足度检查** — 对照约束列表逐条验证
2. **角色一致性检查** — 角色行为是否符合角色卡设定
3. **知识隔离检查** — 角色是否泄露了不该知道的信息
4. **重复检测** (`agents/evaluation/repetition_detector.py`) — 检测重复短语/表达
5. **AI 味检测** (`agents/evaluation/slop_detector.py`) — 基于 `config/slop_patterns.json` 检测
6. **伏笔追踪** — 检查伏笔的铺设和回收情况
7. **风格漂移检测** (`agents/evaluation/style_drift_detector.py`) — 对比参考风格

**输出 (Output)**:

```json
{
  "passed": true,
  "score": 0.85,
  "issues": [
    {
      "type": "repetition",
      "severity": "warning",
      "detail": "「目光」在500字内出现了4次",
      "location": "paragraph_3"
    },
    {
      "type": "slop",
      "severity": "info",
      "detail": "「不禁」为AI常见表达",
      "location": "paragraph_7"
    }
  ]
}
```

**评估参数**: temperature=0.3, max_tokens=4000（低 temperature 保证评估一致性）

---

#### 生成完成后钩子

- **触发**: Pipeline 四步全部完成后
- **函数**: `_run_chapter_complete_hook()`
- **逻辑**:
  1. 生成章节摘要 → 存入 L2 Chapter Buffer
  2. 提取关键事件 → 存入 L4 Episodic Timeline
  3. 跟踪角色状态变化 → 更新 L3 Semantic Memory
  4. 更新伏笔状态（新增/回收） → 更新 L4

---

### 功能 2：Agent-Skill 框架

#### BaseAgent（Agent 基类）

- **文件**: `agents/base_agent.py`
- **输入**: prompt 模板路径 + 上下文数据字典
- **输出**: LLM 响应（结构化 JSON 或自由文本）

**核心方法**:

| 方法 | 功能 | 输入 | 输出 |
|------|------|------|------|
| `build_messages()` | 组装 LLM 消息列表 | 上下文数据 | `list[dict]` (role/content) |
| `generate()` | 调用 ModelRouter | messages, params | LLM 响应文本 |
| `parse_response()` | 解析输出格式 | raw response | 结构化数据 |

**集成点**:
- `core/event_bus.py` — 事件发布（生成开始/完成/失败）
- `core/skill_registry.py` — 技能查找和调用
- `agents/model_router.py` — LLM 调用路由

---

#### BaseSkill（Skill 基类）

- **文件**: `agents/base_skill.py`

**Skill 生命周期**:
```
input → validate_input → build_prompt → LLM call → parse_output → validate_output → output
```

**SkillMeta 元数据结构**:

```python
@dataclass
class SkillMeta:
    name: str                  # 技能唯一标识
    display_name: str          # 显示名称
    description: str           # 功能描述
    version: str               # 版本号
    input_schema: dict         # 输入 JSON Schema
    output_schema: dict        # 输出 JSON Schema
    model_role: str            # 对应的模型角色（用于 ModelRouter）
    temperature: float         # 默认温度
    max_tokens: int            # 默认最大 token 数
    tags: list[str]            # 分类标签
    learnable: bool            # 是否支持学习优化
    permissions: list[str]     # 所需权限
```

**每个 Skill 目录结构**:
```
agents/<domain>/skills/<skill_name>/
├── SKILL.md          # 元数据文件（YAML frontmatter + 描述）
└── skill.py          # 实现文件（继承 BaseSkill）
```

---

#### SkillRegistry（技能注册中心）

- **文件**: `core/skill_registry.py`

| 方法 | 功能 | 输入 | 输出 |
|------|------|------|------|
| `scan_directory()` | 扫描 agents/ 目录加载所有 SKILL.md + skill.py | 目录路径 | 注册的技能数量 |
| `register(skill)` | 注册单个技能 | BaseSkill 实例 | None |
| `get(name)` | 按名称获取技能 | 技能名 | BaseSkill 实例 |
| `find_by_tags(tags)` | 按标签搜索 | 标签列表 | 技能列表 |
| `list_all()` | 列出所有技能 | - | 技能列表 |
| `watch_learned_skills()` | 监控 learned_skills/ 目录热重载 | - | None |

---

#### 已注册 Skills 完整清单

| 域 | Skill 名称 | 文件路径 | 功能描述 |
|----|-----------|---------|----------|
| **生产** | `scene_direct` | `agents/production/skills/scene_direct/` | 将大纲拆分为场景计划 |
| **生产** | `actor_perform` | `agents/production/skills/actor_perform/` | 角色沉浸式表演 |
| **生产** | `editor_write` | `agents/production/skills/editor_write/` | 合并素材编写最终章节 |
| **评估** | `consistency_check` | `agents/evaluation/skills/consistency_check/` | 设定一致性检查 |
| **评估** | `quality_score` | `agents/evaluation/skills/quality_score/` | 文本质量评分 |
| **评估** | `repetition_detect` | `agents/evaluation/skills/repetition_detect/` | 重复表达检测 |
| **评估** | `slop_detect` | `agents/evaluation/skills/slop_detect/` | AI味/套路表达检测 |
| **评估** | `style_drift_detect` | `agents/evaluation/skills/style_drift_detect/` | 风格漂移检测 |
| **分析** | `character_profile` | `agents/reference_extractors/skills/character_profile/` | 从文本提取角色画像 |
| **分析** | `narrative_extract` | `agents/reference_extractors/skills/narrative_extract/` | 叙事结构提取 |
| **分析** | `rhetoric_classify` | `agents/reference_extractors/skills/rhetoric_classify/` | 修辞手法分类 |
| **分析** | `shuangdian_extract` | `agents/reference_extractors/skills/shuangdian_extract/` | 爽点/嗨点提取 |
| **分析** | `style_extract` | `agents/reference_extractors/skills/style_extract/` | 写作风格指纹提取 |
| **规划** | `calibration` | `agents/planner/skills/calibration/` | 参考作品风格校准 |
| **规划** | `constraint_disambiguate` | `agents/planner/skills/constraint_disambiguate/` | 约束冲突消歧 |
| **规划** | `marketing_advice` | `agents/planner/skills/marketing_advice/` | 市场定位建议 |

---

### 功能 3：四层记忆系统（RAG Memory）

**文件目录**: `rag/memory/`

```
┌───────────────────────────────────────────────────────────┐
│ L1: Immediate Context (4-8K tokens)                       │
│ 文件: rag/memory/immediate.py                             │
│ 存储: LLM 上下文窗口                                       │
│ 内容: 当前场景 + 上一场景的完整文本                           │
│ 用途: 直接工作素材，确保场景间连贯性                          │
├───────────────────────────────────────────────────────────┤
│ L2: Chapter Buffer (2-4K tokens)                          │
│ 文件: rag/memory/chapter_buffer.py                        │
│ 存储: LLM 上下文窗口                                       │
│ 内容: 最近 5-10 章的压缩摘要                                │
│ 用途: 中期连续性，角色弧线跟踪                               │
├───────────────────────────────────────────────────────────┤
│ L3: Semantic Memory (无限)                                 │
│ 文件: rag/memory/semantic_store.py                         │
│ 存储: ChromaDB 向量数据库                                   │
│ 内容: 全部设定、角色状态、事件、世界书条目                     │
│ 用途: 语义相似度检索（余弦相似度）                            │
├───────────────────────────────────────────────────────────┤
│ L4: Episodic Timeline (无限)                               │
│ 文件: rag/memory/episodic_timeline.py                      │
│ 存储: SQLite 结构化数据库                                   │
│ 内容: 关键事件、因果链、时间轴、伏笔状态                      │
│ 用途: 精确结构化查询（时间/因果/角色维度）                     │
└───────────────────────────────────────────────────────────┘
```

#### Memory Manager（记忆管理器）

- **文件**: `rag/memory/manager.py`

| 方法 | 输入 | 输出 | 说明 |
|------|------|------|------|
| `load_context()` | project_id, chapter_num, query_keywords | 组装好的上下文字符串 | 从四层加载并合并 |
| `update_after_chapter()` | project_id, chapter_num, chapter_text | None | 章节完成后更新记忆 |
| `consolidate()` | project_id | 压缩统计 | 手动触发记忆压缩 |

**自动压缩逻辑**:
- L2 token 预算超出时 → 最旧的摘要压缩并转存到 L3（向量化）和 L4（事件提取）
- 由 `rag/memory/consolidator.py` 执行

#### Memory Consolidator（记忆压缩器）

- **文件**: `rag/memory/consolidator.py`
- **输入**: 超出预算的 L2 摘要
- **处理**: 调用 LLM 提取关键信息 → 向量化存入 ChromaDB + 事件存入 SQLite
- **输出**: 压缩后的精简摘要

#### Knowledge Isolation Engine（知识隔离引擎）

- **文件**: `rag/memory/knowledge_isolation.py`
- **功能**: 确保 ActorAgent 只知道角色应该知道的信息

**数据模型**:
- **数据表**: `information_events`（谁在什么时候知道了什么）
- **三种知识状态**:
  - `known_true` — 已知真实信息
  - `known_false` — 已知但错误的信息（误解、被欺骗）
  - `unknown` — 明确不知道的信息

| 方法 | 输入 | 输出 | 说明 |
|------|------|------|------|
| `get_character_view()` | character_name, chapter_num | 知识视图字典 | 该角色当前应知道的所有信息 |
| `record_information_event()` | character, info_id, status, chapter | None | 记录信息传递事件 |
| `get_explicitly_unknown()` | character_name, chapter_num | 不可知列表 | 生成注入 prompt 的"你不知道"列表 |

---

### 功能 4：约束系统（Constraint System）

**文件目录**: `constraints/`、`agents/constraints/`

#### 五级优先级

| 优先级 | 类型 | 说明 | 示例 |
|--------|------|------|------|
| 1 (最高) | **Hard rules** | 硬性规则，绝不可违反 | "主角不能死" |
| 2 | **Knowledge isolation** | 知识隔离 | "角色A不知道角色B的身世" |
| 3 | **Plot constraints** | 情节约束 | "本章必须揭示XX真相" |
| 4 | **Narrative constraints** | 叙事约束 | "使用第三人称限制视角" |
| 5 (最低) | **Style constraints** | 风格约束 | "对话占比不超过40%" |

#### 约束执行三阶段

**生成前 (Pre-generation)**:
- **正面重构 (Positive Reframing)**: 将"不要做X"转为"要做Y"，提高 LLM 遵守率
- **消歧器 (Disambiguator)**: `agents/planner/skills/constraint_disambiguate/` — 检测并解决约束间冲突
- **好例/坏例**: 为每条约束提供正面和反面示例

**生成中 (During)**:
- ChromaDB 语义检索：实时匹配相关约束注入上下文

**生成后 (Post)**:
- Evaluator 违规扫描：逐条检查约束满足情况

#### 核心文件

| 文件 | 功能 | 输入 | 输出 |
|------|------|------|------|
| `constraints/assembler.py` | 约束组装 | 约束列表 + 优先级 | 格式化的 prompt 片段 |
| `constraints/store.py` | 约束持久化 | 项目ID | 约束 CRUD 操作 |
| `constraints/violation_detector.py` | 违规检测 | 文本 + 约束列表 | 违规报告 |
| `rag/constraint_store.py` | 约束语义索引 | 约束文本 | ChromaDB 存储/检索 |

---

### 功能 5：Model Provider 层（LLM 调用）

#### ModelRouter（模型路由器）

- **文件**: `models/router.py`（核心实现）、`agents/model_router.py`（Agent 层封装）

**路由逻辑**:

```
ModelRouter.generate(role, messages, temperature, max_tokens)
    ↓
1. 查找 config/models.yaml 中该 role 的配置
    ↓
2. 确定 provider + model_name
    ↓
3. 实例化/获取 Provider
    ↓
4. 调用 Provider.generate(messages, temperature, max_tokens)
    ↓
5. 返回响应文本
```

| 方法 | 输入 | 输出 |
|------|------|------|
| `generate()` | role, messages, temperature, max_tokens | 响应文本 |
| `generate_stream()` | role, messages, temperature, max_tokens | AsyncGenerator[str] |
| `get_provider()` | provider_name | BaseLLMProvider 实例 |

**降级策略**: 配置文件 → enabled providers 列表 → 自动检测本地 Ollama

---

#### Provider 实现

| Provider | 文件 | 支持模型 | 特点 |
|----------|------|---------|------|
| OpenAI | `models/openai_provider.py` | GPT-4o, GPT-4, GPT-3.5 | 标准 OpenAI API |
| Anthropic | `models/anthropic_provider.py` | Claude 4.6, Claude 4.5, Haiku | Messages API |
| DeepSeek | `models/deepseek_provider.py` | DeepSeek-V3, DeepSeek-R1 | OpenAI 兼容 API |
| Gemini | `models/gemini_provider.py` | Gemini Pro, Gemini Ultra | Google AI API |
| Ollama | `models/ollama_provider.py` | 本地任意模型 | REST API, 自动检测 |
| vLLM | `models/vllm_provider.py` | 自部署模型 | OpenAI 兼容 API |
| LoRA | `models/lora_provider.py` | 微调模型 | LoRA adapter 加载 |

#### BaseLLMProvider（Provider 基类）

- **文件**: `models/base.py`

| 方法 | 输入 | 输出 | 说明 |
|------|------|------|------|
| `generate()` | messages, temperature, max_tokens | str | 同步/异步生成 |
| `generate_stream()` | messages, temperature, max_tokens | AsyncGenerator[str] | 流式生成 |
| `health_check()` | - | bool | 连接健康检查 |
| `list_models()` | - | list[str] | 列出可用模型 |

---

#### Cost Estimator（成本估算器）

- **文件**: `models/cost_estimator.py`（核心）、`agents/cost_estimator.py`（Agent 层）
- **价格表**: `config/model_providers.json`
- **输入**: 消息列表 (messages) + 模型名称
- **输出**: `{ "input_tokens": 1234, "output_tokens": 567, "estimated_cost_usd": 0.05 }`
- **用途**: 生成前弹出成本确认对话框

#### A/B Compare（模型对比）

- **文件**: `models/ab_compare.py`（核心）、`agents/ab_compare.py`（Agent 层）
- **输入**: 同一组 messages + 两个不同模型配置
- **输出**: 两个模型的并行生成结果 + 质量评分对比
- **前端组件**: `ui/frontend/src/components/editor/ModelCompare.tsx`

---

### 功能 6：数据库系统

#### 三库架构

```
┌─────────────────────────────────────────────────┐
│  App DB: data/novels.db                         │
│  Schema: database/db_schema.py                  │
│          database/creation_schema.py             │
│  Handler: database/db_handler.py                │
│  用途: 项目管理、角色、世界书、章节、版本控制       │
├─────────────────────────────────────────────────┤
│  Crawler DB: InkOctoBot_Crawler.db              │
│  用途: 市场数据、排行榜、竞品分析（只读）          │
├─────────────────────────────────────────────────┤
│  Reference DB: data/references.db               │
│  Schema: database/reference_schema.py           │
│  用途: 参考作品存储、风格指纹提取                  │
├─────────────────────────────────────────────────┤
│  Vector DB: ChromaDB                            │
│  Handler: rag/vector_store.py                   │
│  用途: 语义记忆（L3层）                           │
└─────────────────────────────────────────────────┘
```

#### App DB 主要表

| 表名 | 主要字段 | 用途 |
|------|---------|------|
| `projects` | id, name, genre, status, created_at | 项目管理 |
| `characters` | id, project_id, name, card_data(JSON), decision_model(JSON) | 角色卡片 |
| `worldbook_entries` | id, project_id, keyword, content, category | 世界观设定 |
| `chapters` | id, project_id, volume_num, chapter_num, title, content | 章节内容 |
| `versions` | id, chapter_id, content, created_at, source | 版本历史 |
| `settings` | key, value | 系统设置 |
| `information_events` | id, character, info_id, status, chapter_num | 知识隔离追踪 |
| `memory_summaries` | id, project_id, chapter_num, summary | L2 章节摘要 |
| `episodic_events` | id, project_id, event_type, data(JSON), chapter_num | L4 事件时间轴 |

#### Crawler DB 主要表

| 表名 | 主要字段 | 用途 |
|------|---------|------|
| `novels` | uid, title, author, word_count, status | 小说基本信息 |
| `novel_titles` | id, novel_uid, platform_title | 平台标题 |
| `tags` | id, name | 标签 |
| `novel_tag_map` | novel_uid, tag_id | 小说-标签关联 |
| `rank_lists` | id, platform, list_name, category | 排行榜定义 |
| `rank_snapshots` | id, list_id, snapshot_date | 排行榜快照 |
| `rank_entries` | id, snapshot_id, novel_uid, rank | 排行数据 |
| `first_n_chapters` | id, novel_uid, chapter_num, content | 前N章内容 |

#### Reference DB 主要表

| 表名 | 主要字段 | 用途 |
|------|---------|------|
| `reference_works` | id, title, type, source_path | 参考作品元数据 |
| `reference_entries` | id, work_id, chunk_text, embedding | 参考作品分块+向量 |

#### 数据库操作层

- **文件**: `database/db_handler.py`
- **连接方式**: SQLite + aiosqlite（异步）
- **接口模式**: 异步上下文管理器

```python
async with get_db() as db:
    result = await db.execute("SELECT ...", params)
```

---

### 功能 7：评估子系统

**文件目录**: `agents/evaluation/`

| 评估器 | 文件 | 输入 | 输出 | 说明 |
|--------|------|------|------|------|
| **Evaluator** | `evaluator.py` | 章节文本 + 约束 + 角色卡 | 综合评估报告 (JSON) | 编排所有子评估器 |
| **RepetitionDetector** | `repetition_detector.py` | 文本 (str) | `[{phrase, count, positions}]` | 检测高频重复词/短语 |
| **SlopDetector** | `slop_detector.py` | 文本 (str) | `[{pattern, matches, severity}]` | 基于模式库检测AI味 |
| **StyleDriftDetector** | `style_drift_detector.py` | 文本 + 参考风格指纹 | `{drift_score, dimensions}` | 计算风格偏离度 |
| **QualityScorer** | `quality_scorer.py` | 文本 (str) | `{score, breakdown}` | 多维质量打分 |
| **ConsistencyChecker** | `consistency_checker.py` | 文本 + 世界规则 + 角色卡 | `[{violation, severity}]` | 设定一致性检查 |
| **CrossChapterChecker** | `cross_chapter_checker.py` | 多章节文本列表 | `[{issue, chapters}]` | 跨章节连续性问题 |
| **EditAnalyzer** | `edit_analyzer.py` | 用户编辑 diff (before/after) | `{patterns, preferences}` | 学习用户风格偏好 |

#### Slop Detection 配置

- **文件**: `config/slop_patterns.json`
- **内容示例**:
```json
{
  "chinese_patterns": [
    {"pattern": "不禁", "severity": "medium", "category": "filler"},
    {"pattern": "竟然", "severity": "low", "category": "exclamation"},
    {"pattern": "一抹微笑", "severity": "high", "category": "cliche"}
  ]
}
```

---

### 功能 8：分析子系统（市场分析）

**文件目录**: `analysis/`

#### 文件 Pipeline

```
analysis/data_access.py          → 从 Crawler DB 读取原始数据
    ↓
analysis/feature_extraction/
├── pipeline.py                  → 特征提取编排
├── narrative_extractor.py       → 叙事结构特征
├── nlp_stats.py                → NLP 统计特征（词频、句长等）
├── embedding_cluster.py         → 向量聚类特征
├── rhetoric_classifier.py       → 修辞手法分类
└── shuangdian_templates.py      → 爽点模板匹配
    ↓
analysis/formula_engine/
├── aggregator.py                → 公式聚合计算
├── constraint_converter.py      → 约束转换
└── presets.py                  → 预设公式
    ↓
analysis/metrics.py              → 指标计算
analysis/heat.py                 → 热度计算
analysis/trend_analyzer.py       → 趋势分析
    ↓
analysis/visualization.py        → Matplotlib/Seaborn 图表生成
analysis/report.py              → 报告生成
```

| 文件 | 输入 | 输出 |
|------|------|------|
| `data_access.py` | Crawler DB 路径 | 原始小说/排行数据 DataFrame |
| `feature_extraction/pipeline.py` | 小说文本列表 | 特征矩阵 |
| `metrics.py` | 排行数据 | 计算指标（收藏率、更新频率等）|
| `heat.py` | 时间序列数据 | 热度分数 |
| `trend_analyzer.py` | 多期特征数据 | 趋势报告 |
| `visualization.py` | 分析结果 | PNG 图表文件 |
| `report.py` | 所有分析结果 | 综合 Markdown 报告 |

**API 触发**: `GET /analysis/run?platform=qidian&lookback=30&top_k=50`

---

### 功能 9：预处理子系统

**文件目录**: `preprocessing/`

```
preprocessing/
├── pipeline.py              → 预处理编排入口
├── chapter_splitter.py       → 章节分割
├── character_profiler.py     → 角色画像提取
├── fragment_selector.py      → 优质片段选择
├── rhythm_analyzer.py        → 节奏分析（句长/段落分布）
├── style_extractor.py        → 风格指纹提取
└── lora/
    ├── data_constructor.py   → LoRA 训练数据构建
    ├── quality_filter.py     → 训练数据质量过滤
    └── trainer.py            → LoRA 微调训练器
```

| 文件 | 输入 | 输出 | 说明 |
|------|------|------|------|
| `pipeline.py` | 原始文本文件 | 处理后的结构化数据 | 编排整个预处理流程 |
| `chapter_splitter.py` | 完整小说文本 | 章节列表 | 自动识别章节边界 |
| `character_profiler.py` | 章节文本列表 | 角色画像字典 | 提取角色特征 |
| `fragment_selector.py` | 章节列表 + 质量标准 | 优质片段列表 | 选择高质量训练片段 |
| `rhythm_analyzer.py` | 文本 | 节奏特征向量 | 分析句长/段落节奏 |
| `style_extractor.py` | 文本 | 风格指纹字典 | 提取风格维度特征 |
| `lora/data_constructor.py` | 优质片段 + 指令 | JSONL 训练数据 | 构建 SFT 训练集 |
| `lora/quality_filter.py` | 训练数据 | 过滤后数据 | 质量过滤 |
| `lora/trainer.py` | 训练数据 + 基座模型 | LoRA adapter | 微调训练 |

---

### 功能 10：安全子系统

**文件目录**: `security/`

| 文件 | 功能 | 输入 | 输出 |
|------|------|------|------|
| `api_key_manager.py` | API 密钥管理 | provider_name, api_key | 加密存储/读取 |
| `data_isolation.py` | 数据隔离 | project_id | 隔离的数据访问上下文 |

**API Key 加密方式**: keyring（系统密钥链）+ Fernet 对称加密（备选）

---

### 功能 11：事件系统

**文件**: `core/event_bus.py`、`core/event_types.py`

**模式**: 发布/订阅 (Pub/Sub) + 异步

| 事件类型 | 触发时机 | 携带数据 |
|---------|---------|---------|
| `GENERATION_STARTED` | Pipeline 启动 | session_id, project_id |
| `GENERATION_STEP_COMPLETED` | 每步完成 | step_name, result_summary |
| `EVALUATION_COMPLETED` | 评估完成 | score, issues |
| `SKILL_EXECUTED` | Skill 执行完成 | skill_name, execution_time |
| `CHAPTER_COMPLETE` | 章节生成完成 | chapter_num, text_length |
| `MEMORY_UPDATED` | 记忆更新 | layer, operation |
| `CONSTRAINT_VIOLATION` | 约束违反 | constraint_id, severity |

**用途**:
- WebSocket 实时推送 → 前端 UI 更新
- Agent 间协调（松耦合通信）
- 日志记录

---

### 功能 12：学习系统

**文件**: `core/skill_learner.py`

**学习 Pipeline**:

```
用户在编辑器修改AI生成的文本
    ↓
EditAnalyzer 计算 diff（before/after）
    ↓
提取修改模式:
  - 删除的表达 → "用户不喜欢这类表达"
  - 添加的表达 → "用户偏好这类表达"
  - 句式变换 → "用户偏好的句式"
    ↓
存储为 user_preferences（JSON）
    ↓
下次生成时注入 EditorWriter 的 system prompt
```

**Triggers 系统**: `core/triggers.py`
- 定义技能执行的触发条件
- 支持事件驱动的自动技能执行

---

## 四、Web UI API 完整路由表

### 项目管理 (`ui/backend/app/routers/project_api.py`)

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| GET | `/projects/` | - | 项目列表 |
| POST | `/projects/` | `{name, genre, description}` | 新项目 |
| GET | `/projects/{pid}` | - | 项目详情 |
| PUT | `/projects/{pid}` | 更新数据 | 更新后的项目 |
| DELETE | `/projects/{pid}` | - | 删除确认 |

### 角色管理 (`ui/backend/app/routers/characters_api.py`)

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| GET | `/characters/` | `project_id` | 角色列表 |
| POST | `/characters/` | 角色数据（含A层+B层） | 新角色 |
| PUT | `/characters/{cid}` | 更新数据 | 更新后的角色 |
| DELETE | `/characters/{cid}` | - | 删除确认 |

### 世界书 (`ui/backend/app/routers/worldbook_api.py`)

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| GET | `/worldbook/` | `project_id` | 世界书条目列表 |
| POST | `/worldbook/` | 条目数据 | 新条目 |
| PUT | `/worldbook/{eid}` | 更新数据 | 更新后的条目 |
| DELETE | `/worldbook/{eid}` | - | 删除确认 |

### 数据管理 (`ui/backend/app/routers/data_api.py`)

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| GET | `/data/projects` | - | 项目列表 |
| POST | `/data/projects` | `{name, genre}` | 新项目 |
| GET | `/data/characters` | `project_id?` | 角色列表 |
| POST | `/data/characters` | 角色数据 | 新角色 |
| GET | `/data/worldbook` | `project_id?` | 世界书条目 |
| POST | `/data/worldbook` | 条目数据 | 新条目 |
| GET | `/data/editor` | `project_id` | 卷/章数据 |
| PUT | `/data/editor` | 编辑器数据 | 保存确认 |
| GET | `/data/settings` | - | 系统设置 |
| PUT | `/data/settings` | 设置数据 | 保存确认 |
| GET | `/data/chat_history` | `project_id` | 聊天历史 |

### 生成 Pipeline (`ui/backend/app/routers/generation_api.py`)

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| POST | `/api/generation/start` | GenerateRequest | `{status, session_id}` |
| GET | `/api/generation/status/{sid}` | - | `{status, step, waiting, result}` |
| GET | `/api/generation/events/{sid}` | `after` | `{status, events[], total}` |
| POST | `/api/generation/confirm/{sid}` | `{action}` | `{status}` |
| POST | `/api/generation/stop/{sid}` | - | `{status, message}` |
| POST | `/api/generation/scene-plan` | GenerateRequest | `{status, scenes[]}` |
| POST | `/api/generation/rewrite` | RewriteRequest | `{status, rewritten}` |
| POST | `/api/generation/evaluate` | EvalRequest | `{status, evaluation}` |
| POST | `/api/generation/quick-generate` | GenerateRequest | `{text, tokens}` |

### 规划 (`ui/backend/app/routers/planner_api.py`)

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| POST | `/api/planner/volume` | VolumePlanRequest | `{status, volumes[]}` |
| POST | `/api/planner/chapter` | ChapterPlanRequest | `{status, plan}` |

### 评估 (`ui/backend/app/routers/eval_api.py`)

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| POST | `/api/eval/analyze` | EvalTextRequest | `{issues[], score, passed}` |
| POST | `/api/eval/consistency` | ConsistencyCheckRequest | `{violations[], passed}` |

### 模型管理 (`ui/backend/app/routers/model_api.py`)

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| GET | `/api/models/providers` | - | Provider 列表 |
| POST | `/api/models/test` | TestConnectionRequest | `{connected, models[]}` |
| GET | `/api/models/ollama` | `base_url?` | `{models[]}` |
| GET | `/api/models/cost/{pid}` | - | 成本摘要 |

### Skills (`ui/backend/app/routers/skill_api.py`)

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| GET | `/api/skills` | - | `{skills[], total}` |
| GET | `/api/skills/{name}` | - | `{meta, skill_md}` |
| POST | `/api/skills/execute` | SkillExecuteRequest | `{result, execution_time_ms}` |

### 编辑器 (`ui/backend/app/routers/editor_api.py`)

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| GET | `/api/editor/versions/{cid}` | - | 版本列表 |
| POST | `/api/editor/save-version` | SaveVersionRequest | 版本记录 |
| POST | `/api/editor/diff` | `{text_a, text_b}` | `{diff, lines_changed}` |
| POST | `/api/editor/word-count` | WordCountRequest | `{count}` |

### 版本管理 (`ui/backend/app/routers/version_api.py`)

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| GET | `/api/versions/{chapter_id}` | - | 版本历史 |
| POST | `/api/versions/` | 版本数据 | 新版本 |
| POST | `/api/versions/restore/{vid}` | - | 恢复确认 |

### 数据库（爬虫数据）(`ui/backend/app/routers/db_api.py`)

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| GET | `/db/overview` | - | 数据库统计 |
| GET | `/db/top_novels` | - | 排行榜小说 |
| GET | `/db/rank_lists` | - | 排行榜列表 |
| GET | `/db/novel/{uid}` | - | 小说详情+章节 |
| GET | `/db/tag_stats` | - | 标签统计 |

### 参考作品 (`ui/backend/app/routers/reference_api.py`)

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| GET | `/references/works` | - | 参考作品列表 |
| POST | `/references/works` | 作品数据 | 新作品 |
| POST | `/references/works/upload` | 文件上传 | 上传确认 |
| GET | `/references/works/{wid}` | - | 作品详情+条目 |

### 分析 (`ui/backend/app/routers/analysis_api.py`)

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| GET | `/analysis/run` | platform, lookback, top_k | 分析报告 |
| GET | `/analysis/trends` | genre, tag | 趋势数据 |
| GET | `/analysis/tags` | platform | 标签分布 |

### 公式引擎 (`ui/backend/app/routers/formula_api.py`)

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| GET | `/formula/presets` | - | 预设公式列表 |
| POST | `/formula/evaluate` | 公式 + 数据 | 计算结果 |

### 营销建议 (`ui/backend/app/routers/marketing_api.py`)

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| POST | `/api/marketing/advice` | 项目数据 | 营销建议 |

### Prompt 管理 (`ui/backend/app/routers/prompt_api.py`)

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| GET | `/api/prompts` | - | Prompt 模板列表 |
| GET | `/api/prompts/{name}` | - | Prompt 详情 |
| PUT | `/api/prompts/{name}` | 更新内容 | 保存确认 |

### 安全 (`ui/backend/app/routers/security_api.py`)

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| POST | `/api/security/api-key` | provider, key | 保存确认 |
| GET | `/api/security/api-key/{provider}` | - | 密钥状态（不返回明文） |
| DELETE | `/api/security/api-key/{provider}` | - | 删除确认 |

### 设置 (`ui/backend/app/routers/settings_api.py`)

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| GET | `/api/settings` | - | 所有设置 |
| PUT | `/api/settings` | 设置 KV 对 | 保存确认 |

### 配置 (`ui/backend/app/routers/config_api.py`)

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| GET | `/api/config` | - | 当前配置 |
| PUT | `/api/config` | 配置数据 | 保存确认 |
| POST | `/api/config/validate` | 配置数据 | 验证结果 |

### 事件流 (`ui/backend/app/routers/events_api.py`)

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| WebSocket | `/ws/events` | - | 实时事件流 |
| GET | `/api/events/history` | session_id, after | 历史事件 |

### 任务管理 (`ui/backend/app/routers/tasks_api.py`)

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| GET | `/api/tasks` | - | 任务列表 |
| GET | `/api/tasks/{tid}` | - | 任务状态 |

### 报告 (`ui/backend/app/routers/reports_api.py`)

| 方法 | 路径 | 输入 | 输出 |
|------|------|------|------|
| GET | `/api/reports` | project_id | 报告列表 |
| GET | `/api/reports/{rid}` | - | 报告详情 |

---

## 五、CLI 命令完整表

**入口**: `cli.py`（Typer 应用）

| 命令 | 功能 | 输入参数 | 输出 |
|------|------|---------|------|
| `ink project create <name>` | 创建项目 | name, --genre | 项目 ID |
| `ink project list` | 列出项目 | - | 项目表格 |
| `ink project delete <id>` | 删除项目 | id, --force | 确认信息 |
| `ink agent list` | 列出所有 Agent | - | Agent + Skills 表格 |
| `ink skill list` | 列出所有 Skills | - | Skills 表格 |
| `ink skill test <name>` | 测试单个 Skill | name, --input JSON | Skill 输出 |
| `ink skill create <name> <agent>` | 创建 Skill 模板 | 名称, Agent 类型 | 创建的文件路径 |
| `ink generate chapter` | 生成章节 | --project, --chapter, --dry-run | 章节文本 |
| `ink generate evaluate` | 评估章节 | --project, --chapter | 评估报告 |
| `ink analysis trend` | 市场趋势分析 | --genre, --tag, --lookback | 趋势报告 |
| `ink memory status` | 记忆系统状态 | --project | 各层统计信息 |
| `ink memory consolidate` | 强制记忆压缩 | --project | 压缩结果 |
| `ink model list` | 列出配置的模型 | - | 模型列表 |
| `ink model test <provider>` | 测试模型连接 | provider 名称 | 连接状态 |
| `ink config show` | 显示当前配置 | - | 配置内容 |
| `ink config validate` | 验证配置正确性 | - | 验证结果 |
| `ink db info` | 数据库信息 | - | DB 路径和统计 |

---

## 六、配置文件一览

### 核心配置

| 文件 | 路径 | 用途 |
|------|------|------|
| `app_config.yaml` | `config/` | 全局应用设置（端口、日志级别、默认值）|
| `paths.yaml` | `config/` | 数据库和输出目录路径映射 |
| `models.yaml` | `config/` | LLM 模型路由配置（role → provider/model 映射）|
| `model_providers.json` | `config/` | Provider 注册信息 + 各模型价格表 |
| `model_providers.yaml` | `config/` | Provider 配置（YAML 格式版本）|
| `skill_permissions.yaml` | `config/` | Skill 权限控制 |

### 模型预设

| 文件 | 路径 | 用途 |
|------|------|------|
| `balanced.json` | `config/model_presets/` | 平衡预设（质量/速度/成本均衡）|
| `cost_optimal.json` | `config/model_presets/` | 成本优先预设 |
| `quality_first.json` | `config/model_presets/` | 质量优先预设 |

### Prompt 模板

| 文件 | 路径 | 用途 |
|------|------|------|
| `scene_director.yaml` | `config/prompts/` | SceneDirector system prompt |
| `actor_agent.yaml` | `config/prompts/` | ActorAgent system prompt |
| `narrator_agent.yaml` | `config/prompts/` | NarratorAgent system prompt |
| `editor_writer.yaml` | `config/prompts/` | EditorWriter system prompt |
| `story_architect.yaml` | `config/prompts/` | StoryArchitect（大纲规划）prompt |

### 风格与角色模板

| 文件 | 路径 | 用途 |
|------|------|------|
| `concise.yaml` | `config/style_profiles/` | 简洁风格配置 |
| `literary.yaml` | `config/style_profiles/` | 文学风格配置 |
| `protagonist.yaml` | `config/character_templates/` | 主角模板 |
| `antagonist.yaml` | `config/character_templates/` | 反派模板 |
| `supporting.yaml` | `config/character_templates/` | 配角模板 |

### 约束预设

| 文件 | 路径 | 用途 |
|------|------|------|
| `urban.yaml` | `config/constraint_presets/` | 都市类约束预设 |
| `xianxia.yaml` | `config/constraint_presets/` | 仙侠类约束预设 |

### 爬虫与分析配置

| 文件 | 路径 | 用途 |
|------|------|------|
| `websites.yaml` | `config/` | 平台配置（起点、番茄 URL + CSS 选择器）|
| `crawler.yaml` | `config/` | 爬虫并发数、延迟、重试策略 |
| `selenium.yaml` | `config/` | Selenium WebDriver 配置 |
| `antiblock.yaml` | `config/` | 反检测配置（代理、UA 轮换）|
| `antibot.yaml` | `config/` | 反机器人检测策略 |
| `scheduler.yaml` | `config/` | 定时爬取任务配置 |
| `analysis.yaml` | `config/` | 市场分析参数 |

### 评估配置

| 文件 | 路径 | 用途 |
|------|------|------|
| `slop_patterns.json` | `config/` | AI "味道" 检测模式列表 |

---

## 七、前端页面结构

**文件目录**: `ui/frontend/src/`

### 页面组件

| 页面文件 | 路由 | 功能 |
|---------|------|------|
| `DashboardPage.tsx` | `/` | 项目概览、快速入口、最近活动 |
| `ProjectListPage.tsx` | `/projects` | 项目列表 CRUD |
| `ProjectSetupPage.tsx` | `/projects/setup` | 新项目向导 |
| `EditorPage.tsx` | `/editor` | 三栏编辑器（大纲树 + 文本编辑 + AI面板）|
| `CharacterManagerPage.tsx` | `/characters` | 角色卡管理（A层定性 + B层量化决策模型）|
| `WorldBookPage.tsx` | `/worldbook` | 世界观设定管理 |
| `StorylinePage.tsx` | `/storyline` | 故事线/大纲管理 |
| `ReferenceLibraryPage.tsx` | `/references` | 参考作品管理 |
| `AnalysisDashboardPage.tsx` | `/analysis` | 市场分析图表仪表盘 |
| `RankingsPage.tsx` | `/rankings` | 排行榜浏览 |
| `SettingsPage.tsx` | `/settings` | 系统设置（Provider、Pipeline 配置）|
| `SkillsPage.tsx` | `/skills` | 技能浏览和测试 |

### 功能组件

**编辑器组件** (`components/editor/`):

| 组件 | 功能 |
|------|------|
| `TextEditor.tsx` | TipTap 富文本编辑器主体 |
| `ChapterTree.tsx` | 左侧章节/卷目录树 |
| `AIPanel.tsx` | 右侧 AI 功能面板（生成/评估/建议）|
| `AgentChat.tsx` | AI 对话交互组件 |
| `VersionHistory.tsx` | 版本历史浏览/回滚 |
| `ModelCompare.tsx` | A/B 模型输出对比 |
| `EditorAdvice.tsx` | 编辑建议展示 |

**角色组件** (`components/characters/`):

| 组件 | 功能 |
|------|------|
| `CharacterCard.tsx` | 角色卡片展示（A层定性数据）|
| `DecisionModelPanel.tsx` | B层量化决策模型编辑 |
| `RelationshipGraph.tsx` | 角色关系图谱可视化 |

**参考作品组件** (`components/reference/`):

| 组件 | 功能 |
|------|------|
| `ReferenceCard.tsx` | 参考作品卡片 |
| `EntryEditor.tsx` | 参考条目编辑 |
| `NarrativeTimeline.tsx` | 叙事时间线 |
| `StyleRadar.tsx` | 风格雷达图 |

**分析组件** (`components/analysis/`):

| 组件 | 功能 |
|------|------|
| `TrendChart.tsx` | 趋势折线图 |
| `ShuangdianRank.tsx` | 爽点排行展示 |

**记忆组件** (`components/memory/`):

| 组件 | 功能 |
|------|------|
| `EpisodicTimeline.tsx` | L4 事件时间轴可视化 |

**共享组件** (`components/shared/`):

| 组件 | 功能 |
|------|------|
| `CostConfirmDialog.tsx` | API 成本确认弹窗 |
| `DisambiguationCard.tsx` | 约束消歧卡片（展示冲突+选项）|
| `ModelSelector.tsx` | 模型选择器下拉 |
| `StyleSliders.tsx` | 风格维度滑块（调节文风参数）|

**Hooks**:

| Hook | 功能 |
|------|------|
| `useResizable.ts` | 可拖拽调整面板大小 |
| `useTheme.ts` | 主题切换（亮/暗模式） |

**API 层** (`api/`):

| 文件 | 功能 |
|------|------|
| `client.ts` | Axios HTTP 客户端封装 |
| `types.ts` | TypeScript 类型定义（对应后端 Pydantic 模型）|

---

## 八、辅助模块

### RAG 层非记忆模块

| 文件 | 功能 | 输入 | 输出 |
|------|------|------|------|
| `rag/character_cards.py` | 角色卡片管理 | project_id | 角色卡 CRUD |
| `rag/world_book.py` | 世界书管理 | project_id | 世界书条目 CRUD |
| `rag/decision_engine.py` | 角色决策引擎 | 角色卡B层 + 场景 | 行为量化指导 |
| `rag/vector_store.py` | ChromaDB 向量存储封装 | 文本 + 元数据 | 向量化存储/检索 |
| `rag/constraint_store.py` | 约束语义索引 | 约束文本 | 语义检索 |
| `rag/reference_db.py` | 参考作品数据库操作 | 作品数据 | CRUD |

### 核心基础设施

| 文件 | 功能 |
|------|------|
| `core/__init__.py` | 核心模块初始化 |
| `core/config.py` | 配置加载和管理 |
| `core/event_bus.py` | 事件总线（Pub/Sub） |
| `core/event_types.py` | 事件类型定义（枚举） |
| `core/log_setup.py` | 日志配置（Loguru） |
| `core/skill_learner.py` | 技能学习系统 |
| `core/skill_registry.py` | 技能注册中心 |
| `core/triggers.py` | 触发器系统 |

### UI 后端基础设施

| 文件 | 功能 |
|------|------|
| `ui/backend/app/main.py` | FastAPI 应用入口，路由注册，CORS 配置 |
| `ui/backend/app/runner.py` | Uvicorn 服务器启动 |
| `ui/backend/app/settings.py` | 应用设置 |
| `ui/backend/app/store.py` | 内存数据存储（生成会话等）|
| `ui/backend/app/utils.py` | 通用工具函数 |
| `ui/backend/app/runtime_paths.py` | 运行时路径解析 |
