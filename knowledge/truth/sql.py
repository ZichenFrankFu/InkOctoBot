"""SQL string constants for TruthFileStore.

Centralized to keep store.py focused on logic, and to make SQL diffs easy
to review. All statements are parameterized — never interpolate values.
"""
from __future__ import annotations


# ─────────── truth_current_state ───────────

INSERT_SPO = """
INSERT INTO truth_current_state (
    triple_id, project_id, subject, predicate, object,
    valid_from_chapter, confidence
) VALUES (?, ?, ?, ?, ?, ?, ?)
"""

# Mark all currently-valid triples with the same (subject, predicate) as
# superseded by a new triple_id. ``valid_to_chapter = ?`` is the chapter
# before the new triple's valid_from_chapter (closing the window).
SUPERSEDE_SPO = """
UPDATE truth_current_state
SET valid_to_chapter = ?, superseded_by = ?
WHERE project_id = ? AND subject = ? AND predicate = ?
  AND valid_to_chapter IS NULL AND triple_id != ?
"""

# action=invalidate: just close the window for matching (subject, predicate, object)
INVALIDATE_SPO = """
UPDATE truth_current_state
SET valid_to_chapter = ?
WHERE project_id = ? AND subject = ? AND predicate = ? AND object = ?
  AND valid_to_chapter IS NULL
"""

# ─────────── character_ledger ───────────

INSERT_LEDGER = """
INSERT INTO character_ledger (
    ledger_id, project_id, character_name, chapter_num,
    category, key, operation, delta, new_value, reason, in_text_evidence
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

LATEST_LEDGER_VALUE = """
SELECT new_value FROM character_ledger
WHERE project_id = ? AND character_name = ? AND key = ?
  AND chapter_num <= ?
ORDER BY chapter_num DESC, created_at DESC
LIMIT 1
"""

# ─────────── pending_hooks ───────────

INSERT_HOOK = """
INSERT INTO pending_hooks (
    hook_id, project_id, description, status, importance,
    origin_chapter, expected_payoff_chapter,
    last_mention_chapter, last_advance_chapter,
    pressure_threshold,
    is_spoiler, revealed_to_chars_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

GET_HOOK = """
SELECT hook_id, project_id, description, status, importance,
       origin_chapter, expected_payoff_chapter,
       last_mention_chapter, last_advance_chapter,
       pressure_threshold
FROM pending_hooks WHERE hook_id = ?
"""

UPDATE_HOOK_MENTION = """
UPDATE pending_hooks
SET last_mention_chapter = ?, updated_at = CURRENT_TIMESTAMP
WHERE hook_id = ?
"""

UPDATE_HOOK_PROGRESS = """
UPDATE pending_hooks
SET status = ?, last_advance_chapter = ?, last_mention_chapter = ?,
    updated_at = CURRENT_TIMESTAMP
WHERE hook_id = ?
"""

UPDATE_HOOK_STATUS = """
UPDATE pending_hooks
SET status = ?, updated_at = CURRENT_TIMESTAMP
WHERE hook_id = ?
"""

# Pressure scan: open|progressing hooks where the gap exceeds threshold.
SCAN_FOR_PRESSURE = """
SELECT hook_id, status, importance, last_advance_chapter, pressure_threshold
FROM pending_hooks
WHERE project_id = ? AND status IN ('open','progressing')
"""

INSERT_HOOK_EVENT = """
INSERT INTO hook_events (
    event_id, hook_id, project_id, chapter_num, action,
    before_status, after_status, evidence
) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

# ─────────── subplot_threads ───────────

INSERT_SUBPLOT = """
INSERT INTO subplot_threads (
    thread_id, project_id, name, description, status,
    start_chapter, last_advanced_chapter,
    related_hook_ids_json, related_character_ids_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

UPDATE_SUBPLOT = """
UPDATE subplot_threads
SET status = ?, last_advanced_chapter = ?,
    related_hook_ids_json = ?, updated_at = CURRENT_TIMESTAMP
WHERE thread_id = ?
"""

GET_SUBPLOT_BY_NAME = """
SELECT thread_id FROM subplot_threads
WHERE project_id = ? AND name = ?
LIMIT 1
"""

# ─────────── emotion_arcs ───────────

INSERT_EMOTION_ARC = """
INSERT INTO emotion_arcs (
    arc_id, project_id, character_name, chapter_num,
    from_state, to_state, trigger
) VALUES (?, ?, ?, ?, ?, ?, ?)
"""

# ─────────── character_relations ───────────

UPSERT_RELATION = """
INSERT INTO character_relations (
    rel_id, project_id, character_a, character_b, relation_type,
    sentiment_score, trust_level, last_interaction_chapter, note
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(project_id, character_a, character_b) DO UPDATE SET
    relation_type = excluded.relation_type,
    sentiment_score = excluded.sentiment_score,
    trust_level = excluded.trust_level,
    last_interaction_chapter = excluded.last_interaction_chapter,
    note = excluded.note,
    updated_at = CURRENT_TIMESTAMP
"""

# ─────────── chapter_summaries (re-used) ───────────

UPSERT_CHAPTER_SUMMARY = """
INSERT INTO chapter_summaries (
    summary_id, project_id, chapter_num, summary_text,
    key_events_json, character_states_json, is_active
) VALUES (?, ?, ?, ?, ?, ?, 1)
ON CONFLICT(project_id, chapter_num) DO UPDATE SET
    summary_text = excluded.summary_text,
    key_events_json = excluded.key_events_json,
    character_states_json = excluded.character_states_json,
    is_active = 1
"""

# ─────────── truth_apply_log (idempotency) ───────────

FIND_APPLY_LOG = """
SELECT apply_id, chapter_num, applied_counts_json, cross_ref_issues_json
FROM truth_apply_log
WHERE project_id = ? AND deltas_hash = ?
"""

INSERT_APPLY_LOG = """
INSERT INTO truth_apply_log (
    apply_id, project_id, chapter_num, deltas_hash,
    applied_counts_json, cross_ref_issues_json
) VALUES (?, ?, ?, ?, ?, ?)
"""

LATEST_APPLIED_CHAPTER = """
SELECT MAX(chapter_num) FROM truth_apply_log WHERE project_id = ?
"""
