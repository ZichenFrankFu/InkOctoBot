# UI 修改方案 — 对接全部后端能力

版本：v1.0 · 适用：`ui/frontend/src/**`

> 写这份的直接动因：用户在 manual paste 流程下走到 ch1 finalize 后，
> `text_versions` 里写入的全是 0 字符。后端 log 全 200 OK、
> `commit_tasks` 跑了 10 行 sub-task，但每个都拿到空文本——说明
> UI 把空内容送到了 save-version。问题不是某一行 React 代码，是
> "Manual paste 流" + "Save Version 流" 这两条路径没接上。

---

## 0. 现状盘点（诚实）

### 0.1 后端有 36 个 router，前端只接了一半

| 后端 router | 前端有 UI? | 缺口 |
|---|---|---|
| `/api/data/*` (项目/角色/世界书/...) | ✅ | OK |
| `/api/editor/*` | ⚠️ 部分 | save-version 路径不可靠 |
| `/api/generation/*` (start, quick-generate, single-writer) | ⚠️ 部分 | 多 agent 入口 / confirm-gate / 事件流暴露不全 |
| `/api/llm-paste/*` (手动粘贴收件箱) | ⚠️ 仅嵌入式 | 没有独立 Inbox 页面 |
| `/api/notifications/*` | ⚠️ | 入口隐蔽，没"事件流"视图 |
| `/api/llm-audit/*` | ⚠️ | DevConsole 里有，主流程看不到 |
| `/api/commit-pipeline/*` (sub-task 状态) | ❌ | 完全没暴露 |
| `/api/state-review/*` (Truth Files audit gate) | ❌ | audit_failed 没 UI 走通 |
| `/api/snapshots/*` + reminder | ❌ | 章节快照入口缺失 |
| `/api/historical-view/*` | ❌ | 完全没用 |
| `/api/validator/*` | ❌ | 完全没用 |
| `/api/security/*` | ❌ | 完全没用 |
| `/api/embedding/*` 切换 + reindex | ⚠️ | 只在 Settings 里，缺 reindex 进度 |
| **`/api/domain-learning/*` (Part B 领域知识)** | ❌ **本次新增功能 0 UI** | gate1 / 编译 / gate2 / accept 全靠 curl |
| **Edit Learning (Part A 偏好捕获)** | ❌ **本次新增功能 0 UI** | 用户看不到自己被学到了什么 |
| `/api/market-extractor/*` + `/api/platform-profiles/*` | ⚠️ | 市场页有，但 platform_directive 串到生成的链路看不见 |

### 0.2 现有页面的关键 bug（已诊断）

| 页面 | 问题 | 状态 |
|---|---|---|
| `EditorPage` | 章节树空 volumes 数组崩溃 | ✅ 已修（seed 补 editor blob） |
| `EditorPage` | `manifest.writing_knowledge.length` 崩溃 | ✅ 已修（后端补字段） |
| `EditorPage` | save-version 把空字符串提交（**当前主问题**） | ❌ 待修，本 spec §3 |
| `EditorPage` | WebLLMPromptPanel × 5 处复用，行为不一致 | ❌ 待统一 |
| `EditorPage` | chat-history PUT 因 message_id 重用 500 | ✅ 已修（后端 INSERT OR REPLACE） |
| `EditorPage` | chapter_outline + time_location + characters_block 三处重复显示 | ⚠️ production prompt 模板 overlap |
| 任何页面 | 后端 sub-task 状态 / commit_tasks 失败 | ❌ 没有任何 UI 暴露 |
| 任何页面 | Truth Files audit_failed 阻断 | ❌ 没有 review UI |

### 0.3 用户感知到的直接痛点

1. **"我点了生成、贴了内容、保存了，但 verify 看到 chars=0"** —— 不知道贴到了哪、不知道保存的是哪一份。
2. **"prompt 里看到很多重复 / 漏字段"** —— 不知道哪些 loader 真的注入了。
3. **"我编辑了正文也保存了，没看到任何反馈"** —— 不知道 edit_observation 是否捕获、什么时候触发批量提取。
4. **"通知中心要点几下才看到"** —— 后台事件不进入主流程。

---

## 1. 设计目标

