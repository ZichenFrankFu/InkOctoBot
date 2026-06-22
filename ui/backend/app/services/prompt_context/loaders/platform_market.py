"""Platform market directive loader.

Reads ONLY from real extracted features — no hardcoded platform style
descriptions, no static alias tables. Every value the loader emits to
the prompt comes from a row the market-extractor pipeline actually
persisted.

Three data sources, in priority order:

  · ``platform_profiles``                  (synthesized profile — Phase 4)
  · ``category_aggregated_stats``          (高级特征提取 cross-work aggregate)
  · ``compute_cache[opening_nlp:<platform>]`` (基础特征提取 NLP measurements)

Name resolution is also data-driven (``_data_driven_platform_matches``):
the loader inspects which platform identifiers each source has actually
stored, then case-insensitively matches them against the project's
platform string (exact > stored ⊂ project > project ⊂ stored). No
hardcoded mapping between display labels and crawler-side identifiers.

Truncates each subsection so the rendered body stays under
``LOADER_BUDGETS["platform_directive"]["max"]`` — the budget allocator
will further clip when the global RAG budget is tight.

Returns ``None`` only when:
- the project has no platform set, or
- the project's platform string doesn't overlap any stored identifier
  (i.e. no extractor data exists for this platform).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from ..budget_allocator import LOADER_BUDGETS
from ..loader_protocol import LoaderPlan
from ..utils import clip, section

logger = logging.getLogger("inkoctobot.services.prompt_context.platform_market")


_BLOCK = "platform_directive"
_TITLE = "平台风格基线"

# Per-subsection caps so a single noisy field can't blow the budget.
# The aggregate budget cap is enforced by render(budget) → clip().
_CAP_PER_SUBSECTION = 320      # chars
_TOP_N_DIST = 3                # top-N entries per distribution
_TOP_N_VOCAB = 12              # top-N genre vocabulary terms


def _resolve_project_platform_category(db_path: str, project_id: str) -> tuple[str, str]:
    """Pull (platform, category) for ``project_id`` from the projects row.

    The dedicated columns are added by ``_ensure_projects_market_columns``
    and written by ``project_store.upsert_project``. Legacy projects
    (created before upsert wrote them) only have these values stashed in
    ``style_profile_json``'s extra blob — fall back to that so older
    rows still resolve without a separate backfill migration.

    Returns ``("", "")`` when the row is missing or the columns are
    absent on the schema.
    """
    try:
        with sqlite3.connect(db_path) as con:
            con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT * FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
    except sqlite3.OperationalError:
        return "", ""
    if not row:
        return "", ""
    d = dict(row)
    platform = str(d.get("platform") or "").strip()
    category = str(d.get("category") or "").strip()
    if not platform or not category:
        # Legacy fallback: pull from style_profile_json.extra.
        try:
            extra = json.loads(d.get("style_profile_json") or "{}") or {}
        except (TypeError, ValueError, json.JSONDecodeError):
            extra = {}
        if not platform:
            platform = str(extra.get("platform") or "").strip()
        if not category:
            category = str(extra.get("category") or "").strip()
    if not category:
        # Last fallback: legacy ``genre`` column did double-duty as a
        # category label.
        category = str(d.get("genre") or "").strip()
    return platform, category


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


def _data_driven_platform_matches(
    db_path: str, project_platform: str,
) -> list[str]:
    """Discover every stored platform identifier that matches the
    project's platform string, purely from real extracted data.

    No hardcoded alias table — we scan the three places the market
    extractor actually persists platform-keyed rows:

      · ``platform_profiles.platform``        (synthesized profile)
      · ``category_aggregated_stats.platform``(aggregated stats)
      · ``compute_cache`` keys of the form ``opening_nlp:<platform>``
        (basic NLP cache)

    Then match the stored values against ``project_platform`` with three
    tiers of decreasing strictness (exact > project-contains-stored >
    stored-contains-project, all case-insensitive). Ordering is preserved
    so the loader tries the strongest match first.

    Returns ``[]`` when none of the stored values overlap — the loader
    surfaces a "no real data for this platform" hint to the user instead
    of falling back to anything synthesized.
    """
    if not (project_platform or "").strip():
        return []
    pp = project_platform.strip().lower()

    stored: list[str] = []
    seen: set[str] = set()

    def _push(v: object) -> None:
        s = str(v or "").strip()
        if not s or s.lower() in seen:
            return
        stored.append(s)
        seen.add(s.lower())

    try:
        with sqlite3.connect(db_path) as con:
            for sql in (
                "SELECT DISTINCT platform FROM platform_profiles "
                "WHERE platform IS NOT NULL AND platform != ''",
                "SELECT DISTINCT platform FROM category_aggregated_stats "
                "WHERE platform IS NOT NULL AND platform != ''",
            ):
                try:
                    for (v,) in con.execute(sql).fetchall():
                        _push(v)
                except sqlite3.OperationalError:
                    continue
            try:
                rows = con.execute(
                    "SELECT cache_key FROM compute_cache "
                    "WHERE cache_key LIKE 'opening_nlp:%'"
                ).fetchall()
                for (key,) in rows:
                    if not isinstance(key, str) or ":" not in key:
                        continue
                    suffix = key.split(":", 1)[1]
                    if suffix and suffix != "all":
                        _push(suffix)
            except sqlite3.OperationalError:
                pass
    except sqlite3.OperationalError:
        return []

    exact: list[str] = []
    project_contains: list[str] = []
    stored_contains: list[str] = []
    for v in stored:
        vl = v.lower()
        if vl == pp:
            exact.append(v)
        elif vl in pp:
            # The stored value is shorter than (and contained in) the
            # project's value — e.g. project="起点中文网", stored="起点".
            project_contains.append(v)
        elif pp in vl:
            # The project's value is contained in the stored value —
            # e.g. project="起点", stored="起点中文网".
            stored_contains.append(v)
    return exact + project_contains + stored_contains


def _load_active_profile(db_path: str, platform: str, category: str) -> str:
    """Return a usable platform-directive body for the latest non-
    superseded profile (any confidence except ``'low'``).

    Lookup order:
      1. Exact match — ``platform = X AND category = Y``.
      2. Platform-wide profile — ``platform = X AND category = ''``.
         The MarketFeatureExtractionPage currently always submits with
         ``category=""``, so most users land here even when their
         project has a category set.
      3. Any profile for the platform — last resort, picks whichever
         row has the highest profile_version regardless of category.

    Each tier is tried with every platform name actually stored in the
    DB that matches the project's value via ``_data_driven_platform_matches``
    — no hardcoded alias table; we just look at what's there.

    Prefer ``loader_payload`` (the 1000-char prose blob synthesized for
    direct prompt injection). When that column is empty (e.g. older
    rows, or the LLM forgot the field), fall back to a concatenation of
    ``profile_summary`` + ``signature_devices_description`` +
    ``style_baseline`` + ``pacing_guidance`` so the user still gets a
    usable block.
    """
    base_select = (
        "SELECT loader_payload, profile_summary, "
        "       style_baseline, signature_devices_description, "
        "       pacing_guidance, confidence_label, category "
        "FROM platform_profiles "
        "WHERE platform = ? "
        "AND superseded_by_profile_id IS NULL "
        "AND (confidence_label IS NULL OR confidence_label != 'low') "
    )
    matches = _data_driven_platform_matches(db_path, platform)
    candidates: list[tuple[str, tuple]] = []
    for plat_alias in matches:
        if category:
            candidates.append((
                base_select + "AND category = ? ORDER BY profile_version DESC LIMIT 1",
                (plat_alias, category),
            ))
        # Platform-wide profile (category='') — what the manual extractor saves.
        candidates.append((
            base_select + "AND (category = '' OR category IS NULL) "
            "ORDER BY profile_version DESC LIMIT 1",
            (plat_alias,),
        ))
        # Last resort — any profile for this alias.
        candidates.append((
            base_select + "ORDER BY profile_version DESC LIMIT 1",
            (plat_alias,),
        ))

    row = None
    try:
        with sqlite3.connect(db_path) as con:
            con.row_factory = sqlite3.Row
            for sql, params in candidates:
                row = con.execute(sql, params).fetchone()
                if row:
                    break
    except sqlite3.OperationalError:
        return ""
    if not row:
        logger.debug(
            "platform_directive: no active profile for platform=%r category=%r "
            "(data-driven matches: %s)",
            platform, category, matches)
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


def _safe_json(raw: Any) -> Any:
    """Parse a JSON-text column. Returns ``None`` on any failure so the
    fallback builder can skip the field silently."""
    if raw is None:
        return None
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def _top_dist(raw: Any, n: int = _TOP_N_DIST) -> str:
    """Render a ``{label: count}`` distribution as
    ``"label1 N · label2 M · label3 K"``. Empty → ``""``."""
    obj = _safe_json(raw)
    if not isinstance(obj, dict) or not obj:
        return ""
    items = sorted(obj.items(), key=lambda kv: -int(kv[1] or 0))[:n]
    return " · ".join(f"{k} {v}" for k, v in items if k)


def _stat_brief(raw: Any, fmt: str = "{p50:.0f}") -> str:
    """Render a ``{mean, std, p25, p50, p75}`` stat dict as a short line."""
    obj = _safe_json(raw)
    if not isinstance(obj, dict) or not obj:
        return ""
    try:
        return fmt.format(**{k: float(v or 0) for k, v in obj.items()})
    except (KeyError, ValueError, TypeError):
        return ""


def _genre_vocab_brief(raw: Any, n: int = _TOP_N_VOCAB) -> str:
    """Top-N genre vocabulary terms as a comma-joined string."""
    obj = _safe_json(raw)
    if not isinstance(obj, list) or not obj:
        return ""
    terms: list[str] = []
    for item in obj[:n]:
        if isinstance(item, dict):
            t = str(item.get("term") or "").strip()
        elif isinstance(item, list) and item:
            t = str(item[0] or "").strip()
        else:
            t = str(item or "").strip()
        if t:
            terms.append(t)
    return "、".join(terms)


def _load_aggregated_stats(
    db_path: str, platform: str, category: str,
) -> dict | None:
    """Pull the latest ``category_aggregated_stats`` row, preferring an
    exact (platform, category) match. Falls back to platform-only when
    the project's category is blank or no exact row exists.

    Platform name matching is data-driven via
    ``_data_driven_platform_matches`` — every stored platform value
    that overlaps the project's value is tried, in match-strength order.
    """
    matches = _data_driven_platform_matches(db_path, platform)
    if not matches:
        return None
    candidates: list[tuple[str, tuple]] = []
    base = "SELECT * FROM category_aggregated_stats WHERE platform = ? "
    for plat_alias in matches:
        if category:
            candidates.append((
                base + "AND category = ? ORDER BY aggregated_at DESC LIMIT 1",
                (plat_alias, category),
            ))
        candidates.append((
            base + "ORDER BY aggregated_at DESC LIMIT 1",
            (plat_alias,),
        ))
    try:
        with sqlite3.connect(db_path) as con:
            con.row_factory = sqlite3.Row
            for sql, params in candidates:
                row = con.execute(sql, params).fetchone()
                if row:
                    return dict(row)
    except sqlite3.OperationalError:
        return None
    return None


def _load_opening_nlp_cache(db_path: str, platform: str) -> dict | None:
    """Read the cached ``opening_nlp:<platform>`` payload written by the
    "基础特征提取" tab. Each cache key's platform suffix is matched
    against the project's value via ``_data_driven_platform_matches``,
    so projects saved with the human label still find a cache row
    written under whatever crawler-side identifier the tab used.

    Falls back to the platform-agnostic ``opening_nlp:all`` entry when
    no platform-specific row matches.
    """
    matches = _data_driven_platform_matches(db_path, platform)
    cache_keys: list[str] = []
    seen: set[str] = set()
    for plat_alias in matches:
        key = f"opening_nlp:{plat_alias}"
        if key not in seen:
            cache_keys.append(key)
            seen.add(key)
    cache_keys.append("opening_nlp:all")
    try:
        with sqlite3.connect(db_path) as con:
            for key in cache_keys:
                row = con.execute(
                    "SELECT payload_json FROM compute_cache WHERE cache_key = ?",
                    (key,),
                ).fetchone()
                if row and row[0]:
                    try:
                        data = json.loads(row[0])
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if isinstance(data, dict) and data.get("available"):
                        return data
    except sqlite3.OperationalError:
        return None
    return None


def _build_from_market_data(
    db_path: str, platform: str, category: str,
) -> str:
    """Compose a platform directive from the raw market-extractor outputs.

    Each subsection is independently capped at ``_CAP_PER_SUBSECTION``
    chars so a noisy genre-vocabulary doesn't drown the more useful
    distributions. Sections with no data are skipped.
    """
    parts: list[str] = []
    stats = _load_aggregated_stats(db_path, platform, category)
    if stats:
        line_count = int(stats.get("source_works_count") or 0)
        scope = (
            f"高级特征（{platform}"
            + (f" · {stats.get('category') or category}" if (stats.get("category") or category) else "")
            + f"，源于 {line_count} 部代表作）"
        )
        parts.append(scope)
        # Distributions — keep only top-3 each.
        for field, label in [
            ("opening_hook_type_distribution_json",      "开篇钩子"),
            ("protagonist_cheat_type_distribution_json", "主角金手指"),
            ("worldview_type_distribution_json",         "世界观"),
            ("writing_style_distribution_json",          "主导文风"),
            ("emotional_tone_distribution_json",         "情感基调"),
            ("chapter_end_hook_type_distribution_json",  "章末钩子"),
        ]:
            line = _top_dist(stats.get(field))
            if line:
                parts.append(clip(f"{label}：{line}", _CAP_PER_SUBSECTION))
        # Numerical brief — chapter pacing.
        wc = _stat_brief(stats.get("chapter_word_count_stats_json"),
                          "章均 {p50:.0f} 字 (p25 {p25:.0f} ~ p75 {p75:.0f})")
        if wc:
            parts.append(wc)
        dr = _stat_brief(stats.get("dialogue_ratio_stats_json"),
                          "对话占比 {p50:.0%} (p25 {p25:.0%} ~ p75 {p75:.0%})")
        if dr:
            parts.append(dr)
        # Power-growth pacing nodes.
        first_break = int(stats.get("first_breakthrough_chapter_median") or 0)
        antag = int(stats.get("antagonist_first_chapter_median") or 0)
        slap = int(stats.get("first_face_slap_chapter_median") or 0)
        pacing_bits: list[str] = []
        if first_break:
            pacing_bits.append(f"首次突破 第{first_break}章")
        if antag:
            pacing_bits.append(f"主反派登场 第{antag}章")
        if slap:
            pacing_bits.append(f"首次打脸 第{slap}章")
        if pacing_bits:
            parts.append("节点中位：" + " · ".join(pacing_bits))
        # Genre vocabulary — top terms only.
        vocab = _genre_vocab_brief(stats.get("genre_vocabulary_top_json"))
        if vocab:
            parts.append(clip(f"题材高频词：{vocab}", _CAP_PER_SUBSECTION))

    nlp = _load_opening_nlp_cache(db_path, platform)
    if nlp:
        sample = int(nlp.get("sample_count") or 0)
        novels = int(nlp.get("unique_novels") or 0)
        scope = f"基础特征（开篇 NLP，{novels} 部 / {sample} 个样本）"
        parts.append(scope)
        wcs = nlp.get("word_count_summary") or {}
        if isinstance(wcs, dict) and wcs.get("mean"):
            parts.append(
                f"开篇字数 均 {int(wcs.get('mean') or 0)} "
                f"(min {int(wcs.get('min') or 0)} / max {int(wcs.get('max') or 0)})"
            )
        dr = nlp.get("dialogue_ratio") or {}
        if isinstance(dr, dict) and dr.get("mean") is not None:
            parts.append(f"开篇对话占比 均 {float(dr.get('mean') or 0):.1%}")
        sl = nlp.get("sentence_length") or {}
        if isinstance(sl, dict) and sl.get("mean"):
            parts.append(f"开篇平均句长 {float(sl.get('mean') or 0):.1f} 字")
        # Top-3 opening / closing sentence types.
        for field, label in [
            ("first_sentence_types", "首句类型"),
            ("end_hook_types",       "尾钩类型"),
        ]:
            items = nlp.get(field) or []
            if isinstance(items, list) and items:
                top = items[:_TOP_N_DIST]
                line = " · ".join(
                    f"{x.get('label')} {x.get('count')}"
                    for x in top if isinstance(x, dict) and x.get("label")
                )
                if line:
                    parts.append(clip(f"{label}：{line}", _CAP_PER_SUBSECTION))

    return "\n".join(parts).strip()


def plan(project_id: str, exclude: set | None = None) -> LoaderPlan | None:
    try:
        from ui.backend.app.services.project_paths import get_db_path
        db_path = get_db_path()
    except Exception:
        return None

    platform, category = _resolve_project_platform_category(db_path, project_id)
    # Allow a category-less project to still pick up the platform-wide
    # profile (the manual-submit path always writes with category='').
    if not platform:
        logger.debug(
            "platform_directive: project %s has no platform set", project_id)
        return None

    payload = _load_active_profile(db_path, platform, category)
    if not payload:
        # No synthesized profile — fall back to raw market-extractor
        # outputs (高级 + 基础 特征) so the user's "提取完成但没有 profile"
        # case still injects something usable.
        payload = _build_from_market_data(db_path, platform, category)
        if payload:
            logger.debug(
                "platform_directive: synthesized fallback body for %s/%s "
                "from category_aggregated_stats + opening_nlp cache",
                platform, category)
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
