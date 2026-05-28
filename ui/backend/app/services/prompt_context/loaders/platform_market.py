"""Platform market directive loader.

Reads from the ``platform_profiles`` table populated by the market
extractor (services/market_extractor). The latest active profile for
the project's (platform, category) is consumed verbatim — no LLM call
at prompt-build time.

Returns ``None`` when:
- the project has no platform / category set, or
- no profile exists for that pair, or
- the latest profile is confidence='low' (per spec § 五).
"""
from __future__ import annotations

import logging
import sqlite3

from ..budget_allocator import LOADER_BUDGETS
from ..loader_protocol import LoaderPlan
from ..utils import clip, section

logger = logging.getLogger("inkoctobot.services.prompt_context.platform_market")


_BLOCK = "platform_directive"
_TITLE = "平台风格基线"


def _resolve_project_platform_category(db_path: str, project_id: str) -> tuple[str, str]:
    try:
        with sqlite3.connect(db_path) as con:
            con.row_factory = sqlite3.Row
            # The projects table may not have platform/category columns
            # in older schemas — wrap in try so loader skips cleanly.
            row = con.execute(
                "SELECT * FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
        if not row:
            return "", ""
        d = dict(row)
        return (
            str(d.get("platform") or "").strip(),
            str(d.get("category") or d.get("genre") or "").strip(),
        )
    except sqlite3.OperationalError:
        return "", ""


def _load_active_profile(db_path: str, platform: str, category: str) -> str:
    """Return the loader_payload for the latest non-superseded profile
    with confidence in {'high', 'medium'}. Empty string if none."""
    try:
        with sqlite3.connect(db_path) as con:
            row = con.execute(
                "SELECT loader_payload FROM platform_profiles "
                "WHERE platform = ? AND category = ? "
                "AND superseded_by_profile_id IS NULL "
                "AND (confidence_label IS NULL OR confidence_label IN ('high', 'medium')) "
                "ORDER BY profile_version DESC LIMIT 1",
                (platform, category),
            ).fetchone()
    except sqlite3.OperationalError:
        return ""
    return (row[0] or "").strip() if row else ""


def plan(project_id: str, exclude: set | None = None) -> LoaderPlan | None:
    try:
        from ui.backend.app.services.project_paths import get_db_path
        db_path = get_db_path()
    except Exception:
        return None

    platform, category = _resolve_project_platform_category(db_path, project_id)
    if not platform or not category:
        return None

    payload = _load_active_profile(db_path, platform, category)
    if not payload:
        return None

    cfg = LOADER_BUDGETS[_BLOCK]
    overhead = len(_TITLE) + 6
    natural = overhead + len(payload)

    def render(budget: int) -> str:
        return section(_TITLE, clip(payload, max(0, budget - overhead)))

    return LoaderPlan(
        block_id=_BLOCK,
        natural_length=natural,
        minimum=cfg["min"], target=cfg["target"], maximum=cfg["max"],
        priority_tier=cfg["tier"],
        render=render,
    )


def load(project_id: str, exclude: set | None = None) -> str:
    p = plan(project_id, exclude)
    return p.render(p.target) if p else ""
