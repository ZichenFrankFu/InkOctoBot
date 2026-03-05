from __future__ import annotations
import json
import time
from pathlib import Path
from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..settings import settings
from ..utils import load_repo_config, get_output_paths, get_rank_keys

router = APIRouter(prefix="/config", tags=["config"])


def _runs_dir(repo_cfg) -> Path:
    out = get_output_paths(repo_cfg)
    runs_dir = Path(out.get("reports", str(settings.repo_root / "outputs" / "reports"))).parent / "config_runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    return runs_dir


class ConfigOverride(BaseModel):
    platform: str | None = Field(default=None, description="qidian|fanqie")
    rank_key: str | None = None
    pages: int | None = None                 # only qidian
    qidian_pages: int | None = None          # legacy fallback
    chapter_count: int = 5
    newbook_chapter_count: int = 2
    no_detail: bool = False
    no_chapters: bool = False
    use_proxy: bool | None = None
    max_retries: int | None = None
    consecutive_threshold: int | None = None


def _defaults_from_repo(repo_cfg) -> dict:
    crawler = getattr(repo_cfg, "CRAWLER_CONFIG", {}) or {}
    antibot = getattr(repo_cfg, "ANTI_BLOCK_CONFIG", {}) or {}
    nested_ab = crawler.get("antibot", {}) or {}

    return {
        "platform": "fanqie",
        "rank_key": "",
        "pages": None,
        "qidian_pages": 2,
        "chapter_count": 5,
        "newbook_chapter_count": 2,
        "no_detail": False,
        "no_chapters": False,
        "use_proxy": bool(crawler.get("use_proxy", False)),
        "max_retries": int(crawler.get("max_retries", 3)),
        "consecutive_threshold": int(nested_ab.get("consecutive_threshold", antibot.get("consecutive_threshold", 3))),
    }


    # runtime overrides from config/crawler.yaml + config/antiblock.yaml
    use_proxy: bool | None = None
    max_retries: int | None = None
    retry_delay: float | None = None
    consecutive_threshold: int | None = None
    antibot_min_html_length: int | None = None
    page_max_retries: int | None = None
    page_retry_delay: float | None = None
    page_default_wait_sec: int | None = None


def _defaults_from_repo(repo_cfg) -> dict:
    crawler = getattr(repo_cfg, "CRAWLER_CONFIG", {}) or {}
    antibot = getattr(repo_cfg, "ANTI_BLOCK_CONFIG", {}) or {}
    nested_ab = crawler.get("antibot", {}) or {}
    page_fetch = crawler.get("page_fetch", {}) or {}

    return {
        "platform": "fanqie",
        "rank_key": "",
        "pages": None,
        "qidian_pages": 2,
        "chapter_count": 5,
        "newbook_chapter_count": 2,
        "no_detail": False,
        "no_chapters": False,

        "use_proxy": bool(crawler.get("use_proxy", False)),
        "max_retries": int(crawler.get("max_retries", 3)),
        "retry_delay": float(crawler.get("retry_delay", 2)),
        "consecutive_threshold": int(nested_ab.get("consecutive_threshold", antibot.get("consecutive_threshold", 3))),
        "antibot_min_html_length": int(nested_ab.get("min_html_length", antibot.get("min_html_length", 800))),
        "page_max_retries": int(page_fetch.get("max_page_retries", 3)),
        "page_retry_delay": float(page_fetch.get("page_retry_delay", 3)),
        "page_default_wait_sec": int(page_fetch.get("default_wait_sec", 10)),
    }


    # runtime overrides from config/crawler.yaml + config/antiblock.yaml
    use_proxy: bool | None = None
    max_retries: int | None = None
    retry_delay: float | None = None
    consecutive_threshold: int | None = None
    antibot_min_html_length: int | None = None
    page_max_retries: int | None = None
    page_retry_delay: float | None = None
    page_default_wait_sec: int | None = None


