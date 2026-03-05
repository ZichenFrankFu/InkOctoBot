# InkOctoBot Project Overview v4.0 — AI 网文创作系统

> 最后更新: 2026-03-04
> 基于 webnovel_trends 爬虫项目 · 本地优先架构

---

## 一、系统愿景

### 1.1 核心隐喻：纯文字版电影制作

User = **编剧 + 导演**。AI = **剧组（演员 + 剪辑师 + 作家）+ 出版社编辑**。

```
电影制作                        系统映射
──────────────────            ──────────────────────────
编剧写剧本                      User 输入世界书 + 人物卡 + 大纲 + 章节剧情
编剧参考成功电影                 User 为每个维度指定"参考作品"
制片人给市场建议                 AI Editor Agent 基于爬虫数据给出优化建议
导演做分镜表                    Scene Planner 拆解章节为场景蓝图
选角 + 读剧本                   Character Architect 扩展人物卡
导演给演员说戏                  Scene Director 注入目标 + 约束 + 知识隔离
演员即兴表演                    Actor Agents (小模型+角色LoRA) 生成原始素材
剪辑 + 后期                     Editor-Stylist 完成剪辑 + 文学风格化
导演审片 / 补拍                 User Review + 定向重新生成
```

### 1.2 本地优先原则

系统除商业 LLM API 调用外，所有功能完全本地运行。不收集用户数据，不依赖外部服务。
商业 API 调用仅在 User 明确选择时发生，且每次调用前显示成本预估等待 User 确认。

---

## 二、User 输入体系

### 2.1 四大创作输入维度

每个维度支持任意粒度输入 + 独立指定参考作品：

```
① 世界书      世界观、力量体系、社会结构、地理、历史、硬规则
② 人物卡      姓名、性格、关系、背景、成长轨迹、行为模式、量化决策参数
③ 分卷大纲    每卷核心冲突、主要事件、情感主题、预估章节数
④ 章节细纲    章节剧情、出场人物、时间地点、情绪走向、章末 hook
```

User 可从极简到极详细自由选择。缺失部分由系统生成草案 → User 确认。

### 2.2 参考作品系统

参考作品来源：
- **平台热门作品**：从爬虫数据中选取（已有开篇 N 章）
- **User 上传作品**：TXT 文件，经预处理 pipeline 提取特征
- **参考作品数据库**：系统内置 + User 积累的已分析作品库

每个维度可独立指定参考作品，用于提取叙事结构、风格指纹、角色塑造手法。

---

## 三、参考作品数据库

### 3.1 设计理念

参考作品是整个系统的"学习素材库"。不同于爬虫采集的榜单数据（面向市场分析），
参考作品数据库面向创作指导——存储经过深度分析的完整作品特征。

### 3.2 数据来源与入库流程

```
来源 A: 平台爬虫数据 (自动)
    榜单热门作品的开篇 N 章 → 批量特征提取 → 入库
    数据: 有限（仅开篇章节），但量大

来源 B: User 上传 (手动)
    User 上传完整 TXT → 预处理 Pipeline → 入库
    数据: 完整，分析更深入

来源 C: 手动标注补充
    User 对已入库作品补充主观评价/标签
```

### 3.3 数据结构

```python
@dataclass
class ReferenceWorkRecord:
    """参考作品数据库记录"""
    ref_id: str                          # 唯一 ID
    title: str
    author: str
    source: Literal["platform", "upload"]
    platform: str | None                 # "qidian" / "fanqie"
    novel_uid: int | None                # 平台 ID（如适用）
    genre: str
    tags: list[str]
    total_words: int | None
    status: str                          # "连载中" / "完本"

    # ── 预处理产出 ──
    preprocessing_status: str            # "pending" / "processing" / "done"

    # 风格指纹
    style_fingerprint: StyleFingerprint | None
    # {avg_sentence_length, dialogue_ratio, description_density,
    #  rhetoric_frequency, vocab_complexity, pacing_profile}

    # 叙事结构
    narrative_structure: NarrativeStructure | None
    # {chapter_beats: [{chapter, function, hooks, pacing, new_elements}],
    #  opening_pattern, climax_positions, hook_density}

    # 角色画像（从文本中自动提取）
    extracted_characters: list[ExtractedCharacter] | None
    # {name, personality_inferred, speech_patterns, relationship_map}

    # 节奏模板
    rhythm_template: RhythmTemplate | None
    # {tension_curve, shuangdian_positions, pacing_segments}

    # 风格片段库（ZeroStylus 句级 + 段级模板）
    style_fragments_collection_id: str | None   # ChromaDB collection

    # User 标注
    user_rating: int | None              # 1-5
    user_notes: str | None
    user_tags: list[str]                 # User 自定义标签
```

### 3.4 数据库 Schema

```sql
-- SQLite: reference_works 主表
CREATE TABLE reference_works (
    ref_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    author TEXT,
    source TEXT NOT NULL,               -- 'platform' / 'upload'
    platform TEXT,
    novel_uid INTEGER,
    genre TEXT,
    tags_json TEXT,                      -- JSON array
    total_words INTEGER,
    file_path TEXT,                      -- 上传文件路径
    preprocessing_status TEXT DEFAULT 'pending',
    -- 分析结果存 JSON（灵活，schema 可演进）
    style_fingerprint_json TEXT,
    narrative_structure_json TEXT,
    extracted_characters_json TEXT,
    rhythm_template_json TEXT,
    style_fragments_collection_id TEXT,  -- ChromaDB 引用
    -- User 标注
    user_rating INTEGER,
    user_notes TEXT,
    user_tags_json TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 参考作品与项目维度的关联
CREATE TABLE project_reference_links (
    project_id TEXT,
    ref_id TEXT,
    dimension TEXT,                      -- 'world' / 'character' / 'volume' / 'chapter' / 'style'
    reference_character_name TEXT,       -- 如果是角色维度：参考的是哪个角色
    notes TEXT,
    PRIMARY KEY (project_id, ref_id, dimension)
);
```

### 3.5 参考作品浏览与管理 UI

参考作品库有独立的管理页面（ReferenceLibrary），支持：
- 浏览所有已分析作品，按 genre/rating/来源 筛选
- 查看单本作品的分析详情（风格雷达图、叙事结构图、角色关系网络）
- 上传新作品触发预处理
- 从爬虫数据中批量导入热门作品
- 在创建项目时从库中选择参考作品关联到具体维度

