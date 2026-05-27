"""Phase 4 part 2 — synthesize platform_profile via cloud LLM (single call).

Reads the most recent ``category_aggregated_stats`` row, asks the LLM
for a 6-part profile, persists into ``platform_profiles``.

Spec § 七 ★: prompt explicitly forbids naming-pattern aggregation. We
also strip any "命名" suggestion if the LLM emits one anyway.
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .llm_extractor import _extract_json

logger = logging.getLogger("inkoctobot.market_extractor.profile_synthesizer")


_PROMPT_TEMPLATE = (
    Path(__file__).resolve().parent / "resources" / "prompts" / "profile_synthesis.txt"
)

_LOADER_PAYLOAD_MAX = 1000


def _load_latest_stats(
    db_path: str, platform: str, category: str,
) -> dict | None:
    with sqlite3.connect(db_path) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM category_aggregated_stats "
            "WHERE platform = ? AND category = ? "
            "ORDER BY aggregated_at DESC LIMIT 1",
            (platform, category),
        ).fetchone()
    return dict(row) if row else None


def _strip_naming_suggestions(text: str) -> str:
    """Defensive — remove any sentence containing 命名 / 取名 / 名字
    so the loader payload never includes naming guidance (spec § 七)."""
    if not text:
        return text
    parts = re.split(r"(?<=[。！？.!?])\s*", text)
    kept = [
        p for p in parts
        if not any(w in p for w in ("命名", "取名", "起名", "名字模式"))
    ]
    return "".join(kept)


async def _call_synthesis_llm(
    stats: dict, platform: str, category: str,
) -> tuple[dict, str]:
    """Single cloud-LLM call to synthesize the 6-part profile. Uses the
    same FallbackRouter chain so a cloud failure can still resolve via
    the local fallback."""
    from llm.fallback_router import get_fallback_router
    from llm.router import ModelRouter

    # Strip housekeeping columns the LLM doesn't need.
    payload = {
        k: v for k, v in stats.items()
        if k not in ("stats_id", "platform", "category", "aggregated_at",
                      "source_works_ids_json")
    }
    prompt = _PROMPT_TEMPLATE.read_text("utf-8").format(
        n_works=stats.get("source_works_count") or 0,
        platform=platform, category=category,
        stats_json=json.dumps(payload, ensure_ascii=False, indent=2),
    )

    router = ModelRouter()
    fr = get_fallback_router(router)
    raw = await fr.invoke(
        primary_role="post_commit",
        prompt=prompt,
        system="你是网文市场分析专家。只输出 JSON。",
        max_tokens=4000, temperature=0.3,
    )
    parsed = _extract_json(raw)
    provider, model = router.resolve_role("post_commit")
    return parsed, f"{provider}/{model}".strip("/")


def _persist(
    db_path: str, platform: str, category: str,
    profile: dict, source_works_ids: list[str], model_label: str,
    started_at: str, completed_at: str,
) -> str:
    """INSERT a new profile row. Marks any prior active profile with
    superseded_by_profile_id."""
    profile_id = f"pp_{uuid.uuid4().hex[:12]}"

    summary = str(profile.get("profile_summary") or "")[:600]
    openings = profile.get("recommended_openings") or []
    style = profile.get("style_baseline") or {}
    devices_desc = _strip_naming_suggestions(
        str(profile.get("signature_devices_description") or "")
    )
    pacing = profile.get("pacing_guidance") or {}
    payload = _strip_naming_suggestions(
        str(profile.get("loader_payload") or "")
    )[:_LOADER_PAYLOAD_MAX]

    # Resolve profile_version — increment from prior max for this
    # (platform, category).
    with sqlite3.connect(db_path) as con:
        row = con.execute(
            "SELECT MAX(profile_version) FROM platform_profiles "
            "WHERE platform = ? AND category = ?",
            (platform, category),
        ).fetchone()
        version = ((row[0] or 0) + 1) if row else 1

        # Supersede prior active profiles.
        con.execute(
            "UPDATE platform_profiles "
            "SET superseded_by_profile_id = ?, valid_until = ? "
            "WHERE platform = ? AND category = ? "
            "AND superseded_by_profile_id IS NULL",
            (profile_id, completed_at, platform, category),
        )

        con.execute(
            "INSERT INTO platform_profiles "
            "(profile_id, platform, category, profile_version, "
            " source_works_count, source_works_ids_json, "
            " extraction_started_at, extraction_completed_at, "
            " profile_summary, recommended_openings_json, "
            " style_baseline, signature_devices_description, "
            " pacing_guidance, loader_payload, loader_token_estimate, "
            " valid_from) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (profile_id, platform, category, version,
             len(source_works_ids),
             json.dumps(source_works_ids, ensure_ascii=False),
             started_at, completed_at,
             summary,
             json.dumps(openings, ensure_ascii=False),
             json.dumps(style, ensure_ascii=False),
             devices_desc,
             json.dumps(pacing, ensure_ascii=False),
             payload, int(len(payload) / 1.7),
             completed_at),
        )
        con.commit()
    return profile_id


async def synthesize(
    db_path: str, platform: str, category: str,
) -> dict[str, Any]:
    """Full synthesis: load stats → cloud LLM → persist."""
    stats = _load_latest_stats(db_path, platform, category)
    if not stats:
        raise RuntimeError(
            f"no category_aggregated_stats for {platform}/{category}; "
            "run Phase 4 aggregator first"
        )
    started_at = datetime.utcnow().isoformat(timespec="seconds")
    profile, model_label = await _call_synthesis_llm(stats, platform, category)
    completed_at = datetime.utcnow().isoformat(timespec="seconds")
    src_ids = json.loads(stats.get("source_works_ids_json") or "[]")
    profile_id = _persist(
        db_path, platform, category, profile, src_ids, model_label,
        started_at, completed_at,
    )
    return {
        "profile_id":         profile_id,
        "platform":           platform,
        "category":           category,
        "source_works_count": len(src_ids),
        "llm_model":          model_label,
        "loader_payload_chars": len(profile.get("loader_payload") or ""),
    }