| # | 目标 | 不目标 |
|---|---|---|
| G1 | **数据流可见**：用户能看到 prompt 里实际装了什么、LLM 实际返了什么、写进了哪张表 | 重做视觉风格 |
| G2 | **Manual paste 是一等公民**：流程明确、token 可追踪、贴错位置不会丢内容 | 替换 API mode |
| G3 | **后端新功能不掉队**：Part A 自学习 / Part B 领域知识 / Truth gates 都有专属 UI | 完成 100% feature parity |
| G4 | **状态机透明**：每章节当前在哪一步（draft / scene_planned / acted / edited / evaluated / finalized）一眼可见 | 用 GraphQL 重构 |
| G5 | **失败可诊断**：commit sub-task 报错 / pipeline 中断 / audit_failed 都直接显示给用户 | 移植到 Next.js |

---

## 2. 信息架构（顶层）

```
顶部导航：
  [项目]  [创作]  [素材]  [学习反馈]  [市场参考]  [系统]
                                      ↑ 新增
                  ↑ 编辑器 (主战场)

侧栏（创作时）：
  章节树 │ 当前章节状态 │ Manual Paste 收件箱（如有 pending）

右侧抽屉（创作时）：
  Prompt 透视 │ 通知 │ LLM 审计 │ Truth 状态
```

具体页面分组：

```
项目 (P)
├─ P1  项目列表       /projects
├─ P2  项目仪表盘     /projects/:id
└─ P3  项目设置       /projects/:id/setup        (含 calibration / 平台 / market 路径 / reference 关联)

创作 (W)
├─ W1  编辑器          /projects/:id/editor       (重构核心)
│   ├─ 章节树          ← /api/data/editor
│   ├─ 章节正文        ← text_versions current
│   ├─ Prompt 透视      ← /api/generation/context-manifest
│   ├─ 生成控制台      ← multi-agent /start | quick-generate | single-writer
│   ├─ Manual Paste 区  ← /api/llm-paste/* (本章 token)
│   ├─ 评估面板        ← evaluation_json
│   ├─ 版本历史 + Diff  ← /api/editor/versions
│   └─ Audit Gate     ← Truth Files settlement issues
├─ W2  Pipeline 历史   /pipeline-history          (新)  ← /api/historical-view + pipeline_sessions
└─ W3  Manual Paste Inbox /paste-inbox            (新)  ← /api/llm-paste/pending (跨章/全局)

素材 (M)
├─ M1  角色            /projects/:id/characters
├─ M2  世界书          /projects/:id/worldbook
├─ M3  故事线 / 支线   /projects/:id/storyline
├─ M4  灵感库          /projects/:id/inspirations
└─ M5  写作技能        /skills                     (跨项目)

学习反馈 (L)  ← 全部新增
├─ L1  写作偏好        /projects/:id/preferences  ← /user_style_preferences + edit_observations
├─ L2  编辑观察日志     /projects/:id/edits        ← edit_observations consumed=0/1
└─ L3  领域知识        /projects/:id/domain       ← /api/domain-learning/* (gate1 + gate2)

市场参考 (R)
├─ R1  市场总览        /market                    ← /db/* + /analysis
├─ R2  参考作品库       /references                ← /references/works
├─ R3  趋势分析        /market/trends             ← /analysis/run
└─ R4  Marketing Agent /market/agent              ← /marketing

系统 (S)
├─ S1  通知中心        /notifications            (改进现有)
├─ S2  LLM 审计        /llm-audit                ← /api/llm-audit (现 DevConsole 里)
├─ S3  设置            /settings                 (provider / embedding / market 路径 / threshold)
├─ S4  Truth State Review /truth-review          (新)  ← /api/state-review
└─ S5  开发者          /dev                       (保留 DevConsole)
```

---

## 3. 核心修复：Manual Paste + Save Version 链路

**问题复述**：用户复制 prompt → 网页 LLM 跑 → 拷回 → 贴到编辑器 → 点保存 → text_versions 0 字符。

### 3.1 现有问题（根因）

