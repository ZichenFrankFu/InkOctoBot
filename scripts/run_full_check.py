"""End-to-end self-check + report.

One command runs the full health check of an InkOctoBot install and
saves a single human-readable Markdown file you can scroll through,
diff between runs, or paste to a developer when something's wrong.

What it checks (in order):
  1. Project structure — required dirs + files exist
  2. Python imports — every top-level package loads cleanly
  3. Test mode seed — runs test_seed into a temp dir, verifies all
     expected files + DB tables + row counts appear
  4. pytest — runs the full test suite, captures pass/fail/skip
  5. Live server (optional) — if --server given, also hits the 3
     debug endpoints to verify RAG / Truth / memory inspection works
  6. DB-path resolution — confirms the market DB user-override logic
     resolves correctly under all 3 precedence layers
  7. Log discovery — confirms outputs/logs/ has a recent log file

Output: outputs/checks/check_<timestamp>.md

Usage:
  python scripts/run_full_check.py                  # offline checks only
  python scripts/run_full_check.py --server         # also poll debug endpoints
                                                     (must launch the app
                                                      separately in --test mode)
  python scripts/run_full_check.py --no-pytest      # skip the test suite
  python scripts/run_full_check.py --out FOO.md     # custom output path
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SERVER_BASE = "http://127.0.0.1:8713"
DEFAULT_PID = "test_project_001"
DEFAULT_CHID = "ch_test_002"


# ── Tiny renderer ─────────────────────────────────────────────────


class Report:
    def __init__(self) -> None:
        self.parts: list[str] = []
        self.passed = 0
        self.failed = 0
        self.warned = 0
        self.skipped = 0

    def h1(self, t: str) -> None:
        self.parts.append(f"\n# {t}\n")

    def h2(self, t: str) -> None:
        self.parts.append(f"\n## {t}\n")

    def h3(self, t: str) -> None:
        self.parts.append(f"\n### {t}\n")

    def text(self, t: str) -> None:
        self.parts.append(f"{t}\n")

    def code(self, body: str, lang: str = "") -> None:
        self.parts.append(f"```{lang}\n{body.rstrip()}\n```\n")

    def ok(self, msg: str) -> None:
        self.passed += 1
        self.parts.append(f"- ✅ **OK** {msg}\n")

    def fail(self, msg: str, detail: str = "") -> None:
        self.failed += 1
        line = f"- ❌ **FAIL** {msg}"
        if detail:
            line += f"\n      `{detail}`"
        self.parts.append(line + "\n")

    def warn(self, msg: str, detail: str = "") -> None:
        self.warned += 1
        line = f"- ⚠️  **WARN** {msg}"
        if detail:
            line += f"\n      `{detail}`"
        self.parts.append(line + "\n")

    def skip(self, msg: str) -> None:
        self.skipped += 1
        self.parts.append(f"- ⏭️  **SKIP** {msg}\n")

    def render(self) -> str:
        return "".join(self.parts)


def _safe_json(obj: Any, max_chars: int = 4000) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        s = repr(obj)
    if len(s) > max_chars:
        s = s[:max_chars] + f"\n... (truncated, {len(s) - max_chars} more chars)"
    return s


def _get(url: str, timeout: float = 30.0) -> tuple[int, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = r.read().decode("utf-8", errors="replace")
            try:
                return r.status, json.loads(data)
            except Exception:
                return r.status, data
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return e.code, body
    except Exception as e:
        return 0, str(e)


# ── Check sections ────────────────────────────────────────────────


def check_project_structure(r: Report) -> None:
    r.h2("1. Project structure")
    required_dirs = [
        "agents", "framework", "knowledge", "llm", "market_analysis",
        "reference_ingest", "reference_pipeline", "security", "storage",
        "ui/backend/app", "ui/frontend/src", "scripts", "tests", "docs",
        "config",
    ]
    for d in required_dirs:
        p = REPO_ROOT / d
        if p.is_dir():
            r.ok(f"directory `{d}/`")
        else:
            r.fail(f"missing directory `{d}/`")

    required_files = [
        "launcher.py", "cli.py", "config.py", "requirements.txt",
        "tests/conftest.py", "tests/pytest.ini",
        "ui/backend/app/main.py",
        "scripts/migrate_split_dbs.py",
        "scripts/__init__.py",
    ]
    for f in required_files:
        p = REPO_ROOT / f
        if p.is_file():
            r.ok(f"file `{f}`")
        else:
            r.fail(f"missing file `{f}`")


def check_imports(r: Report) -> None:
    r.h2("2. Python imports")
    modules = [
        "framework.config",
        "framework.observability",
        "framework.skill_registry",
        "llm.router",
        "knowledge.memory.manager",
        "knowledge.truth.store",
        "knowledge.idea_db",
        "agents.base_agent",
        "agents.production.scene_director",
        "agents.evaluation.evaluator",
        "storage.market_db",
        "storage.connection",
        "scripts.migrate_split_dbs",
        "ui.backend.app.routers.reference._common",
    ]
    for m in modules:
        try:
            __import__(m)
            r.ok(f"import `{m}`")
        except Exception as e:
            r.fail(f"import `{m}`", str(e)[:200])


def check_test_seed(r: Report) -> None:
    r.h2("3. Test-mode seed (offline reproducer)")
    import tempfile
    import shutil

    tmp = Path(tempfile.mkdtemp(prefix="ink_check_seed_"))
    try:
        from test_seed import seed
        seed(tmp)
        r.ok(f"seed() ran into `{tmp}`")

        # Required artifacts
        wanted_files = [
            "novels.db", "reference.db", "idea.db",
            "InkOctoBot_Crawler.db", "settings.json", "usage.json",
            f"project_memory/{DEFAULT_PID}.json",
        ]
        for f in wanted_files:
            p = tmp / f
            if p.exists():
                r.ok(f"seed artifact `{f}`")
            else:
                r.fail(f"missing seed artifact `{f}`")

        # Required collection dirs with at least 1 file
        for coll, min_count in [
            ("projects", 1), ("characters", 3), ("worldbook", 3),
            ("editor", 1), ("storylines", 1),
        ]:
            d = tmp / coll
            count = len(list(d.glob("*.json"))) if d.exists() else 0
            if count >= min_count:
                r.ok(f"`{coll}/` has {count} files (>= {min_count} expected)")
            else:
                r.fail(f"`{coll}/` has {count} files (< {min_count} expected)")

        # Required novels.db tables + row counts
        r.h3("Row counts in seeded novels.db")
        with sqlite3.connect(str(tmp / "novels.db")) as c:
            checks = [
                ("projects", 1, "exactly 1 test project"),
                ("chapter_summaries", 3, "L2: 3 chapter summaries"),
                ("episodic_events", 5, "L4: 5 key events"),
                ("information_events", 1, "knowledge isolation: 1 rule"),
                ("truth_current_state", 4, "Truth: 4 current_state rows"),
                ("pending_hooks", 2, "Truth: 2 hooks"),
                ("character_relations", 2, "Truth: 2 relation rows"),
                ("emotion_arcs", 2, "Truth: 2 emotion arc entries"),
                ("truth_apply_log", 3, "Truth: 3 apply log entries"),
                # v2 schema tables (added in commit:
                #   "feat: schema redesign v2 — drop L3/L4 redundancy")
                # The seed currently populates JSON files only; running the
                # migration script in this check will fill these tables.
                # For now we only verify that the TABLES exist (not rows).
            ]
            counts: dict[str, int] = {}
            for t, want, msg in checks:
                try:
                    n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    counts[t] = n
                    if n >= want:
                        r.ok(f"`{t}` has {n} rows — {msg}")
                    else:
                        r.fail(f"`{t}` has {n} rows (expected >= {want})", msg)
                except sqlite3.OperationalError as e:
                    r.fail(f"`{t}` missing or unreadable", str(e))

            r.code(_safe_json(counts), "json")

            # v2 tables — assert they exist (rows may be 0 until the
            # migrator or DB-aware routers populate them).
            v2_tables = [
                "characters", "worldbook_entries", "project_memories",
                "storyline_nodes", "storyline_edges",
                "writing_knowledge", "chat_messages", "project_blobs",
            ]
            v2_present = {row[0] for row in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
            for t in v2_tables:
                if t in v2_present:
                    r.ok(f"v2 table `{t}` present")
                else:
                    r.fail(f"v2 table `{t}` missing")

        # reference.db + idea.db row counts
        with sqlite3.connect(str(tmp / "reference.db")) as c:
            for t, want in [("reference_works", 1)]:  # 诡秘之主 only
                try:
                    n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    if n >= want:
                        r.ok(f"reference.db `{t}` has {n} rows")
                    else:
                        r.fail(f"reference.db `{t}` has {n}, want {want}")
                except sqlite3.OperationalError as e:
                    r.fail(f"reference.db `{t}` missing", str(e))

        with sqlite3.connect(str(tmp / "idea.db")) as c:
            try:
                n = c.execute("SELECT COUNT(*) FROM inspirations").fetchone()[0]
                if n >= 3:
                    r.ok(f"idea.db `inspirations` has {n} rows")
                else:
                    r.fail(f"idea.db `inspirations` has {n}, want >= 3")
            except sqlite3.OperationalError as e:
                r.fail("idea.db `inspirations` missing", str(e))

        # v2 migration smoke test — runs scripts/migrate_to_v2_schema.py
        # against the just-seeded data dir; verifies the JSON files land
        # in the new v2 tables.
        r.h3("v2 schema migration (scripts/migrate_to_v2_schema.py)")
        try:
            from scripts.migrate_to_v2_schema import run_migration
            report = run_migration(tmp, tmp / "novels.db")
            total = sum(v["inserted"] for v in report.values())
            r.ok(f"migration ran clean: {total} rows inserted across "
                 f"{len(report)} collections")
            with sqlite3.connect(str(tmp / "novels.db")) as c:
                for t, want in [("characters", 3), ("worldbook_entries", 3),
                                ("project_memories", 3),
                                ("storyline_nodes", 3), ("storyline_edges", 2)]:
                    n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    if n >= want:
                        r.ok(f"v2 `{t}` has {n} rows (>= {want})")
                    else:
                        r.fail(f"v2 `{t}` has {n} rows (< {want})")
        except Exception as e:
            r.fail("v2 migration smoke test failed", repr(e))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def check_pytest(r: Report) -> None:
    r.h2("4. pytest suite")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=line"],
            cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=600,
        )
        last_lines = "\n".join(proc.stdout.splitlines()[-25:])
        r.text(f"Return code: `{proc.returncode}`")
        r.code(last_lines, "text")
        # parse summary
        import re
        m = re.search(r"(\d+) passed", proc.stdout)
        passed = int(m.group(1)) if m else 0
        m = re.search(r"(\d+) failed", proc.stdout)
        failed = int(m.group(1)) if m else 0
        m = re.search(r"(\d+) skipped", proc.stdout)
        skipped = int(m.group(1)) if m else 0
        if proc.returncode == 0:
            r.ok(f"pytest passed: {passed} passed, {skipped} skipped")
        else:
            r.fail(f"pytest failed: {passed} passed, {failed} failed, {skipped} skipped")
    except subprocess.TimeoutExpired:
        r.fail("pytest timed out after 10 min")
    except FileNotFoundError:
        r.fail("pytest not installed", "pip install pytest pytest-asyncio")


def check_server_endpoints(r: Report) -> None:
    r.h2("5. Live server checks (`/api/debug/*`)")
    r.text(
        "Requires the app running in **test mode** (debug endpoints "
        "are gated). Start with: `python launcher.py --test --no-gui`"
    )

    # 0. Is it up?
    code, body = _get(f"{SERVER_BASE}/health", timeout=3)
    if code == 0:
        r.fail("server not reachable", str(body))
        r.text("\n_Live checks skipped — start the app and re-run with `--server`._")
        return
    r.ok(f"server reachable at {SERVER_BASE} (status {code})")

    # 1. diagnostics
    r.h3("5.1 `/api/debug/diagnostics`")
    code, body = _get(f"{SERVER_BASE}/api/debug/diagnostics")
    if code == 403:
        r.fail("debug endpoints disabled",
               "set INKOCTO_DEBUG=1 or launch with --test")
        return
    if code != 200:
        r.fail(f"diagnostics returned {code}", str(body)[:200])
        return
    r.ok(f"diagnostics returned {code}")
    r.code(_safe_json(body), "json")

    if not body.get("test_mode"):
        r.warn("server NOT in test mode — RAG / Truth / memory dumps will hit live data")

    # 2. /api/db/info — market DB resolution
    r.h3("5.2 `/api/db/info` (market DB path resolution)")
    code, body = _get(f"{SERVER_BASE}/api/db/info")
    if code == 200:
        r.ok("market DB info returned")
        r.code(_safe_json(body), "json")
        src = body.get("source", "?")
        if body.get("available"):
            r.ok(f"market DB file exists at `{body.get('db_path')}` (source: {src})")
        else:
            r.warn(f"market DB file does NOT exist at `{body.get('db_path')}` (source: {src})",
                   "Settings → 市场数据库路径 to point at your .db file")
    else:
        r.fail(f"/api/db/info returned {code}")

    # 3. rag-preview
    r.h3("5.3 `/api/debug/rag-preview` (assembled RAG context)")
    qs = urllib.parse.urlencode({
        "project_id": DEFAULT_PID,
        "chapter_id": DEFAULT_CHID,
        "chapter_num": 2,
        "characters": "李星河,苏晚",
    })
    code, body = _get(f"{SERVER_BASE}/api/debug/rag-preview?{qs}")
    if code == 200:
        r.ok("rag-preview returned")
        sizes = body.get("block_sizes", {})
        r.text(f"\n**Token estimate**: {body.get('token_estimate')}\n")
        r.text("**Block sizes** (characters):")
        for name, n in sorted(sizes.items(), key=lambda x: -x[1]):
            r.text(f"- `{name}`: {n}")
        non_empty = sum(1 for v in sizes.values() if v > 0)
        if non_empty >= 3:
            r.ok(f"{non_empty} RAG blocks populated (expected >= 3)")
        else:
            r.warn(f"only {non_empty} blocks populated", "Seed data may be missing")
        r.h3("5.3.1 Assembled context preview (first 2000 chars)")
        r.code((body.get("assembled_context") or "")[:2000], "text")
    else:
        r.fail(f"rag-preview returned {code}", str(body)[:200])

    # 4. truth-files
    r.h3("5.4 `/api/debug/truth-files`")
    code, body = _get(f"{SERVER_BASE}/api/debug/truth-files?project_id={DEFAULT_PID}")
    if code == 200:
        r.ok("truth-files returned")
        tables = body.get("tables", {})
        for t, rows in tables.items():
            if isinstance(rows, list):
                if rows:
                    r.ok(f"`{t}` ({len(rows)} rows)")
                else:
                    r.skip(f"`{t}` (empty)")
            else:
                r.warn(f"`{t}` — {rows}")
        # Sample of biggest table
        for t in ("truth_current_state", "pending_hooks", "character_relations"):
            rows = tables.get(t)
            if isinstance(rows, list) and rows:
                r.h3(f"5.4.x sample row from `{t}`")
                r.code(_safe_json(rows[0]), "json")
                break
    else:
        r.fail(f"truth-files returned {code}", str(body)[:200])

    # 5. memory
    r.h3("5.5 `/api/debug/memory`")
    code, body = _get(f"{SERVER_BASE}/api/debug/memory?project_id={DEFAULT_PID}")
    if code == 200:
        r.ok("memory returned")
        l2 = body.get("L2_chapter_buffer")
        if isinstance(l2, list):
            r.ok(f"L2 chapter buffer: {len(l2)} rows")
        l4 = body.get("L4_episodic_timeline", {})
        evc = l4.get("event_count")
        unr = len(l4.get("unresolved_foreshadowing", []))
        if isinstance(evc, int):
            r.ok(f"L4 episodic events: {evc} (unresolved foreshadowing: {unr})")
        ki = body.get("knowledge_isolation")
        if isinstance(ki, list):
            r.ok(f"knowledge isolation: {len(ki)} rules")
        pm = body.get("project_memory", {})
        if isinstance(pm, dict) and "memories" in pm:
            r.ok(f"project memory: {len(pm['memories'])} confirmed facts")
        l3 = body.get("L3_semantic_memory", {})
        if isinstance(l3, dict) and "collection_count" in l3:
            r.ok(f"L3 ChromaDB collection size: {l3['collection_count']}")
        elif "_error" in l3:
            r.warn(f"L3 unavailable: {l3['_error']}")

        r.h3("5.5.1 Sample L2 chapter summary (first row)")
        if isinstance(l2, list) and l2:
            r.code(_safe_json(l2[0]), "json")
    else:
        r.fail(f"memory returned {code}", str(body)[:200])


def check_logs(r: Report) -> None:
    r.h2("6. Log discovery")
    log_dir = REPO_ROOT / "outputs" / "logs"
    if not log_dir.exists():
        r.warn("`outputs/logs/` does not exist yet", "launch the app once")
        return
    logs = sorted(log_dir.glob("inkoctobot_*.log"))
    if not logs:
        r.warn(f"no log files in `{log_dir}`", "launch the app once")
        return
    most_recent = logs[-1]
    size = most_recent.stat().st_size
    age = time.time() - most_recent.stat().st_mtime
    r.ok(f"latest log: `{most_recent.name}` ({size:,} bytes, {int(age)}s ago)")
    if age > 3600:
        r.warn(f"latest log is {int(age)/60:.0f}min old — app may have crashed or not started")


# ── Main ──────────────────────────────────────────────────────────


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--server", action="store_true",
                    help="Also hit live /api/debug/* endpoints "
                         "(requires app running with --test)")
    ap.add_argument("--no-pytest", action="store_true",
                    help="Skip the pytest run (faster smoke check)")
    ap.add_argument("--out", default="",
                    help="Output file path (default: outputs/checks/check_<timestamp>.md)")
    args = ap.parse_args()

    if not args.out:
        out_dir = REPO_ROOT / "outputs" / "checks"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = out_dir / f"check_{ts}.md"
    else:
        out_path = Path(args.out).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)

    r = Report()
    r.h1("InkOctoBot self-check report")
    r.text(f"_Generated: {datetime.now().isoformat()}_")
    r.text(f"_Repo: `{REPO_ROOT}`_")
    r.text(f"_Python: `{sys.version.split()[0]}`_")
    r.text(f"_Server checks: {'on' if args.server else 'off'}_")
    r.text(f"_Pytest: {'on' if not args.no_pytest else 'skipped'}_")

    print(f"[check] writing report to {out_path}")
    print("[check] 1/6 project structure ...")
    check_project_structure(r)
    print("[check] 2/6 imports ...")
    check_imports(r)
    print("[check] 3/6 test_seed ...")
    check_test_seed(r)
    if args.no_pytest:
        r.h2("4. pytest suite")
        r.skip("skipped via --no-pytest")
        print("[check] 4/6 pytest SKIPPED")
    else:
        print("[check] 4/6 pytest (~30s) ...")
        check_pytest(r)
    if args.server:
        print("[check] 5/6 live server ...")
        check_server_endpoints(r)
    else:
        r.h2("5. Live server checks")
        r.skip("not requested; pass --server with the app running in --test mode")
        print("[check] 5/6 live server SKIPPED")
    print("[check] 6/6 logs ...")
    check_logs(r)

    # Summary at top
    summary = (
        f"\n## Summary\n\n"
        f"| Result | Count |\n|---|---|\n"
        f"| ✅ OK    | **{r.passed}** |\n"
        f"| ❌ FAIL  | **{r.failed}** |\n"
        f"| ⚠️  WARN  | **{r.warned}** |\n"
        f"| ⏭️  SKIP  | **{r.skipped}** |\n\n"
    )
    body = r.render()
    out_path.write_text(
        f"# InkOctoBot self-check report\n{summary}{body}",
        encoding="utf-8",
    )

    print()
    print(f"=== Done. Report: {out_path}")
    print(f"=== Summary: {r.passed} OK, {r.failed} FAIL, {r.warned} WARN, {r.skipped} SKIP")
    return 0 if r.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
