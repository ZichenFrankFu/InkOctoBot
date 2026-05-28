# InkOctoBot Loader 系统完整规格说明（后端纯净版）

本文档是 InkOctoBot prompt 装配系统的完整 spec。包含 14 个 loader 的所有后端细节，可直接交付给 Claude Code 进行实施。UI 改动留给用户后续自行处理。

---

## 文档概览

### 命名约定

```
原 Memory 系统           -> ReaderMemory（读者视角记忆）
原 Truth Files 系统       -> StorylandState（小说世界客观状态）
                            Storyland 指代正在创作的小说中的世界
```

### Loader 目录结构

```
ui/backend/app/services/prompt_context/loaders/
├── market/
│   ├── market_overview.py            # Loader 1
│   └── platform_style.py             # Loader 2
├── library/
│   ├── reference.py                  # Loader 3 主入口
│   ├── reference_features/
│   │   ├── characters.py
│   │   ├── plot.py
│   │   ├── worldview.py
│   │   ├── text_features.py
│   │   └── exemplars.py
│   └── inspiration.py                # Loader 4
├── project_static/
│   ├── character_cards.py            # Loader 5
│   ├── worldbook.py                  # Loader 6
│   └── chapter_outline.py            # Loader 7
├── project_dynamic/
│   ├── reader_memory.py              # Loader 8
│   ├── current_chapter_draft.py      # Loader 9
│   ├── storyland_state.py            # Loader 10
│   ├── foreshadowing.py              # Loader 11
│   └── subplots.py                   # Loader 12
└── learning/
    ├── user_preferences.py            # Loader 13
    └── skills.py                      # Loader 14
```

### 14 个 Loader 速查

| # | Loader | 使用对象 | Budget | 接入位置 |
|---|---|---|---|---|
| 1 | market_overview | 开书助手 | 1500 | 独立 endpoint |
| 2 | platform_style | Writer | 250 | System 段 |
| 3 | reference | Writer | 2400 | System 段 |
| 4 | inspiration | Writer | 800 | Context 段 |
| 5 | character_cards | Writer | 2200 | Context 段 |
| 6 | worldbook | Writer | 1600 | Context 段 |
| 7 | chapter_outline | Writer | 1200 | User 段 |
| 8 | reader_memory | Writer | 4500 | Context 段 |
| 9 | current_chapter_draft | Writer | 4000 | User 段 |
| 10 | storyland_state | Writer | 2000 | Context 段 |
| 11 | foreshadowing | Writer | 1200 | Context 段 |
| 12 | subplots | Writer | 1200 | Context 段 |
| 13 | user_preferences | Writer | 500 | System 段 |
| 14 | skills | Writer | 2400 | Context 段 |

总 Context 装配预算：约 25,800 字符（约 16K tokens），含 user msg 后总 prompt 约 18-20K tokens。

---

# Loader 1: market_overview

### 使用场景

仅用于新项目开书阶段的市场调研。User 在开书助手页面调用，查看当前题材市场份额、增长率、热门标签、爆款门槛等数据，辅助决策开什么题材。**不参与每章 prompt 装配**。

### 数据源

- `InkOctoBot_Crawler.db`（爬虫数据库，已存在）
- 通过 `analysis_api.run_analysis()` 实时聚合
- 30 分钟内存缓存

### 函数签名

```python
def load(
    platform: str,                    # 'qidian' / 'fanqie' / 'qimao'
    category: str | None = None,
    lookback_days: int = 90,
) -> str:
    """加载市场题材分析数据，供开书助手使用。"""
```

### 核心逻辑

1. 检查内存缓存（key = platform × category × lookback_days），命中则返回
2. 调 `analysis_api.run_analysis()` 实时聚合 crawler DB 数据
3. 计算指标：
   - 题材份额（top 10）+ 趋势（与 30 天前对比）
   - 当前上升中的题材
   - 热门标签 frequency
   - top 100 新书的收藏推荐均值
   - 新书 30 天爆款日订门槛
4. 渲染为 markdown 文本
5. 写入缓存

### 输出格式

```markdown
## 市场概览（起点·玄幻，近 90 天）

### 题材份额（top 10）
- 修真：18.2%（趋势 +2.1%）
- 都市修真：12.5%（趋势 +5.3%，上升中）
- 末日：8.7%（趋势 -1.2%）

### 当前开书机会（份额上升中）
- 都市修真 +5.3%
- 系统流末日 +4.8%

### 热门标签
- 大女主、扮猪吃虎、扮黑历史、轻松向

### 数据基准
- top 100 新书：收藏 8000，推荐 12000
- 新书 30 天爆款门槛：日均订阅 200
```

### Budget

1500 字符

### 暴露 API

```
GET /api/storyland/market-snapshot?platform=X&category=Y&lookback_days=Z
```

---

# Loader 2: platform_style

### 使用场景

每章生成时为 Writer 提供平台特征基准。让 Writer 知道目标平台 + 题材的成功作品在开局、句式、惯用手法上的统计特征。**不包含"忌讳"**——市场数据只能提供"成功作品做了什么"，无法提供"应避免什么"。

### 数据源

- 新表 `platform_profiles`（离线 extractor 生成，第一版可用 placeholder）

### 数据库 schema

```sql
-- storage/platform_profile_schema.py
CREATE TABLE IF NOT EXISTS platform_profiles (
    platform TEXT NOT NULL,
    category TEXT NOT NULL,
    profile_json TEXT NOT NULL,
    source_works_count INT NOT NULL,
    extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (platform, category)
);
```

`profile_json` 结构（**注意：删除了 taboo_patterns**）：

```json
{
    "opening_patterns": {
        "in_medias_res": 0.42,
        "dialogue_open": 0.18,
        "worldbuilding": 0.13,
        "character_intro": 0.27
    },
    "first_chapter_signals": {
        "first_dialogue_position_pct": 12,
        "first_conflict_position_pct": 25,
        "first_shuangdian_position_pct": 45
    },
    "style_baseline": {
        "avg_sentence_length": 18,
        "short_sentence_ratio": 0.58,
        "dialogue_ratio": 0.31,
        "description_density": 0.24
    },
    "signature_devices": [
        "第一章末必有强钩子",
        "前 3 章必至少一次主角能力展示",
        "前 5 章必引入主要对立角色"
    ]
}
```

### 函数签名

```python
def load(
    project_id: str,
    chapter_num: int,
    exclude: set | None = None,
) -> str:
    """加载平台风格基准。"""
```

### 核心逻辑

1. 从项目 settings 读 platform + category
2. 查 `platform_profiles` 表，若无数据返回 placeholder 字符串
3. 渲染（**不含 taboo 段落**，**不含节奏分布段落**——节奏在 reference loader 已有）

### 输出格式

```markdown
## 平台风格基准（起点·玄幻，第 1-5 章特征）

### 开局倾向
- 42% 直接进场景，27% 角色介绍，18% 对话开场

### 关键节点位置
- 首次对话在前 12%，首次冲突在前 25%，首爽点在前 45%

### 句式特征
- 平均 18 字 / 句，58% 短句（<15 字）
- 对话占比 31%，描写密度 24%

### 该平台惯用手法
- 第一章末必有强钩子
- 前 3 章必至少一次主角能力展示
- 前 5 章必引入主要对立角色
```

### Budget

250 字符

---

# Loader 3: reference

### 使用场景

每章生成时注入用户选定参考作品的多维特征。User 为每部作品的 5 个 feature（characters / plot / worldview / text_features / exemplars）分别选择是否启用。Loader 按"显式选择 + 自动补充"双路径加载。

### 数据源

- `reference_works` 表（作品级聚合特征）
- `reference_chapters` 表（章节级 chunks + scene_type 标注）
- `project_blobs.reference_injection` blob（配置）

### 数据库 schema

`reference_injection` blob V2 结构：

```json
{
    "version": 2,
    "explicit_selections": {
        "work_id_xxx": {
            "characters": true,
            "plot": true,
            "worldview": false,
            "text_features": false,
            "exemplars": true
        }
    },
    "auto_top_k": {
        "characters": 2,
        "plot": 2,
        "worldview": 2,
        "text_features": 1,
        "exemplars": 3
    },
    "min_relevance": 0.3
}
```

写一个迁移脚本把旧 v1 blob 转 v2。

### 函数签名

```python
def load(
    project_id: str,
    chapter_outline: str,
    chapter_num: int,
    scene_types: list[str] | None = None,
    exclude: set | None = None,
) -> str:
    """加载参考作品综合特征。"""
```

### 核心逻辑

对 5 个 feature 中每一个：

1. 收集所有 `explicit_selections[work_id][feature] == true` 的 work_id
2. 这些 work 的该 feature 必须装入（不参与筛选）
3. 如果配额未满 OR 没有显式选择：
   - 用 embedding 相似度自动补充至 top-K
   - 补充时排除已显式选定的 work

对 exemplars 特殊处理：

- 显式选某 work 的 exemplars = 从该作品 reference_chapters 取段落
- 自动选 exemplars = 跨所有 work，按 scene_type + chapter_outline embedding 找最匹配的 chunk

子模块文件结构：

```
loaders/library/reference_features/
├── characters.py
├── plot.py
├── worldview.py
├── text_features.py
└── exemplars.py
```

每个子模块导出：

```python
def load_for_work(work_id: str, **kwargs) -> str:
    """从单个 work 加载该 feature 的内容。"""

def score_by_outline(work_id: str, outline_embedding) -> float:
    """计算该 work 在该 feature 上和 outline 的相关度。"""
```

主 loader 算法：

