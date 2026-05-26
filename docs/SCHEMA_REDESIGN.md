# Schema Redesign — novels.db v2

> Goal: eliminate redundancy between L3/L4 memory tables and Truth Files,
> and migrate all JSON-file based data into the database, so that
> **every piece of project data lives in one SQLite file**.

## 1. Motivation

The repo originally grew two parallel storage layers:

| Concern | Where it lived (v1) | Problem |
|---|---|---|
| Permanent facts (L3) | `permanent_facts` table | Duplicates `truth_current_state` |
| Foreshadow state machine | `episodic_events.foreshadow_status` + `foreshadow_target_chapter` columns | Duplicates `pending_hooks` + `hook_events` |
| Per-chapter recap (L2) | `chapter_summaries.is_active=1` rows | Same table is **also** Truth File #4. Two writers, one table. Confusing. |
| Character cards | `data/characters/*.json` | Outside DB, can't FK, hard to query |
| Worldbook entries | `data/worldbook/*.json` | Same |
| Per-project memory | `data/project_memory/*.json` | Same |
| Storyline graph | `data/storylines/*.json` | Same |
| Editor doc (volumes + chapters) | `data/editor/<pid>.json` | Same — but `chapters` table already exists! |
| Chat history | `data/chat_history/*.json` | Same |
| Style calibration | `data/calibration/*.json` | Same |
| Reference/knowledge injection | `data/reference_injection_*.json`, `data/knowledge_injection_*.json` | Same |
| Writing knowledge | `data/writing_knowledge/*.json` | Same |

This split made the app brittle: backups had to capture both a SQLite file
and a tree of JSON files; the same conceptual entity (a chapter) lived in
two places (`chapters.final_text` *and* `data/editor/<pid>.json` content);
and the Truth File system overlapped quietly with L3/L4 without anyone
explicitly choosing which one was canonical.

## 2. New rules

1. **One database per project.** `data/novels.db` (or `data_test/novels.db`)
   holds **all** project data. JSON files in `data/` are deprecated; only
   `settings.json`, `usage.json` (app-global state) and the user-imported
   crawler database stay outside.
2. **Truth Files are canonical for state.** Permanent facts go to
   `truth_current_state`; foreshadow state goes to `pending_hooks` +
   `hook_events`. The consolidator emits `TruthDeltas`; it never inserts
   into `permanent_facts` or sets a foreshadow status anywhere else.
3. **`chapter_summaries` is Truth File #4 only.** The L2
   "ChapterBuffer" concept is collapsed: callers that need "what
   happened in chapters N-2..N" query the chapters table joined to
   `chapter_summaries` by `chapter_num` and bound the range.
4. **`episodic_events` becomes an audit log.** It still records "what
   happened in chapter X" (useful for debug & analytics), but it no
   longer carries foreshadow status — that lives in `pending_hooks`.

## 3. Redundancies removed

