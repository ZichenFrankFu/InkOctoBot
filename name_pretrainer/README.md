# InkOctoBot 人名识别预训练器（独立工具）

把 InkOctoBot 的**人名识别（LTP NER）**整套抽出来做**预训练**：依照现有市场数据库
（爬虫库）对全部小说开篇正文跑一遍 NER，建出一个干净的人名库（中文 / 日文 / 西方 +
性别），供你 review、编辑、取名、导入导出。**整个 folder 自包含**，可直接搬进
`InkOctoBot_Crawler` 单独运行，与 InkOctoBot 主程序解耦。

> 分工：本工具负责**全量预训练 + 人工清洗**；InkOctoBot 主程序只负责对市场数据库
> **新增小说正文的增量更新**（同一套 `name_refresh` 逻辑，只处理未处理过的新书）。
> 两边通过**导出 / 导入 JSON** 同步同一份人名库。

## 快速启动

```bash
# macOS / Linux
./run.sh --crawler-db /路径/market.db

# Windows
run.bat --crawler-db C:\路径\market.db
```

脚本会自动创建 `.venv`、安装依赖（首次较慢，含 torch / ltp）、起服务并打开浏览器
（默认 http://127.0.0.1:8765）。市场数据库路径也可在网页里填写、保存。

手动启动：

```bash
pip install -r requirements.txt
python app.py --crawler-db /路径/market.db --port 8765
```

## 用法

1. 顶部填 **市场数据库**（爬虫库 sqlite）路径并保存；确认「识别后端」是 `LTP · GPU/CPU`
   （若显示「LTP 未就绪」，按提示装好 ltp + 兼容版 transformers）。
2. 点 **运行预训练 / 增量更新** → 后台对所有新书跑 NER，进度条实时更新（可关页面，
   进程在后端继续）。首次=全量，之后=只处理新增。
3. 在 **中文 / 日文 / 西方** 三个分组里逐条 review：编辑分类 / 性别 / 例句、删除误报
   （删除即拉黑，不会被再次抽回）。误归类的可一键 **重新分类**。
4. **取名**：按 分类 / 性别 / 姓氏 / 题材 重组生成新名，点名字即复制。
5. **导出** 成 JSON（可填路径写盘或浏览器下载）→ 拿到 InkOctoBot 里 **导入**，即为
   增量更新的干净基线。

## 目录结构（全部自包含）

```
name_pretrainer/
  app.py                 # FastAPI 后端 + 提供 UI
  run.sh / run.bat       # 一键启动
  requirements.txt
  core/                  # 抽出来的人名识别核心（已与 InkOctoBot 解耦）
    ner_backend.py       #   LTP NER（硬件分级、兼容补丁）
    name_library.py      #   人名库 CRUD / 分类 / 性别 / 碎片去重 / 导入导出
    name_generator.py    #   取名（重组生成）
    name_refresh.py      #   对爬虫库新书跑 NER 入库（增量）
    wordlists.py         #   词表（姓/名/音译字/黑名单/性别用字 + 用户 overlay）
    schema.py hardware.py paths.py   # 自包含的 schema / 硬件检测 / 路径
  resources/wordlists/   # 打包词表
  resources/name_library/seed_names.txt
  data/                  # 运行时生成：name_library.db、词表 overlay、配置（gitignored）
  static/index.html      # 单页 UI（原生 JS，无需构建）
```

## 依赖说明

LTP 4.2.x 需要**兼容版 transformers**（新版 5.x 移除了 `batch_encode_plus`，会导致
NER 崩；代码内已有兼容补丁，requirements 也锁了 `transformers>=4.26,<4.41`）。需要
fast tokenizer（`tokenizers`），缺了会回退 slow 报错——按页面诊断提示安装即可。
