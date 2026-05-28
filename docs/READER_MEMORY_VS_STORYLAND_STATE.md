# Memory Layers vs Truth Files — Conceptual Boundaries

> Companion to `docs/SCHEMA_REDESIGN.md`. The v2 redesign split
> redundant storage; this doc fixes the **conceptual** confusion that
> caused the redundancy in the first place.

## TL;DR

| Subsystem | Concern | Storage |
|---|---|---|
| **Truth Files** | **Structured state** — "what is true at chapter X" | 7 tables |
| **Memory Layers** | **Retrievable text** — "what was written near chapter X" | L1 in-proc + L2/L4 SQL + L3 vector |

If you can answer the question with an SQL SELECT, it's Truth.
If you need text similarity or "show me prose from that scene", it's Memory.

---

## Truth Files — the **state authority**

Use Truth Files for any question of the form:

- "Who currently owns the jade pendant?" → `truth_current_state` SPO
- "Is the foreshadow about Black Stone Trading Company resolved?" → `pending_hooks.status`
- "How many spirit stones does the protagonist have?" → `character_ledger`
- "What's the latest sentiment between A and B?" → `character_relations`
- "When did the emperor's mood shift from cold to warm?" → `emotion_arcs`
- "What's the official summary of chapter 17?" → `chapter_summaries`
- "Is the main mystery subplot in setup or climax?" → `subplot_threads`

Truth Files are **monotonic and audited**: every change is a `*Delta`
applied via `TruthFileStore.apply_deltas()` which is atomic, idempotent
(SHA-256 dedup via `truth_apply_log`), and validator-checked.

Agents write to Truth via TruthDeltas. Prompts read Truth via markdown
views from `markdown_renderer.py`.

---

## Memory Layers — the **retrievable corpus**

Use Memory for any question of the form:

- "Show me the current scene context" → **L1 ImmediateContext** (in-process)
- "Show me the recent 10 chapter summaries verbatim" → **L2 ChapterBuffer**
- "Find chapters that talked about a purple sword" → **L3 SemanticMemory** (vector)
- "Did character A and B ever meet? When?" → **L4 EpisodicTimeline** (SQL events)

Memory is **append-mostly free text**. L1 is volatile; L2 has a rolling
window (overflow → consolidator → Truth Files); L3 stores chapter
content for vector search; L4 keeps a log of significant events with
type/character tags so you can filter by SQL.

---

## What each layer **stores** (post-cleanup v2)

### L1 — `knowledge/memory/immediate.py`
**Owns**: scene context dict + chapter context dict, in process memory only.
**Reads from**: nothing (populated by agents during generation).
**Cleared by**: `start_scene()` / process restart.

### L2 — `knowledge/memory/chapter_buffer.py` + table `chapter_summaries`
**Owns**: per-chapter `summary_text` (300-500 chars Chinese), plus
`key_events_json` and `character_states_json` (these last two are
**Truth File #4 fields**, populated via `ChapterSummaryDelta` —
the L2 layer just reads them and doesn't write structured deltas itself).

**Authoritative for**: nothing — it's a view onto Truth File #4 with an
`is_active=1` window flag for "still in rolling context".

**Overflow**: when window exceeds budget, consolidator processes oldest
summaries to emit StatePatch/HookDelta/EmotionArcEntry deltas → Truth.
Then marks the summary `is_active=0`.

### L3 — `knowledge/memory/semantic_store.py` (ChromaDB)
**Owns**: chapter content as vector embeddings, for natural-language
similarity queries.

**Allowed `memory_type` values (post-cleanup)**:
- `chapter_content` — the primary case (vectorize per-chapter prose)
- `chapter_summary` — fallback when consolidator's JSON parse fails

**Removed `memory_type` values** (these previously double-stored Truth
state into ChromaDB):
- ~~`permanent_fact`~~ → use `truth_current_state` SPO
- ~~`foreshadowing`~~ → use `pending_hooks` description
- ~~`character_state`~~ → use `emotion_arcs` or `character_relations`
- ~~`setting`~~ → use `worldbook_entries.content` (already vectorizable separately if needed)

L3 is now **a single-purpose corpus index** rather than a structured
data leak.

### L4 — `knowledge/memory/episodic_timeline.py` + table `episodic_events`
**Owns**: a typed audit log of significant in-story events.

**Schema**:
```
event_id, project_id, chapter_num, scene_index,
event_type IN (plot, character_change, revelation,
               relationship_change, world_change),
description, characters_json, importance (1-5), created_at
```

**Removed in v2**:
- `foreshadow_status`, `foreshadow_target_chapter` → `pending_hooks`
- `causality_json` → **never actually read; dropped**

L4 is the "what happened in chapter X" reference; Truth Files are the
"what is true after chapter X" reference. They're complementary: L4
tells you the **change**, Truth tells you the **state**.

---

## Worldbook vs Characters — entity dedup

Two tables hold entity-like project data. They have overlapping shapes;
without a rule users put the same thing in both.

### Rule

**`characters` is the only place for individual people.**
Name, role, personality, background, appearance, speech style,
relationships — all on the character row. Even a character's home
location, family, secret identity belong here (in `background`,
`extra_json`, or `layer_b_json`).

**`worldbook_entries` is for non-character setting**: places,
organizations, rules, items, technology, factions, history, culture,
natural phenomena.

### Enforcement (this commit)

`worldbook_entries.category` gets a CHECK constraint with an allowed
set: `地点 / 组织 / 规则 / 物品 / 技术 / 文化 / 历史 / 自然 / 杂项`
(plus the legacy English aliases `place/organization/rule/item/tech/
culture/history/nature/misc/social_structure/hard_rules/factions` for
backward compat with existing data).

**Notably absent**: `角色`, `character`, `主角`, `配角`. The constraint
rejects writes with these categories, forcing the UI to push the user
to the characters page. (Existing rows with those categories would
fail the check; the migration coerces them to `杂项` with a warning.)

### What if a person and a setting overlap?

A faction leader IS both a person and a faction concept. The rule:
- The **person** lives in `characters` (name="X", background mentions
  faction).
- The **faction** lives in `worldbook_entries` (title="X 派", content
  describes the org).
- Link them via `characters.relationships_json` (target_name="X 派",
  label="掌门") or via Truth `character_relations`.

This duplicates a name, but separates a person's *biography* from an
org's *charter*. Generation prompts pull both blocks when relevant.

---

## What this means for the code

| File | Change |
|---|---|
| `storage/project_schema.py` | DROP COLUMN `episodic_events.causality_json`; ADD CHECK to `worldbook_entries.category` (allow set documented above) |
| `knowledge/memory/episodic_timeline.py` | Remove `causality` parameter from `add_event()` |
| `knowledge/memory/consolidator.py` | Stop writing `permanent_fact` / `foreshadowing` types to L3 (they were duplicates of Truth state). Keep the chapter_summary fallback path. |
| `knowledge/memory/semantic_store.py` | Document allowed memory_type values; deprecate `store_permanent_fact` / `store_character_state` (kept for backward compat but no-op'd) |
| `ui/backend/app/services/project_store.py` | `upsert_worldbook` coerces banned `角色`/`character` categories to `杂项` with a warning |
| `docs/MEMORY_VS_TRUTH.md` | This file |

Existing data is preserved; only the **write paths** for the
deprecated patterns are stopped. Old rows in L3 with deprecated
memory_type stay until ChromaDB collection is reset.