---

## 四、AI Editor Agent — 市场优化顾问

基于爬虫数据在创作各阶段给出市场建议，建议是建议而非命令。

```
创作阶段                    Editor Agent 做什么
────────────────────      ────────────────────────────────
选定题材后                  该题材热度/竞争度/趋势 + 差异化建议
写完世界观后                与同类热门作品设定对比 + 独特卖点识别
写完人物卡后                角色配置是否覆盖热门 trope + 缺失角色类型提醒
提交分卷大纲后              节奏模式 vs 热门作品基准 + 高潮间隔建议
提交章节细纲后              hook 密度 + 爽点间隔 + 读者流失风险点
每章正文生成后              风格指标 vs 参考作品/热门作品 + 可读性建议
```

数据来源：爬虫采集的 novels/ranks/chapters + 叙事模式库 + 爽点模板库 + 参考作品库。

---

## 五、人物卡系统 — 自然语言 + 量化决策模型

### 5.1 双层设计

```
Layer A: 自然语言描述 → LLM prompt 用
    人格、说话风格、关系网络、canonical scenario-response 范例、成长轨迹

Layer B: 量化决策模型 → Python 决策引擎用
    效用函数权重、前景理论参数、随机行为分布、贝叶斯信任追踪
```

### 5.2 量化模型细节

**效用函数**: 价值维度权重（survival/power/love/loyalty/justice 等），时间折扣因子。
**前景理论**: loss_aversion（经历创伤后上升），risk_aversion_gain/loss。
**随机行为**: Poisson（社恐搭话频率 λ=0.3 vs 社牛 λ=5.0），Bernoulli（冲动动手 p=0.8），支持情境修正因子。
**贝叶斯信任**: Beta(α,β) 分布，观察行为后更新，α/(α+β) = 信任度期望。

**决策引擎串联**: 效用→前景理论→随机波动→信任加权→归一化→自然语言引导注入 LLM。

### 5.3 模型选择

User 可为决策引擎中的 LLM 辅助部分（如"根据人格自动初始化参数"、"根据事件调整参数"）
自由选择模型，支持多模型输出对比后选择。

---

## 六、交互式 Prompt 消歧系统

User 的任何创作指令（正向/反向/风格/情节/约束）经过歧义检测，
有歧义时系统生成多个候选解读（含置信度），通过选择式 UI 引导确认。
反向约束确认后自动生成三层控制：正向重述 + 语义违规模式 + good/bad 示例。
不使用 token ban，用语义级检测替代。

---

## 七、分层记忆系统 + 知识隔离

### 7.1 四层记忆

```
Layer 1: Immediate Context     当前+前一 scene，4-8K tokens，在 context window
Layer 2: Chapter Buffer        最近 5-10 章摘要，2-4K tokens，在 context window
Layer 3: Semantic Memory       全部设定/人物/事件，ChromaDB 向量检索
Layer 4: Episodic Timeline     关键事件因果链+时间轴，SQLite 结构化查询
```

自动压缩降级：Layer 2 最老章节 → LLM 提取 permanent_facts / active_foreshadowing / character_changes → 存入 Layer 3，过渡细节丢弃。

### 7.2 知识隔离

KnowledgeIsolationEngine 为每个 actor agent 构建 filtered world view。
InformationEvent 显式追踪角色间信息流动（含虚假信息标记）。
explicitly_unknown 列表注入 actor prompt 防止 context leaking。

---

## 八、Film Pipeline — 统一的创作执行流程

### 8.1 总流程（所有章节统一）

去掉复杂度路由，每章都走完整的三层 pipeline，保证一致的质量。

```
User 输入完成（世界书 + 人物卡 + 大纲 + 章节细纲 + 参考作品 + 约束）
  ↓
Editor Agent 给出市场优化建议 → [User 决定采纳与否]
  ↓
Story Architect 补全缺失维度 → [User 确认]
  ↓
══ 单章循环 ══
  ↓
Scene Planner → 将章节细纲拆为场景蓝图（每场景: 角色/目标/情绪/约束）
  ↓  [User 可预览调整]
  ↓
Scene Director (reasoning_mid) → 为每个场景生成导演指令
  含: 角色情绪状态、秘密目标、知识隔离指令、情绪弧线、must/must_not
  ↓
Actor Agents (roleplay, 每角色独立 instance) → 多角色交互模拟
  每个 actor 只看到 KnowledgeIsolationEngine 过滤后的信息
  输出: 半结构化"表演记录"（节拍序列: 动作+对话+内心+氛围）
  关键选择点: DecisionEngine 先算概率分布，以自然语言注入 prompt
  ↓
Editor-Stylist (reasoning_mid + 风格LoRA) → 剪辑 + 文学转化
  输入: 原始素材 + 叙事指令（POV/节拍展开压缩/情绪弧线）+ 风格 + 约束
  输出: 正文
  ↓
Evaluation Pipeline → 约束/一致性/知识隔离/重复/slop 检测
  不达标 → targeted rewrite（仅重写问题段落，不重跑全 pipeline）
  ↓
Editor Agent 给出本章优化建议（可选）
  ↓
[User 在文本 Editor 中审阅 + 编辑]
  ↓
User 编辑 → EditAnalyzer 分析 → 反馈回各子系统
  ↓
记忆系统更新 → 版本快照 → 进入下一章
```

### 8.2 Actor 表演格式

```
[节拍1]
张三(紧张): *抽出剑挡在身前* "你到底是谁？"
  内心: 这个人的气势太强了，完全不是一个级别
李四(玩味): *缓缓拔剑* "你猜？"
  内心: 有点意思，居然没跑
[氛围] 空气凝滞，杀意弥漫
```

### 8.3 Editor-Stylist 合并层

一次 LLM call 完成剪辑决策和文学转化。Prompt 结构:

```
素材 + 叙事指令（POV / 节拍处理 / 章末处理）+ 风格参数 + 约束
→ 正文
```

---

## 九、模型路由、选择与成本控制

### 9.1 核心设计: User 完全掌控模型选择

每个 agent 角色（Scene Director / Actor / Editor-Stylist / Editor Agent 等）
都允许 User 自由选择模型。User 可以为每个角色独立指定模型，也可以使用预设方案。

