# Crawler 数据接入说明

当前仓库不再包含爬取实现代码，爬虫能力已迁移到独立仓库。
本仓库只消费外部同步的 SQLite 数据文件：

- `data/InkOctoBot_Crawler.db`

## 配置

在 `config/paths.yaml` 配置：

```yaml
crawler_database:
  path: "data/InkOctoBot_Crawler.db"
```

`config.py` 会导出：

- `CRAWLER_DATABASE`

并保留旧字段 `SPIDER_DATABASE` 作为兼容别名（用于旧代码平滑过渡）。

## 当前读取方

- `ui/backend/app/routers/db_api.py`
- `ui/backend/app/routers/analysis_api.py`
- `ui/backend/app/routers/marketing_api.py`

以上接口均从 crawler 数据库读取榜单/快照/章节等信息。