```python
FEATURES = ['characters', 'plot', 'worldview', 'text_features', 'exemplars']

def load(project_id, chapter_outline, chapter_num, scene_types=None, exclude=None):
    cfg = get_reference_injection_config(project_id)
    outline_emb = embed(chapter_outline)
    
    blocks_by_work = {}
    
    for feature in FEATURES:
        explicit = [
            wid for wid, fs in cfg["explicit_selections"].items() 
            if fs.get(feature, False)
        ]
        
        for wid in explicit:
            text = _load_feature(feature, wid, chapter_outline, scene_types)
            blocks_by_work.setdefault(wid, {})[feature] = text
        
        top_k = cfg["auto_top_k"].get(feature, 2)
        remaining = top_k - len(explicit)
        
        if remaining > 0:
            candidates = list_works_excluding(project_id, explicit)
            scored = [
                (_score_feature(feature, w.work_id, outline_emb), w.work_id) 
                for w in candidates
            ]
            scored.sort(reverse=True)
            
            for score, wid in scored[:remaining]:
                if score < cfg["min_relevance"]:
                    break
                text = _load_feature(feature, wid, chapter_outline, scene_types)
                blocks_by_work.setdefault(wid, {})[feature] = text
    
    return render_reference_block(blocks_by_work, cfg)
```

### 输出格式

```markdown
## 参考作品综合

### 《诡秘之主》（用户显式选定：characters, plot, exemplars）

**剧情结构**：克莱恩穿越后逐步揭开世界真相...

**角色原型**：克莱恩（穿越者占卜师）；奥黛丽（贵族出身）...

**风格样例**（来自该作品 ch 12，scene_type=神秘氛围）：
[段落原文 200 字...]

### 《大奉打更人》（用户显式选定：text_features）

**文本特征**：平均句长 16 字，短句率 65%，对话占比 35%...

### 《诛仙》（自动匹配：worldview，相关度 0.42）

**世界设定**：青云门体系、合欢宗、鬼王宗...

---

### 额外风格样例（自动检索）

[段落 - 来自《XX》ch 28 的探索场景...]

[段落 - 来自《YY》ch 15 的迷雾铺垫...]
```

### Budget

2400 字符

### 暴露 API

```
PUT /api/projects/:id/reference-injection      更新 blob
```

---

# Loader 4: inspiration

### 使用场景

User 在日常使用中记录灵感片段（场景/对话/设定/桥段），存入 idea.db。本 loader 根据当前章节大纲找到相关灵感注入 Writer。

### 数据源

- `idea.db` 的 `inspirations` 表

### 数据库 schema

```sql
CREATE TABLE IF NOT EXISTS inspirations (
    inspiration_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT,                              -- JSON 数组: ['场景','对话','设定']
    embedding BLOB,
    used_in_chapters TEXT DEFAULT '[]',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX idx_inspirations_project ON inspirations(project_id);
```

### 函数签名

```python
def load(
    project_id: str,
    chapter_outline: str,
    on_stage_characters: list[str],
    user_pinned_ids: list[str] | None = None,
    top_k: int = 3,
    min_relevance: float = 0.35,
    exclude: set | None = None,
) -> str:
    """加载与本章相关的灵感片段。"""
```

### 核心逻辑

1. 读所有 `user_pinned_ids` 对应的灵感 → 必须装入
2. 剩余配额按 embedding 相似度自动选 top-K：
   - query embedding = chapter_outline + on_stage_characters 联合
   - 排除已在 `used_in_chapters` 中重复出现 ≥2 次的灵感
3. 渲染为带 tag 的列表

### 输出格式

```markdown
## 相关灵感（用户灵感库）

### 用户显式关联
- [场景] 神秘老者欲言又止的瞬间，可用"风停了一拍"的细节
- [对话] "父亲当年留下的东西，时候到了，自然会回到你手里"

### 系统推荐（基于本章主题）
- [剧情] 传承时机的好戏：师父明明知道，却让徒弟自己去发现
- [描写] 古洞窟的氛围：石壁渗水声 + 远处不知何处的回响
- [设定] 玉佩的功能：不是传说中的攻击法宝，是激活封印的钥匙
```

### Budget

800 字符

### 暴露 API

```
GET    /api/inspirations?project_id=X
POST   /api/inspirations
PUT    /api/inspirations/:id
DELETE /api/inspirations/:id
```

---

# Loader 5: character_cards

### 使用场景

每章生成时为 Writer 提供出场角色的当前状态档案，包括固定特征（外貌/背景/基线性格）和动态 snapshot 状态。**支持多章节 transition 绑定**——一次角色变化可以跨多章渐进发生。

### 数据源

- `characters` 表（固定特征）
- 新表 `character_snapshots`（snapshot 设计 + 多章节绑定状态）
- 新表 `character_snapshot_reminders`（提醒记录）

### 数据库 schema

```sql
-- 主表
CREATE TABLE IF NOT EXISTS character_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    character_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    snapshot_order INT NOT NULL,
    
    -- 设计字段
    expected_chapter_range_start INT,
    expected_chapter_range_end INT,
    trigger_description TEXT,
    
    -- Snapshot 内容
    personality_override TEXT,
    speech_style_override TEXT,
    alias TEXT,
    layer_b_overrides TEXT,           -- JSON
    relations_overrides TEXT,          -- JSON: {target_name: {sentiment, trust, label}}
    other_changes TEXT,
    
    -- 多章节绑定状态
    bound_chapters TEXT,                -- JSON 数组: [28, 30, 32]
    transition_complete_chapter INT,    -- 完成章节, NULL 表示未完成
    bound_by TEXT DEFAULT 'user',       -- 'user' / 'auto'
    bound_at TIMESTAMP,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE (character_id, snapshot_order),
    FOREIGN KEY (character_id) REFERENCES characters(character_id)
);

CREATE INDEX idx_snapshots_char ON character_snapshots(character_id, snapshot_order);
CREATE INDEX idx_snapshots_proj ON character_snapshots(project_id);

-- 提醒记录
CREATE TABLE IF NOT EXISTS character_snapshot_reminders (
    reminder_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL,
    reminder_type TEXT NOT NULL,
        -- 'snapshot_not_started' / 'snapshot_transition_not_completed'
    triggered_at_chapter INT NOT NULL,
    user_acknowledged BOOLEAN DEFAULT 0,
    user_action TEXT,                   -- 'confirmed'/'postponed'/'cancelled'
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (snapshot_id) REFERENCES character_snapshots(snapshot_id)
);
```

### 函数签名

```python
def load(
    project_id: str,
    chapter_num: int,
    on_stage_characters: list[str],
    exclude: set | None = None,
) -> str:
    """加载本章出场角色的当前状态档案。"""
```

依赖的 service：

```python
# services/character_snapshot_resolver.py
class CharacterSnapshotResolver:
    def resolve(self, character_id: str, chapter_num: int) -> dict:
        """返回角色在 chapter_num 时的状态。
        
        Returns: {
            "baseline_snapshot": Snapshot | None,
            "in_transition": Snapshot | None,
            "transition_status": "stable" | "transition_event" | 
                                 "transition_gap" | "transition_complete",
            "previous_snapshot": Snapshot | None,
        }
        """
```

### 核心逻辑

对每个 on_stage_character：

1. 调用 `CharacterSnapshotResolver.resolve(character_id, chapter_num)` 得到 4 种 transition 状态之一：
   - `stable`：稳定期，渲染当前 baseline snapshot（或基线人设）
   - `transition_event`：K 在某 snapshot 的 bound_chapters 里且未完成
   - `transition_gap`：K 在 bound_chapters 范围内但不在列表中
   - `transition_complete`：K 是某 snapshot 的 transition_complete_chapter

2. Resolve 算法：

```python
def resolve(self, character_id, chapter_num):
    snapshots = self._get_snapshots_ordered(character_id)
    
    # Step 1: 找已完成的最新 snapshot
    completed = [
        s for s in snapshots 
        if s.transition_complete_chapter and s.transition_complete_chapter <= chapter_num
    ]
    baseline = completed[-1] if completed else None
    
    # Step 2: 找 chapter_num 是否在某个 transition 中
    in_transition_snap = None
    transition_status = "stable"
    
    for s in snapshots:
        if not s.bound_chapters:
            continue
        bound = json.loads(s.bound_chapters)
        
        if s.transition_complete_chapter == chapter_num:
            in_transition_snap = s
            transition_status = "transition_complete"
            break
        
        if chapter_num in bound:
            if not s.transition_complete_chapter or chapter_num < s.transition_complete_chapter:
                in_transition_snap = s
                transition_status = "transition_event"
                break
        
        if bound and min(bound) <= chapter_num <= max(bound):
            if not s.transition_complete_chapter or chapter_num < s.transition_complete_chapter:
                in_transition_snap = s
                transition_status = "transition_gap"
                break
    
    # Step 3: 找前一个 snapshot
    previous = None
    if in_transition_snap:
        prev_order = in_transition_snap.snapshot_order - 1
        if prev_order >= 1:
            previous = next(
                (s for s in snapshots if s.snapshot_order == prev_order),
                None
            )
    
    return {
        "baseline_snapshot": baseline,
        "in_transition": in_transition_snap,
        "transition_status": transition_status,
        "previous_snapshot": previous,
    }
```

3. 渲染按 transition_status 分支：

```python
def render_character_with_snapshot(character, resolution, chapter_num):
    if resolution["transition_status"] == "stable":
        return _render_stable(character, resolution["baseline_snapshot"])
    elif resolution["transition_status"] == "transition_event":
        return _render_transition_event(character, resolution, chapter_num)
    elif resolution["transition_status"] == "transition_gap":
        return _render_transition_gap(character, resolution, chapter_num)
    elif resolution["transition_status"] == "transition_complete":
        return _render_transition_complete(character, resolution, chapter_num)
```

### 自动检测 + 提醒服务

