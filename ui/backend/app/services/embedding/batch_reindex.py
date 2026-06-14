"""Batch reindex tool (EMBEDDING_SPEC § 6).

When the user switches the active embedding model, every previously
computed vector in the five SQLite tables stops being comparable to
freshly computed ones (different model = different vector space).
The reindex tool walks each table, re-embeds stale rows via
``EmbeddingService``, and persists the new vector + model_key.

Design choices:

- Runs in a daemon thread (DB + GPU/CPU work, not asyncio-friendly).
  Each task gets a UUID; status is queryable + cancellable.
- Per-row failures are isolated (caught + logged) so a single bad row
  doesn't kill the batch.
- Resumable: a re-run only touches rows whose embedding_model_key
  doesn't match the active model. The cancel flag stops the worker at
  the next batch boundary, leaving partial progress in place.
- ChromaDB: best-effort. The current codebase doesn't populate L3
  collections in tests, so we drop+recreate matching collections
  (cheap) and skip if chromadb is unavailable.

Public surface:
  start_reindex(target_model_key=None) -> task_id
  get_status(task_id) -> dict
  cancel(task_id) -> bool
  list_tasks() -> list[dict]
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Literal

from .registry import get_model
from .service import get_embedding_service

logger = logging.getLogger("inkoctobot.services.embedding.batch_reindex")


# Default batch size for embedding requests. Tunable later if a model
# benefits from a different size; 32 is the sentence-transformers
# recommended sweet spot for most BGE-class models.
_BATCH_SIZE = 32


TaskState = Literal["pending", "running", "completed", "failed", "cancelled"]


@dataclass
class TableProgress:
    table: str
    current: int = 0
    total: int = 0
    errors: int = 0


@dataclass
class ReindexError:
    table: str
    row_id: str
    message: str


@dataclass
class ReindexTask:
    task_id: str
    target_model_key: str
    state: TaskState = "pending"
    cancel_flag: bool = False
    tables: dict[str, TableProgress] = field(default_factory=dict)
    errors: list[ReindexError] = field(default_factory=list)
    started_at: float | None = None
    updated_at: float | None = None
    completed_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        overall_current = sum(t.current for t in self.tables.values())
        overall_total = sum(t.total for t in self.tables.values())
        eta = self._eta_seconds(overall_current, overall_total)
        return {
            "task_id":          self.task_id,
            "state":            self.state,
            "target_model_key": self.target_model_key,
            "progress": {
                "table_progress": {
                    t.table: {
                        "current": t.current, "total": t.total,
                        "errors": t.errors,
                    }
                    for t in self.tables.values()
                },
                "overall_current": overall_current,
                "overall_total":   overall_total,
            },
            "eta_seconds":      eta,
            "started_at":       self.started_at,
            "updated_at":       self.updated_at,
            "completed_at":     self.completed_at,
            "errors": [
                {"table": e.table, "row_id": e.row_id, "message": e.message}
                for e in self.errors[:50]
            ],
            "error_count":      len(self.errors),
        }

    def _eta_seconds(self, done: int, total: int) -> int:
        if not self.started_at or done <= 0 or total <= 0 or done >= total:
            return 0
        elapsed = (self.updated_at or time.time()) - self.started_at
        rate = done / elapsed if elapsed > 0 else 0
        remaining = total - done
        return int(remaining / rate) if rate > 0 else 0


# ─────────── task registry ───────────


_tasks: dict[str, ReindexTask] = {}
_tasks_lock = threading.Lock()


def _register(task: ReindexTask) -> None:
    with _tasks_lock:
        _tasks[task.task_id] = task


def get_status(task_id: str) -> dict[str, Any] | None:
    with _tasks_lock:
        t = _tasks.get(task_id)
        if t is None:
            return None
        return t.to_dict()


def list_tasks() -> list[dict[str, Any]]:
    with _tasks_lock:
        return [t.to_dict() for t in _tasks.values()]


def cancel(task_id: str) -> bool:
    with _tasks_lock:
        t = _tasks.get(task_id)
        if t is None or t.state not in ("pending", "running"):
            return False
        t.cancel_flag = True
        return True


def reset_for_tests() -> None:
    with _tasks_lock:
        _tasks.clear()


# ─────────── per-table strategies ───────────


@dataclass
class TableStrategy:
    """A single reindex target — how to count, list, and persist."""

    name: str
    count_stale: Callable[[str, str], int]
    iter_stale: Callable[[str, str, int], Iterable[tuple[str, str]]]
    persist: Callable[[str, str, list[float], str, str], None]


def _strategies(target_model_key: str) -> list[TableStrategy]:
    """All 5 SQLite tables. Each strategy yields ``(row_id, canonical_text)``
    pairs for any row whose embedding_model_key != target."""
    return [
        TableStrategy(
            name="worldbook_entries",
            count_stale=_count_worldbook,
            iter_stale=_iter_worldbook,
            persist=_persist_worldbook,
        ),
        TableStrategy(
            name="inspirations",
            count_stale=_count_inspirations,
            iter_stale=_iter_inspirations,
            persist=_persist_inspiration,
        ),
        TableStrategy(
            name="subplot_threads",
            count_stale=_count_subplots,
            iter_stale=_iter_subplots,
            persist=_persist_subplot,
        ),
        TableStrategy(
            name="skill_index",
            count_stale=_count_skills,
            iter_stale=_iter_skills,
            persist=_persist_skill,
        ),
        TableStrategy(
            name="reference_chapters",
            count_stale=_count_reference_chapters,
            iter_stale=_iter_reference_chapters,
            persist=_persist_reference_chapter,
        ),
    ]


# ── worldbook_entries ──
def _count_worldbook(db_path: str, target_model_key: str) -> int:
    import sqlite3
    with sqlite3.connect(db_path) as c:
        try:
            row = c.execute(
                "SELECT COUNT(*) FROM worldbook_entries "
                "WHERE COALESCE(embedding_model_key, '') != ?",
                (target_model_key,),
            ).fetchone()
        except sqlite3.OperationalError:
            return 0
    return int(row[0] if row else 0)


def _iter_worldbook(db_path: str, target_model_key: str, batch: int):
    import sqlite3
    from ui.backend.app.services import project_store
    last_id = ""
    while True:
        with sqlite3.connect(db_path) as c:
            c.row_factory = sqlite3.Row
            try:
                rows = c.execute(
                    "SELECT entry_id, title, category, content "
                    "FROM worldbook_entries "
                    "WHERE COALESCE(embedding_model_key, '') != ? "
                    "AND entry_id > ? "
                    "ORDER BY entry_id LIMIT ?",
                    (target_model_key, last_id, batch),
                ).fetchall()
            except sqlite3.OperationalError:
                return
        if not rows:
            return
        for r in rows:
            text = project_store.worldbook_embedding_text(dict(r))
            yield r["entry_id"], text
            last_id = r["entry_id"]


def _persist_worldbook(db_path, row_id, vec, text_hash, model_key) -> None:
    from ui.backend.app.services import project_store
    project_store.set_worldbook_embedding(
        db_path, row_id, vec, text_hash, model_key=model_key,
    )


# ── inspirations (idea.db) ──
def _idea_db_path(db_path: str) -> str:
    """Resolve idea.db relative to the project data dir."""
    from pathlib import Path
    return str(Path(db_path).parent / "idea.db")


def _count_inspirations(db_path: str, target_model_key: str) -> int:
    import sqlite3
    from pathlib import Path
    p = _idea_db_path(db_path)
    if not Path(p).exists():
        return 0
    with sqlite3.connect(p) as c:
        try:
            row = c.execute(
                "SELECT COUNT(*) FROM inspirations "
                "WHERE COALESCE(embedding_model_key, '') != ?",
                (target_model_key,),
            ).fetchone()
        except sqlite3.OperationalError:
            return 0
    return int(row[0] if row else 0)


def _iter_inspirations(db_path: str, target_model_key: str, batch: int):
    import sqlite3
    from pathlib import Path
    from knowledge.idea_db import inspiration_embedding_text
    p = _idea_db_path(db_path)
    if not Path(p).exists():
        return
    last_id = ""
    while True:
        with sqlite3.connect(p) as c:
            c.row_factory = sqlite3.Row
            try:
                rows = c.execute(
                    "SELECT id, category, title, content "
                    "FROM inspirations "
                    "WHERE COALESCE(embedding_model_key, '') != ? "
                    "AND id > ? "
                    "ORDER BY id LIMIT ?",
                    (target_model_key, last_id, batch),
                ).fetchall()
            except sqlite3.OperationalError:
                return
        if not rows:
            return
        for r in rows:
            text = inspiration_embedding_text(dict(r))
            yield r["id"], text
            last_id = r["id"]


def _persist_inspiration(db_path, row_id, vec, text_hash, model_key) -> None:
    from knowledge.idea_db import IdeaDB
    IdeaDB(_idea_db_path(db_path)).set_embedding(
        row_id, vec, text_hash, model_key=model_key,
    )


# ── subplot_threads ──
def _count_subplots(db_path: str, target_model_key: str) -> int:
    import sqlite3
    with sqlite3.connect(db_path) as c:
        try:
            row = c.execute(
                "SELECT COUNT(*) FROM subplot_threads "
                "WHERE thread_type = 'sub' "
                "AND COALESCE(embedding_model_key, '') != ?",
                (target_model_key,),
            ).fetchone()
        except sqlite3.OperationalError:
            return 0
    return int(row[0] if row else 0)


def _iter_subplots(db_path: str, target_model_key: str, batch: int):
    import sqlite3
    from ui.backend.app.services.prompt_context.loaders.subplots import (
        _subplot_embedding_text,
    )
    last_id = ""
    while True:
        with sqlite3.connect(db_path) as c:
            c.row_factory = sqlite3.Row
            try:
                rows = c.execute(
                    "SELECT thread_id, name, description "
                    "FROM subplot_threads "
                    "WHERE thread_type = 'sub' "
                    "AND COALESCE(embedding_model_key, '') != ? "
                    "AND thread_id > ? "
                    "ORDER BY thread_id LIMIT ?",
                    (target_model_key, last_id, batch),
                ).fetchall()
            except sqlite3.OperationalError:
                return
        if not rows:
            return
        for r in rows:
            text = _subplot_embedding_text(dict(r))
            yield r["thread_id"], text
            last_id = r["thread_id"]


def _persist_subplot(db_path, row_id, vec, text_hash, model_key) -> None:
    from ui.backend.app.services.prompt_context.loaders.subplots import (
        _persist_embedding,
    )
    _persist_embedding(db_path, row_id, vec, text_hash, model_key=model_key)


# ── skill_index ──
def _count_skills(db_path: str, target_model_key: str) -> int:
    import sqlite3
    with sqlite3.connect(db_path) as c:
        try:
            row = c.execute(
                "SELECT COUNT(*) FROM skill_index "
                "WHERE COALESCE(embedding_model_key, '') != ?",
                (target_model_key,),
            ).fetchone()
        except sqlite3.OperationalError:
            return 0
    return int(row[0] if row else 0)


def _iter_skills(db_path: str, target_model_key: str, batch: int):
    import sqlite3
    from ui.backend.app.services.skill_index import skill_embedding_text
    last_id = ""
    while True:
        with sqlite3.connect(db_path) as c:
            c.row_factory = sqlite3.Row
            try:
                rows = c.execute(
                    "SELECT skill_id, display_name, description, body_snippet "
                    "FROM skill_index "
                    "WHERE COALESCE(embedding_model_key, '') != ? "
                    "AND skill_id > ? "
                    "ORDER BY skill_id LIMIT ?",
                    (target_model_key, last_id, batch),
                ).fetchall()
            except sqlite3.OperationalError:
                return
        if not rows:
            return
        for r in rows:
            text = skill_embedding_text(dict(r))
            yield r["skill_id"], text
            last_id = r["skill_id"]


def _persist_skill(db_path, row_id, vec, text_hash, model_key) -> None:
    from ui.backend.app.services import skill_index
    skill_index.set_embedding(db_path, row_id, vec, text_hash, model_key=model_key)


# ── reference_chapters ──
def _count_reference_chapters(db_path: str, target_model_key: str) -> int:
    import sqlite3
    with sqlite3.connect(db_path) as c:
        try:
            row = c.execute(
                "SELECT COUNT(*) FROM reference_chapters "
                "WHERE COALESCE(is_author_note, 0) = 0 "
                "AND COALESCE(embedding_model_key, '') != ?",
                (target_model_key,),
            ).fetchone()
        except sqlite3.OperationalError:
            return 0
    return int(row[0] if row else 0)


def _iter_reference_chapters(db_path: str, target_model_key: str, batch: int):
    import sqlite3
    last_key: tuple[str, int] = ("", -1)
    while True:
        with sqlite3.connect(db_path) as c:
            c.row_factory = sqlite3.Row
            try:
                rows = c.execute(
                    "SELECT ref_id, number, title, content "
                    "FROM reference_chapters "
                    "WHERE COALESCE(is_author_note, 0) = 0 "
                    "AND COALESCE(embedding_model_key, '') != ? "
                    "AND (ref_id > ? OR (ref_id = ? AND number > ?)) "
                    "ORDER BY ref_id, number LIMIT ?",
                    (target_model_key, last_key[0], last_key[0], last_key[1], batch),
                ).fetchall()
            except sqlite3.OperationalError:
                return
        if not rows:
            return
        for r in rows:
            text = (r["title"] or "") + "\n" + (r["content"] or "")
            yield f"{r['ref_id']}:{r['number']}", text.strip()
            last_key = (r["ref_id"], r["number"])


def _persist_reference_chapter(
    db_path, row_id, vec, text_hash, model_key,
) -> None:
    import json
    import sqlite3
    ref_id, num_str = row_id.split(":", 1)
    with sqlite3.connect(db_path) as c:
        c.execute(
            "UPDATE reference_chapters SET embedding_json = ?, "
            "embedding_text_hash = ?, embedding_model_key = ? "
            "WHERE ref_id = ? AND number = ?",
            (json.dumps(list(vec), ensure_ascii=False),
             text_hash, model_key, ref_id, int(num_str)),
        )
        c.commit()


# ─────────── ChromaDB best-effort recreate ───────────


def _refresh_chromadb_collections(target_model_key: str) -> int:
    """Drop ChromaDB collections whose vectors don't match the active
    embedding model. New collections (with the model_key suffix) are
    created lazily by whatever loader writes to them next.

    Returns the number of collections dropped. Silently no-ops when
    chromadb isn't installed or the persistent dir doesn't exist.
    """
    try:
        from knowledge.vector_store import _DEFAULT_DIR
        from pathlib import Path
        if not Path(_DEFAULT_DIR).exists():
            return 0
        import chromadb
        from chromadb.config import Settings
        client = chromadb.PersistentClient(
            path=str(_DEFAULT_DIR),
            settings=Settings(anonymized_telemetry=False),
        )
    except Exception as e:
        logger.debug("chromadb unavailable, skipping collection refresh: %s", e)
        return 0

    dropped = 0
    try:
        for col in client.list_collections():
            name = col.name
            # Convention: a collection name ending in "_<model_key>" is
            # tagged with its producing model. Drop ones whose suffix
            # doesn't match the new active model.
            if "_" in name and not name.endswith(f"_{target_model_key}"):
                try:
                    client.delete_collection(name)
                    dropped += 1
                except Exception as e:
                    logger.debug("delete collection %s failed: %s", name, e)
            elif "_" not in name:
                # Untagged legacy collection — drop so the next read
                # creates a model-key-tagged one.
                try:
                    client.delete_collection(name)
                    dropped += 1
                except Exception as e:
                    logger.debug("delete legacy collection %s failed: %s", name, e)
    except Exception as e:
        logger.debug("chromadb collection enumeration failed: %s", e)
    return dropped


# ─────────── worker ───────────


def _resolve_db_path() -> str:
    """Pick the project DB path the same way the loaders do."""
    from ui.backend.app.services.project_paths import get_db_path
    return get_db_path()


def _now() -> float:
    return time.time()


def _run_task(task: ReindexTask, db_path: str | None = None) -> None:
    """Worker body. Updates ``task`` in-place. Catches exceptions and
    flips state to 'failed' rather than re-raising."""
    task.state = "running"
    task.started_at = _now()
    task.updated_at = task.started_at
    try:
        svc = get_embedding_service()
        target_model_key = task.target_model_key
        # If we're reindexing for a model that isn't currently active,
        # switch first so the cache + persist all use the new vectors.
        if svc.get_current_model().model_key != target_model_key:
            svc.switch_model(target_model_key)

        path = db_path or _resolve_db_path()
        strategies = _strategies(target_model_key)

        # Phase 1: pre-count so the progress reporter has a denominator.
        for strat in strategies:
            total = strat.count_stale(path, target_model_key)
            task.tables[strat.name] = TableProgress(table=strat.name, total=total)
        task.updated_at = _now()

        # Phase 2: batch-embed each table in turn.
        for strat in strategies:
            tp = task.tables[strat.name]
            if tp.total == 0:
                continue
            buffer: list[tuple[str, str]] = []
            for row_id, text in strat.iter_stale(path, target_model_key, _BATCH_SIZE):
                if task.cancel_flag:
                    task.state = "cancelled"
                    task.completed_at = _now()
                    return
                buffer.append((row_id, text))
                if len(buffer) >= _BATCH_SIZE:
                    _flush(task, strat, buffer, target_model_key, svc, path)
                    buffer.clear()
            if buffer and not task.cancel_flag:
                _flush(task, strat, buffer, target_model_key, svc, path)

        # Phase 3: ChromaDB best-effort.
        try:
            _refresh_chromadb_collections(target_model_key)
        except Exception as e:
            logger.debug("chromadb refresh skipped: %s", e)

        task.state = "completed"
        task.completed_at = _now()
        task.updated_at = task.completed_at
        # 性能预期·机制3: record the finished run so future ETAs use
        # real same-hardware throughput. Never breaks the task.
        try:
            from ui.backend.app.services.duration_estimator import (
                record_duration,
            )
            items = sum(tp.current for tp in task.tables.values())
            record_duration(
                path, "embedding_batch_reindex", items,
                task.completed_at - (task.started_at or task.completed_at),
            )
        except Exception as e:
            logger.debug("duration record skipped: %s", e)
    except Exception as e:
        logger.exception("reindex task %s failed", task.task_id)
        task.state = "failed"
        task.errors.append(ReindexError(
            table="<task>", row_id="", message=str(e),
        ))
        task.completed_at = _now()
        task.updated_at = task.completed_at


def _flush(
    task: ReindexTask, strat: TableStrategy,
    buffer: list[tuple[str, str]], target_model_key: str,
    svc, db_path: str,
) -> None:
    tp = task.tables[strat.name]
    texts = [t for _, t in buffer]
    try:
        vecs = svc.embed_batch(texts)
    except Exception as e:
        # Whole-batch failure — mark every row as errored and skip.
        logger.warning("embed_batch failed for %s: %s", strat.name, e)
        for row_id, _ in buffer:
            task.errors.append(ReindexError(
                table=strat.name, row_id=row_id, message=str(e),
            ))
            tp.errors += 1
            tp.current += 1
        task.updated_at = _now()
        return

    import numpy as np
    for (row_id, text), vec_bytes in zip(buffer, vecs):
        try:
            if not vec_bytes:
                raise RuntimeError("empty embedding")
            vec = np.frombuffer(vec_bytes, dtype="float32").tolist()
            text_hash = svc.compute_text_hash(text)
            strat.persist(db_path, row_id, vec, text_hash, target_model_key)
        except Exception as e:
            task.errors.append(ReindexError(
                table=strat.name, row_id=row_id, message=str(e),
            ))
            tp.errors += 1
            logger.debug("persist failed for %s/%s: %s", strat.name, row_id, e)
        finally:
            tp.current += 1
    task.updated_at = _now()


def start_reindex(
    target_model_key: str | None = None,
    *, db_path: str | None = None, sync: bool = False,
) -> str:
    """Spawn a background reindex task. Returns the task_id."""
    svc = get_embedding_service()
    target = target_model_key or svc.get_current_model().model_key
    # Validate target model exists.
    get_model(target)

    task = ReindexTask(
        task_id=f"rx_{uuid.uuid4().hex[:12]}",
        target_model_key=target,
    )
    _register(task)

    if sync:
        # Test entry point — run inline.
        _run_task(task, db_path=db_path)
        return task.task_id

    thread = threading.Thread(
        target=_run_task, args=(task,), kwargs={"db_path": db_path},
        name=f"reindex-{task.task_id}", daemon=True,
    )
    thread.start()
    return task.task_id
