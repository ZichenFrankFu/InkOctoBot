# InkOctoBot 用户指南

> 一个 AI 小说创作工作台。把"写网文"这件事拆成电影制作工序——你当
> **导演 + 编剧**，AI 当**剧组**（演员、剪辑、作家、评估师）。

---

## 1. 这个系统是什么？

InkOctoBot 是一个**单用户桌面端 AI 创作工作流**：

- 你输入**世界书 + 人物卡 + 章节大纲**
- 系统按"分镜 → 角色表演 → 剪辑 → 质检"四步生成章节正文
- 系统还可以**学习参考作品**（用户上传 .txt），把其风格 / 角色 /
  节奏拆成可调用的特征
- 系统**记忆贯穿整本书**——4 层记忆系统（即时 / 章节缓冲 / 语义
  / 情节图）+ Truth File 系统（角色当前位置、伏笔状态、关系矩阵
  等"事实权威"）让 AI 在第 50 章不会忘掉第 1 章
- 系统**透明**——每个 LLM 调用都可追溯（trace_id + 调试端点 + 完整
  日志），不是黑箱

**它不是什么**：不是一键写完整本书的工具，不是 ChatGPT 的换皮。你
仍然需要做编剧的决策（角色为什么这样做 / 这章要讲什么）；AI 只负责
把决策**落地为高质量的连续叙事**。

---

## 2. 快速安装与启动

### 2.1 前置依赖