```python
# services/snapshot_auto_detector.py
class SnapshotAutoDetector:
    async def detect_after_chapter_commit(
        self, project_id, chapter_num, chapter_content,
    ) -> list[dict]:
        """章节 commit 后检测是否触发了 pending snapshot。"""
        results = []
        chapter = get_chapter(project_id, chapter_num)
        characters = chapter.on_stage_entities.get('characters', [])
        
        for char_name in characters:
            char = get_character_by_name(project_id, char_name)
            if not char:
                continue
            
            pending = self._get_next_pending(char.character_id)
            if not pending:
                continue
            
            if chapter_num < pending.expected_chapter_range_start:
                continue
            
            match = await self._llm_check_trigger(
                pending.trigger_description, chapter_content, char_name,
            )
            
            if match["triggered"] and match["confidence"] > 0.7:
                results.append({
                    "character_id": char.character_id,
                    "character_name": char_name,
                    "snapshot_id": pending.snapshot_id,
                    "snapshot_order": pending.snapshot_order,
                    "trigger": pending.trigger_description,
                    "confidence": match["confidence"],
                    "reason": match["reason"],
                    "chapter_num": chapter_num,
                })
        
        return results
    
    def auto_bind(self, snapshot_id, chapter_num):
        """auto 检测后绑定，不覆盖 user 主动绑定。"""
        snap = get_snapshot(snapshot_id)
        if snap.bound_by == 'user':
            return
        bound = json.loads(snap.bound_chapters or '[]')
        if chapter_num not in bound:
            bound.append(chapter_num)
            bound.sort()
            update_snapshot(snapshot_id, {
                "bound_chapters": json.dumps(bound),
                "bound_by": "auto",
                "bound_at": now(),
            })

# services/snapshot_reminder.py
def check_overdue_reminders(project_id, current_chapter_num):
    """每次 build_prompt 前检查是否要 emit 提醒。"""
    reminders = []
    
    for snap in get_all_pending_snapshots(project_id):
        # Case 1: 没绑定任何章节，过期望开始章 +5
        if not snap.bound_chapters:
            if current_chapter_num > snap.expected_chapter_range_start + 5:
                if not _already_reminded(snap.snapshot_id, current_chapter_num, 'snapshot_not_started'):
                    reminders.append({
                        "type": "snapshot_not_started",
                        "character_id": snap.character_id,
                        "snapshot_order": snap.snapshot_order,
                        "expected_at": snap.expected_chapter_range_start,
                        "current": current_chapter_num,
                        "trigger": snap.trigger_description,
                    })
                    _mark_reminded(snap.snapshot_id, current_chapter_num, 'snapshot_not_started')
        
        # Case 2: 有 bound 但没标 complete，超 expected_range_end
        elif snap.bound_chapters and not snap.transition_complete_chapter:
            if current_chapter_num > snap.expected_chapter_range_end:
                if not _already_reminded(snap.snapshot_id, current_chapter_num, 'snapshot_transition_not_completed'):
                    bound = json.loads(snap.bound_chapters)
                    reminders.append({
                        "type": "snapshot_transition_not_completed",
                        "character_id": snap.character_id,
                        "snapshot_order": snap.snapshot_order,
                        "bound_so_far": bound,
                        "expected_complete_by": snap.expected_chapter_range_end,
                        "current": current_chapter_num,
                    })
                    _mark_reminded(snap.snapshot_id, current_chapter_num, 'snapshot_transition_not_completed')
    
    return reminders
```

### 输出格式

**情况 stable（K=40，snapshot 2 已完成）**：

```markdown
【张远】（主角）
  外貌：剑眉星目，身形高瘦
  背景：青云山弟子，木灵根
  
  [当前状态]（自第 32 章起完成 Snapshot 2）
  性格：内心矛盾、对身世起疑
  说话方式：变得寡言、常陷入沉思
  loss_aversion：0.8
  decision_threshold：0.6
  对李清漪关系：信任降低（sentiment 60，trust 50）
```

**情况 transition_event（K=30，在 bound_chapters [28,30,32]）**：

```markdown
【张远】（主角）
  外貌：剑眉星目
  
  [正在转变]（从 Snapshot 1 到 Snapshot 2）
  触发：得知自己父亲是神秘组织遗孤
  本章是关键转变事件之一
  绑定章节：28, 30, 32（本章: 30）
  完成章节：第 32 章（距完成还有 2 章）
  
  当前应表现：从「冷静沉稳」逐渐向「内心矛盾、对身世起疑」过渡
  本章可写：第一次出现的相关情绪/行为/认知变化
```

**情况 transition_gap（K=29，在 bound 范围内但不在列表）**：

```markdown
【张远】（主角）
  外貌：剑眉星目
  
  [transition 间歇期]
  正在从 Snapshot 1 向 Snapshot 2 过渡
  绑定章节：28, 30, 32（本章 29 不在其中）
  角色处于不稳定中间状态
  本章可表现：转变中的反复、波动、暂时回旧状态
  无需强行推进转变
```

**情况 transition_complete（K=32，是 complete_chapter）**：

```markdown
【张远】（主角）
  外貌：剑眉星目
  
  [转变完成]（本章 Snapshot 1 到 Snapshot 2 完成）
  触发：得知自己父亲是神秘组织遗孤
  转变历程：第 28, 30, 32 章
  本章应表现：转变定型，角色明确进入新状态
  
  转变后的状态：
  性格：内心矛盾、对身世起疑
  说话方式：变得寡言
  loss_aversion：0.8
  对李清漪关系：信任降低（sentiment 60）
```

### Budget

2200 字符

### 暴露 API

```
POST   /api/snapshots                              创建 snapshot
PUT    /api/snapshots/:id                          编辑 snapshot 内容
DELETE /api/snapshots/:id                          删除 snapshot
POST   /api/snapshots/:id/bind-chapter             添加绑定章节
POST   /api/snapshots/:id/unbind-chapter           移除绑定章节
POST   /api/snapshots/:id/mark-complete            标记完成章节
POST   /api/snapshots/:id/unmark-complete          取消完成标记
GET    /api/snapshots/auto-detect/:chapter_id      触发自动检测
POST   /api/snapshots/auto-detect/:result_id/confirm   确认自动检测
GET    /api/snapshot-reminders?project_id=X        获取未处理提醒
POST   /api/snapshot-reminders/:id/acknowledge     处理提醒
```

辅助服务文件：

```
services/character_snapshot_resolver.py
services/snapshot_auto_detector.py
services/snapshot_reminder.py
```

---

# Loader 6: worldbook

### 使用场景

每章生成时根据章节大纲，从世界书中筛选相关条目注入 Writer。避免全量装入导致 context 浪费。

### 数据源

- `worldbook_entries` 表（加 embedding 缓存字段）

### 数据库 schema

```sql
ALTER TABLE worldbook_entries ADD COLUMN embedding BLOB;
ALTER TABLE worldbook_entries ADD COLUMN embedding_updated_at TIMESTAMP;
```

### 函数签名

```python
def load(
    project_id: str,
    chapter_outline: str,
    on_stage_characters: list[str],
    top_k: int = 8,
    min_relevance: float = 0.3,
    exclude: set | None = None,
) -> str:
    """加载和本章相关的世界书条目。"""
```

### 核心逻辑

1. 构造 query embedding：
   ```python
   query = chapter_outline + "\n人物：" + ",".join(on_stage_characters)
   query_emb = embed(query)
   ```
2. 对每个 worldbook entry：
   - Lazy compute embedding（首次访问时计算，存入 entry 的 embedding 字段）
   - 计算 cosine 相似度
3. 取 top-K 且 score >= min_relevance
4. 按 category 分组渲染

Embedding model 推荐：中文优化的 `text2vec-large-chinese` 或 `bge-base-zh-v1.5`。

### 输出格式

```markdown
## 世界观设定（已按本章相关性筛选）

### 力量体系
[灵根体系] 修真者按灵根属性分金木水火土...
[修为阶段] 练气->筑基->金丹->元婴->化神...

### 地点
[青云山] 三大宗门之首，山门位于云雾深处...
[幽冥谷] 神秘禁地，谷中常有迷雾...

### 组织
[玄阴宗] 与青云山对立的暗夜宗门...
```

### Budget

1600 字符

### 暴露 API

```
POST /api/worldbook/reindex      强制重算所有 embedding
```

---

# Loader 7: chapter_outline

### 使用场景

每章生成时注入本章大纲到 Writer。用户输入的大纲是 Writer 的核心任务说明。

### 数据源

- `chapters` 表：synopsis / time_setting / location / characters / on_stage_entities

### 数据库 schema

```sql
ALTER TABLE chapters ADD COLUMN on_stage_entities TEXT;
-- JSON: {"characters": [...], "locations": [...], "items": [...], "organizations": [...]}
```

数据迁移：

```sql
UPDATE chapters 
SET on_stage_entities = json_object(
    'characters', COALESCE(characters, '[]'),
    'locations', '[]',
    'items', '[]',
    'organizations', '[]'
)
WHERE on_stage_entities IS NULL;
```

### 函数签名

```python
def load(
    project_id: str,
    chapter_id: str,
    exclude: set | None = None,
) -> str:
    """加载本章大纲及元信息。"""
```

### 核心逻辑

简单 lookup：

```python
def load(project_id, chapter_id, exclude=None):
    chapter = get_chapter(project_id, chapter_id)
    entities = json.loads(chapter.on_stage_entities or '{}')
    
    parts = []
    parts.append("### 主线")
    parts.append(chapter.synopsis or "（暂无大纲）")
    
    if chapter.time_setting or chapter.location:
        parts.append("\n### 时间地点")
        if chapter.time_setting:
            parts.append(f"时间：{chapter.time_setting}")
        if chapter.location:
            parts.append(f"地点：{chapter.location}")
    
    if entities.get('characters'):
        parts.append(f"\n出场角色：{', '.join(entities['characters'])}")
    if entities.get('locations'):
        parts.append(f"涉及地点：{', '.join(entities['locations'])}")
    if entities.get('items'):
        parts.append(f"涉及物品：{', '.join(entities['items'])}")
    if entities.get('organizations'):
        parts.append(f"涉及组织：{', '.join(entities['organizations'])}")
    
    return section("本章大纲", "\n".join(parts))
```

### 输出格式

```markdown
## 本章大纲

### 主线
张远独自深入幽冥谷寻找失踪的李清漪。在谷中发现神秘符文，
触发古老封印。最后揭示李清漪可能与玄阴宗有更深关系。

### 时间地点
时间：神武纪元 5012 年春
地点：幽冥谷主洞

出场角色：张远, 李清漪, 神秘黑影
涉及地点：幽冥谷, XX山
涉及物品：玉佩, 神剑
涉及组织：玄阴宗
```

