from __future__ import annotations
import json
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Dict, List

# The on-disk task log is bounded — only the most recent N tasks are kept;
# older finished tasks are dropped on the next write.
_MAX_TASKS = 300


@dataclass
class Task:
    task_id: str
    task_type: str              # "crawler_import" | "analysis" | ...
    status: str                 # "queued"|"running"|"succeeded"|"failed"
    created_at: float
    started_at: Optional[float] = None
    ended_at: Optional[float] = None
    config_run_id: Optional[str] = None
    command: Optional[list[str]] = None
    log_path: Optional[str] = None
    exit_code: Optional[int] = None


class TaskStore:
    """In-memory task index backed by a size-bounded JSONL file.

    Tasks are loaded once at construction and held in memory, so reads
    never touch disk. A write rewrites only the capped task set (atomically
    via a temp file) instead of re-parsing + re-serialising on every call.
    """

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_file = self.base_dir / "tasks.jsonl"
        self._lock = threading.Lock()
        self._tasks: Dict[str, Task] = {}
        self._load()

    def _load(self) -> None:
        if not self.tasks_file.exists():
            return
        with self.tasks_file.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    self._tasks[obj["task_id"]] = Task(**obj)
                except Exception:
                    # Skip malformed/legacy lines rather than crashing.
                    continue

    def _recent(self) -> List[Task]:
        return sorted(self._tasks.values(), key=lambda t: t.created_at, reverse=True)

    def list(self) -> List[Task]:
        with self._lock:
            return self._recent()

    def get(self, task_id: str) -> Optional[Task]:
        with self._lock:
            return self._tasks.get(task_id)

    def upsert(self, task: Task) -> None:
        with self._lock:
            self._tasks[task.task_id] = task
            recent = self._recent()[:_MAX_TASKS]
            self._tasks = {t.task_id: t for t in recent}
            tmp = self.tasks_file.with_suffix(".jsonl.tmp")
            with tmp.open("w", encoding="utf-8") as f:
                for t in recent:
                    f.write(json.dumps(asdict(t), ensure_ascii=False) + "\n")
            tmp.replace(self.tasks_file)


def new_task_id() -> str:
    return f"t{int(time.time()*1000)}"