```
现有流（隐式 / 多入口 / 状态丢失）:

  EditorPage 内 5 处 WebLLMPromptPanel 实例（行为各自定义）
       │
       ├─ outline chat 用一个 panel  → 贴回直接进聊天历史
       ├─ generate 用另一个 panel    → 贴回 onApplyResult 回调是什么?
       ├─ rewrite 又一个              → 替换正文片段
       └─ ...
       
  "保存版本" 按钮独立，读 textarea ref
       │
       └─ 若贴到 outline_chat 而非正文 textarea，
          save-version 拿到的就是空字符串
```

### 3.2 新流（显式 / 单一 Manual Mode 协议）

```
                   ┌─ API 模式 ─→  router.invoke()  ─┐
点击「生成本章」 ──┤                                  ├─→  draft_buffer
                   └─ Manual ────→  paste token 注册 ─┘    （未提交 / 可编辑）
                                          ↑
                                  Paste Inbox 显示
                                  用户点 "提交" 后写入

  draft_buffer → 用户预览 → 编辑（可选）→ 点 「保存为版本」
                                                  │
                                                  ↓
                                            text_versions
                                            (source 由实际来源决定:
                                             ai / user_edit / rewrite)
```

### 3.3 关键改动（前端 + 一点后端契约）

#### 3.3.1 删除 EditorPage 里的 5 个 WebLLMPromptPanel 复用

替换为 **唯一一个** `<GenerationConsole>` 组件，统一管理：

```tsx
<GenerationConsole
  chapterId={activeChId}
  mode={'multi_agent' | 'quick' | 'rewrite' | 'outline_chat'}
  onDraft={(text, source) => setDraftBuffer({text, source})}
/>
```

Console 内部按 mode 选 endpoint：
- `multi_agent`  → `POST /api/generation/start`，订阅事件流，confirm-gate 走 `/confirm`
- `quick`        → `POST /api/generation/quick-generate`，文本回到 draft buffer
- `rewrite`      → `POST /api/generation/rewrite`
- `outline_chat` → `POST /api/outline-chat` + 写聊天历史

Console **永远不直接** 写 text_versions —— 总是回到 draft buffer。

#### 3.3.2 引入 `draft_buffer` 中间态

新组件 `<DraftPreview>`：

```
┌─ 草稿预览 ──────────────────────────────┐
│ 来源: ⚪ AI (qwen2.5:14b)               │
│        ⚪ Manual paste (token: ...)     │
│        ⚪ Web LLM (网页版手贴)          │
│                                          │
│ 字数: 1245 / 目标 2000                  │
│ ─────────────────────────────────────── │
│ [正文预览 (可编辑)]                      │
│ ...                                      │
│ ─────────────────────────────────────── │
│ [取消]  [保存为新版本]                   │
└──────────────────────────────────────────┘
```

`保存为新版本` 才调 `/api/editor/save-version`，**带字数校验**（chars < 50 弹"内容看起来是空的，确定保存？"）。这一个改动就能堵住"text_versions=0 chars"的洞。

#### 3.3.3 Manual Paste Inbox 升级为顶级页面

```
┌─ Manual Paste Inbox ───────────────────┐
│ Pending (3)                             │
│ ──────────────────────────────────────  │
│ ▸ rt_proj / ch1 / scene_director       │
│   prompt: "你是场景导演..."  [复制]    │
│   [贴回结果框  __________  ] [提交]    │
│ ▸ rt_proj / ch1 / actor (林越)         │
│ ▸ ...                                   │
└─────────────────────────────────────────┘
```

打通：每个 `LLMCallSite` 在 manual mode 下注册的 token，都有专属粘贴入口。提交后 token resolve → pipeline 继续。

后端契约：`/api/llm-paste/pending` 已经有，添加每条 token 的 `chapter_id` / `agent_role` / `prompt_preview` 字段方便 UI 显示。

---

## 4. 各页面具体修改

### 4.1 EditorPage（最大改造）

**当前**: 单一巨型组件，3000 行，5 处复用 `WebLLMPromptPanel`，逻辑纠缠。

**目标布局**：