### Budget

1200 字符

---

# Loader 8: reader_memory

### 使用场景

每章生成时为 Writer 提供截至当前编辑章节 K 之前（仅 ch1..K-1）的读者视角记忆。**严格 causal**——绝不让 Writer 看到 K 章及之后的内容。

### 数据源

- `chapter_summaries` 表（L2 sliding window，加 is_anchor 字段）
- ChromaDB（L3 semantic recall）
- `episodic_events` 表（L4 timeline）

### 数据库 schema

```sql
ALTER TABLE chapter_summaries ADD COLUMN is_anchor BOOLEAN DEFAULT 0;
-- is_anchor=1 表示 user 标记为"关键章节"，会一直出现在 memory 中
```

### 函数签名

```python
def load(
    project_id: str,
    chapter_num: int,
    chapter_outline: str = "",
    on_stage_characters: list[str] | None = None,
    window_size: int = 8,
    semantic_top_k: int = 5,
    exclude: set | None = None,
) -> str:
    """加载截至第 K-1 章的读者视角记忆。
    
    严格 causal: 所有查询都带 chapter_num < K 过滤。
    """
```

### 核心逻辑

4 个子部分聚合（**全部带 chapter_num < K 过滤**）：

```python
def load(...):
    parts = []
    
    # 1. L2: 最近 N 章摘要（causal sliding window）
    recent = db.execute("""
        SELECT chapter_num, title, summary
        FROM chapter_summaries
        WHERE project_id = ?
          AND chapter_num < ?                  
          AND chapter_num >= ?
        ORDER BY chapter_num DESC
    """, (project_id, chapter_num, chapter_num - window_size)).fetchall()
    
    if recent:
        parts.append("### 最近章节回顾（读者视角）")
        for r in recent:
            parts.append(f"**第 {r.chapter_num} 章 {r.title}**：{r.summary}")
    
    # 2. Anchor chapters: user 标记的关键章节
    anchors = db.execute("""
        SELECT chapter_num, title, summary
        FROM chapter_summaries
        WHERE project_id = ?
          AND chapter_num < ?
          AND is_anchor = 1
        ORDER BY chapter_num
    """, (project_id, chapter_num)).fetchall()
    
    if anchors:
        parts.append("\n### 关键章节锚点")
        for a in anchors:
            parts.append(f"**第 {a.chapter_num} 章 {a.title}**：{a.summary}")
    
    # 3. L3 ChromaDB: 语义检索
    if chapter_outline:
        chunks = chromadb_client.query(
            collection_name=f"project_{project_id}",
            query_texts=[chapter_outline],
            n_results=semantic_top_k,
            where={
                "project_id": project_id,
                "chapter_num": {"$lt": chapter_num},
            },
        )
        if chunks:
            parts.append("\n### 语义相关历史片段")
            for c in chunks:
                parts.append(f"（来自第 {c.metadata.chapter_num} 章）{c.text}")
    
    # 4. L4 episodic: on-stage 角色的近期事件
    if on_stage_characters:
        placeholders = ','.join(['?'] * len(on_stage_characters))
        params = [project_id, chapter_num, chapter_num - 30] + on_stage_characters
        events = db.execute(f"""
            SELECT chapter_num, event_type, description, characters_involved
            FROM episodic_events
            WHERE project_id = ?
              AND chapter_num < ?
              AND chapter_num >= ?
              AND EXISTS (
                  SELECT 1 FROM json_each(characters_involved)
                  WHERE value IN ({placeholders})
              )
            ORDER BY chapter_num DESC
            LIMIT 10
        """, params).fetchall()
        
        if events:
            parts.append("\n### 相关事件时间线")
            for e in events:
                parts.append(f"- 第 {e.chapter_num} 章：{e.description}")
    
    return section(f"读者视角记忆（截至第 {chapter_num - 1} 章）", "\n".join(parts))
```

### 输出格式

```markdown
## 读者视角记忆（截至第 29 章）

### 最近章节回顾（读者视角）
**第 29 章 暗夜潜行**：张远偷偷离开青云山，前往幽冥谷调查...
**第 28 章 师父的隐瞒**：与师父对话，发现师父似乎隐瞒着重要事情...

### 关键章节锚点
**第 5 章 父亲遗物**：师父第一次提到张远父亲留下的"东西"。
**第 12 章 玉佩的秘密**：李清漪对张远的玉佩有奇怪反应...

### 语义相关历史片段
（来自第 12 章）...李清漪盯着玉佩，眼神中闪过一丝不易察觉的惊惧...
（来自第 22 章）...神秘老者欲言又止，最终只留下一句"时候到了自然会回到你手里"...

### 相关事件时间线
- 第 23 章：李清漪 失踪
- 第 22 章：张远 修为突破到筑基期
- 第 18 章：张远 使用突破丹
```

### Budget

4500 字符

### 暴露 API

```
POST /api/chapters/:id/toggle-anchor      切换 is_anchor 状态
```

---

# Loader 9: current_chapter_draft

### 使用场景

如果正在编辑的章节已有正文（user 手写 / AI 生成 / 混合），根据生成模式智能加载。**核心原则**：user 手写内容永远有价值；AI 生成的失败 draft 在 fresh regenerate 模式下不应加载（避免锚定到失败模式）。

支持 4 种生成模式：

- `fresh`：全新生成
- `continue`：续写
- `rewrite_from`：保留前段，重写后段
- `modify_section`：仅修改某段

### 数据源

- 新表 `chapter_segments`（段落级内容溯源）
- 新表 `chapter_failed_generations`（失败 draft 归档）

### 数据库 schema

```sql
CREATE TABLE IF NOT EXISTS chapter_segments (
    segment_id TEXT PRIMARY KEY,
    chapter_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    sequence_order INT NOT NULL,
    content TEXT NOT NULL,
    
    source TEXT NOT NULL,
        -- 'user_written'         user 手写
        -- 'ai_generated'         AI 生成后 user 未改动
        -- 'ai_user_edited'       AI 生成后 user 编辑过
    
    generation_id TEXT,
    original_ai_content TEXT,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    modified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE (chapter_id, sequence_order)
);

CREATE INDEX idx_segments_chapter ON chapter_segments(chapter_id, sequence_order);

CREATE TABLE IF NOT EXISTS chapter_failed_generations (
    failed_gen_id TEXT PRIMARY KEY,
    chapter_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    content_snippet TEXT,
    full_content TEXT,
    rejected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    rejection_reason TEXT,
    issues_detected TEXT
);
```

数据迁移：

```python
# 把现有 chapters.content 拆成 segments，全部初始标记为 user_written（保守）
for chapter in get_all_chapters():
    if chapter.content:
        paragraphs = [p.strip() for p in chapter.content.split('\n\n') if p.strip()]
        for i, p in enumerate(paragraphs):
            insert_segment(chapter.chapter_id, i, p, source='user_written')
```

### 函数签名

```python
def load(
    project_id: str,
    chapter_id: str,
    generation_mode: str,
    revision_anchor: dict | None = None,
    include_failed_drafts_as_hint: bool = True,
    exclude: set | None = None,
) -> str:
    """加载本章已有正文。"""
```

依赖的 service：

```python
# services/segment_manager.py
def get_segments(chapter_id: str) -> list[Segment]:
    """获取章节所有段落。"""

def update_chapter_content(chapter_id: str, new_text: str):
    """user 编辑后调用。diff-based 推断每段的 source 变化。"""
    # 用 sequence_matcher 找新旧段落的对应关系
    # 段落未变 -> source 保持
    # 段落被改 -> ai_generated 变 ai_user_edited
    # 段落新增 -> user_written

def archive_failed_generation(chapter_id: str, content: str, reason: str = None):
    """user 点重新生成时归档当前内容。"""

# services/failure_analyzer.py
async def analyze_failure(chapter_id: str, failed_content: str):
    """LLM 分析 user 为什么不满意，提取 anti-hint。"""
    prompt = f"""user 不满意以下章节内容并要求重新生成。
分析其中可能让 user 不满意的具体方向（最多 3 条），用于下次生成时避免。

章节内容（截取）：
{failed_content[:3000]}

输出 JSON: {{"issues": ["...", "...", "..."]}}

每条都要具体，比如:
"开场用了内心独白，节奏拖沓"
"对话太露骨，反派动机说得太白"
"配角戏份过多分散主线"
"""
    return await llm.json_call(prompt)
```

### 核心逻辑