### 9.2 ModelRouter 架构

```python
class ModelRouter:
    """
    统一模型路由层。
    每个 agent 声明 capability tier，Router 根据 User 配置返回实际 provider。
    """
    def get_model(self, agent_role: str, **kwargs) -> LLMProvider:
        # 1. 检查 User 是否为此 agent_role 指定了模型
        # 2. 否则使用预设方案中的默认值
        # 3. 如果是商业 API → 计算成本预估 → 返回 PendingApproval
        ...

class CostEstimator:
    """商业 API 成本预估器"""
    # 各 provider 的定价表
    PRICING = {
        "anthropic": {
            "claude-sonnet-4-5": {"input": 3.0, "output": 15.0},  # $/M tokens
            "claude-haiku-4-5": {"input": 0.80, "output": 4.0},
        },
        "openai": {
            "gpt-4o": {"input": 2.50, "output": 10.0},
            "gpt-4o-mini": {"input": 0.15, "output": 0.60},
        },
        "deepseek": {
            "deepseek-chat": {"input": 0.27, "output": 1.10},
        },
    }

    def estimate(self, provider: str, model: str, 
                 est_input_tokens: int, est_output_tokens: int) -> CostEstimate:
        """返回本次调用的预估成本"""
        pricing = self.PRICING[provider][model]
        input_cost = est_input_tokens / 1_000_000 * pricing["input"]
        output_cost = est_output_tokens / 1_000_000 * pricing["output"]
        return CostEstimate(
            provider=provider, model=model,
            est_input_tokens=est_input_tokens,
            est_output_tokens=est_output_tokens,
            est_cost_usd=input_cost + output_cost,
        )
```

### 9.3 多模型对比机制

User 可为任何 agent 选择多个模型进行并行生成 + 对比:

```python
class ABCompareEngine:
    """
    同一输入发给 N 个模型 → 收集结果 → 并排展示 → User 选择
    """
    async def compare(
        self,
        prompt: str,
        models: list[ModelConfig],    # User 选的多个模型
    ) -> CompareResult:
        results = await asyncio.gather(*[
            self._generate(model, prompt) for model in models
        ])
        return CompareResult(
            variants=[
                Variant(
                    model=m,
                    output=r.text,
                    latency_ms=r.latency,
                    token_count=r.tokens,
                    cost=r.cost,              # 商业 API: 实际花费; 本地: $0
                )
                for m, r in zip(models, results)
            ]
        )
```

### 9.4 成本确认流程（仅商业 API）

```
User 选择了商业 API 模型
  ↓
系统估算 token 消耗
  ↓
弹出确认对话框:
  ┌─────────────────────────────────────────┐
  │  即将调用 Claude Sonnet 4.5             │
  │                                         │
  │  预估 input tokens:  ~3,200             │
  │  预估 output tokens: ~2,500             │
  │                                         │
  │  本次会话累计tokens:  ~5,700              │
  │                                         │
  │    [取消]  [改用本地模型]  [确认并调用]   │
  └─────────────────────────────────────────┘
  ↓
User 确认 → 执行调用 → 记录实际花费
User 取消 → 不调用，可切换为本地模型
```

### 9.5 预设方案

```yaml
# config/models.yaml
presets:
  cost_optimal:                    # 默认: 成本最优，全本地
    scene_director:    { provider: "ollama", model: "qwen2.5:32b" }
    actor_default:     { provider: "ollama", model: "qwen2.5:7b" }
    actor_protagonist: { provider: "ollama", model: "qwen2.5:14b" }
    editor_stylist:    { provider: "ollama", model: "qwen2.5:32b" }
    editor_agent:      { provider: "ollama", model: "qwen2.5:32b" }
    story_architect:   { provider: "ollama", model: "qwen2.5:32b" }
    evaluator:         { provider: "ollama", model: "qwen2.5:14b" }

  balanced:                        # 平衡: 关键环节用 API
    scene_director:    { provider: "ollama", model: "qwen2.5:32b" }
    actor_default:     { provider: "ollama", model: "qwen2.5:7b" }
    actor_protagonist: { provider: "ollama", model: "qwen2.5:14b" }
    editor_stylist:    { provider: "deepseek", model: "deepseek-chat" }
    editor_agent:      { provider: "deepseek", model: "deepseek-chat" }
    story_architect:   { provider: "anthropic", model: "claude-sonnet-4-5" }
    evaluator:         { provider: "ollama", model: "qwen2.5:14b" }

  quality_first:                   # 质量优先: 主要用商业 API
    scene_director:    { provider: "anthropic", model: "claude-sonnet-4-5" }
    actor_default:     { provider: "ollama", model: "qwen2.5:7b" }
    actor_protagonist: { provider: "anthropic", model: "claude-sonnet-4-5" }
    editor_stylist:    { provider: "anthropic", model: "claude-sonnet-4-5" }
    editor_agent:      { provider: "anthropic", model: "claude-sonnet-4-5" }
    story_architect:   { provider: "anthropic", model: "claude-sonnet-4-5" }
    evaluator:         { provider: "deepseek", model: "deepseek-chat" }

# User 可以基于任意预设修改单个 agent 的模型
# 也可以保存自己的自定义预设
custom_presets: {}
```

### 9.6 成本追踪

```sql
CREATE TABLE api_cost_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    chapter_number INTEGER,
    agent_role TEXT,                  -- 'scene_director' / 'actor' / 'editor_stylist' 等
    provider TEXT,
    model TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
);
-- UI 中显示: 按项目/章节/agent 粒度的成本统计
```

---

## 十、UI 设计

### 10.1 设计原则

```
- 本地桌面应用质感 (非典型 web 后台)
- 暗色主题为主 (长时间写作护眼), 支持亮色切换
- 左侧导航精简, 核心功能在主编辑器页
- 所有设置均可在 UI 中完成, 无需编辑配置文件
- 中文优先排版 (思源宋体/Noto Serif SC 正文, 思源黑体/Noto Sans SC UI)
```

### 10.2 页面架构总览

