"""One-shot migration: move reference + inspiration tables out of novels.db.

History: before the DB-rename, all reference works and inspirations lived
inside ``data/novels.db`` along with the project / chapter / memory
tables. The rename split them into ``data/reference.db`` and
``data/idea.db`` so the three surfaces are decoupled.

This script handles the data move for installs that already accumulated
data under the old layout. It is **idempotent and safe to run multiple
times** — if a destination file already has rows it's left alone.

What it does:
  1. Look at ``data/novels.db``. If it doesn't exist, nothing to do.
  2. If novels.db has ``reference_works`` table → copy
     {reference_works, reference_entries, project_reference_links,
     work_index_progress, reference_chapters} to ``data/reference.db``
     and DROP them from novels.db.
  3. If novels.db has ``inspirations`` table → copy to ``data/idea.db``
     and DROP from novels.db.

Run manually:  python scripts/migrate_split_dbs.py
              python scripts/migrate_split_dbs.py --data-dir data_test
Run via launcher: handled automatically on startup if the source tables
                  exist and the destination files don't yet.
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger("inkoctobot.scripts.migrate_split_dbs")

REF_TABLES = [
    "reference_works",
    "reference_entries",
    "project_reference_links",
    "work_index_progress",
    "reference_chapters",
]
IDEA_TABLES = ["inspirations"]


def _has_table(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _row_count(conn: sqlite3.Connection, name: str) -> int:
    if not _has_table(conn, name):
        return 0
    return int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])


def _copy_tables(
    src_path: Path,
    dst_path: Path,
    tables: list[str],
    ensure_fn,
    label: str,
) -> dict:
    """Copy ``tables`` from src_path to dst_path. Drop from src after success.

    ``ensure_fn(conn)`` is called on the destination first to create the
    schema. Returns a summary dict {table: copied_rows}.
    """
    summary: dict[str, int] = {}

    if not src_path.exists():
        logger.info("migrate_split_dbs source missing path=%s — nothing to do", src_path)
        return summary

    with sqlite3.connect(str(src_path)) as src:
        src.row_factory = sqlite3.Row
        present = [t for t in tables if _has_table(src, t)]
        if not present:
            logger.info(
                "migrate_split_dbs %s no source tables found — nothing to migrate",
                label,
            )
            return summary

        # Bail safely if dst already has data — don't double-insert.
        # This is the steady state after a successful migration (or when
        # the user populated the new DB before any migration ran), so
        # log at INFO rather than WARNING — there's nothing actionable.
        if dst_path.exists():
            with sqlite3.connect(str(dst_path)) as probe:
                for t in present:
                    if _row_count(probe, t) > 0:
                        logger.info(
                            "migrate_split_dbs %s already migrated "
                            "(table=%s has data in %s) — skipping",
                            label, t, dst_path.name,
                        )
                        return summary

        dst_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(dst_path)) as dst:
            dst.row_factory = sqlite3.Row
            ensure_fn(dst)
            dst.commit()

            for t in present:
                rows = src.execute(f"SELECT * FROM {t}").fetchall()
                if not rows:
                    summary[t] = 0
                    logger.info("migrate_split_dbs %s table=%s rows=0 (empty source)", label, t)
                    continue
                cols = rows[0].keys()
                placeholders = ", ".join(["?"] * len(cols))
                col_list = ", ".join(cols)
                dst.executemany(
                    f"INSERT OR REPLACE INTO {t} ({col_list}) VALUES ({placeholders})",
                    [tuple(r[c] for c in cols) for r in rows],
                )
                dst.commit()
                summary[t] = len(rows)
                logger.info(
                    "migrate_split_dbs %s table=%s rows=%d copied to %s",
                    label, t, len(rows), dst_path.name,
                )

        # Drop from source after successful copy.
        for t in present:
            src.execute(f"DROP TABLE IF EXISTS {t}")
        src.commit()
        logger.info(
            "migrate_split_dbs %s dropped %d table(s) from %s",
            label, len(present), src_path.name,
        )

    return summary


def migrate(data_dir: Path) -> dict[str, dict]:
    """Run both migrations. Returns {'reference': {...}, 'idea': {...}}."""
    novels = data_dir / "novels.db"
    reference = data_dir / "reference.db"
    idea = data_dir / "idea.db"

    out: dict[str, dict] = {"reference": {}, "idea": {}}

    from storage.reference_schema import ensure_reference_tables
    from storage.idea_schema import ensure_idea_tables

    out["reference"] = _copy_tables(novels, reference, REF_TABLES,
                                     ensure_reference_tables, "reference")
    out["idea"] = _copy_tables(novels, idea, IDEA_TABLES,
                                ensure_idea_tables, "idea")
    return out


def needs_migration(data_dir: Path) -> bool:
    """Cheap check: should launcher.py auto-run this on startup?"""
    novels = data_dir / "novels.db"
    if not novels.exists():
        return False
    try:
        with sqlite3.connect(str(novels)) as c:
            return any(_has_table(c, t) for t in REF_TABLES + IDEA_TABLES)
    except Exception:
        return False


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    )
    parser = argparse.ArgumentParser(
        description="Migrate reference + inspiration tables out of novels.db"
    )
    parser.add_argument(
        "--data-dir", default="data",
        help="Path to the data directory (default: data)",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    if not data_dir.exists():
        print(f"data dir not found: {data_dir}")
        raise SystemExit(1)

    print(f"\nMigrating split DBs under {data_dir} ...\n")
    summary = migrate(data_dir)
    print("\n=== Migration summary ===")
    for db_label, tables in summary.items():
        total = sum(tables.values())
        print(f"  {db_label}.db: {total} rows ({tables})")
    print("\nDone.")


if __name__ == "__main__":
    import sys
    # Allow running from anywhere — make repo root importable.
    repo_root = Path(__file__).resolve().parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    main()