```python
def load(project_id, chapter_id, generation_mode, revision_anchor=None, 
         include_failed_drafts_as_hint=True, exclude=None):
    
    segments = get_segments(chapter_id)
    
    if not segments:
        return ""
    
    if generation_mode == 'fresh':
        return _render_fresh_mode(project_id, chapter_id, segments, 
                                   include_failed_drafts_as_hint)
    elif generation_mode == 'continue':
        return _render_continue_mode(segments)
    elif generation_mode == 'rewrite_from':
        return _render_rewrite_from_mode(segments, revision_anchor)
    elif generation_mode == 'modify_section':
        return _render_modify_section_mode(segments, revision_anchor)
    else:
        return ""

def _render_fresh_mode(project_id, chapter_id, segments, include_failed_hint):
    """规则：
    - 仅加载 user_written 和 ai_user_edited 段落
    - 不加载 pure ai_generated 段落（避免锚定）
    - 失败 draft 转 anti-hint
    """
    valuable = [s for s in segments if s.source in ('user_written', 'ai_user_edited')]
    parts = []
    
    if valuable:
        parts.append("[本章已有用户保留的内容，新生成必须保持与这些内容的连贯]")
        for seg in valuable:
            label = "user 手写" if seg.source == 'user_written' else "user 编辑过"
            parts.append(f"\n[段落 {seg.sequence_order}（{label}）]")
            parts.append(seg.content)
        
        positions = [s.sequence_order for s in valuable]
        gaps = _detect_gaps(positions)
        if gaps:
            parts.append(f"\n[空缺位置：段落 {gaps} 需要新生成]")
    
    if include_failed_hint:
        failed = get_recent_failed_generations(chapter_id, limit=2)
        for f in failed:
            if f.issues_detected:
                issues = json.loads(f.issues_detected)
                if not parts:
                    parts.append("[上次生成方向 - user 不满意，请避免以下问题]")
                else:
                    parts.append("\n[请避免以下方向]")
                for issue in issues[:3]:
                    parts.append(f"- 避免：{issue}")
    
    if not parts:
        return ""
    return section("本章已有内容", "\n".join(parts))

def _render_continue_mode(segments):
    """续写：取末尾完整段落（max 3500 字符）。"""
    max_tail = 3500
    selected = []
    total = 0
    for seg in reversed(segments):
        if total + len(seg.content) > max_tail:
            break
        selected.insert(0, seg)
        total += len(seg.content)
    
    if not selected:
        selected = [segments[-1]]
    
    truncated = len(selected) < len(segments)
    parts = [f"[本章已写 {sum(len(s.content) for s in segments)} 字符，请从最后段之后续写]"]
    
    if truncated:
        parts.append(f"\n...（前 {len(segments) - len(selected)} 段省略）")
    
    for seg in selected:
        if seg.source == 'user_written':
            parts.append(f"\n[user 手写]\n{seg.content}")
        else:
            parts.append(f"\n{seg.content}")
    
    return section("本章已有正文（续写）", "\n".join(parts))

def _render_rewrite_from_mode(segments, anchor):
    """从某段开始重写：前段保留，后段抛弃 + 转 anti-hint。"""
    rewrite_from = _resolve_anchor(segments, anchor)
    
    kept = segments[:rewrite_from]
    discarded = segments[rewrite_from:]
    
    parts = [f"[user 决定从第 {rewrite_from + 1} 段开始重写]"]
    
    if kept:
        parts.append(f"\n[保留部分（{len(kept)} 段，必须保持连贯）]")
        for seg in kept:
            parts.append(seg.content)
    
    parts.append(f"\n[请从「{kept[-1].content[-50:] if kept else '本章开头'}」之后续写新内容]")
    
    if discarded:
        parts.append(f"\n[避免重复方向：原本第 {rewrite_from + 1} 段开头是「{discarded[0].content[:100]}...」，请换个写法]")
    
    return section("本章已有正文（保留前段，重写后段）", "\n".join(parts))

def _render_modify_section_mode(segments, anchor):
    """修改某段：前后保留，中段加修改说明。"""
    start = anchor.get("start_paragraph", 0)
    end = anchor.get("end_paragraph", start)
    instruction = anchor.get("modification_instruction", "")
    
    before = segments[:start]
    target = segments[start:end + 1]
    after = segments[end + 1:]
    
    parts = []
    
    if before:
        parts.append("[前文 - 保留]")
        for seg in before:
            parts.append(seg.content)
    
    parts.append(f"\n[待修改段落（第 {start + 1}-{end + 1} 段，原内容）]")
    for seg in target:
        parts.append(seg.content)
    
    if instruction:
        parts.append(f"\n[修改要求]\n{instruction}")
    
    if after:
        parts.append("\n[后文 - 保留，新修改的内容需要与之自然连接]")
        for seg in after:
            parts.append(seg.content)
    
    return section("本章已有正文（局部修改）", "\n".join(parts))

def _resolve_anchor(segments, anchor):
    """解析 revision_anchor，返回段落 index。"""
    if "paragraph_index" in anchor:
        return anchor["paragraph_index"]
    elif "anchor_text" in anchor:
        for i, seg in enumerate(segments):
            if anchor["anchor_text"] in seg.content:
                return i
    return len(segments)
```

### 输出格式

**continue 模式**：

```markdown
## 本章已有正文（续写）
[本章已写 2150 字符，请从最后段之后续写]

...（前 5 段省略）

[user 手写]
张远站在洞口，望着那片漆黑深处，心中升起一种莫名的预感。

师父当年那句意味深长的话，他终于明白了一半。

张远握紧手中长剑，迈出了第一步。
```

**fresh 模式**：

```markdown
## 本章已有内容
[本章已有用户保留的内容，新生成必须保持与这些内容的连贯]

[段落 0（user 手写）]
晨曦微露，青云山的雾还未散尽。

[段落 1（user 手写）]
张远独自走在山道上，心事重重。

[空缺位置：段落 2-10 需要新生成]

[请避免以下方向]
- 避免：开场用了内心独白，节奏拖沓
- 避免：对话太露骨，反派动机说得太白
```

**rewrite_from 模式**：

```markdown
## 本章已有正文（保留前段，重写后段）
[user 决定从第 5 段开始重写]

[保留部分（4 段，必须保持连贯）]
晨曦微露，青云山的雾还未散尽。
张远独自走在山道上，心事重重...
张远停下脚步，看着远处幽冥谷的方向。

[请从「张远停下脚步，看着远处幽冥谷的方向。」之后续写新内容]

[避免重复方向：原本第 5 段开头是「不知不觉，已经走到了谷口...」，请换个写法]
```

### Budget

4000 字符

### 暴露 API

```
POST   /api/generation/fresh              fresh 模式
POST   /api/generation/continue           continue 模式
POST   /api/generation/rewrite-from       rewrite_from 模式
POST   /api/generation/modify-section     modify_section 模式
POST   /api/chapters/:id/archive-failed   归档失败 draft
GET    /api/chapters/:id/segments         获取所有 segments
PUT    /api/chapters/:id/content          更新内容（自动 diff）
```

---

# Loader 10: storyland_state

### 使用场景

每章生成时为 Writer 提供 Storyland（小说世界）的客观状态——**不只是角色，也包括地点、物品、组织的状态**。例如"XX山被打平后，后续提到 XX山时 Writer 都应知道它已是平的"。

### 数据源

- `truth_current_state` 表（SPO 状态，加 subject_type 字段）
- `character_ledger` 表
- `emotion_arcs` 表
- 新表 `storyland_entities`
- **不再包含**关系矩阵（已转移到 character_snapshots.relations_overrides）

### 数据库 schema

```sql
-- 新表：实体注册中心
CREATE TABLE IF NOT EXISTS storyland_entities (
    entity_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    entity_type TEXT NOT NULL,
        -- 'character' / 'location' / 'item' / 'organization' / 'concept'
    description TEXT,
    introduced_chapter INT,
    aliases TEXT,
    metadata TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (project_id, name)
);

CREATE INDEX idx_entities_type ON storyland_entities(project_id, entity_type);
CREATE INDEX idx_entities_introduced ON storyland_entities(project_id, introduced_chapter);

-- truth_current_state 加 subject_type
ALTER TABLE truth_current_state ADD COLUMN subject_type TEXT DEFAULT 'character';
CREATE INDEX idx_truth_state_subject_type 
    ON truth_current_state(project_id, subject_type, valid_from_chapter);
```

数据迁移：

```python
def migrate_truth_state_subject_type():
    """根据现有 subject 名字推断 subject_type。"""
    db.execute("""
        UPDATE truth_current_state 
        SET subject_type = 'character' 
        WHERE subject IN (SELECT name FROM characters)
    """)
    db.execute("""
        UPDATE truth_current_state 
        SET subject_type = 'location' 
        WHERE subject IN (
            SELECT name FROM worldbook_entries WHERE category = '地点'
        )
    """)
    # 其它类型类似

def backfill_entities_from_existing():
    """从 characters 表和 worldbook 表回填 entities 表。"""
    # characters -> entity_type='character'
    # worldbook_entries(category='地点') -> 'location'
    # worldbook_entries(category='组织') -> 'organization'
    # worldbook_entries(category='物品') -> 'item'
```

### 函数签名

```python
def load(
    project_id: str,
    chapter_num: int,
    on_stage_entities: dict[str, list[str]],
    pov_character: str | None = None,
    exclude: set | None = None,
) -> str:
    """加载截至第 K-1 章的 Storyland 客观状态。
    
    on_stage_entities: {
        "characters": [...],
        "locations": [...],
        "items": [...],
        "organizations": [...]
    }
    """
```

### 核心逻辑

```python
def load(project_id, chapter_num, on_stage_entities, pov_character=None, exclude=None):
    all_entity_names = [
        name for type_list in on_stage_entities.values() for name in type_list
    ]
    
    parts = []
    
    for subject_type, header in [
        ('character', '角色当前位置 / 状态'),
        ('location', '地点状态'),
        ('item', '关键物品状态'),
        ('organization', '组织状态'),
    ]:
        type_entities = on_stage_entities.get(
            {'character': 'characters', 'location': 'locations',
             'item': 'items', 'organization': 'organizations'}[subject_type],
            []
        )
        if not type_entities:
            continue
        
        placeholders = ','.join(['?'] * len(type_entities))
        params = [project_id, subject_type, chapter_num, chapter_num] + type_entities
        
        rows = db.execute(f"""
            SELECT subject, predicate, object, valid_from_chapter
            FROM truth_current_state
            WHERE project_id = ?
              AND subject_type = ?
              AND valid_from_chapter < ?
              AND (valid_to_chapter IS NULL OR valid_to_chapter >= ?)
              AND subject IN ({placeholders})
            ORDER BY subject, valid_from_chapter DESC
        """, params).fetchall()
        
        if rows:
            parts.append(f"### {header}")
            for r in rows:
                parts.append(f"- {r.subject} {r.predicate} {r.object}（自第 {r.valid_from_chapter} 章）")
    
    # character_ledger 仅角色
    chars = on_stage_entities.get('characters', [])
    if chars:
        placeholders = ','.join(['?'] * len(chars))
        params = [project_id] + chars + [chapter_num]
        ledger = db.execute(f"""
            SELECT character_name, item_type, current_value, last_changed_chapter, last_delta
            FROM character_ledger
            WHERE project_id = ?
              AND character_name IN ({placeholders})
              AND last_changed_chapter < ?
            ORDER BY character_name, item_type
        """, params).fetchall()
        
        if ledger:
            parts.append("\n### 角色资源 ledger")
            parts.append("| 角色 | 项目 | 当前值 | 最后变更 |")
            parts.append("|---|---|---|---|")
            for l in ledger:
                delta_str = f"+{l.last_delta}" if l.last_delta > 0 else str(l.last_delta)
                parts.append(f"| {l.character_name} | {l.item_type} | {l.current_value} | 第 {l.last_changed_chapter} 章 {delta_str} |")
    
    # emotion_arcs 仅角色
    if chars:
        placeholders = ','.join(['?'] * len(chars))
        params = [project_id] + chars + [chapter_num, chapter_num - 3]
        arcs = db.execute(f"""
            SELECT character_name, current_emotion, prev_emotion, changed_chapter, trigger
            FROM emotion_arcs
            WHERE project_id = ?
              AND character_name IN ({placeholders})
              AND changed_chapter < ?
              AND changed_chapter >= ?
            ORDER BY character_name, changed_chapter DESC
        """, params).fetchall()
        
        if arcs:
            parts.append("\n### 情绪轨迹（近 3 章）")
            for a in arcs:
                parts.append(f"- {a.character_name}: {a.prev_emotion} -> {a.current_emotion}（自第 {a.changed_chapter} 章，触发: {a.trigger}）")
    
    return section(f"Storyland 客观状态（截至第 {chapter_num - 1} 章）", "\n".join(parts))
```

