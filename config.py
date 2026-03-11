# config.py
"""
InkOctoBot 配置加载器（薄兼容层）

实际配置存储在 config/ 目录的 YAML 文件中。
本文件加载 YAML 并暴露与旧 config.py 完全相同的属性名，
使得所有 `import config; config.WEBSITES` 的代码无需修改。
"""
import os
import sys
from pathlib import Path
from typing import Any, Dict

import yaml

# ------------------------------------------------------------------
# BASE_DIR：源码态 vs PyInstaller 打包态
# ------------------------------------------------------------------
if hasattr(sys, "_MEIPASS"):
    if os.name == "nt":
        BASE_DIR = os.path.join(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
            "InkOctoBot",
        )
    else:
        BASE_DIR = os.path.join(
            os.path.expanduser("~"), ".local", "share", "InkOctoBot"
        )
    os.makedirs(BASE_DIR, exist_ok=True)
    # 打包态下 YAML 文件在 _MEIPASS/config/ 里
    _CONFIG_DIR = os.path.join(sys._MEIPASS, "config")
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    _CONFIG_DIR = os.path.join(BASE_DIR, "config")


def _load_yaml(filename: str) -> Dict[str, Any]:
    """加载 config/ 目录下的 YAML 文件，不存在则返回空 dict"""
    path = os.path.join(_CONFIG_DIR, filename)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# ------------------------------------------------------------------
# 加载所有 YAML
# ------------------------------------------------------------------
_paths = _load_yaml("paths.yaml")
_websites = _load_yaml("websites.yaml")
_crawler = _load_yaml("crawler.yaml")
_selenium = _load_yaml("selenium.yaml")
_antiblock = _load_yaml("antiblock.yaml")
_scheduler = _load_yaml("scheduler.yaml")
_analysis = _load_yaml("analysis.yaml")


# ------------------------------------------------------------------
# 暴露与旧 config.py 完全相同的属性名
# ------------------------------------------------------------------

# --- WEBSITES ---
WEBSITES: Dict[str, Any] = _websites

# --- DATABASE ---
_db_cfg = _paths.get("database", {})
_db_rel = _db_cfg.get("path", "data/novels.db")
DATABASE: Dict[str, Any] = {
    "path": os.path.join(BASE_DIR, _db_rel),
    "tables": {
        "novels": "novels",
        "novel_titles": "novel_titles",
        "tags": "tags",
        "novel_tag_map": "novel_tag_map",
        "rank_lists": "rank_lists",
        "rank_snapshots": "rank_snapshots",
        "rank_entries": "rank_entries",
        "first_n_chapters": "first_n_chapters",
    },
}

# --- SPIDER_DATABASE ---
_crawler_db_cfg = _paths.get("crawler_database", {})
_crawler_db_rel = _crawler_db_cfg.get("path", "data/InkOctoBot_Crawler.db")
CRAWLER_DATABASE: Dict[str, Any] = {
    "path": os.path.join(BASE_DIR, _crawler_db_rel),
    "tables": DATABASE["tables"],
}
# backward-compatible alias
SPIDER_DATABASE: Dict[str, Any] = CRAWLER_DATABASE

# --- OUTPUT_PATHS ---
_out_cfg = _paths.get("output_paths", {})
OUTPUT_PATHS: Dict[str, str] = {
    "data": os.path.join(BASE_DIR, _out_cfg.get("data_raw", "outputs/data/raw")),
    "logs": os.path.join(BASE_DIR, _out_cfg.get("logs", "outputs/logs")),
    "reports": os.path.join(BASE_DIR, _out_cfg.get("reports", "outputs/reports")),
    "visualizations": os.path.join(
        BASE_DIR, _out_cfg.get("visualizations", "outputs/reports/visualizations")
    ),
}

# 确保目录存在
for _p in OUTPUT_PATHS.values():
    os.makedirs(_p, exist_ok=True)
os.makedirs(os.path.dirname(DATABASE["path"]), exist_ok=True)
os.makedirs(os.path.dirname(CRAWLER_DATABASE["path"]), exist_ok=True)

# --- SELENIUM_CONFIG ---
SELENIUM_CONFIG: Dict[str, Any] = _selenium

# --- CRAWLER_CONFIG ---
CRAWLER_CONFIG: Dict[str, Any] = _crawler

# --- ANTI_BLOCK_CONFIG ---
ANTI_BLOCK_CONFIG: Dict[str, Any] = _antiblock

# --- SCHEDULER ---
SCHEDULER: Dict[str, Any] = _scheduler

# --- ANALYSIS_CONFIG ---
ANALYSIS_CONFIG: Dict[str, Any] = _analysis