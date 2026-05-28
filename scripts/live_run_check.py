"""Layer B live-run helper for InkOctoBot.

Three modes, in the order you'd run them on a fresh machine:

  --env       Environment self-check: imports, schema, embedding, LLM
              provider connection, market/reference DB paths.
  --seed      Seed the "轨道挽歌" 5-chapter project into the active DB
              so the runbook walkthrough has something concrete to do.
              Does NOT touch market/reference DBs — use your real data.
  --verify    Inspect the DB after a walkthrough and report which
              tables actually got rows. Calls out the ones that
              should have data but don't, with §四 diagnostics
              pointers from the spec.

Usage::

  python scripts/live_run_check.py --env
  python scripts/live_run_check.py --seed
  python scripts/live_run_check.py --verify --project rt_proj

A Markdown report lands in ``outputs/checks/live_run_<timestamp>.md``."""
from __future__ import annotations

import argparse
import importlib
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ─────────── tiny report builder ───────────


class Report:
    def __init__(self, title: str) -> None:
        self.title = title
        self.lines: list[str] = []
        self.passed = 0
        self.warned = 0
        self.failed = 0

    def section(self, name: str) -> None:
        self.lines.append(f"\n## {name}\n")

    def ok(self, msg: str) -> None:
        self.lines.append(f"- ✅ {msg}")
        self.passed += 1

    def warn(self, msg: str) -> None:
        self.lines.append(f"- ⚠️ {msg}")
        self.warned += 1

    def fail(self, msg: str) -> None:
        self.lines.append(f"- ❌ {msg}")
        self.failed += 1

    def note(self, msg: str) -> None:
        self.lines.append(f"  - {msg}")

    def write(self) -> Path:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = REPO_ROOT / "outputs" / "checks"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"live_run_{ts}.md"
        body = (
            f"# {self.title}\n\n"
            f"Generated: {datetime.now().isoformat()}\n\n"
            f"**{self.passed}** passed · **{self.warned}** warnings · "
            f"**{self.failed}** failed\n"
            + "\n".join(self.lines)
        )
        out_path.write_text(body, encoding="utf-8")
        return out_path


# ─────────── --env ───────────


_REQUIRED_IMPORTS = [
    "fastapi", "pydantic", "pydantic_settings",
    "ui.backend.app.main",
    "storage.project_schema",
    "agents.production.writer",
    "agents.production.scene_director",
    "agents.learning",  # part A/B
    "ui.backend.app.services.chapter_context",
]


_REQUIRED_TABLES = [
    "projects", "chapters", "text_versions", "characters",
    "worldbook_entries", "user_style_preferences",
    "edit_observations", "domain_suggestions",
    "skill_index", "pipeline_sessions",
    "chapter_summaries", "episodic_events",
    "truth_current_state",
]


_OPTIONAL_DEPS = [
    ("sentence_transformers", "real embeddings (zh/en)"),
    ("chromadb", "vector retrieval for chunks"),
]


def _get_db_path() -> str:
    try:
        from ui.backend.app.services.project_paths import get_db_path
        return get_db_path()
    except Exception:
        return ""