实体自动注册 hook（`services/entity_registry.py`）：

```python
class EntityRegistry:
    def upsert_character(self, project_id, character):
        """character CRUD 时同步进 storyland_entities。"""
        self.upsert_entity(
            project_id=project_id,
            name=character.name,
            entity_type='character',
            description=character.background,
            introduced_chapter=character.introduced_chapter,
            aliases=character.aliases_json,
        )
    
    def upsert_worldbook_entry(self, project_id, entry):
        """worldbook entry 同步（仅 location/organization/item 类）。"""
        if entry.category in ('地点', '组织', '物品'):
            type_map = {'地点': 'location', '组织': 'organization', '物品': 'item'}
            self.upsert_entity(
                project_id=project_id,
                name=entry.name,
                entity_type=type_map[entry.category],
                description=entry.content,
            )

# 接入位置：
# characters CRUD route 调 entity_registry.upsert_character()
# worldbook CRUD route 调 entity_registry.upsert_worldbook_entry()
```

### 输出格式

```markdown
## Storyland 客观状态（截至第 49 章）

### 角色当前位置 / 状态
- 张远 在 青云山主峰（自第 5 章）
- 张远 修为 筑基期（自第 22 章）

### 地点状态
- XX山 物理状态 山顶被打平岩浆裸露（自第 47 章）
- 青云山主峰 警戒状态 开启大阵防护（自第 35 章）

### 关键物品状态
- 玉佩 当前持有者 张远（自第 3 章）
- 玉佩 激活状态 部分激活（自第 22 章）
- 神剑 出鞘状态 已出鞘（自第 28 章）

### 组织状态
- 玄阴宗 与青云山的关系 公开敌对（自第 35 章）
- 玄阴宗 当前据点 北部寒冰山脉（自第 40 章）

### 角色资源 ledger
| 角色 | 项目 | 当前值 | 最后变更 |
|---|---|---|---|
| 张远 | 灵石 | 120 | 第 22 章 +50 |
| 张远 | 突破丹 | 2 | 第 18 章 -1 |

### 情绪轨迹（近 3 章）
- 张远: 困惑 -> 警觉（自第 47 章，触发: 发现 XX山被打平）
```

### Budget

2000 字符

### 暴露 API

```
GET    /api/entities?project_id=X&type=Y       列出实体
POST   /api/entities                             新建实体
PUT    /api/entities/:id                         编辑
DELETE /api/entities/:id                         删除
GET    /api/storyland-state?project_id=X&chapter_num=Y   查询状态
```

---

# Loader 11: foreshadowing

### 使用场景

每章生成时为 Writer 提供所有未收束的伏笔信息，按紧迫度和重要性分级。**新增 user 主动"完全收回"功能**——user 可以权威 override 系统判断。

### 数据源

- `pending_hooks` 表（加新字段）

### 数据库 schema

```sql
ALTER TABLE pending_hooks ADD COLUMN user_marked_fully_resolved BOOLEAN DEFAULT 0;
ALTER TABLE pending_hooks ADD COLUMN user_resolved_at_chapter INT;
ALTER TABLE pending_hooks ADD COLUMN user_resolve_notes TEXT;
```

### 函数签名

```python
def load(
    project_id: str,
    chapter_num: int,
    pov_character: str | None = None,
    exclude: set | None = None,
) -> str:
    """加载所有未收束的伏笔，按紧迫度和重要性分级。"""
```

### 核心逻辑

```python
def load(project_id, chapter_num, pov_character=None, exclude=None):
    hooks = db.execute("""
        SELECT *
        FROM pending_hooks
        WHERE project_id = ?
          AND user_marked_fully_resolved = 0
          AND status NOT IN ('resolved', 'abandoned')
          AND origin_chapter < ?
        ORDER BY 
            CASE status
                WHEN 'pressured' THEN 1
                WHEN 'near_payoff' THEN 2
                WHEN 'progressing' THEN 3
                WHEN 'open' THEN 4
            END,
            CASE importance
                WHEN 'A' THEN 1
                WHEN 'B' THEN 2
                WHEN 'C' THEN 3
            END,
            origin_chapter
    """, (project_id, chapter_num)).fetchall()
    
    # POV spoiler filter
    if pov_character:
        hooks = [
            h for h in hooks 
            if not h.is_spoiler 
               or pov_character in json.loads(h.revealed_to_chars_json or '[]')
        ]
    
    if not hooks:
        return ""
    
    parts = []
    
    groups = {
        'pressured': '急需推进或回收（pressured，已超期）',
        'near_payoff': '接近回收（near_payoff）',
        'progressing': '推进中（progressing）',
        'open': '已埋设（open，可暂时不动）',
    }
    
    for status_key, header in groups.items():
        group_hooks = [h for h in hooks if h.status == status_key]
        if group_hooks:
            parts.append(f"### {header}")
            for h in group_hooks:
                age = chapter_num - h.origin_chapter
                parts.append(
                    f"- [{h.importance} 级·{h.title}]（第 {h.origin_chapter} 章埋设，已 {age} 章）\n"
                    f"  {h.description}"
                )
    
    return section("未收束伏笔（按紧迫度排序）", "\n".join(parts))
```

**关键约束**：user_marked_fully_resolved=1 的 hook 永远不显示，即使 auto 系统重新打开（防止误判覆盖用户决策）。

API 实现：

```python
@router.post("/foreshadowing/{hook_id}/fully-resolve")
async def fully_resolve_hook(hook_id: str, body: FullyResolveRequest):
    db.execute("""
        UPDATE pending_hooks 
        SET user_marked_fully_resolved = 1,
            user_resolved_at_chapter = ?,
            user_resolve_notes = ?,
            status = 'resolved'
        WHERE hook_id = ?
    """, (body.chapter_num, body.notes, hook_id))
    
    audit_log("user_fully_resolved", hook_id=hook_id, ...)
```

### 输出格式

```markdown
## 未收束伏笔（按紧迫度排序）

### 急需推进或回收（pressured，已超期）
- [A 级·黑衣人玉佩]（第 3 章埋设，已 22 章）
  神秘人留下的玉佩，纹路含义不明
- [B 级·失落的师叔]（第 8 章埋设，已 17 章）
  曾出现一次的师叔消失，下落不明

### 接近回收（near_payoff）
- [A 级·李清漪身世]（第 12 章埋设，已 13 章）
  她对玄阴宗有奇怪反应

### 推进中（progressing）
- [A 级·父亲遗物]（第 5 章埋设，已 20 章）
  师父提到"时候到了自然会回到手里的东西"

### 已埋设（open，可暂时不动）
- [B 级·星门激活条件]（第 17 章埋设，已 8 章）
- [C 级·后山的废塔]（第 9 章埋设，已 16 章）
```

### Budget

1200 字符

### 暴露 API

```
POST /api/foreshadowing/:hook_id/fully-resolve
```

---

# Loader 12: subplots

### 使用场景

每章生成时为 Writer 提供本章涉及的故事线（主线 + 相关支线）。通过 embedding 相似度匹配支线，主线则全部装入。

### 数据源

- `subplot_threads` 表（加 thread_type 和 embedding 字段）

### 数据库 schema

```sql
ALTER TABLE subplot_threads ADD COLUMN thread_type TEXT NOT NULL DEFAULT 'sub';
    -- 'main' 主线 / 'sub' 支线

ALTER TABLE subplot_threads ADD COLUMN embedding BLOB;
ALTER TABLE subplot_threads ADD COLUMN embedding_updated_at TIMESTAMP;
```

### 函数签名

```python
def load(
    project_id: str,
    chapter_outline: str,
    chapter_num: int,
    top_k: int = 5,
    min_relevance: float = 0.3,
    exclude: set | None = None,
) -> str:
    """加载本章涉及的故事线。主线全部，支线 embedding 匹配 top-K。"""
```

### 核心逻辑

```python
def load(project_id, chapter_outline, chapter_num, top_k=5, min_relevance=0.3, exclude=None):
    subplots = db.execute("""
        SELECT subplot_id, name, summary, thread_type, status, embedding
        FROM subplot_threads
        WHERE project_id = ? AND status != 'resolved'
    """, (project_id,)).fetchall()
    
    # 主线全部装入
    main_lines = [s for s in subplots if s.thread_type == 'main']
    
    # 支线 embedding 筛选
    sub_lines_pool = [s for s in subplots if s.thread_type == 'sub']
    
    query_emb = embed(chapter_outline)
    scored = []
    for s in sub_lines_pool:
        if not s.embedding:
            s.embedding = embed(s.summary)
            update_subplot_embedding(s.subplot_id, s.embedding)
        score = cosine(query_emb, s.embedding)
        scored.append((score, s))
    
    scored.sort(reverse=True)
    selected_subs = [s for score, s in scored[:top_k] if score >= min_relevance]
    
    parts = []
    
    if main_lines:
        parts.append("### 主线（必须推进或呼应）")
        for m in main_lines:
            parts.append(f"- [{m.name}]（{m.status}）{m.summary}")
    
    if selected_subs:
        parts.append("\n### 相关支线（本章可推进）")
        for s in selected_subs:
            parts.append(f"- [{s.name}]（{s.status}）{s.summary}")
    
    if not parts:
        return ""
    return section("当前涉及的故事线", "\n".join(parts))
```