```
┌─ App Shell ───────────────────────────────────────────────────┐
│ ┌─ Left Sidebar (全局导航, 可折叠) ─┐  ┌─ Main Content ─────┐ │
│ │                                    │  │                     │ │
│ │ 📖 编辑器 (主页面)                 │  │  (当前选中的页面)    │ │
│ │ 📂 项目列表                        │  │                     │ │
│ │ 👤 人物卡管理                      │  │                     │ │
│ │ 🌍 世界书管理                      │  │                     │ │
│ │ 📊 分析面板                        │  │                     │ │
│ │ 📚 参考作品库                      │  │                     │ │
│ │ ⚙️ 设置                           │  │                     │ │
│ │   ├ 模型配置                       │  │                     │ │
│ │   ├ 约束管理                       │  │                     │ │
│ │   └ 系统设置                       │  │                     │ │
│ │                                    │  │                     │ │
│ └────────────────────────────────────┘  └─────────────────────┘ │
└───────────────────────────────────────────────────────────────┘
```

### 10.3 Page 1: 编辑器 — 主页面 (EditorPage)

这是 User 日常使用最多的页面。三栏布局:

```
┌─────────────┬────────────────────────────────────┬──────────────────────┐
│ 左侧: 目录树  │ 中间: 文本编辑器                     │ 右侧: AI 面板         │
│ (可调宽度)    │ (核心区域, 占最大空间)               │ (可调宽度, 可折叠)    │
├─────────────┼────────────────────────────────────┼──────────────────────┤
│             │                                    │                      │
│ ▼ 第一卷    │  第三章  初入宗门                     │ ┌─ AI 控制区 ──────┐ │
│   第1章     │  ─────────────                      │ │                  │ │
│   第2章     │                                    │ │ [生成本章] ▼模型选择│ │
│ ● 第3章 ◄  │  清晨的阳光穿过竹林，洒在蜿蜒         │ │                  │ │
│   第4章     │  的石阶上。张远背着包袱，跟在          │ │ 状态: 等待        │ │
│   ...       │  师兄身后，沿着山路缓缓向上。          │ │ Pipeline: ●○○○   │ │
│             │                                    │ │                  │ │
│ ▼ 第二卷    │  "从今天起，你就是青云门的             │ ├─ Editor 建议 ────┤ │
│   第31章    │  外门弟子了。"师兄头也不回地说。       │ │                  │ │
│   ...       │                                    │ │ 本章对话占比12%，  │ │
│             │  张远点了点头，却没有答话。他的         │ │ 同类热门平均30%。  │ │
│ ──────────  │  目光越过师兄的肩膀，望向山顶          │ │ 建议增加角色互动。 │ │
│             │  那座若隐若现的主殿。                   │ │ [采纳] [忽略]     │ │
│ 章节信息:   │                                    │ │                  │ │
│ 字数: 3,421 │  |  ← 光标位置                      │ ├─ 版本历史 ───────┤ │
│ 状态: 已编辑 │                                    │ │                  │ │
│ 版本: v3    │                                    │ │ v3 (当前) 手动编辑 │ │
│             │  ──────────────────                 │ │ v2 AI 生成 14:32  │ │
│ [+ 新章节]  │  工具栏: B I U | 撤销 重做 | 版本   │ │ v1 AI 生成 14:20  │ │
│             │                                    │ │ [对比 v2↔v3]     │ │
│             │                                    │ │                  │ │
│             │                                    │ ├─ 多模型对比 ─────┤ │
│             │                                    │ │                  │ │
│             │                                    │ │ (当有多模型输出时) │ │
│             │                                    │ │ Model A | Model B │ │
│             │                                    │ │ [选用A] [选用B]   │ │
│             │                                    │ │                  │ │
└─────────────┴────────────────────────────────────┴──────────────────────┘
```

**左栏 — 目录树**:
- 分卷 > 分章节的树形结构，可拖拽排序
- 每章显示状态标记：空白 / AI生成 / 已编辑 / 已定稿
- 字数统计
- 点击章节切换编辑区内容
- 底部 [+ 新章节] 按钮

**中栏 — 文本编辑器**:
- 富文本编辑器（基于 TipTap 或 ProseMirror）
- 支持基础格式：加粗、斜体、分段
- 工具栏：撤销/重做、版本切换、全屏模式、字数统计
- AI 生成的内容以淡色背景标记（User 编辑后背景消失）
- 选中文本后可呼出上下文菜单："重写此段" / "扩展" / "缩减" / "换风格"

**右栏 — AI 面板**:
- **AI 控制区**: [生成本章] 按钮 + 模型选择下拉 + pipeline 进度指示器
- **Editor 建议**: AI 编辑的市场优化建议卡片，可采纳/忽略
- **版本历史**: 当前章节的所有版本列表（AI 生成 + 手动编辑），支持版本对比（diff view）
- **多模型对比**: 当 User 选择了多个模型时，并排展示不同模型的输出，User 选择最喜欢的

### 10.4 Page 2: 项目列表 (ProjectList)