```
┌─ Editor (project rt_proj / ch1) ───────────────────────────────────┐
│ 左侧 (固定 240px):                                                │
│   章节树                                                          │
│     第一卷                                                        │
│       ⊙ ch1 [draft]    林越发现裂痕                              │
│       · ch2             残响同步                                  │
│       · ch3             ...                                       │
│   [+ 新增章节]                                                    │
│                                                                    │
│ 中央 (主区域):                                                    │
│   ┌─ 章节信息 ───────────────────────────────────────┐           │
│   │ 标题 / 大纲 / 时间地点 / POV / 出场角色 /         │           │
│   │ 本章特别要求 (有提示气泡 → 学习偏好用)           │           │
│   └──────────────────────────────────────────────────┘           │
│   ┌─ 生成控制台 ─────────────────────────────────────┐           │
│   │ [多 agent 流] [快速生成] [Manual Paste] [自己写] │           │
│   │ 事件流: SceneDirector → Actor → Writer ...      │           │
│   │ 当前步骤: editor_writer (40%)                    │           │
│   │ [取消] [跳到下一步 confirm]                      │           │
│   └──────────────────────────────────────────────────┘           │
│   ┌─ 草稿 / 正文 ────────────────────────────────────┐           │
│   │ tab: [当前正文(v3)] [草稿待提交] [评估]          │           │
│   │ ┌──────────────────────────────────────┐         │           │
│   │ │ [TextEditor]                          │         │           │
│   │ │ 1245 字 / 目标 2000                   │         │           │
│   │ └──────────────────────────────────────┘         │           │
│   │ [保存为新版本]  [回滚到 v2]  [Diff]              │           │
│   └──────────────────────────────────────────────────┘           │
│                                                                    │
│ 右侧抽屉 (默认收起，按钮展开):                                    │
│   tab: [Prompt 透视] [通知] [审计] [Truth] [偏好]                │
│                                                                    │
│   Prompt 透视:                                                    │
│     14 个 loader 状态:                                            │
│       ✅ user_special_requirements (220 chars)                   │
│       ❌ platform_directive (未配置)                              │
│       ✅ worldbook (810 chars)                                    │
│       ... [展开看完整 prompt]                                     │
└────────────────────────────────────────────────────────────────────┘
```

**关键变化**：
1. 去除 `WebLLMPromptPanel` 多处复用 → 用 `<GenerationConsole mode=...>` 统一
2. 引入 `<DraftPreview>` 中间态 → 防止空 save
3. 引入 `<PromptInspector>` 显示 14 个 loader 状态 → 用户能看到 worldbook 有没有真注入
4. 右侧抽屉显示通知 / 审计 / Truth 状态 / 已学到的偏好 → 把后端隐藏状态拉出来

### 4.2 新增 `DomainLearningPage` (`/projects/:id/domain`)

对接 `/api/domain-learning/*`：

```
┌─ 领域知识 (rt_proj) ─────────────────────────────────┐
│ Proposed (1)    Compiling (0)    Needs Review (0)    │
│ Accepted (0)    Rejected (0)                          │
│ ─────────────────────────────────────────────────── │
│                                                       │
│ ▸ 航天动力学 (Proposed)                              │
│   理由: 你在 5 章中反复要求航天技术真实感           │
│   [研究 (API 模式 ~$0.05)] [研究 (手动)] [拒绝]     │
│                                                       │
│ ───── 已编译待审 ─────                                │
│   (空)                                                │
│                                                       │
│ ───── 已采纳 ─────                                    │
│   (空)                                                │
└───────────────────────────────────────────────────────┘
```

**Gate 2 软门**：点 "Needs Review" 行 → 弹大对话框 → 显示 compiled_content (markdown 渲染) → `[直接采纳]` / `[编辑后采纳]` / `[弃用]` 三个按钮。`编辑后采纳` 进入 inline editor。

### 4.3 新增 `PreferencesPage` (`/projects/:id/preferences`)

```
┌─ 写作偏好 (从你的编辑中学习) ────────────────────────┐
│                                                       │
│ 风格偏好 (3)                                          │
│   ✓ 偏好克制叙述               conf=0.55  obs=3      │
│   ✓ 短句、名词为主              conf=0.45  obs=3      │
│   · 强调技术真实感              conf=0.30  obs=2     │
│                                                       │
│ 内容偏好 (1)                                          │
│   · 聚焦舱段结构与轨道细节      conf=0.40  obs=2     │
│                                                       │
│ 节奏偏好 (0)                                          │
│   (空)                                                │
│                                                       │
│ ─────────────────────────────────────────────────── │
│ 下次批量学习: 还差 1 章编辑 (阈值=5，当前 4)         │
│ [立即触发批量学习] [调整阈值]                         │
└───────────────────────────────────────────────────────┘
```