### 输出格式

```markdown
## 当前涉及的故事线

### 主线（必须推进或呼应）
- [张远的身世之谜]（building）从认识父亲遗物开始的真相揭露
- [对抗玄阴宗]（climax）和玄阴宗的最终对决线

### 相关支线（本章可推进）
- [李清漪的家族秘密]（setup）她对玄阴宗的奇怪反应
- [师父的隐瞒]（building）师父似乎知道某个秘密但不肯说
- [幽冥谷的远古封印]（setup）幽冥谷可能藏着重要遗物
```

### Budget

1200 字符

### 暴露 API

```
GET    /api/subplots?project_id=X
POST   /api/subplots
PUT    /api/subplots/:id
DELETE /api/subplots/:id
POST   /api/subplots/reindex      强制重算所有 embedding
```

---

# Loader 13: user_preferences

### 使用场景

每章生成时为 Writer 提供从用户历史修改学习到的写作偏好。由 EditAnalyzer 在用户每次修改后自动更新。

### 数据源

- `user_style_preferences` 表（已存在）

### 数据库 schema

无新增。

### 函数签名

```python
def load(
    project_id: str,
    min_confidence: float = 0.5,
    per_type_limit: int = 3,
    exclude: set | None = None,
) -> str:
    """加载从用户历史修改中学习到的偏好。"""
```

### 核心逻辑

```python
def load(project_id, min_confidence=0.5, per_type_limit=3, exclude=None):
    prefs = db.execute("""
        SELECT preference_type, content, confidence
        FROM user_style_preferences
        WHERE project_id = ?
          AND confidence >= ?
        ORDER BY preference_type, confidence DESC
    """, (project_id, min_confidence)).fetchall()
    
    if not prefs:
        return ""
    
    by_type = {}
    for p in prefs:
        by_type.setdefault(p.preference_type, []).append(p)
    
    parts = []
    type_labels = {'style': '风格', 'content': '内容', 'pacing': '节奏'}
    
    for ptype, label in type_labels.items():
        if ptype in by_type:
            parts.append(f"### {label}")
            for p in by_type[ptype][:per_type_limit]:
                parts.append(f"- {p.content}")  # 不显示置信度
    
    return section("从历史修改中学习的用户偏好", "\n".join(parts))
```

### 输出格式

```markdown
## 从历史修改中学习的用户偏好

### 风格
- 偏好短句，平均句长 15-25 字
- 避免成语堆叠
- 心理描写多于环境描写

### 内容
- 偏好"展示"而非"说出"
- 角色对话要带潜台词

### 节奏
- 每段控制在 3-5 句
- 章末必有钩子
```

### Budget

500 字符

---

# Loader 14: skills

### 使用场景

每章生成时为 Writer 提供创作技能。skill = 一段写作指令。**双路径**：user 主动 pin 的强制装入 + embedding 相似度自动匹配 top-K。

### 数据源

- `agents/skills/` 目录下的 SKILL.md 文件
- `skill_registry` 表（加 embedding 字段）
- 项目级 pin 表

### 数据库 schema

```sql
ALTER TABLE skill_registry ADD COLUMN description_embedding BLOB;
ALTER TABLE skill_registry ADD COLUMN embedding_updated_at TIMESTAMP;

CREATE TABLE IF NOT EXISTS project_skill_pins (
    project_id TEXT NOT NULL,
    skill_id TEXT NOT NULL,
    pinned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_id, skill_id)
);
```

### 函数签名

```python
def load(
    project_id: str,
    chapter_outline: str,
    on_stage_characters: list[str],
    user_pinned_skill_ids: list[str] | None = None,
    max_total: int = 5,
    min_relevance: float = 0.3,
    exclude: set | None = None,
) -> str:
    """加载创作技能：user pin + embedding 相似度 top-K。"""
```

### 核心逻辑

```python
def load(project_id, chapter_outline, on_stage_characters, 
         user_pinned_skill_ids=None, max_total=5, min_relevance=0.3, exclude=None):
    
    available = registry.list_active(project_id)
    
    pin_ids = set(user_pinned_skill_ids or [])
    pin_ids.update(get_project_skill_pins(project_id))
    
    pinned = [s for s in available if s.skill_id in pin_ids]
    
    candidates = [s for s in available if s.skill_id not in pin_ids]
    
    query = chapter_outline + "\n人物：" + ",".join(on_stage_characters)
    query_emb = embed(query)
    
    scored = []
    for s in candidates:
        if not s.description_embedding:
            s.description_embedding = embed(s.description)
            update_skill_embedding(s.skill_id, s.description_embedding)
        score = cosine(query_emb, s.description_embedding)
        scored.append((score, s))
    
    scored.sort(reverse=True)
    
    remaining_quota = max_total - len(pinned)
    auto_selected = [s for score, s in scored[:remaining_quota] if score >= min_relevance]
    
    parts = []
    
    if pinned:
        parts.append(f"### 用户主动选中（{len(pinned)} 项）")
        for skill in pinned:
            parts.append(f"\n**{skill.display_name}**")
            parts.append(skill.body)
    
    if auto_selected:
        parts.append(f"\n### 系统推荐（本章相关，{len(auto_selected)} 项）")
        for skill in auto_selected:
            parts.append(f"\n**{skill.display_name}**（type: {skill.type}）")
            parts.append(skill.body)
    
    if not parts:
        return ""
    return section(f"创作技能（已加载 {len(pinned) + len(auto_selected)} 项）", "\n".join(parts))
```

### 输出格式

```markdown
## 创作技能（已加载 5 项）

### 用户主动选中（2 项）

**钩子设计模板**
[SKILL.md body 内容...]

**爽点节奏指引**
[SKILL.md body 内容...]

### 系统推荐（本章相关，3 项）

**神秘氛围描写**（type: technique）
[body...]

**中医草药基础**（type: knowledge）
[body...]

**冷峻人设强化**（type: learned）
[body...]
```

### Budget

2400 字符

### 暴露 API

```
POST   /api/skills/:id/pin-to-project
DELETE /api/skills/:id/pin-to-project
POST   /api/skills/reindex
```

---

# Builder 整合

### 完整 Builder 签名

```python
# ui/backend/app/services/prompt_context/builder.py

def build_chapter_prompt(
    project_id: str,
    chapter_id: str,
    chapter_num: int,
    on_stage_entities: dict[str, list[str]],
    pov_character: str | None = None,
    scene_types: list[str] | None = None,
    
    generation_mode: str = 'fresh',
    revision_anchor: dict | None = None,
    
    linked_inspirations: list[str] | None = None,
    user_pinned_skill_ids: list[str] | None = None,
    
    rag_excludes: list[str] | None = None,
) -> dict:
    """组装单 agent 章节生成的完整 prompt。"""
    
    chapter = get_chapter(project_id, chapter_id)
    chapter_outline = chapter.synopsis
    on_stage_characters = on_stage_entities.get('characters', [])
    
    excl = parse_rag_excludes(rag_excludes or [])
    
    reminders = snapshot_reminder.check_overdue_reminders(project_id, chapter_num)
    
    blocks = {}
    
    # System Layer (3)
    blocks["platform_style"] = platform_style.load(
        project_id, chapter_num, exclude=excl.get("platform_style"))
    blocks["reference"] = reference.load(
        project_id, chapter_outline, chapter_num, scene_types, 
        exclude=excl.get("reference"))
    blocks["user_preferences"] = user_preferences.load(
        project_id, exclude=excl.get("user_preferences"))
    
    # Project Static (3)
    blocks["character_cards"] = character_cards.load(
        project_id, chapter_num, on_stage_characters, 
        exclude=excl.get("character_cards"))
    blocks["worldbook"] = worldbook.load(
        project_id, chapter_outline, on_stage_characters, 
        exclude=excl.get("worldbook"))
    blocks["chapter_outline"] = chapter_outline_loader.load(
        project_id, chapter_id, exclude=excl.get("chapter_outline"))
    
    # Project Dynamic (5)
    blocks["reader_memory"] = reader_memory.load(
        project_id, chapter_num, chapter_outline, on_stage_characters, 
        exclude=excl.get("reader_memory"))
    blocks["current_chapter_draft"] = current_chapter_draft.load(
        project_id, chapter_id, generation_mode, revision_anchor, 
        exclude=excl.get("current_chapter_draft"))
    blocks["storyland_state"] = storyland_state.load(
        project_id, chapter_num, on_stage_entities, pov_character, 
        exclude=excl.get("storyland_state"))
    blocks["foreshadowing"] = foreshadowing.load(
        project_id, chapter_num, pov_character, 
        exclude=excl.get("foreshadowing"))
    blocks["subplots"] = subplots.load(
        project_id, chapter_outline, chapter_num, 
        exclude=excl.get("subplots"))
    
    # Resources (2)
    blocks["inspiration"] = inspiration.load(
        project_id, chapter_outline, on_stage_characters,
        user_pinned_ids=linked_inspirations, exclude=excl.get("inspiration"))
    blocks["skills"] = skills.load(
        project_id, chapter_outline, on_stage_characters,
        user_pinned_skill_ids=user_pinned_skill_ids, exclude=excl.get("skills"))
    
    system_msg = _compose_system(blocks)
    context_msg = _compose_context(blocks)
    user_msg = _compose_user(blocks, chapter, pov_character, generation_mode)
    
    return {
        "system": system_msg,
        "context": context_msg,
        "user": user_msg,
        "blocks": blocks,
        "reminders": reminders,
        "diagnostics": _compute_diagnostics(blocks),
    }
```

### 三段组装