```
┌────────────────────────────────────────────────────────────┐
│  我的项目                                    [+ 新建项目]  │
│                                                            │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │ 🗡️ 青云剑歌  │  │ 🌌 星际流浪  │  │ 🏙️ 都市修仙  │     │
│  │              │  │              │  │              │     │
│  │ 仙侠 · 32章 │  │ 科幻 · 8章   │  │ 都市 · 0章   │     │
│  │ 12.8万字     │  │ 3.2万字      │  │ 草稿中       │     │
│  │              │  │              │  │              │     │
│  │ API 累计$2.3 │  │ API 累计$0   │  │              │     │
│  │ 上次: 2小时前│  │ 上次: 昨天   │  │ 刚创建       │     │
│  │              │  │              │  │              │     │
│  │ [打开] [设置]│  │ [打开] [设置]│  │ [设置] [删除]│     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

新建项目流程: 输入项目名+题材 → 进入 ProjectSetup 页。

### 10.5 Page 3: 项目设置 (ProjectSetup)

Tab 式布局，4 个 tab 对应 4 个创作输入维度 + 1 个约束/风格 tab:

```
┌────────────────────────────────────────────────────────────┐
│  项目设置 — 青云剑歌                                        │
│                                                            │
│  [世界书] [人物卡] [分卷大纲] [章节细纲] [风格与约束]       │
│  ─────────────────────────────────────────────────────     │
│                                                            │
│  (以"世界书" tab 为例)                                      │
│                                                            │
│  ┌─ 编辑区 ─────────────────────┐ ┌─ 参考作品 ──────────┐ │
│  │                               │ │                      │ │
│  │ 世界观概述:                   │ │ 已关联:              │ │
│  │ [___________________________] │ │  📖 仙逆 (世界观)    │ │
│  │ [___________________________] │ │  📖 凡人修仙 (力量)  │ │
│  │                               │ │                      │ │
│  │ 力量体系:                     │ │ [+ 从库中添加]       │ │
│  │ [___________________________] │ │ [+ 上传新作品]       │ │
│  │                               │ │                      │ │
│  │ 硬规则:                       │ │ ── AI 补全建议 ──   │ │
│  │ + 不要出现现代科技             │ │                      │ │
│  │ + 灵气浓度决定修炼速度        │ │ 基于参考作品，建议   │ │
│  │ [+ 添加规则]                  │ │ 补充以下设定:        │ │
│  │                               │ │ · 阵法体系 [采纳]   │ │
│  │ [用AI补全缺失部分]            │ │ · 丹药炼制 [采纳]   │ │
│  │                               │ │                      │ │
│  └───────────────────────────────┘ └──────────────────────┘ │
│                                                            │
│  ── AI Editor 建议 ─────────────────────────────────────   │
│  💡 与当前修仙榜 Top 20 相比，你的灵根设定较为传统。         │
│     "以棋入道"的独特金手指建议在第一章内展示。               │
│     [详情] [采纳] [忽略]                                    │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

### 10.6 Page 4: 人物卡管理 (CharacterManager)

```
┌────────────────────────────────────────────────────────────┐
│  人物卡 — 青云剑歌                              [+ 新角色] │
│                                                            │
│  ┌─ 角色列表 ──┐  ┌─ 角色详情 ─────────────────────────┐  │
│  │              │  │                                     │  │
│  │ ● 张远 (主角)│  │  [基础信息] [性格与行为] [决策模型]  │  │
│  │ ○ 林小霜    │  │  [关系网络] [成长轨迹] [参考作品]    │  │
│  │ ○ 陈师兄    │  │  ──────────────────────────────     │  │
│  │ ○ 魔教教主  │  │                                     │  │
│  │              │  │  (以"决策模型" tab 为例)             │  │
│  │              │  │                                     │  │
│  │              │  │  效用权重:                           │  │
│  │              │  │  正义 ████████░░ 0.30               │  │
│  │              │  │  忠诚 ██████░░░░ 0.25               │  │
│  │              │  │  情感 █████░░░░░ 0.20               │  │
│  │              │  │  生存 ███░░░░░░░ 0.15               │  │
│  │              │  │  权力 ██░░░░░░░░ 0.10               │  │
│  │              │  │                                     │  │
│  │              │  │  Loss Aversion: 1.8 [▪───●───▪]    │  │
│  │              │  │  时间折扣:      0.4 [▪─●─────▪]    │  │
│  │              │  │                                     │  │
│  │              │  │  随机行为:                           │  │
│  │              │  │  主动搭话 λ=0.8 (偏内向)            │  │
│  │              │  │  冲动行事 p=0.35 (较冷静)           │  │
│  │              │  │                                     │  │
│  │              │  │  [用AI根据性格描述自动校准]          │  │
│  │              │  │  模型: [Qwen2.5:32B ▼] [运行]      │  │
│  │              │  │                                     │  │
│  └──────────────┘  └─────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────┘
```

### 10.7 Page 5: 世界书管理 (WorldBookEditor)

类似 ProjectSetup 的世界书 tab，但更完整:
- 支持分类浏览（力量体系 / 势力 / 地理 / 历史 / 规则）
- 条目式编辑，每条可独立关联参考作品
- AI 一致性检查（"这条规则和第3条是否矛盾？"）
- 知识图谱可视化（d3 力导向图展示势力/角色/地点关系）

### 10.8 Page 6: 分析面板 (AnalysisDashboard)

整合已有的分析功能 + 新增的叙事模式/爽点模板:
- 热度趋势图（题材热度变化）
- 标签分布饼图
- 叙事模式统计（开篇类型分布、hook 类型分布）
- 爽点模板排行（哪些爽点模式最常出现在 Top 50）

### 10.9 Page 7: 参考作品库 (ReferenceLibrary)

```
┌────────────────────────────────────────────────────────────┐
│  参考作品库                    [上传作品] [从榜单导入]       │
│                                                            │
│  搜索: [____________] 筛选: [全部▼] [仙侠▼] [★4+▼]       │
│                                                            │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  │
│  │ 仙逆     │  │ 凡人修仙 │  │ 诡秘之主 │  │ 我的上传1│  │
│  │ 仙侠     │  │ 修真     │  │ 奇幻     │  │ 都市     │  │
│  │ ★★★★★   │  │ ★★★★☆   │  │ ★★★★★   │  │ 未评分   │  │
│  │ 来源:平台 │  │ 来源:平台 │  │ 来源:上传 │  │ 来源:上传 │  │
│  │ 分析:完成 │  │ 分析:完成 │  │ 分析:完成 │  │ 分析中...│  │
│  │ [详情]    │  │ [详情]    │  │ [详情]    │  │ [详情]    │  │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘  │
│                                                            │
│  (点击"详情"展开):                                         │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ 仙逆 — 详细分析                                      │  │
│  │                                                      │  │
│  │ [风格指纹]  [叙事结构]  [角色画像]  [节奏模板]        │  │
│  │                                                      │  │
│  │ 风格雷达图:            叙事结构时间线:               │  │
│  │   (Recharts 雷达)        (章节 beat 可视化)          │  │
│  │                                                      │  │
│  │ 已关联到项目: 青云剑歌 (世界观+人物)                  │  │
│  └──────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────┘
```

### 10.10 Page 8: 设置 (Settings)

三个子页面:

