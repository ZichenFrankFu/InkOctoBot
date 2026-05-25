# Truth File System

> The state authority for InkOctoBot, modeled on InkOS's seven canonical
> truth files. SQLite holds the canonical state; Markdown is an on-demand
> view. All state mutations go through one entry point — `TruthFileStore.apply_deltas`.

---

## Table of Contents

1. [What is the Truth File system?](#1-what-is-the-truth-file-system)
2. [The seven truth files](#2-the-seven-truth-files)
3. [Architecture](#3-architecture)
4. [End-to-end data flow](#4-end-to-end-data-flow)
5. [Read & render path](#5-read--render-path)
6. [Key invariants](#6-key-invariants)
7. [Validation rules](#7-validation-rules)
8. [Verification guide](#8-verification-guide)
9. [Writer integration roadmap](#9-writer-integration-roadmap)

---

## 1. What is the Truth File system?

The Truth File system is the **single source of truth** for everything the
LLM needs to know about a project's evolving state: where characters are,
what resources they have, what hooks are pending, what subplots are
active, which emotions they're carrying, who knows whom.

Before this system, those facts were scattered across at least five
storage surfaces:

- `data/foreshadowing/<pid>.json` for plot hooks
- `data/characters/*.json` for character cards (with `current_location`,
  `mood`, `relationships`)
- L4 `episodic_events` table for foreshadow status
- L2 `chapter_summaries.character_states_json` for per-chapter mood snapshots
- ad-hoc fields scattered through `worldbook` and `knowledge_injection`

Three problems followed:

1. **The LLM couldn't see consistent context**. Each prompt builder had
   to bridge these surfaces independently. Hooks went stale; ledger drift
   went undetected; spoilers leaked.
2. **There was no contract for state mutation**. Writers wrote freeform
   text; downstream consumers parsed it heuristically. Numerical
   reconciliations (i.e. "Zhang Yuan's qi dropped from 80 to 50 because
   he used the cloud-breaking sword") were not enforced to be closed.
3. **There was no audit trail**. You couldn't ask "when did this hook
   become pressured?" or "what was the ledger at chapter 5?".

The Truth File system replaces this with one storage surface (SQLite),
one mutation contract (`TruthDeltas` Pydantic models), one validation
layer (12 cross-file rules), and one read API (`TruthFileStore.query_*`
+ markdown renderer).

---

## 2. The seven truth files

| # | Truth File | Meaning | SQLite Table | Migration Source |
|---|---|---|---|---|
| 1 | `current_state` | SPO triples about characters / world facts with chapter validity windows | `truth_current_state` | `character_cards` (location, mood) |
| 2 | `particle_ledger` | Append-only ledger of resource/item changes with closed equations | `character_ledger` | none (built by Writer Phase 2) |
| 3 | `pending_hooks` | Plot hooks with status state machine (open / progressing / pressured / near_payoff / resolved / abandoned) | `pending_hooks` + `hook_events` | `foreshadowing.json`, `episodic_events` (planted) |
| 4 | `chapter_summaries` | Per-chapter recap + key events + mood | `chapter_summaries` (re-used from `creation_schema.py`) | already exists |
| 5 | `subplot_board` | Parallel narrative threads with status (setup / building / climax / resolution / dormant) | `subplot_threads` | none (optional LLM extraction) |
| 6 | `emotional_arcs` | Per-character emotion transitions per chapter | `emotion_arcs` | `chapter_summaries.character_states_json` deltas |
| 7 | `character_matrix` | Pairwise relationships (A's view of B) with sentiment & trust scores | `character_relations` | `character_cards.relationships` |

All seven are exposed through one `TruthFileKind` enum (`knowledge/truth/schemas.py`).

The `chapter_summaries` table is **re-used**, not duplicated. The
truth file system just adapts the existing L2 buffer.

Two supporting tables exist:

- `hook_events` — audit log of every hook transition (new / mention /
  progress / resolve / abandon)
- `truth_apply_log` — idempotency log: `(project_id, deltas_hash)` is
  unique, so re-applying the same `TruthDeltas` is a no-op

---

## 3. Architecture

Four layers, each a single Python file:

```
+--------------------------------------------------------------+
|                       Callers                                |
|   (Writer, Evaluator, Scene Director, migrate CLI, UI ...)   |
+--------------------------------------------------------------+
            |                              ^
            |  apply_deltas(TruthDeltas)   |  render_for_prompt()
            v                              |
+--------------------------------------------------------------+
| Layer 4 - Presentation                                        |
|   knowledge/truth/markdown_renderer.py                              |
|     render_current_state, render_pending_hooks, ...           |
|     7 renderers + YAML frontmatter + char-budget truncation   |
+--------------------------------------------------------------+
            |                              ^
            v                              |
+--------------------------------------------------------------+
| Layer 3 - Validation                                          |
|   knowledge/truth/validators.py                                     |
|     validate_deltas(): 12 cross-file rules                    |
|     validate_state(): 2 state-audit rules + table stats       |
|     Layer 1: within-delta    (no DB)                          |
|     Layer 2: character xref  (caller-supplied set)            |
|     Layer 3: DB-aware        (read-only queries)              |
+--------------------------------------------------------------+
            |                              ^
            v                              |
+--------------------------------------------------------------+
| Layer 2 - Service                                             |
|   knowledge/truth/store.py                                          |
|     TruthFileStore.apply_deltas (atomic transaction)          |
|     TruthFileStore.query_*       (8 query methods)            |
|     TruthFileStore.render_*      (3 render methods)           |
|     _recompute_pressure          (post-apply pressure scan)   |
|   knowledge/truth/sql.py                                            |
|     Parameterized SQL string constants                        |
+--------------------------------------------------------------+
            |                              ^
            v                              |
+--------------------------------------------------------------+
| Layer 1 - Storage                                             |
|   database/truth_schema.py     (8 tables + DDL)               |
|   knowledge/truth/schemas.py         (Pydantic v2 frozen models)    |
|   config/truth_files.yaml      (thresholds + enum mappings)   |
+--------------------------------------------------------------+
                       |
                       v
                +-----------------+    +-----------------------+
                | SQLite (truth)  |==>>| Markdown view (export)|
                |                 |    | data/truth_files/     |
                |                 |    |   {project_id}/       |
                | (canonical)     |    | (not read back)       |
                +-----------------+    +-----------------------+
```

### Migration tooling

```
knowledge/truth/migrate.py         - 4 source-specific migrators + orchestrator
scripts/migrate_to_truth_files.py - CLI entry point
```

---

## 4. End-to-end data flow

### Write path: `apply_deltas`

```
LLM (Writer Phase 2, future)                  Caller (Writer / migration / UI)
        |                                                |
        v                                                v
  TruthDeltas                                  store.apply_deltas(
  (Pydantic, frozen, 7 sub-lists)                deltas,
        |                                        validate=True,
        |                                        known_characters=...,
        |                                        allow_backfill=False)
        +--------------------------------+
                                         |
                                         v
+-------------------------------------------------------------------+
| 1. _hash_deltas                                                   |
|    sha256(json.dumps(deltas.model_dump(mode="json"),              |
|                       sort_keys=True))                            |
+-------------------------------------------------------------------+
                                         |
                                         v
+-------------------------------------------------------------------+
| 2. SELECT FROM truth_apply_log                                    |
|    WHERE project_id=? AND deltas_hash=?                           |
|                                                                   |
|    HIT  -> return cached ApplyResult (idempotent_hit=True)        |
|    MISS -> continue                                               |
+-------------------------------------------------------------------+
                                         |
                                         v
+-------------------------------------------------------------------+
| 3. validators.validate_deltas (if validate=True)                  |
|    Layer 1 (no DB):                                               |
|      - hook.no_duplicate_id                                       |
|      - ledger.closed_equation                                     |
|      - state.no_conflicting_triple                                |
|    Layer 2 (known_characters):                                    |
|      - xref.character_in_emotion                                  |
|      - xref.character_in_relation                                 |
|    Layer 3 (DB queries):                                          |
|      - chapter.monotonic    (suppressible by allow_backfill)      |
|      - hook.no_orphan_progress                                    |
|      - hook.transition_valid                                      |
|      - ledger.matches_current                                     |
|      - xref.hook_in_subplot                                       |
|      - relation.symmetric_sentiment_drift  (warning)              |
|      - subplot.hook_resolved_subplot_should_advance  (info)       |
|                                                                   |
|    Any error-severity issue -> abort, return success=False        |
+-------------------------------------------------------------------+
                                         |
                                         v
+-------------------------------------------------------------------+
| 4. BEGIN TRANSACTION                                              |
|    a. _apply_state_patches                                        |
|         For each StatePatch:                                      |
|           - SUPERSEDE_SPO closes old triples for                  |
|             (subject, predicate) by setting valid_to_chapter      |
|             and superseded_by                                     |
|           - INSERT_SPO inserts the new triple                     |
|         (or INVALIDATE_SPO just closes window for "invalidate")   |
|    b. _apply_reconciliations                                      |
|         INSERT_LEDGER (append-only)                               |
|    c. _apply_hook_deltas                                          |
|         new      -> INSERT_HOOK   + hook_event(new)               |
|         mention  -> UPDATE_HOOK_MENTION + hook_event(mention)     |
|         progress -> UPDATE_HOOK_PROGRESS + hook_event(progress)   |
|         resolve  -> UPDATE_HOOK_STATUS + hook_event(resolve)      |
|         abandon  -> UPDATE_HOOK_STATUS + hook_event(abandon)      |
|    d. _apply_chapter_summary                                      |
|         UPSERT_CHAPTER_SUMMARY (on conflict update)               |
|    e. _apply_subplots                                             |
|         INSERT_SUBPLOT or UPDATE_SUBPLOT                          |
|    f. _apply_emotion_arcs                                         |
|         INSERT_EMOTION_ARC (append-only)                          |
|    g. _apply_relations                                            |
|         UPSERT_RELATION on UNIQUE(project_id, A, B)               |
+-------------------------------------------------------------------+
                                         |
                                         v
+-------------------------------------------------------------------+
| 5. _recompute_pressure(conn, current_chapter)                     |
|    Scan: SELECT FROM pending_hooks                                |
|          WHERE project_id=? AND status IN ('open','progressing')  |
|    For each: if current_chapter - last_advance_chapter            |
|              >= pressure_threshold then status='pressured'        |
|              (threshold default 5; importance A default 3)        |
+-------------------------------------------------------------------+
                                         |
                                         v
+-------------------------------------------------------------------+
| 6. INSERT INTO truth_apply_log                                    |
|    (apply_id, project_id, chapter_num, deltas_hash,               |
|     applied_counts_json, cross_ref_issues_json)                   |
+-------------------------------------------------------------------+
                                         |
                                         v
+-------------------------------------------------------------------+
| 7. COMMIT  (or any sub-step failure -> ROLLBACK)                  |
+-------------------------------------------------------------------+
                                         |
                                         v
                              ApplyResult(
                                success=True,
                                applied_counts={...},
                                cross_ref_issues=[...],
                                deltas_hash="...",
                                idempotent_hit=False)
```

Failure modes:

- Any error-severity validator issue: aborts before step 4. Nothing
  touches the DB. `success=False`, the issues are in
  `cross_ref_issues`.
- Any `sqlite3.Error` during step 4: rolls back the entire transaction.
  `success=False`, `sqlite_errors` is populated, the apply log is **not**
  written (so the same deltas can be retried).
- Warning / info severity issues: do not abort. They are returned in
  `cross_ref_issues` but the apply proceeds and commits.

---

## 5. Read & render path

There are three reading APIs, with progressively higher levels of
abstraction:

### a) Raw queries — `store.query_*`

Eight methods returning `list[dict]`:

```python
store.query_current_state(subject=None, predicate=None, chapter_num=None)
store.query_ledger(character, key=None, as_of_chapter=None)    # scalar
store.list_ledger_entries(character=None, key=None)
store.query_pending_hooks(status=None, importance=None)
store.list_pressured_hooks(current_chapter)
store.query_chapter_summary(chapter_num)
store.list_chapter_summaries(limit=None)
store.query_subplot_threads(status=None)
store.query_emotion_arc(character=None, since_chapter=None)
store.query_character_matrix(character=None)
```

Use these when you want the raw data — e.g. PostWriteValidator checking
`query_ledger("Zhang Yuan", "qi", as_of_chapter=N-1) == 80`.

### b) Per-file markdown — `store.render_for_prompt`

```python
md = store.render_for_prompt(
    TruthFileKind.pending_hooks,
    chapter_num=5,
    characters=["Zhang Yuan", "Li Qingyi"],   # filter
    budget_tokens=2000,                         # ~4000 chars
)
```

Use when you want one truth file as a Markdown string for prompt
injection. Each kind has its own renderer in
`knowledge/truth/markdown_renderer.py` with hand-tuned formatting.

The `characters` filter narrows the output for relevance — e.g. for
`emotional_arcs` it returns only the listed characters.

### c) Bundle for whole-prompt — `store.render_bundle_for_prompt`

```python
bundle = store.render_bundle_for_prompt(
    chapter_num=12,
    characters=["Zhang Yuan", "Li Qingyi"],
    kinds=None,        # None = all 7
    budgets=None,      # None = per-kind defaults from config
)
# bundle is {TruthFileKind: str}; feed each into a RAG context block
```

Use this from the Writer / RAG context assembler — one call returns all
the truth file blocks ready to slot into the prompt template.

### d) File export — `store.export_markdown`

```python
paths = store.export_markdown(
    output_dir=None,                # None = data/truth_files/{pid}/
    kinds=None,                     # None = all 7
    chapter_pointer=None,           # None = LATEST_APPLIED_CHAPTER
)
# paths is {TruthFileKind: Path}
```

Use this for human inspection or Git diffs. The Markdown header says:

```
<!-- Note: this file is a view of the canonical state held in SQLite.
     Hand edits are NOT read back. -->
```

Editing exported files has no effect on the system. SQLite is the
authority.

---

## 6. Key invariants

| # | Invariant | Enforced by |
|---|---|---|
| 1 | **Atomicity** — a `TruthDeltas` bundle either all applies or none does | `apply_deltas` wraps steps 4-6 in one SQLite transaction; any error -> `conn.rollback()` |
| 2 | **Immutability of history** — old facts are never deleted, only superseded | `truth_current_state.superseded_by` + `valid_to_chapter` window-close; ledger / emotion_arcs / hook_events are append-only |
| 3 | **Idempotency** — applying the same `TruthDeltas` twice has zero side effects | `UNIQUE(project_id, deltas_hash)` on `truth_apply_log` + step 2 lookup |
| 4 | **Chapter monotonicity** — applies must move forward in chapter time | `chapter.monotonic` validator rule, defaulting on; `allow_backfill=True` opens it for migration |
| 5 | **Hook state-machine closure** — terminal hooks (resolved / abandoned) reject all future mutations | `hook.transition_valid` validator rule |
| 6 | **Numerical closure** — every ledger entry satisfies `old + delta == new` | `ledger.closed_equation` validator rule (within-delta) + `ledger.matches_current` (vs DB) |

---

## 7. Validation rules

All in `knowledge/truth/validators.py`. Three layers, executed in cheap-to-expensive order:

### Layer 1 — within-delta (no DB access)

| Rule ID | Severity | What it catches |
|---|---|---|
| `hook.no_duplicate_id` | error | Same `hook_id` repeated in this delta bundle |
| `ledger.closed_equation` | error | `old_value + delta != new_value` for `add` / `subtract` ops |
| `state.no_conflicting_triple` | warning | Same `(subject, predicate, chapter)` has both upsert and invalidate in the bundle |

### Layer 2 — known_characters cross-ref

These run only if the caller supplies a `known_characters` set (or the
resolver succeeds via `data_api._list("characters", project_id)`). If the
set cannot be resolved, the rules degrade to no-op rather than firing
false positives.

| Rule ID | Severity | What it catches |
|---|---|---|
| `xref.character_in_emotion` | error | `EmotionArcEntry.character` not in known set |
| `xref.character_in_relation` | error | `RelationUpdate.character_a/b` not in known set |

### Layer 3 — DB-aware (read-only queries)

| Rule ID | Severity | What it catches |
|---|---|---|
| `chapter.monotonic` | error | `deltas.chapter_num < max(truth_apply_log.chapter_num)`; suppressible via `allow_backfill=True` |
| `hook.no_orphan_progress` | error | `action != "new"` targets a `hook_id` that doesn't exist in DB and isn't created in this batch |
| `hook.transition_valid` | error | Any action against a hook whose current status is `resolved` or `abandoned` |
| `ledger.matches_current` | error | `old_value` doesn't match the ledger's current value for `(character, key)` |
| `xref.hook_in_subplot` | error | `SubplotUpdate.related_hook_ids` references a hook neither in DB nor created in this batch |
| `relation.symmetric_sentiment_drift` | warning | `(A,B)` sentiment differs from `(B,A)` by more than threshold (default 40) |
| `subplot.hook_resolved_subplot_should_advance` | info | A hook resolved while a referencing subplot stayed in `setup` |

### State-audit rules (no deltas)

Run by `validate_state()` against the persisted DB. Useful for
post-migration sanity check.

| Rule ID | Severity | What it catches |
|---|---|---|
| `audit.orphaned_subplot_hooks` | warning | Subplots reference hooks that were deleted |
| `audit.emotion_arc_unknown_character` | warning | Persisted emotion arcs reference characters no longer in cards |

### Behavior on issues

| Severity | Behavior in `apply_deltas(validate=True)` |
|---|---|
| `error` | Aborts before step 4. No DB writes. `ApplyResult.success=False`. |
| `warning` | Returned in `cross_ref_issues` but the apply proceeds and commits. |
| `info` | Same as warning — informational nudges. |

---

## 8. Verification guide

Four progressively manual layers.

### Layer 1 — Automated tests (daily)

```bash
# All 114 truth tests
python3 -m pytest tests/truth/ -v

# Per module
python3 -m pytest tests/truth/test_schemas.py             #  15 tests
python3 -m pytest tests/truth/test_truth_schema_ddl.py    #   7 tests
python3 -m pytest tests/truth/test_store_apply.py         #  17 tests
python3 -m pytest tests/truth/test_validators.py          #  26 tests
python3 -m pytest tests/truth/test_render_and_query.py    #  31 tests
python3 -m pytest tests/truth/test_migrate.py             #  16 tests
python3 -m pytest tests/truth/integration/ -v             #   2 tests
```

Pre-existing unrelated failures in the wider suite (6 cases involving
embedding-cluster deps and `pytest-asyncio`) are not regressions.

### Layer 2 — CLI exercise against a real project

```bash
# Step 1: pick a project that has migratable data
ls data/projects/ 2>/dev/null
ls data/foreshadowing/ 2>/dev/null
ls data/characters/ 2>/dev/null

# Step 2: dry-run migration (no writes, JSON report)
python3 -m scripts.migrate_to_truth_files <project_id> --dry-run --json

# Step 3: real migration
python3 -m scripts.migrate_to_truth_files <project_id>

# Step 4: re-run to confirm idempotency (every apply_result should have
# idempotent_hit=true)
python3 -m scripts.migrate_to_truth_files <project_id> --json \
  | grep idempotent_hit

# Step 5: export markdown views
python3 -c "
from rag.truth.store import TruthFileStore
paths = TruthFileStore('<project_id>').export_markdown()
for k, p in paths.items():
    print(k.value, '->', p)
"

# Step 6: human inspection
ls data/truth_files/<project_id>/
cat data/truth_files/<project_id>/pending_hooks.md
cat data/truth_files/<project_id>/character_matrix.md
cat data/truth_files/<project_id>/current_state.md
```

### Layer 3 — Programmatic smoke test (REPL)

```python
from rag.truth.store import TruthFileStore
from rag.truth.schemas import (
    TruthDeltas, HookDelta, NumericalReconciliation, TruthFileKind,
)

store = TruthFileStore("<project_id>")

# Apply a minimal delta
res = store.apply_deltas(TruthDeltas(
    chapter_num=99,
    hook_deltas=[HookDelta(description="smoke test hook", action="new")],
), validate=True)
assert res.success
print("applied_counts:", res.applied_counts)
print("deltas_hash:", res.deltas_hash)

# Query
hooks = store.query_pending_hooks(status="open")
print(f"open hooks: {len(hooks)}")

# Render
md = store.render_for_prompt(TruthFileKind.pending_hooks, chapter_num=99)
print(md[:500])

# Idempotency
res2 = store.apply_deltas(TruthDeltas(
    chapter_num=99,
    hook_deltas=[HookDelta(description="smoke test hook", action="new")],
))
assert res2.idempotent_hit is True
print("idempotency: OK")
```

### Layer 4 — Failure-path smoke test

```python
# A bad ledger delta must be rejected by ledger.closed_equation
bad = TruthDeltas(chapter_num=100, particle_reconciliations=[
    NumericalReconciliation(
        character="A", resource="X",
        old_value=80, operation="subtract", delta=-30, new_value=60,  # 80-30=50
        reason="x", in_text_evidence="y",
    ),
])
res = store.apply_deltas(bad, validate=True)
assert res.success is False
rules = [i.rule_id for i in res.cross_ref_issues]
assert "ledger.closed_equation" in rules
print("gate OK:", rules)
```

### Layer 5 — Emoji-free repo check

```bash
grep -nP '[\x{1F300}-\x{1FFFF}\x{2600}-\x{27BF}\x{2300}-\x{23FF}]' \
    knowledge/truth/*.py \
    scripts/migrate_to_truth_files.py \
    tests/truth/*.py \
    tests/truth/integration/*.py
# expect: no output
```

---

## 9. Writer integration roadmap

The Truth File system was built so Writer can plug in without changing
internals. The plug points are:

| Writer phase / component | Truth File API used |
|---|---|
| **Phase 1 — prompt assembly** | `store.render_bundle_for_prompt(chapter_num=N, characters=[...])` returns 7 Markdown blocks to inject into the RAG context |
| **Phase 2 — settlement output** | Writer LLM produces a JSON block parseable into `TruthDeltas`; caller invokes `store.apply_deltas(deltas, validate=True, known_characters=...)` |
| **PostWriteValidator** | `store.query_ledger(char, key, as_of_chapter=N-1)` returns the expected ledger anchor; `apply_deltas` then enforces `ledger.matches_current` |
| **Pressured-hook injection** | `store.list_pressured_hooks(current_chapter=N)` returns hooks the Writer should address this chapter |
| **Spoiler-filtered context** | `store.query_pending_hooks(status="open")` plus an `is_spoiler` flag (future field) |
| **Audit gate** | After `apply_deltas`, check `ApplyResult.cross_ref_issues` for any `severity == "error"`; that is the gate signal |

The contracts above are stable. Writer's internal structure
(`PromptComposer`, `Phase2Settlement`, `PostWriteValidator`, `AuditGate`)
can change freely without touching `knowledge/truth/`.

---

## Files

| File | Role |
|---|---|
| `knowledge/truth/__init__.py` | Public exports |
| `knowledge/truth/schemas.py` | Pydantic v2 frozen models for all deltas and results |
| `knowledge/truth/store.py` | `TruthFileStore` — single entry point |
| `knowledge/truth/sql.py` | Parameterized SQL strings |
| `knowledge/truth/validators.py` | 12 cross-file rules + 2 audit rules |
| `knowledge/truth/markdown_renderer.py` | 7 renderers + frontmatter helper |
| `knowledge/truth/migrate.py` | 4 source-specific migrators + orchestrator |
| `database/truth_schema.py` | 8-table DDL + `ensure_truth_tables` |
| `config/truth_files.yaml` | Thresholds + enum mappings + render budgets |
| `scripts/migrate_to_truth_files.py` | CLI: `python -m scripts.migrate_to_truth_files <pid>` |
| `tests/truth/` | 114 tests across 6 files + integration |