def run_env_check() -> Report:
    r = Report("InkOctoBot live-run · environment self-check")

    r.section("1. Python imports")
    for mod in _REQUIRED_IMPORTS:
        try:
            importlib.import_module(mod)
            r.ok(f"import {mod}")
        except Exception as e:
            r.fail(f"import {mod} — {type(e).__name__}: {e}")

    r.section("2. Optional dependencies")
    for pkg, note in _OPTIONAL_DEPS:
        try:
            importlib.import_module(pkg)
            r.ok(f"{pkg} present ({note})")
        except Exception:
            r.warn(f"{pkg} missing — {note} will degrade. "
                   f"`pip install {pkg.replace('_', '-')}`")

    r.section("3. DB schema")
    db = _get_db_path()
    if not db:
        r.fail("project_paths.get_db_path() unavailable")
    elif not Path(db).exists():
        r.warn(f"DB file not yet created at {db}; will be initialised "
               f"on first project save.")
    else:
        try:
            with sqlite3.connect(db) as con:
                names = {
                    r[0] for r in con.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table'"
                    ).fetchall()
                }
            for t in _REQUIRED_TABLES:
                if t in names:
                    r.ok(f"table `{t}` exists")
                else:
                    r.warn(f"table `{t}` missing — run "
                           f"`ensure_creation_tables(conn)` once")
        except Exception as e:
            r.fail(f"could not inspect DB: {e}")

    r.section("4. LLM provider")
    try:
        from llm.router import ModelRouter
        router = ModelRouter()
        prov, model = router.resolve_role("post_commit")
        if prov:
            r.ok(f"role `post_commit` resolves → {prov} / {model}")
        else:
            r.warn("no provider bound for `post_commit` role")
    except Exception as e:
        r.fail(f"could not resolve provider: {e}")

    r.section("5. Embedding settings")
    try:
        from ui.backend.app.settings import (
            get_embedding_language_mode, get_embedding_model_key,
        )
        r.ok(f"language mode: {get_embedding_language_mode()}")
        r.ok(f"model key:     {get_embedding_model_key()}")
    except Exception as e:
        r.fail(f"embedding settings unreadable: {e}")

    r.section("6. Market / reference DB paths")
    try:
        from ui.backend.app.settings import settings as _s
        market_db = getattr(_s, "market_db_path", None) or ""
        ref_db = getattr(_s, "reference_db_path", None) or ""
        for label, p in (("market DB", market_db),
                         ("reference DB", ref_db)):
            if not p:
                r.warn(f"{label}: path not configured (use setting page)")
            elif not Path(p).exists():
                r.warn(f"{label}: configured path {p} doesn't exist")
            else:
                r.ok(f"{label} → {p}")
    except Exception as e:
        r.warn(f"market/reference path inspection failed: {e}")

    r.section("7. Edit-learning threshold")
    try:
        from ui.backend.app.settings import get_edit_learning_threshold
        r.ok(f"threshold = {get_edit_learning_threshold()} chapters")
    except Exception as e:
        r.fail(f"could not read threshold: {e}")

    return r


# ─────────── --seed ───────────


def run_seed() -> Report:
    r = Report("InkOctoBot live-run · seed 轨道挽歌 project")
    r.section("Schema bootstrap")
    db = _get_db_path()
    if not db:
        r.fail("get_db_path() returned empty; aborting.")
        return r
    Path(db).parent.mkdir(parents=True, exist_ok=True)
    try:
        with sqlite3.connect(db) as con:
            from storage.project_schema import ensure_creation_tables
            ensure_creation_tables(con)
        r.ok(f"schema ensured on {db}")
    except Exception as e:
        r.fail(f"schema bootstrap failed: {e}")
        return r

    r.section("Seed insert")
    try:
        from tests.fixtures.realdata.seed_project import seed_project
        info = seed_project(db)
        r.ok(f"project {info['project_id']} (轨道挽歌) inserted")
        r.note(f"chapters: {', '.join(info['chapter_ids'])}")
        r.note(f"characters: {', '.join(info['character_names'])}")
    except Exception as e:
        r.fail(f"seed failed: {e}")
        return r

    r.section("Next steps")
    r.note("Open the editor, find project `轨道挽歌`.")
    r.note("Follow docs/LIVE_RUN_RUNBOOK.md sections A → I.")
    r.note("When done, run: `python scripts/live_run_check.py "
           "--verify --project rt_proj`")
    return r


# ─────────── --verify ───────────