每条偏好右键支持 "禁用 / 删除"（写到表的额外字段）。

### 4.4 新增 `TruthReviewPage` (`/truth-review`)

对接 `/api/state-review/*`：

```
┌─ Truth State Review ────────────────────────────────┐
│ Project: rt_proj                                     │
│ ─────────────────────────────────────────────────  │
│ ⚠️ ch3 audit_failed                                  │
│   issues:                                             │
│     • 角色"邓星"在 ch1 已知裂痕，ch3 又"首次发现"   │
│     • 时间矛盾：ch3 说"第三日"，ch2 也是"第三日"    │
│                                                       │
│   [人工标记为可接受] [回到 ch3 编辑] [回退到 v1]    │
│                                                       │
│ ✅ ch1 audit_passed (2 SPO)                          │
│ ✅ ch2 audit_passed (3 SPO)                          │
└──────────────────────────────────────────────────────┘
```

### 4.5 新增 `PipelineHistoryPage` (`/pipeline-history`)

对接 `/api/historical-view/*` + `pipeline_sessions`：

```
session_id | project | chapter | started_at | status | duration | view
─────────────────────────────────────────────────────────────────────
gen_abc123 | rt_proj | ch5     | 19:00      | complete | 3m20s  | [→]
gen_def456 | rt_proj | ch4     | 14:30      | paused_audit | -- | [→]
...

点 [→]:
  完整事件流回放（agent → result → confirm → next agent ...）
  每步耗时 / token 数 / 成本
  可重放某一步
```

### 4.6 Settings 页扩展

加入新 spec 引入的设置：

```
[模型 & API]
  Provider | qwen2.5:14b (ollama)  ✓
  Manual mode (全局开关)   ☐
  Cost confirm 阈值       ¥0.10

[Embedding]
  当前模型: bge-base-zh
  语言模式: 中文
  [切换模型] [全量 reindex]

[学习反馈]
  ★ 编辑批量学习阈值: [5] 章 (1-50)
  ★ Special_req 权重: 0.40 (vs edit: 0.15)  [保持默认]

[市场 / 参考]
  Market DB 路径: data/market.db    [浏览]
  Reference DB 路径: data/references.db
```

---

## 5. 横切组件

### 5.1 `<PromptInspector>` —— 解决"prompt 里到底有啥"

```tsx
<PromptInspector chapterId={id} mode="single">
  // 内部:
  // - 拉 /api/generation/context-manifest
  // - 拉 /api/generation/render-prompt?prompt_only=true
  // - 14 loader 各自显示: 块名 / 字数 / 是否激活 / [展开]
  // - 整个 prompt 一键复制
</PromptInspector>
```

放在编辑器右侧抽屉 + 生成前确认对话框里。

### 5.2 `<EventStream>` —— 多 agent 实时事件

```tsx
<EventStream sessionId={sid}>
  // 订阅 WebSocket /api/generation/events/:sid
  // 渲染事件时间线:
  //   ⏵ pipeline_start
  //   🎬 scene_director → 30 scenes planned [confirm?]
  //   🎭 actor (林越) → 850 chars
  //   📝 editor_writer (40%) [streaming...]
</EventStream>
```

每个 confirm-gate 步骤显示行内按钮：`[满意，下一步]` / `[需要调整]` / `[终止]`。

### 5.3 `<NotificationFeed>` —— 后台事件主动 surface

订阅 `/api/notifications/feed`：

```
🔔 post-commit (ch1) — chromadb_indexer 失败
🔔 batch_extract triggered — 学到 4 条偏好  [→ 看]
🔔 领域建议: 航天动力学  [→ 决定]
🔔 audit_failed (ch3) — 需要人工审阅  [→ 去 Review]
```

不只显示，要带 deep-link 跳到对应页面/行。

### 5.4 `<ManualPasteSlot>` —— 嵌入式粘贴入口

