"""CLI: migrate a project's existing state into the Truth File system.

Usage:
    python -m scripts.migrate_to_truth_files <project_id> \
        [--sources foreshadowing_json,episodic_events,character_cards,chapter_summaries] \
        [--db-path PATH] \
        [--dry-run]

By default all sources run. Re-running is safe (idempotent via
truth_apply_log hash + description-based dedup).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Allow running as `python -m scripts.migrate_to_truth_files`
HERE = Path(__file__).resolve()
PROJECT_ROOT = HERE.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from knowledge.truth.migrate import migrate_all  # noqa: E402
from knowledge.truth.store import TruthFileStore  # noqa: E402


_ALL_SOURCES = (
    "foreshadowing_json", "episodic_events",
    "character_cards", "chapter_summaries",
)


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Migrate existing project state to the Truth File system.",
    )
    p.add_argument("project_id", help="ID of the project to migrate.")
    p.add_argument(
        "--sources", default=",".join(_ALL_SOURCES),
        help=(
            "Comma-separated list of sources. "
            f"Default = all ({','.join(_ALL_SOURCES)})."
        ),
    )
    p.add_argument(
        "--db-path", default=None,
        help="SQLite path override. Default uses config/paths.yaml.",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print what would be migrated without writing.",
    )
    p.add_argument(
        "--json", action="store_true",
        help="Emit the report as JSON (machine-readable).",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    sources = tuple(s.strip() for s in args.sources.split(",") if s.strip())
    bad = [s for s in sources if s not in _ALL_SOURCES]
    if bad:
        print(f"unknown sources: {bad}", file=sys.stderr)
        return 2

    store = TruthFileStore(args.project_id, db_path=args.db_path)
    reports = migrate_all(store, sources=sources, dry_run=args.dry_run)

    if args.json:
        payload = {
            src: {
                "success": rep.success,
                "skipped_reason": rep.skipped_reason,
                "batches_applied": rep.batches_applied,
                "rows_emitted": rep.rows_emitted,
                "apply_results": [
                    {
                        "success": r.success,
                        "chapter_num": r.chapter_num,
                        "applied_counts": r.applied_counts,
                        "idempotent_hit": r.idempotent_hit,
                    } for r in rep.apply_results
                ],
            }
            for src, rep in reports.items()
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if all(r.success for r in reports.values()) else 1

    # human-readable summary
    print(f"Project: {args.project_id}   dry_run={args.dry_run}")
    print("=" * 64)
    for src, rep in reports.items():
        status = "[OK]" if rep.success else "[FAIL]"
        print(f"  {status} {src}")
        if rep.skipped_reason:
            print(f"      skipped: {rep.skipped_reason}")
        if rep.rows_emitted:
            for k, v in rep.rows_emitted.items():
                print(f"      {k}: {v}")
        if rep.apply_results:
            applied_total = sum(
                sum(r.applied_counts.values()) for r in rep.apply_results
            )
            idem = sum(1 for r in rep.apply_results if r.idempotent_hit)
            print(
                f"      batches: {rep.batches_applied}   "
                f"applied_rows: {applied_total}   idempotent: {idem}"
            )
    print("=" * 64)
    return 0 if all(r.success for r in reports.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
