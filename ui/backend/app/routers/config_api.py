from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..settings import settings
from ..utils import get_output_paths, get_rank_keys, load_repo_config

router = APIRouter(prefix="/config", tags=["config"])


def _runs_dir(repo_cfg) -> Path:
    out = get_output_paths(repo_cfg)
    runs_dir = Path(out.get("reports", str(settings.repo_root / "outputs" / "reports"))).parent / "config_runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    return runs_dir


class ConfigOverride(BaseModel):
    platform: str | None = Field(default=None, description="qidian|fanqie (legacy)")
    platforms: list[str] | None = Field(default=None, description="multi-platform selection")

    rank_key: str | None = None
    rank_keys: list[str] | None = None
    qidian_ranks: list[str] | None = None
    fanqie_ranks: list[str] | None = None

    pages: int | None = None
    qidian_pages: int | None = None

    chapter_count: int = Field(default=5, ge=1, le=5)
    newbook_chapter_count: int = Field(default=2, ge=1, le=5)

    use_proxy: bool | None = None

    # keep backward compatibility for old saved configs
    no_detail: bool = False
    no_chapters: bool = False
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
        "platforms": ["fanqie"],
        "rank_key": "",
        "rank_keys": [],
        "qidian_ranks": [],
        "fanqie_ranks": [],
        "pages": 1,
        "qidian_pages": 2,
        "chapter_count": 5,
        "newbook_chapter_count": 2,
        "use_proxy": bool(crawler.get("use_proxy", False)),
        "no_detail": False,
        "no_chapters": False,
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
        "platforms": ["fanqie", "qidian"],
        "rank_keys": rank_keys,
        "notes": {
            "pages": "番茄固定 1 页；起点默认 2 页，可手动调整",
            "rank_key": "支持多选；选择 ALL 表示该平台全部榜单",
            "use_proxy": "运行时临时覆盖 crawler.use_proxy，不改写本地 YAML",
        },
    }


@router.post("/runs")
def create_run(override: ConfigOverride):
    repo_cfg = load_repo_config(settings.repo_root)
    runs_dir = _runs_dir(repo_cfg)

    run_id = f"cfg_{int(time.time() * 1000)}"
    path = runs_dir / f"{run_id}.json"
    payload = override.model_dump()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"run_id": run_id, "path": str(path), "config": payload}


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
        runs.append({"run_id": run_id, "created_at": p.stat().st_mtime, "config": data})
        if len(runs) >= max(limit, 1):
            break

    return {"runs": runs}


@router.delete("/runs/{run_id}")
def delete_run(run_id: str):
    repo_cfg = load_repo_config(settings.repo_root)
    path = _runs_dir(repo_cfg) / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"config run not found: {run_id}")
    path.unlink()
    return {"ok": True, "run_id": run_id}