**模型配置 (ModelSettings)**:
```
┌────────────────────────────────────────────────────────────┐
│  模型配置                                                  │
│                                                            │
│  预设方案: [成本最优(全本地) ▼]  [保存自定义] [重置]        │
│                                                            │
│  Agent 角色         当前模型                    状态        │
│  ────────────      ─────────────────────────   ──────     │
│  Story Architect   [Qwen2.5:32B (Ollama) ▼]   ● 在线     │
│  Scene Director    [Qwen2.5:32B (Ollama) ▼]   ● 在线     │
│  Actor (默认)      [Qwen2.5:7B (Ollama)  ▼]   ● 在线     │
│  Actor (主角)      [Qwen2.5:14B (Ollama) ▼]   ● 在线     │
│  Editor-Stylist    [Qwen2.5:32B (Ollama) ▼]   ● 在线     │
│  Editor Agent      [Qwen2.5:32B (Ollama) ▼]   ● 在线     │
│  Evaluator         [Qwen2.5:14B (Ollama) ▼]   ● 在线     │
│                                                            │
│  [+ 添加模型提供商]  [测试所有连接]                         │
│                                                            │
│  ── 成本追踪 ──────────────────────────────────────────   │
│  本月 API 支出: $2.35                                      │
│  (饼图: 按 agent 分布)                                     │
│                                                            │
│  ── A/B 对比模式 ──────────────────────────────────────   │
│  ☑ 启用 Editor-Stylist 多模型对比                          │
│    对比模型: ☑ Qwen2.5:32B  ☑ DeepSeek-Chat               │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

**约束管理 (ConstraintManager)**: 交互式消歧 wizard + 已有约束列表 + 冲突检测。

**系统设置 (SystemSettings)**: 主题切换、数据目录路径、ChromaDB 路径、自动备份间隔。

### 10.11 版本回溯系统

每章维护一个版本链:

```python
@dataclass
class ChapterVersion:
    version_id: str
    chapter_number: int
    content: str                     # 完整正文
    source: str                      # 'ai_generated' / 'user_edited' / 'ai_rewrite'
    model_used: str | None           # 如果是 AI 生成
    parent_version_id: str | None    # 上一版本
    diff_from_parent: str | None     # 与上一版本的 diff (JSON patch)
    timestamp: datetime
    metadata: dict                   # 评估分数、pipeline 参数等
```

UI 支持:
- 版本列表 (右侧面板)
- 两版本并排 diff 对比 (高亮增删)
- 回滚到任意历史版本
- 标记版本 ("定稿" / "草稿" / "AI初稿")

---

## 十一、隐私保护与权限管理

### 11.1 本地优先架构

```
所有数据存储在本地:
  SQLite 数据库 → 本地文件
  ChromaDB 向量库 → 本地目录
  配置文件 → 本地 YAML/JSON
  上传的参考作品 → 本地文件
  LoRA 权重 → 本地文件
  生成的正文 → 本地 SQLite + 文件

不存在任何 Anthropic/OpenAI/第三方 数据收集。
```

### 11.2 商业 API 调用的数据隔离

```
User 选择使用商业 API 时:
  1. 仅发送当前场景所需的 prompt（不发送整本书内容）
  2. 发送前明确展示将发送的内容和目标 API
  3. 不发送 User 个人信息
  4. API 响应仅存储在本地
  5. 支持 opt-out: 任何时候可切回本地模型
```

### 11.3 API Key 管理

```python
# API key 存储在本地加密文件中，不存入 SQLite
class APIKeyManager:
    """
    API key 本地加密存储。
    使用 OS 级别的 keyring (macOS Keychain / Windows Credential Manager / Linux Secret Service)
    如果 keyring 不可用，fallback 到本地加密文件 (Fernet symmetric encryption)。
    """
    def store_key(self, provider: str, api_key: str): ...
    def get_key(self, provider: str) -> str | None: ...
    def delete_key(self, provider: str): ...
    def list_providers_with_keys(self) -> list[str]: ...