每个会触发 LLM 调用的按钮旁边显示：

```
[生成 (API)]   [生成 (手动)]
                  ↓ 点击后
                  ┌─ Manual Paste ───────────┐
                  │ 1. 复制下面的 prompt:    │
                  │   [复制 (2300 chars)]    │
                  │ 2. 在网页 LLM 跑         │
                  │ 3. 粘回结果:             │
                  │   [_________________]    │
                  │   [提交]                 │
                  └──────────────────────────┘
```

每次提交都创建一个 paste token（如果是某个 LLMCallSite），或直接走 draft_buffer（如果是 quick-generate 类）。

---

## 6. 后端契约调整（小，配合 UI）

| 改动 | 现状 | 需要 |
|---|---|---|
| `/api/llm-paste/pending` 响应增字段 | 只有 token / prompt | 加 `chapter_id`, `agent_role`, `system_prompt`, `created_at` |
| `/api/generation/context-manifest` 增 loader 状态 | rag / skills 三个数组 | 加 `loaders: [{block_id, active, char_count, render_preview}]` |
| `/api/generation/render-prompt` 新增 | 没有 | GET 返回当前 chapter 渲染好的完整 prompt（含 system + user）|
| `/api/notifications/feed` SSE | poll-based | 改 server-sent events，UI 实时收 |
| `/api/commit-pipeline/status/:chapter_id` 新增 | 没有 | 列 6 sub-task 各自状态、错误 |
| `pipeline_sessions` 列表 API | 部分 | 加 `GET /api/generation/sessions?project_id=...` |

---

## 7. 优先级（落地路线图）

### P0 — 修当前 paste 流程的 0 字符灾难（1-2 天）

1. ✏️ EditorPage 引入 `draftBuffer` 中间态 + `<DraftPreview>` 组件
2. ✏️ "保存为新版本" 字数校验 + 来源 source 标签
3. ✏️ 删除 5 处 WebLLMPromptPanel 复用，集中到 `<GenerationConsole>`
4. ✏️ Manual Paste Inbox 升级独立页面 `/paste-inbox`，token 含 chapter context

### P1 — 让后端新功能可用（3-5 天）

5. 🆕 `DomainLearningPage` (Part B 完整 UI)
6. 🆕 `PreferencesPage` (Part A 可视化)
7. 🆕 `TruthReviewPage` (audit_failed 走通)
8. 🆕 `PromptInspector` 右抽屉
9. 🆕 `EventStream` 多 agent 事件流
10. 🆕 `NotificationFeed` deep-link

### P2 — 透明度 + Polish（5-7 天）

11. 🆕 `PipelineHistoryPage` (session 回放)
12. 🆕 `<CommitTaskStatus>` 显示 6 sub-task 进度
13. ✏️ Settings 加 edit_learning_threshold / cost_confirm UI
14. ✏️ LLM Audit 从 DevConsole 提到顶层
15. ✏️ 全局快捷键 / 主题统一

### P3 — 锦上添花

16. `<ABCompare>` 两个 model 输出并排对比
17. `<TokenCostMeter>` 实时 token 消耗
18. 国际化 (i18n) 把所有硬编码中文走 t() 函数

---

## 8. 实施约束

### ✅ 必做
- 每个新增 / 重构页面：先写单元测试（vitest）验证 API 调用契约
- 任何"保存"按钮都要二次确认 + 字数 / 内容校验
- 所有错误必须 surface（不能只 console.log，要进 NotificationFeed）
- 删 `WebLLMPromptPanel` 时保留旧逻辑作为参考，新组件验证通过再删
- TS 类型定义 (`api/types.ts`) 必须和后端响应 100% 一致 —— 多次崩在这

### ❌ 不做
- 不重写视觉风格（保留现有 theme）
- 不替换 React/Vite 工具链
- 不引入 GraphQL / tRPC（FastAPI REST 已经够用）
- 不引入新的状态管理库（现有 useState/Context 够 P0）
- 不为 i18n 阻塞 P0/P1（中文硬编码先维持）

---

## 9. 测试策略

### 9.1 前端单测（vitest）

