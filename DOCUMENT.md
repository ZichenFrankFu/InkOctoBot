# InkOctoBot 数据存储路径文档

> 本文档与 `config/paths.yaml` 及 `config.py` 保持同步。
> 所有路径均相对于项目根目录（`BASE_DIR`）。

---

## 1. 配置系统

| 文件 | 说明 |
|------|------|
| `config.py` | 配置加载器（薄兼容层），加载 YAML 并暴露属性名 |
| `config/paths.yaml` | 路径配置（数据库、输出目录） |
| `config/websites.yaml` | 网站配置（起点、番茄等平台参数） |
| `config/crawler.yaml` | 爬虫配置（并发数、间隔、重试策略） |
| `config/selenium.yaml` | Selenium WebDriver 配置 |
| `config/antiblock.yaml` | 反爬虫配置（代理池、UA轮换） |
| `config/scheduler.yaml` | 定时调度配置 |
| `config/analysis.yaml` | 分析模块配置 |

---

## 2. 数据存储路径

### 2.1 SQLite 数据库

| 路径 | 说明 |
|------|------|
| `data/novels.db` | 市场数据主数据库 |

**数据表：**

| 表名 | 说明 |
|------|------|
| `novels` | 小说基础信息（platform, author, intro, category, status, total_words） |
| `novel_titles` | 小说标题（支持多标题/别名） |
| `tags` | 标签定义表 |
| `novel_tag_map` | 小说-标签多对多映射 |
| `rank_lists` | 排行榜定义（platform, rank_family, rank_sub_cat） |
| `rank_snapshots` | 排行榜快照（snapshot_date, item_count） |
| `rank_entries` | 排行榜条目（rank, total_recommend, reading_count） |
| `first_n_chapters` | 小说前N章内容（用于开篇分析） |

### 2.2 JSON 文件存储（前端 CRUD）

由 `ui/backend/app/routers/data_api.py` 管理，按 collection 分目录存储。

| 路径 | 说明 |
|------|------|
| `data/projects/*.json` | 项目文件 |
| `data/characters/*.json` | 角色卡片（含 Layer A 定性 + Layer B 量化参数） |
| `data/worldbook/*.json` | 世界书条目 |
| `data/editor/*.json` | 编辑器状态（卷/章大纲、内容、版本） |
| `data/storyline/*.json` | 剧情线节点与连线 |
| `data/settings.json` | 全局设置（供应商、Pipeline 分配、系统参数） |

### 2.3 参考作品库（SQLite）

由 `ui/backend/app/routers/reference_api.py` 管理。

| 路径 | 说明 |
|------|------|
| `data/references.db` | 参考作品数据库 |

**数据表：**

| 表名 | 说明 |
|------|------|
| `reference_works` | 参考作品信息（title, creator, media_type, genre, source, 预处理状态等） |
| `reference_entries` | 参考作品条目（entry_type, content, position_label, user_notes） |

---

## 3. 输出路径

| 路径 | 配置键 | 说明 |
|------|--------|------|
| `outputs/data/raw/` | `output_paths.data_raw` | 爬虫原始数据 |
| `outputs/logs/` | `output_paths.logs` | 运行日志 |
| `outputs/reports/` | `output_paths.reports` | 分析报告（Markdown） |
| `outputs/reports/visualizations/` | `output_paths.visualizations` | 分析图表（PNG） |

---

## 4. 爬虫模块

| 文件 | 说明 |
|------|------|
| `spiders/base_spider.py` | 抽象基类（Selenium WebDriver、代理池、反爬） |
| `spiders/qidian_spider.py` | 起点中文网爬虫（畅销/月票/推荐/收藏/新书榜） |
| `spiders/fanqie_spider.py` | 番茄小说爬虫（阅读榜、新书榜） |
| `spiders/fanqie_font_decoder.py` | 番茄加密字体解码器 |
| `spiders/antibot.py` | 反机器人检测/规避模块 |

---

## 5. 分析模块

| 文件 | 说明 |
|------|------|
| `analysis/data_access.py` | SQLite 数据加载（长表格式） |
| `analysis/heat.py` | 统一热度计算（百分位、MAD Z-score、tanh 压缩） |
| `analysis/metrics.py` | 周聚合、时间窗口统计、机会分数、标签共现 |
| `analysis/trend_analyzer.py` | 核心编排器（数据→热度→指标→可视化→报告） |
| `analysis/visualization.py` | 图表生成 |
| `analysis/report.py` | Markdown 报告生成 |
| `analysis/run_analysis.py` | CLI 入口（`python run_analysis.py --db <path> --platform <p> --top_k 20`） |
| `analysis/feature_extraction/` | 高级特征提取（风格指纹、叙事结构、角色提取、节奏分析） |
| `analysis/formula_engine/` | 可扩展公式引擎（预设系统） |
| `analysis/ANALYSIS.md` | 分析方法论详细文档 |

---

## 6. UI 架构

### 6.1 后端（FastAPI）

| 路由文件 | 前缀 | 说明 |
|----------|------|------|
| `ui/backend/app/routers/db_api.py` | `/api/db/` | 市场数据库查询（novels.db） |
| `ui/backend/app/routers/analysis_api.py` | `/api/analysis/` | 趋势分析运行 |
| `ui/backend/app/routers/data_api.py` | `/api/data/` | JSON 文件 CRUD |
| `ui/backend/app/routers/reference_api.py` | `/api/references/` | 参考作品库 CRUD |

### 6.2 前端页面（React + TypeScript）

| 页面文件 | 导航标签 | 说明 |
|----------|----------|------|
| `DashboardPage.tsx` | 首页 | 项目概览、字数统计、参考偏好 |
| `RankingsPage.tsx` | 市场数据库 | 排行榜浏览 |
| `ReferenceLibraryPage.tsx` | 参考作品库 | 参考作品管理（含批量全选） |
| `AnalysisDashboardPage.tsx` | 分析面板 | 市场概览 + 趋势分析（已合并） |
| `ProjectListPage.tsx` | 项目管理 | 项目列表 |
| `ProjectSetupPage.tsx` | - | 项目设置 |
| `EditorPage.tsx` | 编辑器 | 大纲/内容编辑、Pipeline 自动生成 |
| `CharacterManagerPage.tsx` | 角色管理 | 角色卡片（Layer A/B）、关系图 |
| `WorldBookPage.tsx` | 世界书 | 世界观设定 |
| `StorylinePage.tsx` | 剧情线 | 节点图 + 时间周轴 |
| `SettingsPage.tsx` | 设置 | Pipeline/供应商/系统配置 |

---

## 7. BASE_DIR 规则

| 环境 | BASE_DIR |
|------|----------|
| 开发（源码） | 项目根目录 |
| PyInstaller（Windows） | `%LOCALAPPDATA%/InkOctoBot/` |
| PyInstaller（Linux/Mac） | `~/.local/share/InkOctoBot/` |

所有相对路径在运行时由 `config.py` 拼接 `BASE_DIR` 得到绝对路径。