```

### 11.4 项目级访问控制

单用户本地场景不需要复杂的 RBAC，但需要:
- 项目数据隔离：每个项目独立的数据目录
- 导出控制：导出时可选择是否包含 AI 生成标记
- 清理功能：一键删除项目所有数据（含 ChromaDB 中的向量、版本历史等）

---

## 十二、项目结构

```
webnovel_trends/
│
├── spiders/                             # ✅ 已有，保留
│   ├── base_spider.py
│   ├── qidian_spider.py
│   ├── fanqie_spider.py
│   ├── fanqie_font_decoder.py
│   └── antibot.py
│
├── database/                            # ✅ 已有 → 扩展
│   ├── db_schema.py                     # 追加新表 (见下方 schema 清单)
│   └── db_handler.py
│
├── tasks/                               # ✅ 已有，保留
│   ├── scheduler.py
│   └── run_spiders_once.py
│
├── analysis/                            # ✅ 已有 → 扩展
│   ├── run_analysis.py
│   ├── trend_analyzer.py
│   ├── data_access.py
│   ├── heat.py
│   ├── metrics.py
│   ├── report.py
│   ├── feature_extraction/              # ← 新增
│   │   ├── pipeline.py
│   │   ├── nlp_stats.py                 # jieba + SnowNLP
│   │   ├── embedding_cluster.py         # text2vec + KMeans
│   │   ├── narrative_extractor.py       # 叙事结构标注
│   │   ├── rhetoric_classifier.py       # 修辞分类
│   │   └── shuangdian_templates.py      # 爽点模板
│   └── formula_engine/                  # ← 新增
│       ├── aggregator.py
│       ├── constraint_converter.py
│       └── presets.py
│
├── preprocessing/                       # ← 新增: 参考作品预处理
│   ├── pipeline.py                      # 5步主入口
│   ├── chapter_splitter.py
│   ├── style_extractor.py              # PROSE 迭代风格收敛
│   ├── character_profiler.py
│   ├── rhythm_analyzer.py
│   ├── fragment_selector.py             # ZeroStylus
│   └── lora/
│       ├── data_constructor.py
│       ├── quality_filter.py
│       └── trainer.py                   # SFT + Constitutional DPO
│
├── rag/                                 # ← 新增: RAG 知识库
│   ├── world_book.py
│   ├── character_cards.py               # NL 层管理
│   ├── decision_engine.py               # 量化决策引擎
│   ├── constraint_store.py
│   ├── reference_db.py                  # 参考作品数据库管理
│   ├── vector_store.py                  # ChromaDB 封装
│   └── memory/
│       ├── manager.py                   # 记忆总控
│       ├── immediate.py                 # Layer 1
│       ├── chapter_buffer.py            # Layer 2
│       ├── semantic_store.py            # Layer 3 (ChromaDB)
│       ├── episodic_timeline.py         # Layer 4 (SQLite)
│       ├── knowledge_isolation.py       # 知识隔离引擎
│       └── consolidator.py             # 压缩降级
│
├── agents/                              # ← 新增: 多 Agent 创作层
│   ├── model_router.py                  # 模型路由
│   ├── cost_estimator.py                # 商业 API 成本预估
│   ├── ab_compare.py                    # 多模型对比引擎
│   ├── model_providers/
│   │   ├── base.py
│   │   ├── openai_provider.py
│   │   ├── anthropic_provider.py
│   │   ├── deepseek_provider.py
│   │   ├── ollama_provider.py
│   │   ├── vllm_provider.py
│   │   └── lora_provider.py
│   │
│   ├── planner/
│   │   ├── story_architect.py           # 补全缺失维度
│   │   ├── volume_planner.py
│   │   ├── chapter_planner.py
│   │   └── scene_planner.py             # 章节→场景蓝图
│   │
│   ├── editor_agent.py                  # AI 编辑 (市场建议)
│   │
│   ├── production/                      # Film Pipeline 执行层
│   │   ├── scene_director.py            # 导演指令
│   │   ├── actor_agent.py               # 角色扮演
│   │   ├── scene_simulator.py           # 多角色交互模拟
│   │   └── editor_stylist.py            # 剪辑 + 文学转化
│   │
│   ├── constraints/
│   │   ├── disambiguator.py             # 交互式消歧
│   │   ├── assembler.py                 # 约束组装
│   │   └── violation_detector.py        # 语义违规检测
│   │
│   └── evaluation/
│       ├── quality_scorer.py
│       ├── repetition_detector.py
│       ├── consistency_checker.py
│       └── slop_detector.py
│
├── evaluation/                          # ← 新增: 反馈循环
│   ├── preference_store.py
│   ├── edit_analyzer.py
│   └── style_drift_detector.py
│
├── security/                            # ← 新增: 安全与隐私
│   ├── api_key_manager.py               # API key 加密存储
│   └── data_isolation.py                # 项目数据隔离
│
├── config/                              # ← 新增
│   ├── app_config.yaml
│   ├── models.yaml                      # 模型路由 + 预设方案
│   ├── model_providers.json             # 提供商注册 + 定价表
│   ├── model_presets/
│   │   ├── cost_optimal.json
│   │   ├── balanced.json
│   │   └── quality_first.json
│   ├── constraint_presets/
│   ├── style_profiles/
│   ├── character_templates/
│   ├── prompts/                         # Agent prompt 模板
│   └── slop_patterns.json
│
├── data/
│   ├── webnovel.db                      # SQLite 主库
│   ├── chromadb/                        # ChromaDB
│   ├── references/                      # 参考作品文件
│   └── projects/
│       └── project_001/
│           ├── world_book.yaml
│           ├── characters/
│           ├── volumes/
│           ├── chapters/
│           ├── lora/
│           └── exports/
│
├── ui/
│   ├── backend/                         # ✅ 已有 → 扩展
│   │   └── app/
│   │       ├── main.py
│   │       ├── settings.py
│   │       ├── store.py
│   │       ├── runner.py
│   │       └── routers/
│   │           ├── config_api.py            # ✅ 已有
│   │           ├── tasks_api.py             # ✅ 已有
│   │           ├── reports_api.py           # ✅ 已有
│   │           ├── db_api.py                # ✅ 已有
│   │           ├── analysis_api.py          # ← 新增
│   │           ├── formula_api.py           # ← 新增
│   │           ├── prompt_api.py            # ← 新增: 消歧
│   │           ├── project_api.py           # ← 新增
│   │           ├── worldbook_api.py         # ← 新增
│   │           ├── characters_api.py        # ← 新增
│   │           ├── planner_api.py           # ← 新增
│   │           ├── reference_api.py         # ← 新增
│   │           ├── generation_api.py        # ← 新增
│   │           ├── editor_api.py            # ← 新增
│   │           ├── eval_api.py              # ← 新增
│   │           ├── version_api.py           # ← 新增: 版本管理
│   │           ├── model_api.py             # ← 新增: 模型管理+成本
│   │           └── security_api.py          # ← 新增: API key 管理
│   │
│   └── frontend/
│       └── src/
│           ├── App.tsx
│           ├── pages/
│           │   ├── ConfigPage.tsx           # ✅ 已有
│           │   ├── RunnerPage.tsx           # ✅ 已有
│           │   ├── ReportsPage.tsx          # ✅ 已有
│           │   ├── DatabasePage.tsx         # ✅ 已有
│           │   ├── EditorPage.tsx           # ← 核心: 三栏编辑器
│           │   ├── ProjectListPage.tsx      # ← 项目列表
│           │   ├── ProjectSetupPage.tsx     # ← 项目设置 (4维度+约束)
│           │   ├── CharacterManager.tsx     # ← 人物卡 (含决策模型面板)
│           │   ├── WorldBookEditor.tsx      # ← 世界书
│           │   ├── AnalysisDashboard.tsx    # ← 分析面板
│           │   ├── ReferenceLibrary.tsx     # ← 参考作品库
│           │   └── SettingsPage.tsx         # ← 设置 (模型/约束/系统)
│           ├── components/
│           │   ├── LogViewer.tsx            # ✅ 已有
│           │   ├── editor/
│           │   │   ├── ChapterTree.tsx      # 左栏目录树
│           │   │   ├── TextEditor.tsx       # 中栏富文本编辑器
│           │   │   ├── AIPanel.tsx          # 右栏 AI 面板
│           │   │   ├── VersionHistory.tsx   # 版本历史+diff
│           │   │   ├── ModelCompare.tsx     # 多模型输出对比
│           │   │   └── EditorAdvice.tsx     # AI 编辑建议卡片
│           │   ├── shared/
│           │   │   ├── CostConfirmDialog.tsx # 商业 API 成本确认
│           │   │   ├── ModelSelector.tsx     # 模型选择器 (含多选)
│           │   │   ├── DisambiguationCard.tsx
│           │   │   └── StyleSliders.tsx
│           │   ├── characters/
│           │   │   ├── CharacterCard.tsx
│           │   │   ├── DecisionModelPanel.tsx
│           │   │   └── RelationshipGraph.tsx
│           │   ├── reference/
│           │   │   ├── ReferenceCard.tsx
│           │   │   ├── StyleRadar.tsx       # 风格雷达图
│           │   │   └── NarrativeTimeline.tsx # 叙事结构图
│           │   └── analysis/
│           │       ├── TrendChart.tsx
│           │       └── ShuangdianRank.tsx
│           └── lib/
│               ├── api.ts                   # API client
│               ├── types.ts                 # 类型定义
│               └── theme.ts                 # 主题配置
│
├── config.py                            # 薄读取层
├── requirements.txt
├── main.py                              # 扩展子命令
└── README.md
```

---

## 十三、数据存储 Schema 总览

### SQLite 新增表

```sql
-- 参考作品库
reference_works                  (ref_id, title, author, source, genre, 分析结果JSON, ...)
project_reference_links          (project_id, ref_id, dimension, ...)