- **Python 3.11+**
- **Node.js 20+**（仅前端构建时需要；运行打包版无需）
- **一个 LLM**（任选其一）：
  - 本地：[Ollama](https://ollama.com/)（推荐 Qwen2.5-14B 或更大）
  - 远程：OpenAI / Anthropic / DeepSeek / Gemini 任一 API key

### 2.2 源码运行（开发态）

```bash
git clone <repo-url> InkOctoBot && cd InkOctoBot
pip install -r requirements.txt
cd ui/frontend && npm ci && npm run build && cd ../..
python launcher.py
```

启动后桌面会弹出 PyWebView 窗口，地址 `http://127.0.0.1:8713`。
首次启动会自动建库到 `data/novels.db`。

### 2.3 测试模式（隔离数据，**强烈建议先用这个**）

```bash
python launcher.py --test
```

会把数据写到 `data_test/`（与生产数据完全隔离），并预置示例项目 +
mock LLM。安全地"按按试试"。

### 2.4 无界面模式（CI / 服务器）

```bash
python launcher.py --no-gui
# 然后用浏览器访问 http://127.0.0.1:8713
```

### 2.5 CLI（可选）

```bash
python cli.py --help                  # ink agent / skill / extract / model / config / db
python cli.py agent list              # 列出所有 agent + skill 数
python cli.py skill list              # 列出所有已注册 skill
python cli.py extract ingest          # 批量 ingest 参考作品 .txt
python cli.py model list              # 当前配置的 LLM provider
python cli.py db info                 # 数据库路径 + 大小
```

---

## 3. 你打开后第一件该做的事

### 3.1 配置一个 LLM（必须）

进入「设置」页（左侧栏底部齿轮图标）→ 「模型供应商」标签：

- **Ollama 本地**：填 `http://localhost:11434` 作为 base_url，
  `models` 列表填你已经 pull 的模型名（如 `qwen2.5:14b`）。**Enabled** 打勾。
- **OpenAI / Anthropic / DeepSeek**：填 api_key + 选你想用的模型
- **测试**：在「Pipeline 配置」里把任意 agent role 绑到刚配的 provider，
  点「测试」按钮，看到非空返回即可。

> 密钥安全：目前 api_key 仍存在 `data/settings.json`。已规划升级到
> 三层解析（env > OS keychain > 旧 JSON 兜底）；当前用户请确保
> `data/` 目录权限收紧。

### 3.2 创建第一个项目

「项目」页 → 「+ 新建项目」：

| 字段 | 说明 |
|---|---|
| 标题 | 任意 |
| 题材 | 玄幻 / 都市 / 科幻 / 末世 / ... |
| Logline | 一句话主线（建议 ≤ 30 字） |
| 目标平台 | 起点 / 番茄 / 通用（影响市场指令注入） |
| 字数目标 | 单章建议 2500-4000 字 |

项目创建后会自动生成 `data/projects/<project_id>.json` + 在
`novels.db` 创建相关行。

---

## 4. 创作流：5 步走完一章

### 4.1 步骤 1 — 准备地基

按从粗到细的顺序在以下页面填资料：

1. **世界书页**（左栏「世界书」）：写世界规则、地图、组织。每条
   `{标题, 分类, 内容}`，是后续所有生成的硬约束（违反 = 评估失败）。
2. **角色管理页**（左栏「角色」）：每个上场角色填卡片
   - **Layer A**（定性）：外貌、性格、背景、说话风格、关系
   - **Layer B**（量化，可选）：决策模型参数（攻击性 / 信任度 /
     好奇心等 0-100 滑块），用于跨章一致性检查
3. **剧情线页**（可选）：拖节点图把故事弧画出来，每个节点关联章节。
4. **大纲**（在编辑器页）：左侧目录树先把卷 + 章名 + 一句话梗概排好。
   每章可以单独填详细大纲（synopsis）+ 出场角色 + 时间 + 地点。

### 4.2 步骤 2 — 关联参考作品（强烈建议）

「参考作品库」页 → 「+ 上传」一本 .txt 小说（UTF-8 或 GB18030）。
系统会自动：

1. **Ingest**：清洗、章节切分、剔除作者闲谈段
2. **Preprocess**：检测格式、分卷、生成 raw + cleaned 副本
3. **Feature Extraction**（你点「提取」触发）：
   - 风格指纹（句长 / 对话比 / 描写密度）
   - 叙事结构（章节级 plot outline）
   - 角色原型 / 世界设定 / 节奏曲线（爽点 / 钩子密度）

然后在「项目」→「关联参考作品」里：勾选要借鉴的作品 ×
要借鉴的维度（风格 / 角色 / 情节 / 节奏）。

### 4.3 步骤 3 — 风格校准（一次性）

「设置」→「风格校准」：拖 4 个滑块（基调 / 节奏 / 修辞 / 视角）+ 受众。
也可以「试笔」让 AI 用当前参数生成一段，你看了不满意继续调。

### 4.4 步骤 4 — 生成一章

「编辑器」页 → 选定一章 → 右栏「AI 创作面板」：

- **单智能体模式**：一个 LLM 调用直接生成正文。最快，适合中小章节。
- **集群式（Film Pipeline）**：四步走，可以看到中间结果
  1. **SceneDirector** 出分镜表（场景列表 + 角色指令）
  2. **ActorAgent** 每个出场角色独立"表演"（带知识隔离）
  3. **NarratorAgent** 补环境描写
  4. **EditorWriter** 剪辑成文学化正文
  5. **Evaluator** 5 维度评估 + 不达标自动 targeted_rewrite（最多 3 次）

**Manual 模式**：勾上「manual」，每一步会弹窗给你 prompt——你把
prompt 复制到任何网页大模型（DeepSeek 网页版 / 通义 / Claude.ai），
把结果粘回来。**完全无需 API key**。

### 4.5 步骤 5 — 审阅 + 反馈

生成完毕后：

- **左栏「评估结果」**：5 维度分 + issues 列表 + 完整 process log
- 你直接在编辑器里改文字。EditAnalyzer 会**学习**你的修改类型
  （删了 AI 味词 / 改了句式 / 增加了对话），下次生成时把你的偏好作为
  「用户写作偏好」自动注入 prompt。
- 「版本历史」面板：每次生成 / 编辑都有快照，可 diff + 回滚。

---

## 5. 几个高级用法

### 5.1 Skill 系统：自定义写作技巧

任何 SKILL.md + skill.py 双文件组合放进 `agents/<area>/skills/<name>/`
就被自动注册。你可以在 UI 的「技能」页查看 / 启用 / 禁用所有 skill，
或自己写一个 skill（CLI `python cli.py skill create my_thing evaluation`
出脚手架）。

InkOctoBot 还能**自己生成 skill**：当 EditAnalyzer 检测到你反复修改
同一类问题（比如反复删某种 AI 味句式），它会自动调用 SkillLearner，
让 LLM 生成一个对应的检测 skill，经过 AST 沙箱校验后热加载。

### 5.2 Truth File 系统：状态权威

第 50 章的人物在哪？身上还剩多少银两？跟反派关系如何？传统记忆做
不到这一点。InkOctoBot v3 引入了 InkOS 风格的 **7 个 Truth File**：

| Truth File | 内容 |
|---|---|
| `current_state` | SPO 三元组 + 章节有效区间（"张三 位置 青云山 [ch12..ch45]"） |
| `particle_ledger` | 资源 / 物品流水（"灵气从 80 降到 50，因为用了破云剑"——闭合方程） |
| `pending_hooks` | 伏笔状态机（open / progressing / pressured / near_payoff / resolved） |
| `chapter_summaries` | 每章总结 + key events + mood |
| `subplot_board` | 并行支线（setup / building / climax / resolution / dormant） |
| `emotional_arcs` | 每角色每章情绪转折 |
| `character_matrix` | 关系矩阵（A 对 B 的 sentiment + trust） |

所有写入走**一个入口** `TruthFileStore.apply_deltas`——12 条跨文件校验
通过才生效，自带 idempotency 日志，可一键导出为 Markdown 视图给 LLM。
完整架构见 `docs/truth_file_system.md`。

### 5.3 市场分析

「市场数据库」页：展示从独立爬虫仓库同步过来的起点 / 番茄榜单数据。
「分析面板」按平台 / 题材 / 时间窗口算热度、机会标签、爽点排行。

这些数据自动注入到 `MarketingAgent` 的选题建议 prompt 里——你创建
新项目时，AI 会基于真实市场数据给「选题 + 书名 + 简介」建议。

---

## 6. 数据存哪？

- `data/novels.db` — 主 SQLite（项目、章节、记忆、Truth Files）
- `data/InkOctoBot_Crawler.db` — 市场数据，只读，由独立爬虫仓库同步
- `data/references.db` — 参考作品库
- `data/chromadb/` — ChromaDB 向量库（语义记忆 + 约束检索）
- `data/projects/`, `data/characters/`, `data/worldbook/`, `data/editor/`,
  `data/storyline/` — 各 collection 的 JSON 文件
- `data/settings.json` — 模型 / pipeline / 系统配置（UI 可写）
- `data/usage.json` — LLM token 使用统计（自动防抖写盘）

详细路径表见 `DOCUMENT.md`。

---

## 7. 故障排查（常见问题）

### 问题: 启动后界面空白
看 `outputs/logs/inkoctobot_*.log`。常见原因：
- 前端没 build：`cd ui/frontend && npm run build`
- 8713 端口被占用：`lsof -i:8713` 查一下
- 日志里有 `Could not init provider` → 设置页面把没用的 provider
  disable 掉

### 问题: 生成不出东西
1. 「设置 → 模型供应商」里至少有一个 enabled 且有 model
2. 「设置 → Pipeline 配置」里 `scene_director` / `editor_writer` /
   `evaluator` 都绑到具体的 provider+model
3. 看右下角 EventBus：`GENERATION_STEP_COMPLETED` 事件有没有发出
4. 看日志 `inkoctobot.llm.router` 行：`route role=X provider=Y model=Z`

### 问题: 章节生成后丢上下文
检查记忆系统：
- L2 缓冲（章节摘要）有没有写入？查 `data/novels.db.chapter_summaries`
- L3 ChromaDB collection 数？`/api/debug/diagnostics`
- 创建了 Truth File 但没看到效果？目前 Writer 集成是 Phase 4，
  写入侧还没完全切——读取侧（prompt 注入）已就位

### 问题: 评估总是过不去
- 看「评估结果」面板的 dimension_scores —— 哪一维分最低
- 检查 `data/settings.json:evaluation.score_threshold`（默认 70）
- 完整 evaluation JSON 在 `inkoctobot.agents.evaluation.evaluator` 的
  DEBUG 日志里——开 JSON 模式：`INKOCTO_LOG_JSON=1 python launcher.py`

---

## 8. 接下来读什么

- `docs/TESTING_AND_LOGS.md` — 测试 + 日志查看指南（**强烈建议读**）
- `docs/truth_file_system.md` — Truth File 系统完整架构（586 行）
- `docs/FEATURES.md` — 功能特性详细清单
- `docs/SKILL_AUTHORING.md` — 写自定义 skill 教程
- `docs/CLI_REFERENCE.md` — CLI 完整参考
- 各 `<package>/WORKFLOW.md` — 每个长流程模块的工作流文档
  （observability / memory / evaluation / production / skills /
  reference_pipeline / reference_ingest）