def _defaults_from_repo(repo_cfg) -> dict:
    crawler = getattr(repo_cfg, "CRAWLER_CONFIG", {}) or {}
    antibot = getattr(repo_cfg, "ANTI_BLOCK_CONFIG", {}) or {}
    nested_ab = crawler.get("antibot", {}) or {}
    page_fetch = crawler.get("page_fetch", {}) or {}

    return {
        "platform": "fanqie",
        "rank_key": "",
        "pages": None,
        "qidian_pages": 2,
        "chapter_count": 5,
        "newbook_chapter_count": 2,
        "no_detail": False,
        "no_chapters": False,

        "use_proxy": bool(crawler.get("use_proxy", False)),
        "max_retries": int(crawler.get("max_retries", 3)),
        "retry_delay": float(crawler.get("retry_delay", 2)),
        "consecutive_threshold": int(nested_ab.get("consecutive_threshold", antibot.get("consecutive_threshold", 3))),
        "antibot_min_html_length": int(nested_ab.get("min_html_length", antibot.get("min_html_length", 800))),
        "page_max_retries": int(page_fetch.get("max_page_retries", 3)),
        "page_retry_delay": float(page_fetch.get("page_retry_delay", 3)),
        "page_default_wait_sec": int(page_fetch.get("default_wait_sec", 10)),
    }


    # runtime overrides from config/crawler.yaml + config/antiblock.yaml
    use_proxy: bool | None = None
    max_retries: int | None = None
    retry_delay: float | None = None
    consecutive_threshold: int | None = None
    antibot_min_html_length: int | None = None
    page_max_retries: int | None = None
    page_retry_delay: float | None = None
    page_default_wait_sec: int | None = None


def _defaults_from_repo(repo_cfg) -> dict:
    crawler = getattr(repo_cfg, "CRAWLER_CONFIG", {}) or {}
    antibot = getattr(repo_cfg, "ANTI_BLOCK_CONFIG", {}) or {}
    nested_ab = crawler.get("antibot", {}) or {}
    page_fetch = crawler.get("page_fetch", {}) or {}

    return {
        "platform": "fanqie",
        "rank_key": "",
        "pages": None,
        "qidian_pages": 2,
        "chapter_count": 5,
        "newbook_chapter_count": 2,
        "no_detail": False,
        "no_chapters": False,

        "use_proxy": bool(crawler.get("use_proxy", False)),
        "max_retries": int(crawler.get("max_retries", 3)),
        "retry_delay": float(crawler.get("retry_delay", 2)),
        "consecutive_threshold": int(nested_ab.get("consecutive_threshold", antibot.get("consecutive_threshold", 3))),
        "antibot_min_html_length": int(nested_ab.get("min_html_length", antibot.get("min_html_length", 800))),
        "page_max_retries": int(page_fetch.get("max_page_retries", 3)),
        "page_retry_delay": float(page_fetch.get("page_retry_delay", 3)),
        "page_default_wait_sec": int(page_fetch.get("default_wait_sec", 10)),
    }


@router.get("/schema")
def get_schema():
    repo_cfg = load_repo_config(settings.repo_root)
    rank_keys = get_rank_keys(repo_cfg)
    return {
        "defaults": _defaults_from_repo(repo_cfg),
        "rank_keys": rank_keys,
        "notes": {
            "pages": "仅起点有效；番茄固定 1 页滚动",
            "rank_key": "必须与 config.WEBSITES[platform]['rank_urls'] key 完全一致",
            "runtime_overrides": "以下高级参数仅影响本次运行；不改写 config/*.yaml",
            "use_proxy": "运行时临时覆盖 crawler.use_proxy，不改写本地 YAML",
            "max_retries": "运行时临时覆盖 crawler.max_retries",
            "consecutive_threshold": "运行时临时覆盖 anti-bot 连续触发阈值",
        },
    }


@router.post("/runs")
def create_run(override: ConfigOverride):
    repo_cfg = load_repo_config(settings.repo_root)
    runs_dir = _runs_dir(repo_cfg)

    run_id = f"cfg_{int(time.time()*1000)}"
    path = runs_dir / f"{run_id}.json"
    payload = override.model_dump()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"run_id": run_id, "path": str(path), "config": payload}
    path.write_text(json.dumps(override.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    return {"run_id": run_id, "path": str(path)}


@router.get("/runs")
def list_runs(limit: int = 30):
    repo_cfg = load_repo_config(settings.repo_root)
    runs_dir = _runs_dir(repo_cfg)

    runs = []
    for p in sorted(runs_dir.glob("cfg_*.json"), reverse=True):
        run_id = p.stem
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        runs.append({
            "run_id": run_id,
            "created_at": p.stat().st_mtime,
            "config": data,
        })
        if len(runs) >= max(limit, 1):
            break

    return {"runs": runs}