每个新组件至少 3 个测试：
- 默认渲染不报错
- API mock 成功 → 渲染期望内容
- API mock 失败 → 走 ErrorBoundary 不崩

### 9.2 端到端（playwright，新增）

P0 流程必走 playwright：
```
test('manual paste flow saves correct content', async ({ page }) => {
  await page.goto('/projects/rt_proj/editor');
  await page.click('text=ch1');
  await page.click('text=生成 (手动)');
  // Copy prompt
  const prompt = await page.locator('[data-test=prompt-preview]').innerText();
  expect(prompt).toContain('启明号');
  // Paste mock content
  await page.locator('[data-test=paste-area]').fill('林越被警报叫醒...' + 'x'.repeat(1000));
  await page.click('text=提交');
  // Draft preview shows
  await expect(page.locator('[data-test=draft-preview]')).toBeVisible();
  await expect(page.locator('[data-test=word-count]')).toContainText('1000+');
  // Save
  await page.click('text=保存为新版本');
  // verify via backend
  const res = await page.request.get('/api/data/versions?project_id=rt_proj&chapter_id=rt_ch1');
  const data = await res.json();
  expect(data.versions[0].content.length).toBeGreaterThan(500);
});
```

### 9.3 后端契约测试（pytest）

后端契约改动（§6）的每一项加测试：
- `/api/llm-paste/pending` 响应含新字段
- `/api/generation/context-manifest` 响应含 `loaders`
- `/api/generation/render-prompt` 端点存在 + 形态正确
- `/api/commit-pipeline/status/:cid` 形态

---

## 10. 验收

P0 完成的判据：
- ☐ 用户走 Manual Paste 流，**保存的 text_versions chars > 0**（用 `_diag.py` 验证）
- ☐ 用户能在 UI 看到自己刚才 paste 的内容字数 + 来源标签
- ☐ 字数 < 50 时阻止 / 提醒保存
- ☐ EditorPage 不再 import WebLLMPromptPanel
- ☐ Manual Paste Inbox 显示完整 pending 列表

P1 完成的判据：
- ☐ Part B 领域知识闸门 1 / 2 在 UI 可走完整 flow，**用户没碰过命令行**
- ☐ 用户看得见已学到的偏好 + 当前 batch 进度
- ☐ audit_failed 章节在 TruthReviewPage 有专门的解决入口
- ☐ Prompt Inspector 显示 14 loader 状态 + 完整 prompt
- ☐ EventStream 展示多 agent 实时进度

---

## 附录 A：后端 endpoint 覆盖矩阵

| Endpoint | UI 页面 | 优先级 |
|---|---|---|
| `GET /api/data/projects` | P1 ProjectList | 现有 |
| `POST /api/data/projects` | P1 ProjectList | 现有 |
| `GET /api/data/editor` | W1 EditorPage | 现有 |
| `POST /api/data/editor` | W1 EditorPage | 现有 |
| `POST /api/editor/save-version` | W1 → `<DraftPreview>` | **P0 重写** |
| `GET /api/editor/versions` | W1 → VersionHistory | 现有 |
| `POST /api/generation/start` | W1 → `<GenerationConsole multi_agent>` | **P0 重写** |
| `POST /api/generation/quick-generate` | W1 → `<GenerationConsole quick>` | **P0 重写** |
| `POST /api/generation/rewrite` | W1 → `<GenerationConsole rewrite>` | P1 |
| `POST /api/generation/single-writer` | W1 → option | P1 |
| `GET /api/generation/context-manifest` | W1 → `<PromptInspector>` | **P1 新** |
| `GET /api/generation/render-prompt` | W1 → `<PromptInspector>` | **P1 新（需后端配合）** |
| `POST /api/generation/confirm` | W1 → `<EventStream>` confirm 按钮 | **P1 新** |
| `GET /api/generation/sessions` | W2 PipelineHistory | **P2 新（需后端）** |
| `GET /api/llm-paste/pending` | W3 PasteInbox | **P0 重做** |
| `POST /api/llm-paste/:token` | W3 PasteInbox | **P0 重做** |
| `GET /api/notifications/*` | S1 NotificationFeed | **P1 改进** |
| `GET /api/llm-audit/*` | S2 LLMAuditPage | P2 |
| `GET /api/commit-pipeline/status/:cid` | W1 → `<CommitTaskStatus>` | **P2 新（需后端）** |
| `GET /api/state-review/*` | S4 TruthReviewPage | **P1 新** |
| `POST /api/domain-learning/suggest` | L3 DomainLearningPage | **P1 新** |
| `GET /api/domain-learning/suggestions` | L3 | **P1 新** |
| `POST /api/domain-learning/:id/approve` | L3 gate 1 | **P1 新** |
| `POST /api/domain-learning/:id/submit-manual` | L3 manual mode | **P1 新** |
| `POST /api/domain-learning/:id/accept` | L3 gate 2 | **P1 新** |
| `GET /api/skills/*` | M5 SkillsPage | 现有 |
| `GET /api/embedding/*` | S3 Settings | 现有（缺 reindex 进度）|
| `GET /api/historical-view/*` | W2 PipelineHistory | **P2 新** |
| `GET /api/snapshots/*` | M3 Storyline 内嵌 | P2 |
| `GET /api/validator/*` | DevConsole（保留）| P3 |
| `GET /api/security/*` | DevConsole（保留）| P3 |
| `GET /api/market-extractor/*` | R1 Market | 现有 |
| `GET /api/platform-profiles/*` | R1 Market / S3 Settings | P2 |
| `GET /api/references/*` | R2 ReferenceLibrary | 现有 |
| `GET /api/data/inspirations` | M4 Inspirations | 现有 |
| `GET /api/db/*` (market crawler) | R1 Market | 现有 |
| `GET /api/analysis/*` | R3 Trends | 现有 |
| `GET /api/marketing/*` | R4 Marketing Agent | 现有 |

