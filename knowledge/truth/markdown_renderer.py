"""Markdown view renderers for the Truth File system (C4).

Each TruthFileKind has a dedicated render_* function producing a string
with YAML frontmatter + Markdown body. These outputs are read-only views:
SQLite is the canonical source.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from knowledge.truth.schemas import TruthFileKind


SCHEMA_VERSION = 1
GENERATOR = "InkOctoBot/v3-truth"


# ──────────────────────────────────────────────────────────────
# Frontmatter helper
# ──────────────────────────────────────────────────────────────

def _frontmatter(
    *, project_id: str, kind: TruthFileKind, chapter_pointer: int,
) -> str:
    iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        "---\n"
        f"project_id: {project_id}\n"
        f"truth_file: {kind.value}\n"
        f"generated_at: {iso}\n"
        f"schema_version: {SCHEMA_VERSION}\n"
        f"chapter_pointer: {chapter_pointer}\n"
        f"generator: {GENERATOR}\n"
        "---\n"
        "\n"
        "<!-- Note: this file is a view of the canonical state held in SQLite. "
        "Hand edits are NOT read back. -->\n"
        "\n"
    )


def _truncate(text: str, budget_chars: int) -> str:
    """If text exceeds budget, truncate and append marker."""
    if budget_chars <= 0 or len(text) <= budget_chars:
        return text
    return text[: max(budget_chars - 30, 0)] + "\n\n_(truncated for budget)_\n"


# ──────────────────────────────────────────────────────────────
# 1. current_state
# ──────────────────────────────────────────────────────────────

def render_current_state(
    triples: list[dict[str, Any]], *, project_id: str,
    chapter_pointer: int, budget_chars: int = 6000,
) -> str:
    """Render SPO triples grouped by subject. Each subject section lists
    its predicates with chapter window."""
    head = _frontmatter(project_id=project_id,
                        kind=TruthFileKind.current_state,
                        chapter_pointer=chapter_pointer)
    if not triples:
        return head + "# Current State\n\n_(empty)_\n"

    by_subject: dict[str, list[dict[str, Any]]] = {}
    for t in triples:
        by_subject.setdefault(t["subject"], []).append(t)

    lines = ["# Current State", ""]
    for subj in sorted(by_subject.keys()):
        lines.append(f"## {subj}")
        # Sort by valid_from to make timelines readable
        for t in sorted(
            by_subject[subj],
            key=lambda r: (r["predicate"], r["valid_from_chapter"]),
        ):
            window = f"Ch {t['valid_from_chapter']}"
            if t.get("valid_to_chapter") is not None:
                window += f" – Ch {t['valid_to_chapter']}"
            else:
                window += " – present"
            lines.append(f"- **{t['predicate']}** → {t['object']}  *({window})*")
        lines.append("")
    return _truncate(head + "\n".join(lines), budget_chars)


# ──────────────────────────────────────────────────────────────
# 2. particle_ledger
# ──────────────────────────────────────────────────────────────

def render_particle_ledger(
    entries: list[dict[str, Any]], *, project_id: str,
    chapter_pointer: int, budget_chars: int = 4000,
) -> str:
    head = _frontmatter(project_id=project_id,
                        kind=TruthFileKind.particle_ledger,
                        chapter_pointer=chapter_pointer)
    if not entries:
        return head + "# Particle Ledger\n\n_(empty)_\n"

    by_char: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        by_char.setdefault(e["character_name"], []).append(e)

    lines = ["# Particle Ledger", ""]
    for char in sorted(by_char.keys()):
        lines.append(f"## {char}")
        lines.append("")
        lines.append("| Chapter | Resource | Op | Delta | New Value | Reason |")
        lines.append("|---|---|---|---|---|---|")
        # Sort by chapter desc → most recent first
        for e in sorted(by_char[char],
                        key=lambda r: (r["chapter_num"], r.get("created_at", "")),
                        reverse=True):
            op = e["operation"]
            delta_disp = "—" if op == "set" else (
                f"+{e['delta']}" if e["delta"] >= 0 else str(e["delta"])
            )
            reason = (e.get("reason") or "").replace("|", "\\|")
            lines.append(
                f"| Ch {e['chapter_num']} | {e['key']} | {op} | "
                f"{delta_disp} | {e['new_value']} | {reason} |"
            )
        lines.append("")
    return _truncate(head + "\n".join(lines), budget_chars)


# ──────────────────────────────────────────────────────────────
# 3. pending_hooks
# ──────────────────────────────────────────────────────────────

_STATUS_HEADER = {
    "pressured":   "[Pressured]",
    "near_payoff": "[Near Payoff]",
    "progressing": "[Progressing]",
    "open":        "[Open]",
    "resolved":    "[Resolved]",
    "abandoned":   "[Abandoned]",
}

_STATUS_ORDER = [
    "pressured", "near_payoff", "progressing", "open", "resolved", "abandoned",
]


def render_pending_hooks(
    hooks: list[dict[str, Any]], *, project_id: str,
    chapter_pointer: int, budget_chars: int = 5000,
    resolved_limit: int = 5,
) -> str:
    head = _frontmatter(project_id=project_id,
                        kind=TruthFileKind.pending_hooks,
                        chapter_pointer=chapter_pointer)
    if not hooks:
        return head + "# Pending Hooks\n\n_(empty)_\n"

    by_status: dict[str, list[dict[str, Any]]] = {s: [] for s in _STATUS_ORDER}
    for h in hooks:
        by_status.setdefault(h["status"], []).append(h)

    lines = ["# Pending Hooks", ""]
    for status in _STATUS_ORDER:
        bucket = by_status.get(status) or []
        if not bucket:
            continue
        if status == "resolved":
            # most recent first; cap to limit
            bucket = sorted(
                bucket,
                key=lambda h: (h.get("last_advance_chapter") or h.get("origin_chapter") or 0),
                reverse=True,
            )[:resolved_limit]
            heading = f"{_STATUS_HEADER[status]} (recent {len(bucket)})"
        else:
            heading = _STATUS_HEADER[status]
        lines.append(f"## {heading}")
        lines.append("")
        for h in bucket:
            lines.append(
                f"### `[{h['hook_id']}]` {h['description']}"
            )
            meta = [f"**importance**: {h['importance']}"]
            if h.get("origin_chapter") is not None:
                meta.append(f"**origin**: Ch {h['origin_chapter']}")
            if h.get("last_advance_chapter") is not None:
                meta.append(f"**last_advance**: Ch {h['last_advance_chapter']}")
            if h.get("expected_payoff_chapter") is not None:
                meta.append(
                    f"**expected_payoff**: Ch {h['expected_payoff_chapter']}"
                )
            if h.get("pressure_threshold") is not None:
                meta.append(f"**threshold**: {h['pressure_threshold']}")
            lines.append("- " + " · ".join(meta))
            lines.append("")
    return _truncate(head + "\n".join(lines), budget_chars)


# ──────────────────────────────────────────────────────────────
# 4. chapter_summaries
# ──────────────────────────────────────────────────────────────

def render_chapter_summaries(
    summaries: list[dict[str, Any]], *, project_id: str,
    chapter_pointer: int, budget_chars: int = 8000,
    recent_limit: int = 10,
) -> str:
    head = _frontmatter(project_id=project_id,
                        kind=TruthFileKind.chapter_summaries,
                        chapter_pointer=chapter_pointer)
    if not summaries:
        return head + "# Chapter Summaries\n\n_(empty)_\n"

    # Most recent first, capped
    sorted_summ = sorted(
        summaries, key=lambda s: s.get("chapter_num", 0), reverse=True,
    )[:recent_limit]
    lines = [f"# Chapter Summaries (recent {len(sorted_summ)})", ""]
    for s in sorted_summ:
        lines.append(f"## Chapter {s['chapter_num']}")
        if s.get("summary_text"):
            lines.append("")
            lines.append(s["summary_text"])
        events = s.get("key_events")
        if isinstance(events, str):
            try:
                events = json.loads(events) if events else []
            except Exception:
                events = []
        if events:
            lines.append("")
            lines.append("**Key events:**")
            for ev in events:
                lines.append(f"- {ev}")
        lines.append("")
    return _truncate(head + "\n".join(lines), budget_chars)


# ──────────────────────────────────────────────────────────────
# 5. subplot_board
# ──────────────────────────────────────────────────────────────

def render_subplot_board(
    threads: list[dict[str, Any]], *, project_id: str,
    chapter_pointer: int, budget_chars: int = 3500,
) -> str:
    head = _frontmatter(project_id=project_id,
                        kind=TruthFileKind.subplot_board,
                        chapter_pointer=chapter_pointer)
    if not threads:
        return head + "# Subplot Board\n\n_(empty)_\n"

    by_status: dict[str, list[dict[str, Any]]] = {}
    for t in threads:
        by_status.setdefault(t["status"], []).append(t)

    status_order = ["climax", "building", "setup", "resolution", "dormant"]
    lines = ["# Subplot Board", ""]
    for status in status_order:
        bucket = by_status.get(status) or []
        if not bucket:
            continue
        lines.append(f"## {status.title()}")
        lines.append("")
        for t in bucket:
            related = t.get("related_hook_ids") or []
            if isinstance(related, str):
                try:
                    related = json.loads(related) if related else []
                except Exception:
                    related = []
            lines.append(f"### {t['name']}")
            if t.get("description"):
                lines.append(t["description"])
            meta = [f"**start**: Ch {t.get('start_chapter', '?')}"]
            if t.get("last_advanced_chapter") is not None:
                meta.append(
                    f"**last_advanced**: Ch {t['last_advanced_chapter']}"
                )
            if related:
                meta.append(
                    f"**hooks**: " + ", ".join(f"`{h}`" for h in related)
                )
            lines.append("- " + " · ".join(meta))
            lines.append("")
    return _truncate(head + "\n".join(lines), budget_chars)


# ──────────────────────────────────────────────────────────────
# 6. emotional_arcs
# ──────────────────────────────────────────────────────────────

def render_emotional_arcs(
    arcs: list[dict[str, Any]], *, project_id: str,
    chapter_pointer: int, budget_chars: int = 3000,
) -> str:
    head = _frontmatter(project_id=project_id,
                        kind=TruthFileKind.emotional_arcs,
                        chapter_pointer=chapter_pointer)
    if not arcs:
        return head + "# Emotional Arcs\n\n_(empty)_\n"

    by_char: dict[str, list[dict[str, Any]]] = {}
    for a in arcs:
        by_char.setdefault(a["character_name"], []).append(a)

    lines = ["# Emotional Arcs", ""]
    for char in sorted(by_char.keys()):
        lines.append(f"## {char}")
        for a in sorted(by_char[char], key=lambda r: r["chapter_num"]):
            trigger = a.get("trigger") or "—"
            lines.append(
                f"- **Ch {a['chapter_num']}**: "
                f"{a['from_state']} → {a['to_state']}  *(trigger: {trigger})*"
            )
        lines.append("")
    return _truncate(head + "\n".join(lines), budget_chars)


# ──────────────────────────────────────────────────────────────
# 7. character_matrix
# ──────────────────────────────────────────────────────────────

def render_character_matrix(
    relations: list[dict[str, Any]], *, project_id: str,
    chapter_pointer: int, budget_chars: int = 4500,
) -> str:
    head = _frontmatter(project_id=project_id,
                        kind=TruthFileKind.character_matrix,
                        chapter_pointer=chapter_pointer)
    if not relations:
        return head + "# Character Matrix\n\n_(empty)_\n"

    by_a: dict[str, list[dict[str, Any]]] = {}
    for r in relations:
        by_a.setdefault(r["character_a"], []).append(r)

    lines = ["# Character Matrix", ""]
    for a in sorted(by_a.keys()):
        lines.append(f"## {a}'s view")
        lines.append("")
        lines.append("| Other | Relation | Sentiment | Trust | Last Met |")
        lines.append("|---|---|---:|---:|---|")
        for r in sorted(by_a[a], key=lambda x: x["character_b"]):
            last = (f"Ch {r['last_interaction_chapter']}"
                    if r.get("last_interaction_chapter") is not None else "—")
            lines.append(
                f"| {r['character_b']} | {r['relation_type']} | "
                f"{r['sentiment_score']:+d} | {r['trust_level']} | {last} |"
            )
        lines.append("")
    return _truncate(head + "\n".join(lines), budget_chars)


# ──────────────────────────────────────────────────────────────
# Dispatch
# ──────────────────────────────────────────────────────────────

_DISPATCH = {
    TruthFileKind.current_state:     render_current_state,
    TruthFileKind.particle_ledger:   render_particle_ledger,
    TruthFileKind.pending_hooks:     render_pending_hooks,
    TruthFileKind.chapter_summaries: render_chapter_summaries,
    TruthFileKind.subplot_board:     render_subplot_board,
    TruthFileKind.emotional_arcs:    render_emotional_arcs,
    TruthFileKind.character_matrix:  render_character_matrix,
}


def render(
    kind: TruthFileKind, rows: list[dict[str, Any]], *,
    project_id: str, chapter_pointer: int, budget_chars: int,
) -> str:
    """Dispatch to the renderer for ``kind``."""
    fn = _DISPATCH[kind]
    return fn(
        rows, project_id=project_id, chapter_pointer=chapter_pointer,
        budget_chars=budget_chars,
    )


__all__ = [
    "render", "render_current_state", "render_particle_ledger",
    "render_pending_hooks", "render_chapter_summaries",
    "render_subplot_board", "render_emotional_arcs",
    "render_character_matrix",
]