| Removed | Replaced by |
|---|---|
| `permanent_facts` table | `truth_current_state` (Truth File #1) |
| `episodic_events.foreshadow_status` column | `pending_hooks.status` (Truth File #3) |
| `episodic_events.foreshadow_target_chapter` column | `pending_hooks.expected_payoff_chapter` |
| `episodic_events.event_type='foreshadowing'`/`'foreshadowing_payoff'` | `pending_hooks` + `hook_events` |
| `chapter_summaries.is_active=0` archived rows | Soft-deleted via Truth File ApplyLog instead |
| `MemoryConsolidator.semantic.store_permanent_fact(...)` writes | `TruthFileStore.apply_deltas(StatePatch[...])` |
| `EpisodicTimeline.get_unresolved_foreshadowing/resolve_foreshadowing` | `TruthFileStore.get_pending_hooks(...)` / `apply_deltas(HookDelta(action='resolve'))` |

## 4. New tables (canonical for JSON-backed collections)

All FK to `projects(project_id)` with `ON DELETE CASCADE`.

### 4.1 `characters` — was `data/characters/*.json`

```
character_id        TEXT PRIMARY KEY
project_id          TEXT NOT NULL
name                TEXT NOT NULL
role                TEXT NOT NULL DEFAULT ''           -- 主角/反派/配角/...
description         TEXT NOT NULL DEFAULT ''
personality         TEXT NOT NULL DEFAULT ''
background          TEXT NOT NULL DEFAULT ''
appearance          TEXT NOT NULL DEFAULT ''
speech_style        TEXT NOT NULL DEFAULT ''
tags_json           TEXT NOT NULL DEFAULT '[]'
relationships_json  TEXT NOT NULL DEFAULT '[]'         -- legacy array of {target_id, affinity, ...}
layer_a_json        TEXT NOT NULL DEFAULT '{}'         -- structured Layer A profile
layer_b_json        TEXT NOT NULL DEFAULT '{}'         -- runtime params (mood/state)
extra_json          TEXT NOT NULL DEFAULT '{}'         -- escape hatch for legacy fields
sort_order          INTEGER NOT NULL DEFAULT 0
created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
UNIQUE(project_id, name)
```
Indexes: `(project_id, sort_order)`.

### 4.2 `worldbook_entries` — was `data/worldbook/*.json`

```
entry_id      TEXT PRIMARY KEY
project_id    TEXT NOT NULL
title         TEXT NOT NULL
category      TEXT NOT NULL DEFAULT 'misc'             -- hard_rules/social/factions/...
content       TEXT NOT NULL DEFAULT ''
tags_json     TEXT NOT NULL DEFAULT '[]'
sort_order    INTEGER NOT NULL DEFAULT 0
created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
```
Indexes: `(project_id, category)`.

### 4.3 `project_memories` — was `data/project_memory/<pid>.json`

```
memory_id     TEXT PRIMARY KEY
project_id    TEXT NOT NULL
category      TEXT NOT NULL DEFAULT 'note'             -- rule/setting/secret/note
content       TEXT NOT NULL DEFAULT ''
source        TEXT NOT NULL DEFAULT 'user'             -- user/extracted/import
created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
```
Indexes: `(project_id, category)`.

### 4.4 `storyline_nodes` / `storyline_edges` — was `data/storylines/<pid>.json`

```
storyline_nodes:
  node_id       TEXT PRIMARY KEY
  project_id    TEXT NOT NULL
  title         TEXT NOT NULL DEFAULT ''
  chapter_num   INTEGER
  summary       TEXT NOT NULL DEFAULT ''
  time_label    TEXT NOT NULL DEFAULT ''
  location      TEXT NOT NULL DEFAULT ''
  pos_x         REAL NOT NULL DEFAULT 0
  pos_y         REAL NOT NULL DEFAULT 0
  extra_json    TEXT NOT NULL DEFAULT '{}'

storyline_edges:
  edge_id       TEXT PRIMARY KEY
  project_id    TEXT NOT NULL
  from_node_id  TEXT NOT NULL
  to_node_id    TEXT NOT NULL
  label         TEXT NOT NULL DEFAULT ''
```
Indexes: nodes`(project_id)`, edges`(project_id, from_node_id)`.

### 4.5 `writing_knowledge` — was `data/writing_knowledge/*.json`

```
knowledge_id  TEXT PRIMARY KEY
domain        TEXT NOT NULL DEFAULT 'general'          -- pacing/rhetoric/structure/...
title         TEXT NOT NULL
content       TEXT NOT NULL DEFAULT ''
tags_json     TEXT NOT NULL DEFAULT '[]'
created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
```
Indexes: `(domain)`. Cross-project (no `project_id`) — these are the
user's reusable writing-craft notes.

### 4.6 `chat_messages` — was `data/chat_history/*.json`

```
message_id    TEXT PRIMARY KEY
project_id    TEXT NOT NULL
scope         TEXT NOT NULL DEFAULT 'pipeline'         -- pipeline/outline_chat/single_agent/...
role          TEXT NOT NULL CHECK(role IN ('user','assistant','system','tool'))
content       TEXT NOT NULL DEFAULT ''
meta_json     TEXT NOT NULL DEFAULT '{}'
created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
```
Indexes: `(project_id, scope, created_at)`.

### 4.7 `project_blobs` — single-row-per-project KV blobs

The "one blob per project" collections (editor doc, calibration,
reference_injection, knowledge_injection, foreshadowing legacy) are
the same shape: project_id + scope + JSON payload + updated_at.
Storing them as a unified KV table keeps schema small and isolation
correct.

```
blob_id       TEXT PRIMARY KEY                          -- f"{project_id}__{scope}"
project_id    TEXT NOT NULL
scope         TEXT NOT NULL                             -- 'editor', 'calibration', 'reference_injection', ...
data_json     TEXT NOT NULL DEFAULT '{}'
updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
UNIQUE(project_id, scope)
```

### 4.8 `chapters` — extended in place

The existing `chapters` table already stores `outline`, `final_text`,
`word_count`, `status`, `scene_plan_json`, `evaluation_json`. Adds:

```
synopsis           TEXT NOT NULL DEFAULT ''     -- short blurb (≠ outline)
time_label         TEXT NOT NULL DEFAULT ''
location           TEXT NOT NULL DEFAULT ''
characters_json    TEXT NOT NULL DEFAULT '[]'   -- POV + appearing names
pov_character      TEXT NOT NULL DEFAULT ''
extra_json         TEXT NOT NULL DEFAULT '{}'
```

## 5. Migration

`scripts/migrate_to_v2_schema.py` is idempotent and one-way:

1. Read JSON files under `data/{characters,worldbook,project_memory,
   storylines,writing_knowledge,chat_history,editor,calibration,
   reference_injection,knowledge_injection,foreshadowing}/`.
2. Insert into the new tables. Skip rows that already exist by ID.
3. Print a per-collection count.
4. Do **not** delete the JSON files — the user can review them.

Run from the repo root: `python scripts/migrate_to_v2_schema.py [--data-dir data]`.

Test-mode: `test_seed.py` is updated to populate the new tables
**directly** (no JSON files) so a fresh `--seed --test` produces a
DB-only world.

## 6. Compatibility window

This commit is **additive**:
- New tables are created with `IF NOT EXISTS` — no destructive ops.
- Old `permanent_facts` + `foreshadow_*` columns are still readable so
  current routers / pipelines that read them keep working.
- Consolidator is updated to **also** emit Truth Deltas (so new state
  lands canonically), while keeping existing writes to L3/L4 during the
  transition.

A follow-up commit will:
- Refactor `routers/json_storage_api.py`, `routers/characters_api.py`,
  `routers/worldbook_api.py` to read/write from the new DB tables.
- Drop `permanent_facts`, drop `foreshadow_*` columns from
  `episodic_events`, remove the legacy consolidator writes.
- Remove `data/*.json` collection directories.

## 7. Test coverage

- `tests/storage/test_v2_schema.py` — creates a temp DB, validates DDL,
  FK constraints, and migration is idempotent.
- `tests/storage/test_v2_migrate.py` — runs `migrate_to_v2_schema` on a
  synthetic JSON tree and asserts the row counts match.
- `tests/integration/test_seed_v2.py` — runs `test_seed.py` against
  a tmp dir and asserts the new tables are populated.
