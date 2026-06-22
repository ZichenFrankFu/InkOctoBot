"""Platform market directive loader.

Reads from the ``platform_profiles`` table populated by the market
extractor (services/market_extractor). The latest active profile for
the project's (platform, category) is consumed verbatim — no LLM call
at prompt-build time.

Returns ``None`` when:
- the project has no platform / category set, or
- no profile exists for that pair, or
- the latest profile is confidence='low' (per spec § 五).

Confidence labels we surface: ``high``, ``medium`` (auto-extracted
profile that passed holdout similarity), ``manual`` (user-submitted via
the Web-LLM workflow), and ``NULL`` (auto-extracted but holdout was
never scored). Only ``low`` is filtered out.
"""
from __future__ import annotations

import json
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


def _coerce_payload_field(raw) -> str:
    """``style_baseline`` / ``pacing_guidance`` may be stored as either
    plain prose or a JSON blob; render either as a single line."""
    if not raw:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    if text.startswith("{") or text.startswith("["):
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            return text
        if isinstance(obj, dict):
            return "；".join(f"{k}：{v}" for k, v in obj.items() if v)
        if isinstance(obj, list):
            return "；".join(str(x) for x in obj if x)
    return text


def _load_active_profile(db_path: str, platform: str, category: str) -> str:
    """Return a usable platform-directive body for the latest non-
    superseded profile (any confidence except ``'low'``).

    Prefer ``loader_payload`` (the 1000-char prose blob synthesized for
    direct prompt injection). When that column is empty (e.g. older
    rows, or the LLM forgot the field), fall back to a concatenation of
    ``profile_summary`` + ``signature_devices_description`` +
    ``style_baseline`` + ``pacing_guidance`` so the user still gets a
    usable block.
    """
    try:
        with sqlite3.connect(db_path) as con:
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT loader_payload, profile_summary, "
                "       style_baseline, signature_devices_description, "
                "       pacing_guidance, confidence_label "
                "FROM platform_profiles "
                "WHERE platform = ? AND category = ? "
                "AND superseded_by_profile_id IS NULL "
                "AND (confidence_label IS NULL OR confidence_label != 'low') "
                "ORDER BY profile_version DESC LIMIT 1",
                (platform, category),
            ).fetchone()
    except sqlite3.OperationalError:
        return ""
    if not row:
        logger.debug(
            "platform_directive: no active profile for %s/%s", platform, category)
        return ""
    payload = (row["loader_payload"] or "").strip()
    if payload:
        return payload
    # Synthesize a fallback body from the structured fields.
    parts: list[str] = []
    summary = (row["profile_summary"] or "").strip()
    if summary:
        parts.append(summary)
    devices = (row["signature_devices_description"] or "").strip()
    if devices:
        parts.append(f"代表手法：{devices}")
    style = _coerce_payload_field(row["style_baseline"])
    if style:
        parts.append(f"风格基线：{style}")
    pacing = _coerce_payload_field(row["pacing_guidance"])
    if pacing:
        parts.append(f"节奏指南：{pacing}")
    if not parts:
        logger.debug(
            "platform_directive: profile exists for %s/%s but all body fields blank",
            platform, category)
    return "\n".join(parts).strip()


def plan(project_id: str, exclude: set | None = None) -> LoaderPlan | None:
    try:
        from ui.backend.app.services.project_paths import get_db_path
        db_path = get_db_path()
    except Exception:
        return None

    platform, category = _resolve_project_platform_category(db_path, project_id)
    if not platform or not category:
        logger.debug(
            "platform_directive: project %s missing platform=%r / category=%r",
            project_id, platform, category)
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
