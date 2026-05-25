"""Migration from existing scattered state into the Truth File system.

Sources:
  - foreshadowing.json    -> pending_hooks
  - episodic_events       -> pending_hooks (deduplicated by description)
  - character_cards       -> truth_current_state (location/mood) +
                              character_relations
  - chapter_summaries     -> emotion_arcs (heuristic diff between adjacent
                              chapters' character_states_json)

Each migration emits one or more TruthDeltas batches and applies them
with ``allow_backfill=True``. Idempotency is preserved via the standard
truth_apply_log hash; re-running is safe.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

from knowledge.truth.schemas import (
    ApplyResult, ChapterSummaryDelta, EmotionArcEntry, HookDelta,
    HookImportance, RelationUpdate, StatePatch, TruthDeltas,
)
from knowledge.truth.store import TruthFileStore

logger = logging.getLogger("inkoctobot.knowledge.truth.migrate")


MigrationSource = Literal[
    "foreshadowing_json", "episodic_events", "character_cards",
    "chapter_summaries",
]


@dataclass
class MigrationReport:
    """Per-source outcome of a migration."""
    source: MigrationSource
    batches_applied: int = 0
    rows_emitted: dict[str, int] = field(default_factory=dict)
    apply_results: list[ApplyResult] = field(default_factory=list)
    skipped_reason: str = ""

    @property
    def success(self) -> bool:
        """Skipped (no source data) is success; only failed applies are not."""
        return all(r.success for r in self.apply_results)


# ──────────────────────────────────────────────────────────────
# Helpers — locate source files
# ──────────────────────────────────────────────────────────────

def _foreshadowing_json_path(project_id: str) -> Path | None:
    """Returns path to ``data/foreshadowing/<safe_id>.json`` or None."""
    try:
        from ui.backend.app.routers.data_api import _col, _safe_id
        return _col("foreshadowing") / f"{_safe_id(project_id)}.json"
    except Exception:
        try:
            import config as _cfg
            return (
                Path(_cfg.BASE_DIR) / "data" / "foreshadowing" / f"{project_id}.json"
            )
        except Exception:
            return Path("data") / "foreshadowing" / f"{project_id}.json"


def _character_rows(project_id: str) -> list[dict[str, Any]]:
    """Best-effort lookup of character cards via data_api."""
    try:
        from ui.backend.app.routers.data_api import _list
        return _list("characters", filter_key="project_id",
                     filter_value=project_id)
    except Exception as exc:
        logger.debug("character_rows fallback empty: %s", exc)
        return []


def _config_relation_map() -> dict[str, dict[str, int]]:
    """Load relation_enum_to_score from config/truth_files.yaml."""
    try:
        import config as _cfg
        cfg = _cfg._load_yaml("truth_files.yaml") or {}
        return cfg.get("relation_enum_to_score") or {}
    except Exception:
        return {}


# ──────────────────────────────────────────────────────────────
# 1. foreshadowing.json → pending_hooks
# ──────────────────────────────────────────────────────────────

def migrate_foreshadowing_json(
    store: TruthFileStore, *, dry_run: bool = False,
) -> MigrationReport:
    """Each foreshadowing item becomes one HookDelta(action='new').

    Heuristics:
      - origin_chapter = lowest int found in chapter_ids; default 1
      - expected_payoff_chapter = highest int; default origin+5
      - importance = A if the title contains '核心/关键/重要', else B
      - description = title; if title empty, content
    """
    report = MigrationReport(source="foreshadowing_json")
    path = _foreshadowing_json_path(store.project_id)
    if path is None or not path.exists():
        report.skipped_reason = f"no file at {path}"
        return report

    try:
        data = json.loads(path.read_text("utf-8"))
    except Exception as exc:
        report.skipped_reason = f"parse error: {exc}"
        return report

    items = data.get("items") or []
    if not items:
        report.skipped_reason = "items[] empty"
        return report

    # Group hooks by origin_chapter so each TruthDeltas applies to one chapter.
    by_chapter: dict[int, list[HookDelta]] = {}
    for it in items:
        chapter_ids = it.get("chapter_ids") or []
        ch_ints = [_first_int(ch) for ch in chapter_ids]
        ch_ints = [c for c in ch_ints if c is not None]
        origin = min(ch_ints) if ch_ints else 1
        payoff = max(ch_ints) if ch_ints else origin + 5
        title = (it.get("title") or "").strip()
        content = (it.get("content") or "").strip()
        description = title or content or "(unnamed)"
        importance = HookImportance.A if any(
            kw in title for kw in ("核心", "关键", "重要", "主线")
        ) else HookImportance.B
        # Reproducible hook_id from item id (or hashed description) so
        # re-running migration finds the same id.
        hook_id = f"fmig_{_safe_token(it.get('id') or description)[:24]}"
        by_chapter.setdefault(origin, []).append(
            HookDelta(
                hook_id=hook_id, description=description, action="new",
                importance=importance, expected_payoff_chapter=payoff,
                evidence=(content[:200] if content else ""),
            )
        )

    for chapter_num, hooks in sorted(by_chapter.items()):
        deltas = TruthDeltas(chapter_num=chapter_num, hook_deltas=hooks)
        if dry_run:
            report.batches_applied += 1
            continue
        res = store.apply_deltas(deltas, allow_backfill=True)
        report.apply_results.append(res)
        report.batches_applied += 1

    report.rows_emitted["hook_deltas"] = sum(
        len(v) for v in by_chapter.values()
    )
    return report


# ──────────────────────────────────────────────────────────────
# 2. episodic_events.foreshadow_status='planted' → pending_hooks
#    (deduplicated against already-migrated hooks)
# ──────────────────────────────────────────────────────────────

def migrate_episodic_events(
    store: TruthFileStore, *, dry_run: bool = False,
) -> MigrationReport:
    report = MigrationReport(source="episodic_events")
    rows: list[dict[str, Any]] = []
    with sqlite3.connect(store.db_path) as conn:
        try:
            rows = conn.execute(
                """SELECT event_id, chapter_num, description, importance,
                          foreshadow_target_chapter
                   FROM episodic_events
                   WHERE project_id = ? AND foreshadow_status='planted'""",
                (store.project_id,),
            ).fetchall()
        except sqlite3.Error as exc:
            report.skipped_reason = f"episodic_events query failed: {exc}"
            return report

        if not rows:
            report.skipped_reason = "no planted foreshadowing rows"
            return report

        # Existing migrated descriptions → skip duplicates
        existing_desc = {
            r[0] for r in conn.execute(
                "SELECT description FROM pending_hooks WHERE project_id=?",
                (store.project_id,),
            )
        }

    by_chapter: dict[int, list[HookDelta]] = {}
    for event_id, chapter_num, description, importance_int, target in rows:
        if description in existing_desc:
            continue
        importance = (
            HookImportance.A if (importance_int or 3) >= 4
            else HookImportance.B
        )
        hook_id = f"emig_{_safe_token(event_id)[:24]}"
        by_chapter.setdefault(chapter_num or 1, []).append(
            HookDelta(
                hook_id=hook_id, description=description, action="new",
                importance=importance,
                expected_payoff_chapter=target,
                evidence="migrated from episodic_events",
            )
        )

    for chapter_num, hooks in sorted(by_chapter.items()):
        deltas = TruthDeltas(chapter_num=chapter_num, hook_deltas=hooks)
        if dry_run:
            report.batches_applied += 1
            continue
        res = store.apply_deltas(deltas, allow_backfill=True)
        report.apply_results.append(res)
        report.batches_applied += 1

    report.rows_emitted["hook_deltas"] = sum(
        len(v) for v in by_chapter.values()
    )
    if report.batches_applied == 0 and not report.skipped_reason:
        report.skipped_reason = "all events already present in pending_hooks"
    return report


# ──────────────────────────────────────────────────────────────
# 3. character_cards → truth_current_state + character_relations
# ──────────────────────────────────────────────────────────────

def migrate_character_cards(
    store: TruthFileStore, *, dry_run: bool = False,
) -> MigrationReport:
    report = MigrationReport(source="character_cards")
    rows = _character_rows(store.project_id)
    if not rows:
        report.skipped_reason = "no character cards found"
        return report

    relation_map = _config_relation_map()
    state_patches: list[StatePatch] = []
    relation_updates: list[RelationUpdate] = []

    for r in rows:
        name = (r.get("name") or "").strip()
        if not name:
            continue
        loc = (r.get("current_location") or "").strip()
        if loc:
            state_patches.append(StatePatch(
                subject=name, predicate="在", object=loc,
                action="upsert", valid_from_chapter=1,
            ))
        mood = (r.get("mood") or "").strip()
        if mood:
            state_patches.append(StatePatch(
                subject=name, predicate="情绪", object=mood,
                action="upsert", valid_from_chapter=1,
            ))

        # Two relation formats supported:
        #   - list of {target_name, label}
        #   - dict {name: description}
        rels = r.get("relationships")
        if isinstance(rels, list):
            for rel in rels:
                tgt = (rel.get("target_name") or "").strip()
                label = (rel.get("label") or "").strip()
                if not tgt or not label:
                    continue
                mapping = relation_map.get(label, {})
                relation_updates.append(RelationUpdate(
                    character_a=name, character_b=tgt,
                    relation_type=label,
                    sentiment_score=mapping.get("sentiment", 0),
                    trust_level=mapping.get("trust", 30),
                ))
        elif isinstance(rels, dict):
            for tgt, label in rels.items():
                label = (label or "").strip()
                if not label:
                    continue
                mapping = relation_map.get(label, {})
                relation_updates.append(RelationUpdate(
                    character_a=name, character_b=str(tgt),
                    relation_type=label,
                    sentiment_score=mapping.get("sentiment", 0),
                    trust_level=mapping.get("trust", 30),
                ))

    if not state_patches and not relation_updates:
        report.skipped_reason = "no migratable fields found on cards"
        return report

    deltas = TruthDeltas(
        chapter_num=1,
        current_state_patches=state_patches,
        character_relation_updates=relation_updates,
    )
    report.rows_emitted = {
        "state_patches": len(state_patches),
        "relation_updates": len(relation_updates),
    }
    if not dry_run:
        # Skip validation on migration — relation pairs may refer to
        # characters whose cards we just listed; resolver may not be
        # available in CLI context.
        res = store.apply_deltas(deltas, allow_backfill=True)
        report.apply_results.append(res)
    report.batches_applied = 1
    return report


# ──────────────────────────────────────────────────────────────
# 4. chapter_summaries → emotion_arcs (heuristic diff)
# ──────────────────────────────────────────────────────────────

def migrate_chapter_summaries(
    store: TruthFileStore, *, dry_run: bool = False,
) -> MigrationReport:
    """Walk chapter_summaries in order; for each pair of adjacent chapters,
    look at character_states_json deltas — emit EmotionArcEntry where a
    'mood' key changed."""
    report = MigrationReport(source="chapter_summaries")
    with sqlite3.connect(store.db_path) as conn:
        rows = conn.execute(
            """SELECT chapter_num, character_states_json
               FROM chapter_summaries WHERE project_id=? AND is_active=1
               ORDER BY chapter_num""",
            (store.project_id,),
        ).fetchall()
    if len(rows) < 2:
        report.skipped_reason = "fewer than 2 chapter summaries"
        return report

    states_by_chap: list[tuple[int, dict[str, Any]]] = []
    for ch, raw in rows:
        try:
            states_by_chap.append((ch, json.loads(raw or "{}")))
        except Exception:
            states_by_chap.append((ch, {}))

    arc_entries_by_chap: dict[int, list[EmotionArcEntry]] = {}
    for (ch_prev, prev), (ch_curr, curr) in zip(
        states_by_chap, states_by_chap[1:],
    ):
        # Detect per-character mood transitions. ``character_states_json``
        # has uneven shapes across the codebase; cover both
        # {"char1": {"mood": "..."}} and the simpler {"mood": "..."}.
        if not isinstance(prev, dict) or not isinstance(curr, dict):
            continue
        for char_name, curr_state in curr.items():
            if not isinstance(curr_state, dict):
                continue
            curr_mood = curr_state.get("mood")
            if not curr_mood:
                continue
            prev_state = prev.get(char_name) or {}
            prev_mood = (
                prev_state.get("mood")
                if isinstance(prev_state, dict)
                else None
            )
            if prev_mood and prev_mood != curr_mood:
                arc_entries_by_chap.setdefault(ch_curr, []).append(
                    EmotionArcEntry(
                        character=char_name,
                        from_state=str(prev_mood),
                        to_state=str(curr_mood),
                        trigger="migrated from chapter_summaries",
                    )
                )

    if not arc_entries_by_chap:
        report.skipped_reason = "no per-character mood transitions detected"
        return report

    total = 0
    for chapter_num, arcs in sorted(arc_entries_by_chap.items()):
        deltas = TruthDeltas(chapter_num=chapter_num, emotion_arc_entries=arcs)
        if not dry_run:
            res = store.apply_deltas(deltas, allow_backfill=True)
            report.apply_results.append(res)
        report.batches_applied += 1
        total += len(arcs)
    report.rows_emitted["emotion_arc_entries"] = total
    return report


# ──────────────────────────────────────────────────────────────
# Orchestration
# ──────────────────────────────────────────────────────────────

_DEFAULT_SOURCES: tuple[MigrationSource, ...] = (
    "foreshadowing_json",
    "episodic_events",
    "character_cards",
    "chapter_summaries",
)


def migrate_all(
    store: TruthFileStore,
    *,
    sources: Iterable[MigrationSource] | None = None,
    dry_run: bool = False,
) -> dict[MigrationSource, MigrationReport]:
    """Run all (or a subset of) migrations. Returns a per-source report."""
    if sources is None:
        sources = _DEFAULT_SOURCES
    fns = {
        "foreshadowing_json": migrate_foreshadowing_json,
        "episodic_events":    migrate_episodic_events,
        "character_cards":    migrate_character_cards,
        "chapter_summaries":  migrate_chapter_summaries,
    }
    out: dict[MigrationSource, MigrationReport] = {}
    for src in sources:
        if src not in fns:
            logger.warning("unknown migration source: %s", src)
            continue
        out[src] = fns[src](store, dry_run=dry_run)
    return out


# ──────────────────────────────────────────────────────────────
# Small helpers
# ──────────────────────────────────────────────────────────────

def _first_int(s: Any) -> int | None:
    """Pull the first integer out of e.g. 'ch1', 'chapter_5', '12'."""
    if isinstance(s, int):
        return s
    if not isinstance(s, str):
        return None
    import re
    m = re.search(r"\d+", s)
    return int(m.group(0)) if m else None


def _safe_token(s: Any) -> str:
    """Compact ASCII-safe token from arbitrary string for use in stable IDs."""
    if s is None:
        return "x"
    s = str(s)
    import hashlib
    return hashlib.md5(s.encode("utf-8")).hexdigest()


__all__ = [
    "MigrationReport", "MigrationSource",
    "migrate_foreshadowing_json", "migrate_episodic_events",
    "migrate_character_cards", "migrate_chapter_summaries",
    "migrate_all",
]