---

## 附录 B：现有可复用 vs 必须重写

| 现有组件 | 处理 |
|---|---|
| `components/shared/Confirm.tsx` | ✅ 保留 |
| `components/shared/Dialog.tsx` | ✅ 保留 |
| `components/shared/ErrorBoundary.tsx` | ✅ 保留 + 增强（带 issue 报告链接）|
| `components/shared/Toast.tsx` | ✅ 保留 |
| `components/shared/InlineDiff.tsx` | ✅ 保留 |
| `components/shared/AIChatPanel.tsx` | ✅ 保留（outline_chat 用）|
| `components/shared/WebLLMPromptPanel.tsx` | ❌ **P0 删除**，被 `<GenerationConsole>` + `<ManualPasteSlot>` 替代 |
| `components/shared/GlobalSearch.tsx` | ✅ 保留 |
| `components/shared/FollowUpQuestions.tsx` | ✅ 保留 |
| `components/editor/ChapterTree.tsx` | ✅ 保留（重构后单独提取）|
| `components/editor/EvalReport.tsx` | ✅ 保留 |
| `components/editor/TextEditor.tsx` | ✅ 保留 |
| `components/editor/VersionHistory.tsx` | ✅ 保留 |
| `pages/EditorPage.tsx` | ⚠️ **P0 拆**: 3000 行 → 主页 < 400 行 + 子组件 |

---

## 附录 C：本 spec 与已落地工作的关系

| 之前 spec / commit | 状态 | 本 spec 关系 |
|---|---|---|
| ChapterContext 迁移 (15 loader) | ✅ 已合 | 本 spec §5.1 `<PromptInspector>` 显示 14 loader 状态 |
| Edit Learning Part A (批量学习) | ✅ 已合 | 本 spec §4.3 `PreferencesPage` 是它的 UI |
| Domain Knowledge Part B (领域知识) | ✅ 已合 | 本 spec §4.2 `DomainLearningPage` 是它的 UI |
| Realdata E2E 测试 (Layer A) | ✅ 已合 | 本 spec 不动 |
| seed_project.py | ✅ 已合 | 本 spec 不动 |
| `idea_schema` 顺序修复 | ✅ 已合 | — |
| `chat_messages` UNIQUE 修复 | ✅ 已合 | — |
| Frontend `writing_knowledge` 补字段 | ✅ 已合 | 长期: 重新打包前端 bundle 干净 |

---

Spec 结束。

执行时建议：每个 P0 改动开独立 PR，本 spec 作为索引。