```python
def _compose_system(blocks):
    parts = [
        "你是一位资深的中文网络小说作家。请根据以下信息创作本章正文。"
    ]
    if blocks.get("platform_style"):
        parts.append(blocks["platform_style"])
    if blocks.get("reference"):
        parts.append(blocks["reference"])
    if blocks.get("user_preferences"):
        parts.append(blocks["user_preferences"])
    return "\n\n".join(parts)

def _compose_context(blocks):
    parts = []
    for key in ["character_cards", "worldbook"]:
        if blocks.get(key):
            parts.append(blocks[key])
    for key in ["reader_memory", "storyland_state", "foreshadowing", "subplots"]:
        if blocks.get(key):
            parts.append(blocks[key])
    for key in ["inspiration", "skills"]:
        if blocks.get(key):
            parts.append(blocks[key])
    return "\n\n".join(parts)

def _compose_user(blocks, chapter, pov_character, generation_mode):
    parts = []
    if blocks.get("chapter_outline"):
        parts.append(blocks["chapter_outline"])
    if blocks.get("current_chapter_draft"):
        parts.append(blocks["current_chapter_draft"])
    parts.append(_generate_directive(generation_mode, pov_character, chapter))
    return "\n\n".join(parts)
```

---

# 跨切关注点

## 公共工具

`ui/backend/app/services/prompt_context/utils.py`：

```python
def section(title: str, content: str) -> str:
    """统一的 section 渲染。"""
    if not content or not content.strip():
        return ""
    return f"## {title}\n{content}\n"

def parse_rag_excludes(excludes_list: list[str]) -> dict:
    """解析 user 的 exclude 配置。"""

def truncate_to_budget(text: str, max_chars: int) -> str:
    """按段落安全截断到 budget 内。"""

def embed(text: str) -> bytes:
    """生成 embedding（用项目配置的中文 model）。"""

def cosine(a: bytes, b: bytes) -> float:
    """计算 cosine 相似度。"""
```

## Budget 集中配置

`ui/backend/app/services/prompt_context/budgets.py`：

```python
BUDGETS = {
    "platform_style": 250,
    "reference": 2400,
    "inspiration": 800,
    "character_cards": 2200,
    "worldbook": 1600,
    "chapter_outline": 1200,
    "reader_memory": 4500,
    "current_chapter_draft": 4000,
    "storyland_state": 2000,
    "foreshadowing": 1200,
    "subplots": 1200,
    "user_preferences": 500,
    "skills": 2400,
}
```

## 全部 schema 变更总结

```sql
-- 1. platform_profiles（新表）
-- 2. storyland_entities（新表）
-- 3. truth_current_state 加 subject_type
-- 4. chapters 加 on_stage_entities
-- 5. chapter_summaries 加 is_anchor
-- 6. character_snapshots（新表）
-- 7. character_snapshot_reminders（新表）
-- 8. chapter_segments（新表）
-- 9. chapter_failed_generations（新表）
-- 10. inspirations（新表，在 idea.db）
-- 11. subplot_threads 加 thread_type / embedding
-- 12. pending_hooks 加 user 完全收回字段
-- 13. worldbook_entries 加 embedding
-- 14. skill_registry 加 embedding
-- 15. project_skill_pins（新表）
```

## 重命名总结

```
所有 Python 标识符：
Memory*       -> ReaderMemory*
TruthFile*    -> StorylandState*
truth_*       -> state_*（变量名）

所有目录：
knowledge/memory/        -> knowledge/reader_memory/
knowledge/truth/         -> knowledge/storyland_state/

所有文档：
docs/MEMORY_VS_TRUTH.md  -> docs/READER_MEMORY_VS_STORYLAND_STATE.md
docs/truth_file_system.md -> docs/storyland_state_system.md
（新增）docs/PHILOSOPHY.md

SQL 表名保持不变（避免数据迁移风险）。
```

## 数据迁移脚本清单

```python
# scripts/migrations/
01_rename_memory_to_reader_memory.py
02_rename_truth_to_storyland_state.py
03_add_platform_profiles_table.py
04_add_storyland_entities_table.py
05_add_truth_subject_type_column.py
06_add_chapters_on_stage_entities.py
07_add_chapter_summaries_is_anchor.py
08_add_character_snapshots_tables.py
09_add_chapter_segments_tables.py
10_migrate_chapter_content_to_segments.py
11_add_inspirations_table.py
12_add_subplot_thread_type_embedding.py
13_add_foreshadowing_user_fields.py
14_add_worldbook_embeddings.py
15_add_skill_embeddings.py
16_backfill_truth_subject_type.py
17_backfill_entities_from_characters_and_worldbook.py
```

## 全部 API 端点清单

```
# Entity
GET    /api/entities
POST   /api/entities
PUT    /api/entities/:id
DELETE /api/entities/:id

# Inspiration
GET    /api/inspirations
POST   /api/inspirations
PUT    /api/inspirations/:id
DELETE /api/inspirations/:id

# Snapshot
POST   /api/snapshots
PUT    /api/snapshots/:id
DELETE /api/snapshots/:id
POST   /api/snapshots/:id/bind-chapter
POST   /api/snapshots/:id/unbind-chapter
POST   /api/snapshots/:id/mark-complete
POST   /api/snapshots/:id/unmark-complete
GET    /api/snapshots/auto-detect/:chapter_id
POST   /api/snapshots/auto-detect/:result_id/confirm
GET    /api/snapshot-reminders
POST   /api/snapshot-reminders/:id/acknowledge

# Chapter content
POST   /api/generation/fresh
POST   /api/generation/continue
POST   /api/generation/rewrite-from
POST   /api/generation/modify-section
POST   /api/chapters/:id/archive-failed
GET    /api/chapters/:id/segments
PUT    /api/chapters/:id/content
POST   /api/chapters/:id/toggle-anchor

# Foreshadowing
POST   /api/foreshadowing/:hook_id/fully-resolve

# Market
GET    /api/storyland/market-snapshot
GET    /api/storyland/state

# Skill
POST   /api/skills/:id/pin-to-project
DELETE /api/skills/:id/pin-to-project

# Reference
PUT    /api/projects/:id/reference-injection

# Maintenance
POST   /api/worldbook/reindex
POST   /api/subplots/reindex
POST   /api/skills/reindex
```

## 测试要求

每个 loader 至少包含：

```python
tests/ui_backend/test_<loader_name>.py:
  - test_empty_input         # 无数据时返回空
  - test_basic_output         # 基本输出格式
  - test_budget_respected     # budget 内
  - test_exclude_works        # exclude 参数生效
  - test_chapter_causal       # 因果过滤（如适用）
```

针对复杂 loader 额外测试：

```python
test_character_cards.py:
  - test_4_transition_states
  - test_reminder_overdue

test_current_chapter_draft.py:
  - test_4_modes
  - test_source_tracking
  - test_anti_hint

test_reference.py:
  - test_5_features_dual_path
  - test_explicit_forces
  - test_auto_supplements

test_reader_memory.py:
  - test_causal_filter
  - test_4_sublayers
```

## 实施顺序建议

```
第 1 批（独立 + 最小改动）：
  - 重命名 Memory -> ReaderMemory
  - 重命名 Truth Files -> StorylandState
  - platform_style 简化
  - foreshadowing 加 user 完全收回字段
  - storyland_state 移除 relations 段

第 2 批（基础设施）：
  - storyland_entities 表
  - chapter_segments 表 + segment manager
  - chapter_failed_generations 表 + failure analyzer
  - chapters.on_stage_entities 字段
  - truth_current_state.subject_type 字段

第 3 批（独立新 loader）：
  - inspirations 表 + loader
  - chapter_outline loader 拆出
  - current_chapter_draft loader（依赖第 2 批）

第 4 批（embedding 化改造）：
  - worldbook embedding
  - subplots thread_type + embedding
  - skills embedding + pin
  - reference 5 feature 双路径

第 5 批（大改造）：
  - character_snapshots 表 + resolver + 4 transition 状态
  - auto detector + reminder system
  - storyland_state 推广到全实体

第 6 批（重写 + 集成）：
  - reader_memory loader 完整重写（4 子层）
  - builder 整合 14 loader
  - regression test
```

## 验收清单

```
[ ] 14 个 loader 全部实现，文件路径正确
[ ] 所有 SQL 迁移成功，无数据丢失
[ ] 重命名彻底（grep Memory / TruthFile 应无残留）
[ ] reader_memory 严格因果（chapter_num < K）
[ ] character_cards 4 种 transition 状态正确渲染
[ ] current_chapter_draft 4 种模式行为正确
[ ] reference 5 feature 双路径正确
[ ] foreshadowing user_marked_fully_resolved 永远过滤
[ ] storyland_state 能 render 非角色实体
[ ] 所有 API 端点功能正常
[ ] 单元测试覆盖率 > 80%
[ ] e2e 测试通过
[ ] 文档更新：ARCHITECTURE.md, PHILOSOPHY.md
[ ] CHANGELOG 记录所有变更
```

---

# 给 Claude Code 的执行说明

将本文档保存为 `docs/LOADER_SPEC.md`，作为整个改造的 single source of truth。**UI 改动由用户自行处理，本文档仅涵盖后端逻辑。**

执行模式建议：

```
session 1: 完成"第 1 批"所有改动，运行测试
session 2: 完成"第 2 批"基础设施
session 3: 完成"第 3 批"独立新 loader
session 4: 完成"第 4 批"embedding 改造
session 5: 完成"第 5 批"snapshot 大改造（最复杂，可拆分多 session）
session 6: 完成"第 6 批"重写 + 集成，跑 regression test

每个 session 开头：
  "我需要完成 LOADER_SPEC.md 中的第 X 批改动。
  请先列出涉及的所有文件 + 风险点 + 测试 case，
  等我 confirm 后再开始动代码。"

每个 session 结束：
  "请总结本 session 改动的所有文件，
  跑测试给我结果，
  给我一个 commit message 草稿。"
```

执行过程中如发现 spec 不清晰的地方，先 stop 并和 Frank 确认，不要自行假设。
