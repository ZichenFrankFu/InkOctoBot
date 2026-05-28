"""Quick DB diagnostic for the empty-text-versions case.

Usage:
    python _diag.py
    # or for test mode:
    python _diag.py --test
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    use_test = "--test" in sys.argv
    db = Path(__file__).resolve().parent / (
        "data_test/novels.db" if use_test else "data/novels.db"
    )
    if not db.exists():
        print(f"DB not found: {db}")
        return 1
    print(f"DB: {db}\n")

    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    print("=" * 70)
    print("text_versions (ordered by chapter / version)")
    print("=" * 70)
    rows = con.execute(
        """SELECT chapter_id, version_num, source,
                  length(content) AS chars,
                  substr(content, 1, 80) AS head,
                  model_used, created_at
           FROM text_versions
           ORDER BY chapter_id, version_num"""
    ).fetchall()
    if not rows:
        print("(empty)")
    for r in rows:
        print(f"  ch={r['chapter_id']!s:<10} v{r['version_num']} "
              f"src={r['source']!s:<10} chars={r['chars']:>6} "
              f"model={r['model_used']!r}")
        if r["chars"]:
            print(f"    head: {r['head']!r}")

    print("\n" + "=" * 70)
    print("commit_tasks (per chapter)")
    print("=" * 70)
    try:
        rows = con.execute(
            """SELECT chapter_id, task, status, error_msg, created_at
               FROM commit_tasks
               ORDER BY chapter_id, created_at"""
        ).fetchall()
    except sqlite3.OperationalError as e:
        print(f"(table read failed: {e})")
        rows = []
    if not rows:
        print("(empty)")
    for r in rows:
        line = f"  ch={r['chapter_id']!s:<10} task={r['task']!s:<20} status={r['status']}"
        if r["status"] not in {"completed", "skipped"} and r["error_msg"]:
            line += f"  ERR={(r['error_msg'] or '')[:120]}"
        print(line)

    print("\n" + "=" * 70)
    print("chapter_summaries")
    print("=" * 70)
    rows = con.execute(
        "SELECT chapter_num, length(summary_text) AS chars, "
        "substr(summary_text,1,80) AS head FROM chapter_summaries "
        "WHERE project_id='rt_proj' ORDER BY chapter_num"
    ).fetchall()
    if not rows:
        print("(empty)")
    for r in rows:
        print(f"  ch{r['chapter_num']}: {r['chars']} chars — {r['head']!r}")

    print("\n" + "=" * 70)
    print("truth_current_state")
    print("=" * 70)
    try:
        rows = con.execute(
            "SELECT subject, predicate, object, chapter_num "
            "FROM truth_current_state WHERE project_id='rt_proj' "
            "ORDER BY chapter_num LIMIT 20"
        ).fetchall()
    except sqlite3.OperationalError as e:
        print(f"(table read failed: {e})")
        rows = []
    if not rows:
        print("(empty)")
    for r in rows:
        print(f"  ch{r['chapter_num']}: {r['subject']} {r['predicate']} {r['object']}")

    print("\n" + "=" * 70)
    print("edit_observations")
    print("=" * 70)
    rows = con.execute(
        "SELECT chapter_id, chapter_num, consumed, "
        "length(original_text) AS orig_chars, "
        "length(edited_text) AS edit_chars "
        "FROM edit_observations WHERE project_id='rt_proj' "
        "ORDER BY created_at"
    ).fetchall()
    if not rows:
        print("(empty — no user edits captured)")
    for r in rows:
        print(f"  ch={r['chapter_id']} ch_num={r['chapter_num']} "
              f"consumed={r['consumed']} orig={r['orig_chars']} edit={r['edit_chars']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
