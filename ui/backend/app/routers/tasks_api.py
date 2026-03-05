from __future__ import annotations
import json
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query

from ..settings import settings
from ..utils import load_repo_config, get_output_paths
from ..store import TaskStore, Task, new_task_id
from ..runner import ProcessRunner
from .config_api import ConfigOverride

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _runs_dir(repo_cfg) -> Path:
    out = get_output_paths(repo_cfg)
    runs_dir = Path(out.get("reports", str(settings.repo_root / "outputs" / "reports"))).parent / "config_runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    return runs_dir


def _logs_dir(repo_cfg) -> Path:
    out = get_output_paths(repo_cfg)
    p = out.get("logs", str(settings.repo_root / "outputs" / "logs"))
    return Path(p)


def _ui_tasks_dir(repo_cfg) -> Path:
    out = get_output_paths(repo_cfg)
    base = Path(out.get("logs", str(settings.repo_root / "outputs" / "logs"))).parent / "ui_tasks"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _build_main_cmd(repo_root: Path, python_bin: str, override: dict) -> list[str]:
    cmd = [python_bin, str(repo_root / "main.py"), "once"]

    platforms = override.get("platforms") or ([] if not override.get("platform") else [override["platform"]])
    platforms = [p for p in platforms if p in {"qidian", "fanqie"}]

    rank_keys = override.get("rank_keys") or ([] if not override.get("rank_key") else [override["rank_key"]])
    qidian_ranks = [r.split("::", 1)[1] if "::" in r else r for r in (override.get("qidian_ranks") or [])]
    fanqie_ranks = [r.split("::", 1)[1] if "::" in r else r for r in (override.get("fanqie_ranks") or [])]

    if len(platforms) == 1:
        platform = platforms[0]
        cmd += ["--platform", platform]

        scoped_ranks = qidian_ranks if platform == "qidian" else fanqie_ranks
        if not scoped_ranks:
            scoped_ranks = [r.split("::", 1)[1] if "::" in r else r for r in rank_keys]
        if len(scoped_ranks) == 1:
            cmd += ["--rank_key", scoped_ranks[0]]

        if platform == "qidian":
            pages = override.get("pages")
            if pages is None:
                pages = override.get("qidian_pages", 2)
            cmd += ["--pages", str(int(pages))]
    else:
        if qidian_ranks:
            cmd += ["--qidian_ranks", ",".join(qidian_ranks)]
        if fanqie_ranks:
            cmd += ["--fanqie_ranks", ",".join(fanqie_ranks)]
        if override.get("qidian_pages") is not None:
            cmd += ["--qidian_pages", str(int(override["qidian_pages"]))]

    cmd += ["--chapter_count", str(int(override.get("chapter_count", 5)))]
    cmd += ["--newbook_chapter_count", str(int(override.get("newbook_chapter_count", 2)))]

    if override.get("no_detail"):
        cmd.append("--no_detail")
    if override.get("no_chapters"):
        cmd.append("--no_chapters")

    if override.get("use_proxy") is True:
        cmd.append("--use_proxy")
    elif override.get("use_proxy") is False:
        cmd.append("--no_use_proxy")

    if override.get("max_retries") is not None:
        cmd += ["--max_retries", str(int(override["max_retries"]))]
    if override.get("retry_delay") is not None:
        cmd += ["--retry_delay", str(float(override["retry_delay"]))]

    if override.get("consecutive_threshold") is not None:
        cmd += ["--consecutive_threshold", str(int(override["consecutive_threshold"]))]
    if override.get("antibot_min_html_length") is not None:
        cmd += ["--antibot_min_html_length", str(int(override["antibot_min_html_length"]))]

    if override.get("page_max_retries") is not None:
        cmd += ["--page_max_retries", str(int(override["page_max_retries"]))]
    if override.get("page_retry_delay") is not None:
        cmd += ["--page_retry_delay", str(float(override["page_retry_delay"]))]
    if override.get("page_default_wait_sec") is not None:
        cmd += ["--page_default_wait_sec", str(int(override["page_default_wait_sec"]))]

    return cmd


def _start_task_from_override(override: dict, config_run_id: str | None):
    repo_cfg = load_repo_config(settings.repo_root)
    cmd = _build_main_cmd(settings.repo_root, settings.python_bin, override)

    store = TaskStore(_ui_tasks_dir(repo_cfg))
    runner = ProcessRunner(store)

    task_id = new_task_id()
    log_path = _logs_dir(repo_cfg) / f"ui_{task_id}.log"

    task = Task(task_id=task_id, task_type="spider", status="queued", created_at=__import__("time").time(), config_run_id=config_run_id)
    store.upsert(task)
    runner.run_background(task=task, cmd=cmd, log_path=log_path)

    return {"task_id": task_id, "log_path": str(log_path), "command": cmd}


@router.post("/spider")
def start_spider(run_id: str):
    repo_cfg = load_repo_config(settings.repo_root)
    runs_dir = _runs_dir(repo_cfg)
    run_path = runs_dir / f"{run_id}.json"
    if not run_path.exists():
        raise HTTPException(status_code=404, detail=f"config run not found: {run_id}")

    override = json.loads(run_path.read_text(encoding="utf-8"))
    return _start_task_from_override(override, run_id)


@router.post("/spider/adhoc")
def start_spider_adhoc(override: ConfigOverride):
    payload = override.model_dump()
    return _start_task_from_override(payload, None)


@router.get("")
def list_tasks():
    repo_cfg = load_repo_config(settings.repo_root)
    store = TaskStore(_ui_tasks_dir(repo_cfg))
    return {"tasks": [t.__dict__ for t in store.list()]}


@router.get("/{task_id}")
def get_task(task_id: str):
    repo_cfg = load_repo_config(settings.repo_root)
    store = TaskStore(_ui_tasks_dir(repo_cfg))
    t = store.get(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="task not found")
    return t.__dict__


@router.get("/{task_id}/logs")
def get_logs(task_id: str, offset: int = Query(default=0, ge=0)):
    repo_cfg = load_repo_config(settings.repo_root)
    store = TaskStore(_ui_tasks_dir(repo_cfg))
    t = store.get(task_id)
    if not t or not t.log_path:
        raise HTTPException(status_code=404, detail="task/log not found")

    p = Path(t.log_path)
    if not p.exists():
        return {"offset": offset, "text": ""}

    data = p.read_bytes()
    if offset >= len(data):
        return {"offset": offset, "text": ""}

    chunk = data[offset:]
    text = chunk.decode("utf-8", errors="replace")
    return {"offset": len(data), "text": text}
