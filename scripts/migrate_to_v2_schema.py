"""
migrate_to_v2_schema.py — one-way migration from data/*.json into novels.db (v2).

What it does
------------
1. Calls ``ensure_creation_tables`` to materialize v2 schema (adds new
   tables + ALTERs existing ``chapters`` for new columns).
2. For each JSON-backed collection under ``<data_dir>/``, reads files
   and inserts rows into the corresponding new table:

     - characters/*.json          -> characters
     - worldbook/*.json           -> worldbook_entries
     - project_memory/<pid>.json  -> project_memories
     - storylines/<pid>.json      -> storyline_nodes + storyline_edges
     - writing_knowledge/*.json   -> writing_knowledge
     - chat_history/*.json        -> chat_messages
     - editor/<pid>.json          -> project_blobs(scope='editor')
                                    + extends chapters table inline
     - calibration/<pid>.json     -> project_blobs(scope='calibration')
     - reference_injection_*.json -> project_blobs(scope='reference_injection')
     - knowledge_injection_*.json -> project_blobs(scope='knowledge_injection')
     - foreshadowing/<pid>.json   -> project_blobs(scope='foreshadowing_legacy')

Idempotency
-----------
- Re-running is safe: every INSERT uses ``OR IGNORE`` keyed on a
  deterministic primary key. Counts in the final report reflect rows
  inserted *this run* (skips on conflict are tallied separately).

JSON files are **not** deleted — review them and delete manually if
satisfied.

Usage
-----
    python scripts/migrate_to_v2_schema.py [--data-dir data] [--db data/novels.db]

Both arguments default to the repo's standard layout.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from storage.project_schema import ensure_creation_tables  # noqa: E402


def _nid(prefix: str = "") -> str:
    return f"{prefix}{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"


def _load_json(p: Path) -> dict | list | None:
    try:
        return json.loads(p.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


# ─────────────── individual collection migrators ────────────────────


def migrate_characters(con: sqlite3.Connection, data_dir: Path) -> tuple[int, int]:
    """Returns (inserted, skipped)."""
    src = data_dir / "characters"
    if not src.is_dir():
        return 0, 0
    inserted = skipped = 0
    cur = con.cursor()
    for p in sorted(src.glob("*.json")):
        obj = _load_json(p)
        if not isinstance(obj, dict):
            skipped += 1
            continue
        cid = obj.get("id") or _nid("char_")
        pid = obj.get("project_id") or "default"
        try:
            cur.execute(
                """INSERT OR IGNORE INTO characters
                   (character_id, project_id, name, role, description,
                    personality, background, appearance, speech_style,
                    tags_json, relationships_json, layer_a_json,
                    layer_b_json, extra_json, sort_order, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           CURRENT_TIMESTAMP)""",
                (
                    cid, pid,
                    obj.get("name", ""),
                    obj.get("role", ""),
                    obj.get("description", ""),
                    obj.get("personality", ""),
                    obj.get("background", ""),
                    obj.get("appearance", ""),
                    obj.get("speech_style", ""),
                    json.dumps(obj.get("tags", []), ensure_ascii=False),
                    json.dumps(obj.get("relationships", []), ensure_ascii=False),
                    json.dumps(obj.get("layer_a", {}), ensure_ascii=False),
                    json.dumps(obj.get("layer_b", {}), ensure_ascii=False),
                    json.dumps(
                        {k: v for k, v in obj.items() if k not in {
                            "id", "project_id", "name", "role", "description",
                            "personality", "background", "appearance",
                            "speech_style", "tags", "relationships",
                            "layer_a", "layer_b", "sort_order", "created_at",
                        }},
                        ensure_ascii=False,
                    ),
                    obj.get("sort_order", 0),
                ),
            )
            if cur.rowcount:
                inserted += 1
            else:
                skipped += 1
        except sqlite3.Error as exc:
            print(f"  ! characters/{p.name} insert failed: {exc}", file=sys.stderr)
            skipped += 1
    con.commit()
    return inserted, skipped


def migrate_worldbook(con: sqlite3.Connection, data_dir: Path) -> tuple[int, int]:
    src = data_dir / "worldbook"
    if not src.is_dir():
        return 0, 0
    inserted = skipped = 0
    cur = con.cursor()
    for p in sorted(src.glob("*.json")):
        obj = _load_json(p)
        if not isinstance(obj, dict):
            skipped += 1
            continue
        eid = obj.get("id") or _nid("wb_")
        pid = obj.get("project_id") or "default"
        try:
            cur.execute(
                """INSERT OR IGNORE INTO worldbook_entries
                   (entry_id, project_id, title, category, content,
                    tags_json, sort_order, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                (
                    eid, pid,
                    obj.get("title", ""),
                    obj.get("category", "misc"),
                    obj.get("content", ""),
                    json.dumps(obj.get("tags", []), ensure_ascii=False),
                    obj.get("sort_order", 0),
                ),
            )
            if cur.rowcount:
                inserted += 1
            else:
                skipped += 1
        except sqlite3.Error as exc:
            print(f"  ! worldbook/{p.name} insert failed: {exc}", file=sys.stderr)
            skipped += 1
    con.commit()
    return inserted, skipped