-- 创作项目
projects                         (project_id, name, genre, created_at, ...)
project_world_books              (entry_id, project_id, category, content, ...)
project_characters               (char_id, project_id, name, role, nl_card JSON, quant_model JSON, ...)
project_volume_plans             (volume_id, project_id, volume_number, core_conflict, ...)
project_chapter_plans            (chapter_id, project_id, chapter_number, plot_summary, scenes JSON, ...)

-- 生成内容与版本
generated_chapters               (chapter_id, project_id, chapter_number, current_content, ...)
chapter_versions                 (version_id, chapter_id, content, source, model_used, diff, timestamp, ...)

-- 记忆与知识隔离
information_events               (event_id, project_id, chapter, scene_id, information, source_char, ...)
episodic_timeline                (event_id, project_id, chapter, description, participants, causal_links, ...)

-- 叙事模式
narrative_patterns               (pattern_id, genre, pattern_type, data JSON, ...)
shuangdian_templates             (template_id, setup_type, reversal_type, stats JSON, ...)

-- 评估与反馈
preferences                      (id, project_id, context_hash, generation_a, generation_b, user_choice, ...)
evaluation_logs                  (id, project_id, chapter, scores JSON, violations JSON, ...)
editor_advice_log                (id, project_id, chapter, advice, user_action, ...)

-- 成本追踪
api_cost_log                     (id, project_id, chapter, agent_role, provider, model, tokens, cost, ...)
```

### ChromaDB Collections

```
semantic_memory_{project_id}     长期语义记忆
character_states_{project_id}    人物状态快照
style_fragments_{ref_id}         参考作品风格片段
violation_patterns               约束违规模式
reference_excerpts               参考作品片段
```

---

## 十四、实施路径

### Phase 0 — 基础设施 (Week 1, 5天)

```
- 完成 OPTIMIZATION_PLAN.md 关键项
- 创建 config/ + YAML/JSON 骨架
- db_schema.py 追加所有新表
- FastAPI router 骨架 (返回 200)
- React 页面骨架 + 路由
- ChromaDB + security 模块搭建
里程碑: 新 API 返回 200, 新页面可访问
```

### Phase 1 — 离线学习层 (Week 2-4, 23天)

```
- 特征提取 Pipeline
- 叙事模式 + 爽点模板提取
- 公式引擎 + 约束预设
- 参考作品数据库 (schema + 管理 API + 预处理入库)
- AnalysisDashboard + ReferenceLibrary 前端
里程碑: 参考作品库可浏览, 100+ 书分析完成
```

### Phase 2 — 知识库层 (Week 5-8, 30天)

```
- 世界书 + 人物卡 + 分卷/章节大纲数据管理
- 人物决策引擎 (效用+前景+随机+贝叶斯)
- 分层记忆系统 + 知识隔离
- 交互式 Prompt 消歧系统
- 约束管理
- ProjectSetupPage + CharacterManager + WorldBookEditor + ConstraintWizard
里程碑: 创建项目 → 录入设定 → 知识库可检索
```

### Phase 3 — 创作执行层 (Week 9-13, 34天)

```
- ModelRouter + 6 Provider + CostEstimator + ABCompare
- Planner 层 (Story/Volume/Chapter/Scene)
- AI Editor Agent
- Film Pipeline (Director → Actor → Editor-Stylist)
- Evaluation Pipeline
- 版本管理系统
- EditorPage 三栏编辑器 (核心 UI)
- SettingsPage (模型配置 + 成本追踪)
里程碑: 端到端生成第一章, 编辑器可用, 版本回溯可用
```

### Phase 4 — LoRA + 打磨 (Week 14-16, 25天)

```
- LoRA 训练 Pipeline
- HITL 反馈循环 + EditAnalyzer
- 项目导出 (TXT/DOCX/EPUB)
- API key 加密存储
- 主题切换 (暗/亮)
- 系统优化 + Bug 修复 + 文档
里程碑: 完整闭环系统可交付
```

总计: ~117 人天 ≈ 16 周 (1人) / 8 周 (2人)

---

## 附录

### A: 论文参考

PROSE (风格收敛) · ZeroStylus (双层模板) · Weaver (Constitutional DPO) · CoSER (角色模拟) · StoryWriter (NLN + ReIO 压缩) · Agents' Room (多 Agent 叙事) · BookWorld (动态环境) · Revealed vs Stated Prefs (偏好收集) · OSST Authorship (log-prob 度量) · Contrastive Prompting (正反例) · Slop Detection (AI 味检测)

### B: 约束优先级 (system prompt 组装)

1. 硬约束 (世界观/逻辑) → 2. 知识隔离 → 3. 情绪弧线 → 4. 叙事风格 → 5. 修辞风格

### C: 技术栈

```
前端:  React 18 + TypeScript + Tailwind + TipTap(编辑器) + Recharts/D3 + shadcn/ui
后端:  FastAPI + WebSocket (生成流式输出)
存储:  SQLite + ChromaDB + YAML/JSON
AI:    Ollama/vLLM (本地) + OpenAI/Anthropic/DeepSeek API (可选)
ML:    PEFT + bitsandbytes (LoRA) + text2vec-large-chinese (embedding)
NLP:   jieba + SnowNLP
安全:  keyring / Fernet (API key 加密)
```
