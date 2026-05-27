"""Per-block soft character budgets for RAG context assembly.

Numbers are approximate (CJK character ≈ token / 1.7). A block whose
content exceeds its budget is clipped with an explicit truncation
marker — see ``utils.clip``. Tuning here affects the size of every
prompt the generation pipeline builds.
"""
from __future__ import annotations

BUDGETS: dict[str, int] = {
    "character_cards":     1800,
    "worldbook":           1600,
    "reference_summary":   2000,
    "referenced_materials": 2600,
    "writing_knowledge":   1600,
    "writing_skills":      2400,
    "project_memory":      1600,
    "adjacent_context":    1200,
    "foreshadowing":        800,
    "user_preferences":     800,
    # LOADER_SPEC v3 — Batch 3 loaders
    "inspiration":          800,
    "chapter_outline":     1200,
    "current_chapter_draft": 4000,
}