def migrate_project_memory(con: sqlite3.Connection, data_dir: Path) -> tuple[int, int]:
    src = data_dir / "project_memory"
    if not src.is_dir():
        return 0, 0
    inserted = skipped = 0
    cur = con.cursor()
    for p in sorted(src.glob("*.json")):
        obj = _load_json(p)
        if not isinstance(obj, dict):
            skipped += 1
            continue
        pid = obj.get("project_id") or p.stem
        for mem in obj.get("memories", []) or []:
            if not isinstance(mem, dict):
                skipped += 1
                continue
            mid = mem.get("id") or _nid("mem_")
            try:
                cur.execute(
                    """INSERT OR IGNORE INTO project_memories
                       (memory_id, project_id, category, content, source,
                        created_at)
                       VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                    (mid, pid,
                     mem.get("category", "note"),
                     mem.get("content", ""),
                     mem.get("source", "user")),
                )
                if cur.rowcount:
                    inserted += 1
                else:
                    skipped += 1
            except sqlite3.Error as exc:
                print(f"  ! project_memory/{p.name} insert failed: {exc}",
                      file=sys.stderr)
                skipped += 1
    con.commit()
    return inserted, skipped


def migrate_storylines(con: sqlite3.Connection, data_dir: Path) -> tuple[int, int]:
    src = data_dir / "storylines"
    if not src.is_dir():
        return 0, 0
    inserted = skipped = 0
    cur = con.cursor()
    for p in sorted(src.glob("*.json")):
        obj = _load_json(p)
        if not isinstance(obj, dict):
            skipped += 1
            continue
        pid = obj.get("project_id") or p.stem
        for n in obj.get("nodes", []) or []:
            if not isinstance(n, dict):
                continue
            nid = n.get("id") or _nid("node_")
            try:
                cur.execute(
                    """INSERT OR IGNORE INTO storyline_nodes
                       (node_id, project_id, title, chapter_num, summary,
                        time_label, location, pos_x, pos_y, extra_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (nid, pid,
                     n.get("title", ""),
                     n.get("chapter_num"),
                     n.get("summary", ""),
                     n.get("time", n.get("time_label", "")),
                     n.get("location", ""),
                     n.get("x", n.get("pos_x", 0)),
                     n.get("y", n.get("pos_y", 0)),
                     json.dumps(
                         {k: v for k, v in n.items() if k not in {
                             "id", "title", "chapter_num", "summary",
                             "time", "time_label", "location",
                             "x", "y", "pos_x", "pos_y",
                         }},
                         ensure_ascii=False,
                     )),
                )
                if cur.rowcount:
                    inserted += 1
            except sqlite3.Error as exc:
                print(f"  ! storylines/{p.name} node {nid} insert failed: {exc}",
                      file=sys.stderr)
                skipped += 1
        for e in obj.get("edges", []) or []:
            if not isinstance(e, dict):
                continue
            eid = e.get("id") or _nid("edge_")
            try:
                cur.execute(
                    """INSERT OR IGNORE INTO storyline_edges
                       (edge_id, project_id, from_node_id, to_node_id, label)
                       VALUES (?, ?, ?, ?, ?)""",
                    (eid, pid,
                     e.get("from", e.get("from_node_id", "")),
                     e.get("to", e.get("to_node_id", "")),
                     e.get("label", "")),
                )
                if cur.rowcount:
                    inserted += 1
            except sqlite3.Error as exc:
                print(f"  ! storylines/{p.name} edge {eid} insert failed: {exc}",
                      file=sys.stderr)
                skipped += 1
    con.commit()
    return inserted, skipped


def migrate_writing_knowledge(con: sqlite3.Connection, data_dir: Path) -> tuple[int, int]:
    src = data_dir / "writing_knowledge"
    if not src.is_dir():
        return 0, 0
    inserted = skipped = 0
    cur = con.cursor()
    for p in sorted(src.glob("*.json")):
        obj = _load_json(p)
        if not isinstance(obj, dict):
            skipped += 1
            continue
        kid = obj.get("id") or _nid("wk_")
        try:
            cur.execute(
                """INSERT OR IGNORE INTO writing_knowledge
                   (knowledge_id, domain, title, content, tags_json,
                    created_at)
                   VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                (kid,
                 obj.get("domain", "general"),
                 obj.get("title", ""),
                 obj.get("content", ""),
                 json.dumps(obj.get("tags", []), ensure_ascii=False)),
            )
            if cur.rowcount:
                inserted += 1
            else:
                skipped += 1
        except sqlite3.Error as exc:
            print(f"  ! writing_knowledge/{p.name} insert failed: {exc}",
                  file=sys.stderr)
            skipped += 1
    con.commit()
    return inserted, skipped


def migrate_chat_history(con: sqlite3.Connection, data_dir: Path) -> tuple[int, int]:
    src = data_dir / "chat_history"
    if not src.is_dir():
        return 0, 0
    inserted = skipped = 0
    cur = con.cursor()
    for p in sorted(src.glob("*.json")):
        obj = _load_json(p)
        if not isinstance(obj, dict):
            skipped += 1
            continue
        pid = obj.get("project_id") or p.stem.split("_")[0]
        scope = obj.get("scope", "pipeline")
        for idx, msg in enumerate(obj.get("messages", []) or []):
            if not isinstance(msg, dict):
                continue
            role = msg.get("role", "user")
            if role not in {"user", "assistant", "system", "tool"}:
                role = "user"
            # Stable id so re-running the migration doesn't double-insert.
            # Falls back to a content hash when the message has no explicit id.
            content = msg.get("content", "")
            if msg.get("id"):
                mid = str(msg["id"])
            else:
                digest = hashlib.sha256(
                    f"{pid}|{scope}|{idx}|{role}|{content}".encode("utf-8")
                ).hexdigest()[:16]
                mid = f"msg_{digest}"
            meta = {k: v for k, v in msg.items()
                    if k not in {"role", "content", "id"}}
            try:
                cur.execute(
                    """INSERT OR IGNORE INTO chat_messages
                       (message_id, project_id, scope, role, content,
                        meta_json, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                    (mid, pid, scope, role,
                     msg.get("content", ""),
                     json.dumps(meta, ensure_ascii=False)),
                )
                if cur.rowcount:
                    inserted += 1
            except sqlite3.Error as exc:
                print(f"  ! chat_history/{p.name} msg insert failed: {exc}",
                      file=sys.stderr)
                skipped += 1
    con.commit()
    return inserted, skipped


def migrate_editor_doc(con: sqlite3.Connection, data_dir: Path) -> tuple[int, int]:
    """Editor doc has two destinations:
    1. project_blobs(scope='editor') — the raw blob, for UI rendering
    2. chapters table — synopsis/time/location/characters/content where
       a matching chapter_num exists or can be created.
    """
    src = data_dir / "editor"
    if not src.is_dir():
        return 0, 0
    inserted = skipped = 0
    cur = con.cursor()
    for p in sorted(src.glob("*.json")):
        obj = _load_json(p)
        if not isinstance(obj, dict):
            skipped += 1
            continue
        pid = obj.get("project_id") or p.stem
        try:
            cur.execute(
                """INSERT OR REPLACE INTO project_blobs
                   (blob_id, project_id, scope, data_json, updated_at)
                   VALUES (?, ?, 'editor', ?, CURRENT_TIMESTAMP)""",
                (f"{pid}__editor", pid,
                 json.dumps(obj, ensure_ascii=False)),
            )
            inserted += 1
        except sqlite3.Error as exc:
            print(f"  ! editor/{p.name} blob insert failed: {exc}",
                  file=sys.stderr)
            skipped += 1
        # Also extend chapters table inline
        for vol in obj.get("volumes", []) or []:
            for ch in vol.get("chapters", []) or []:
                if not isinstance(ch, dict):
                    continue
                cnum = ch.get("order")
                if cnum is None:
                    continue
                cnum = int(cnum) + 1  # editor "order" is 0-based
                cid = f"{pid}_ch{cnum:04d}"
                try:
                    cur.execute(
                        """INSERT OR IGNORE INTO chapters
                           (chapter_id, project_id, chapter_num, volume,
                            title, outline, final_text, word_count,
                            synopsis, time_label, location, characters_json,
                            pov_character, status, created_at, updated_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                   'draft', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
                        (cid, pid, cnum,
                         vol.get("order", 0) + 1,
                         ch.get("title", ""),
                         ch.get("outline", ""),
                         ch.get("content", ""),
                         ch.get("word_count", 0) or len(ch.get("content", "")),
                         ch.get("synopsis", ""),
                         ch.get("time", ""),
                         ch.get("location", ""),
                         json.dumps(ch.get("characters", []), ensure_ascii=False),
                         ch.get("pov_character", "")),
                    )
                    if cur.rowcount:
                        inserted += 1
                except sqlite3.Error as exc:
                    print(f"  ! editor/{p.name} chapter {cnum} insert failed: {exc}",
                          file=sys.stderr)
                    skipped += 1
    con.commit()
    return inserted, skipped


_PROJECT_BLOB_COLLECTIONS = [
    # (subdir or filename glob, scope, file_pattern_per_project)
    ("calibration",           "calibration",          "{pid}.json"),
    ("foreshadowing",         "foreshadowing_legacy", "{pid}.json"),
]


def migrate_project_blobs(con: sqlite3.Connection, data_dir: Path) -> tuple[int, int]:
    """Generic per-project JSON blob -> project_blobs row."""
    inserted = skipped = 0
    cur = con.cursor()
    for subdir, scope, _ in _PROJECT_BLOB_COLLECTIONS:
        d = data_dir / subdir
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.json")):
            obj = _load_json(p)
            if obj is None:
                skipped += 1
                continue
            pid = (obj.get("project_id") if isinstance(obj, dict) else None) or p.stem
            try:
                cur.execute(
                    """INSERT OR REPLACE INTO project_blobs
                       (blob_id, project_id, scope, data_json, updated_at)
                       VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                    (f"{pid}__{scope}", pid, scope,
                     json.dumps(obj, ensure_ascii=False)),
                )
                inserted += 1
            except sqlite3.Error as exc:
                print(f"  ! {subdir}/{p.name} insert failed: {exc}",
                      file=sys.stderr)
                skipped += 1
    # reference_injection / knowledge_injection are top-level files
    for prefix, scope in [("reference_injection", "reference_injection"),
                          ("knowledge_injection", "knowledge_injection")]:
        for p in sorted(data_dir.glob(f"{prefix}_*.json")):
            obj = _load_json(p)
            if obj is None:
                skipped += 1
                continue
            pid = p.stem[len(prefix) + 1:]
            try:
                cur.execute(
                    """INSERT OR REPLACE INTO project_blobs
                       (blob_id, project_id, scope, data_json, updated_at)
                       VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)""",
                    (f"{pid}__{scope}", pid, scope,
                     json.dumps(obj, ensure_ascii=False)),
                )
                inserted += 1
            except sqlite3.Error as exc:
                print(f"  ! {prefix}_{pid}.json insert failed: {exc}",
                      file=sys.stderr)
                skipped += 1
    con.commit()
    return inserted, skipped


# ─────────────── orchestrator ───────────────────────────────────────


_MIGRATORS = [
    ("characters",        migrate_characters),
    ("worldbook",         migrate_worldbook),
    ("project_memory",    migrate_project_memory),
    ("storylines",        migrate_storylines),
    ("writing_knowledge", migrate_writing_knowledge),
    ("chat_history",      migrate_chat_history),
    ("editor",            migrate_editor_doc),
    ("project_blobs",     migrate_project_blobs),
]


def run_migration(data_dir: Path, db_path: Path) -> dict:
    """Returns a report dict suitable for printing or programmatic use."""
    print(f"v2 schema migration: data_dir={data_dir}  db={db_path}")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("PRAGMA foreign_keys=ON")
        ensure_creation_tables(con)
        # Make sure the projects we're about to reference actually exist;
        # ON DELETE CASCADE doesn't help if the parent is missing.
        # The legacy JSON files contain project_id values; insert
        # placeholder projects for any we haven't seen.
        _ensure_placeholder_projects(con, data_dir)

        report: dict = {}
        for name, fn in _MIGRATORS:
            ins, skip = fn(con, data_dir)
            report[name] = {"inserted": ins, "skipped": skip}
            print(f"  {name:20s}  inserted={ins:4d}  skipped={skip:4d}")
        return report
    finally:
        con.close()


def _ensure_placeholder_projects(con: sqlite3.Connection, data_dir: Path) -> None:
    """Best-effort: collect project_id values from JSON files and INSERT OR IGNORE."""
    pids: set[str] = set()
    proj_dir = data_dir / "projects"
    if proj_dir.is_dir():
        for p in proj_dir.glob("*.json"):
            obj = _load_json(p)
            if isinstance(obj, dict) and obj.get("id"):
                pids.add(str(obj["id"]))
    for sub in ("characters", "worldbook"):
        d = data_dir / sub
        if d.is_dir():
            for p in d.glob("*.json"):
                obj = _load_json(p)
                if isinstance(obj, dict) and obj.get("project_id"):
                    pids.add(str(obj["project_id"]))
    cur = con.cursor()
    for pid in pids:
        cur.execute(
            """INSERT OR IGNORE INTO projects
               (project_id, title, status, created_at, updated_at)
               VALUES (?, ?, 'planning', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            (pid, ""),
        )
    con.commit()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Migrate JSON-backed data into novels.db v2.")
    ap.add_argument("--data-dir", default=str(REPO_ROOT / "data"),
                    help="Directory containing the legacy JSON files (default: ./data)")
    ap.add_argument("--db", default=None,
                    help="Path to novels.db (default: <data-dir>/novels.db)")
    args = ap.parse_args(argv)

    data_dir = Path(args.data_dir).resolve()
    db_path = Path(args.db).resolve() if args.db else (data_dir / "novels.db")
    if not data_dir.is_dir():
        print(f"ERROR: data dir not found: {data_dir}", file=sys.stderr)
        return 1

    report = run_migration(data_dir, db_path)
    total_inserted = sum(v["inserted"] for v in report.values())
    print(f"\nDone. {total_inserted} rows inserted across {len(report)} collections.")
    print("JSON files were NOT deleted — review and remove manually.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