_VERIFY_TABLES: list[tuple[str, str, str]] = [
    # (table, project-filter SQL, diagnostic hint when empty)
    ("text_versions",
     "JOIN chapters c ON c.chapter_id = text_versions.chapter_id "
     "WHERE c.project_id = ?",
     "no chapter ever finalised; check pipeline `_run_pipeline_inner`"),
    ("chapter_summaries",
     "JOIN chapters c ON c.chapter_id = chapter_summaries.chapter_id "
     "WHERE c.project_id = ?",
     "summarizer sub-task didn't run; check commit_pipeline.summarizer"),
    ("episodic_events",
     "WHERE project_id = ?",
     "event_extractor didn't run or returned empty events"),
    ("truth_current_state",
     "WHERE project_id = ?",
     "state extractor not run / settlement failed; check storyland_state"),
    ("emotion_arcs",
     "WHERE project_id = ?",
     "emotion arc never written — possibly skipped on legacy projects"),
    ("pending_hooks",
     "WHERE project_id = ?",
     "no hooks introduced; if outline had foreshadowing, check extractor"),
    ("edit_observations",
     "WHERE project_id = ?",
     "no user edits captured — confirm you saved via the editor "
     "(source='user_edit'), not just AI finalize"),
    ("user_style_preferences",
     "WHERE project_id = ?",
     "batch extraction hasn't fired — check threshold "
     "(`edit_learning_threshold`) vs your edit count"),
    ("domain_suggestions",
     "WHERE project_id = ?",
     "no domain gap detected by batch extractor; the prompt may need "
     "a stronger special_requirement signal"),
    ("pipeline_sessions",
     "WHERE project_id = ?",
     "pipeline session persistence didn't fire — check mirror_session_to_db"),
    ("commit_tasks",
     "JOIN chapters c ON c.chapter_id = commit_tasks.chapter_id "
     "WHERE c.project_id = ?",
     "post-commit pipeline didn't fire — check fire_and_forget_from_handler"),
    ("skill_index", "WHERE 1=1",
     "no skills indexed yet — should populate on first build_generation_context"),
]


def run_verify(project_id: str) -> Report:
    r = Report(f"InkOctoBot live-run · verify report for project {project_id}")
    db = _get_db_path()
    if not db or not Path(db).exists():
        r.fail("DB not found")
        return r

    r.section("Per-table row counts")
    with sqlite3.connect(db) as con:
        for table, where, hint in _VERIFY_TABLES:
            try:
                if "?" in where:
                    sql = f"SELECT COUNT(*) FROM {table} {where}"
                    n = con.execute(sql, (project_id,)).fetchone()[0]
                else:
                    sql = f"SELECT COUNT(*) FROM {table} {where}"
                    n = con.execute(sql).fetchone()[0]
            except sqlite3.OperationalError as e:
                r.warn(f"`{table}` missing or column mismatch: {e}")
                continue
            if n > 0:
                r.ok(f"`{table}` → {n} rows")
            else:
                r.warn(f"`{table}` empty — {hint}")

    r.section("Key content samples")
    try:
        with sqlite3.connect(db) as con:
            con.row_factory = sqlite3.Row
            v = con.execute(
                "SELECT version_num, source, length(content) AS clen "
                "FROM text_versions tv JOIN chapters c "
                "ON c.chapter_id = tv.chapter_id "
                "WHERE c.project_id = ? "
                "ORDER BY tv.created_at DESC LIMIT 5",
                (project_id,),
            ).fetchall()
            for row in v:
                r.ok(f"text_version v{row['version_num']} "
                     f"source={row['source']} {row['clen']} chars")
            prefs = con.execute(
                "SELECT description, confidence, observation_count "
                "FROM user_style_preferences "
                "WHERE project_id = ? ORDER BY confidence DESC LIMIT 6",
                (project_id,),
            ).fetchall()
            for p in prefs:
                r.ok(f"pref: \"{p['description']}\" "
                     f"(conf={p['confidence']:.2f}, "
                     f"obs={p['observation_count']})")
            sugs = con.execute(
                "SELECT domain, status FROM domain_suggestions "
                "WHERE project_id = ?",
                (project_id,),
            ).fetchall()
            for s in sugs:
                r.ok(f"domain suggestion: {s['domain']} [{s['status']}]")
    except Exception as e:
        r.warn(f"sample inspection failed: {e}")

    return r


# ─────────── CLI ───────────


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Live-run helper (env / seed / verify).",
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--env",    action="store_true",
                   help="run environment self-check only")
    g.add_argument("--seed",   action="store_true",
                   help="bootstrap schema + seed 轨道挽歌 project")
    g.add_argument("--verify", action="store_true",
                   help="post-walkthrough DB inspection (requires --project)")
    ap.add_argument("--project", default="rt_proj",
                    help="project_id to verify (default: rt_proj)")
    args = ap.parse_args()

    if args.env:
        report = run_env_check()
    elif args.seed:
        report = run_seed()
    else:
        report = run_verify(args.project)

    out = report.write()
    print(f"\nReport: {out}")
    print(f"{report.passed} passed · {report.warned} warnings · "
          f"{report.failed} failed")
    return 0 if report.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
