"""Platform market directive loader.

Reads ONLY from real extracted features — no hardcoded platform style
descriptions, no static alias tables. Every value the loader emits to
the prompt comes from a row the market-extractor pipeline (or the
crawler itself) actually persisted.

Data sources scanned, in priority order:

  · ``platform_profiles``                          (synthesized profile)
  · ``category_aggregated_stats``                  (高级特征提取 aggregate)
  · ``compute_cache[opening_nlp:<platform>]``      (基础特征 NLP cache)
  · ``compute_cache[analysis_run_v4:<platform>:*]``(基础特征 趋势 cache —
                                                    tag_rollup / cat_rollup /
                                                    opportunities / panel)
  · Crawler DB ``novels`` / ``novel_titles`` /
    ``tags`` / ``novel_tag_map`` / ``first_n_chapters``
    (raw 市场数据库 — directly aggregated on demand when none of the
     project-DB caches matched the platform; this is the data the user
     sees in 基础特征 / 高级特征 tabs)

Name resolution is data-driven (``_data_driven_platform_matches``):
the loader inspects which platform identifiers each source has actually
stored — including the crawler DB's ``novels.platform`` column — then
case-insensitively matches them against the project's platform string
(exact > stored ⊂ project > project ⊂ stored). No hardcoded mapping
between display labels and crawler-side identifiers.

Per-subsection cap of ``_CAP_PER_SUBSECTION`` chars; overall body
budget enforced by ``render(budget)`` → ``clip()``. The budget allocator
further clips when global RAG budget is tight.

Returns ``None`` only when:
- the project has no platform set, or
- the project's platform string doesn't overlap any stored identifier
  on EITHER the project DB or the crawler DB (i.e. no real data
  exists for this platform anywhere).
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


def _crawler_db_path() -> str:
    """Return the crawler DB path (or '') without raising — the loader
    must keep working when the user hasn't configured 市场数据库."""
    try:
        from ui.backend.app.utils import resolve_crawler_db_path
        from pathlib import Path
        path = resolve_crawler_db_path()
        if path and Path(path).exists():
            return path
    except Exception as e:
        logger.debug("crawler db resolution failed: %s", e)
    return ""


def _scan_stored_platforms(db_path: str, crawler_db: str) -> list[str]:
    """Collect every platform identifier that has been WRITTEN by either
    the market extractor (project DB) or the crawler itself (market DB).

    Order matters only for the caller's "strongest-match-first" tier;
    duplicates are merged case-insensitively.
    """
    stored: list[str] = []
    seen: set[str] = set()

    def _push(v: object) -> None:
        s = str(v or "").strip()
        if not s or s.lower() in seen:
            return
        stored.append(s)
        seen.add(s.lower())

    # Project DB — synthesized + aggregated + cached NLP / trend results.
    if db_path:
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
                        "WHERE cache_key LIKE 'opening_nlp:%' "
                        "OR cache_key LIKE 'analysis_run_v4:%'"
                    ).fetchall()
                    for (key,) in rows:
                        if not isinstance(key, str) or ":" not in key:
                            continue
                        # opening_nlp:<platform>  → suffix is platform
                        # analysis_run_v4:<platform>:<lookback>:<top_k>
                        parts = key.split(":")
                        if len(parts) >= 2:
                            suffix = parts[1]
                            if suffix and suffix not in ("all", "both"):
                                _push(suffix)
                except sqlite3.OperationalError:
                    pass
        except sqlite3.OperationalError:
            pass

    # Crawler DB — every platform the market scraper has ever seen.
    if crawler_db:
        try:
            with sqlite3.connect(crawler_db) as con:
                try:
                    rows = con.execute(
                        "SELECT DISTINCT platform FROM novels "
                        "WHERE platform IS NOT NULL AND platform != '' "
                        "ORDER BY platform"
                    ).fetchall()
                    for (v,) in rows:
                        _push(v)
                except sqlite3.OperationalError:
                    pass
                try:
                    rows = con.execute(
                        "SELECT DISTINCT platform FROM rank_lists "
                        "WHERE platform IS NOT NULL AND platform != ''"
                    ).fetchall()
                    for (v,) in rows:
                        _push(v)
                except sqlite3.OperationalError:
                    pass
        except sqlite3.OperationalError:
            pass

    return stored


def _data_driven_platform_matches(
    db_path: str, project_platform: str, crawler_db: str = "",
) -> list[str]:
    """Discover every stored platform identifier that matches the
    project's platform string, purely from real extracted data.

    No hardcoded alias table — we scan all five sources via
    ``_scan_stored_platforms`` (three project-DB tables + crawler DB's
    ``novels`` and ``rank_lists`` columns), then match each stored value
    against ``project_platform`` with three tiers of decreasing
    strictness (exact > project-contains-stored > stored-contains-project,
    all case-insensitive). Ordering is preserved so the loader tries the
    strongest match first.

    Returns ``[]`` when none of the stored values overlap — the loader
    surfaces a "no real data for this platform" hint to the user instead
    of falling back to anything synthesized.
    """
    if not (project_platform or "").strip():
        return []
    pp = project_platform.strip().lower()
    if not crawler_db:
        crawler_db = _crawler_db_path()
    stored = _scan_stored_platforms(db_path, crawler_db)

    exact: list[str] = []
    project_contains: list[str] = []
    stored_contains: list[str] = []
    for v in stored:
        vl = v.lower()
        if vl == pp:
            exact.append(v)
        elif vl in pp:
            # Stored shorter than (and contained in) project value —
            # e.g. project="起点中文网", stored="起点".
            project_contains.append(v)
        elif pp in vl:
            # Project value contained in stored — e.g. project="起点",
            # stored="起点中文网".
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


def _load_analysis_run_cache(db_path: str, platform: str) -> dict | None:
    """Read the ``analysis_run_v4:<platform>:*`` cached trend payload
    written by ``/api/analysis/run`` — this is what the 基础特征提取 tab
    displays (tag rollup / category rollup / opportunities / co-occurring
    pair-triples / panel). Same payload, fed straight to the prompt.

    Tries every platform alias produced by the data-driven matcher; for
    each, looks for any cache_key starting with ``analysis_run_v4:<alias>:``
    so we don't have to guess the lookback / top_k components.
    """
    matches = _data_driven_platform_matches(db_path, platform)
    if not matches:
        return None
    try:
        with sqlite3.connect(db_path) as con:
            for alias in matches:
                rows = con.execute(
                    "SELECT payload_json, updated_at FROM compute_cache "
                    "WHERE cache_key LIKE ? ORDER BY updated_at DESC LIMIT 1",
                    (f"analysis_run_v4:{alias}:%",),
                ).fetchall()
                for (raw, _ts) in rows:
                    if not raw:
                        continue
                    try:
                        data = json.loads(raw)
                    except (TypeError, json.JSONDecodeError):
                        continue
                    if isinstance(data, dict) and not data.get("empty"):
                        return data
    except sqlite3.OperationalError:
        return None
    return None


def _load_crawler_aggregates(
    crawler_db: str, matches: list[str], category: str,
) -> dict:
    """Aggregate fresh stats directly from the 市场数据库 (crawler DB).

    This is the same source the 基础特征 / 高级特征 tabs visualize, so
    even when none of the project-DB caches contain a row for the
    project's platform, we still surface useful market signal in the
    prompt.

    Returns a dict with whatever fields the SQL could compute — fields
    are skipped silently when their table or columns are absent so old
    crawler DBs degrade gracefully.
    """
    if not crawler_db or not matches:
        return {}
    out: dict[str, Any] = {}
    try:
        with sqlite3.connect(crawler_db) as con:
            con.row_factory = sqlite3.Row
            # Platform totals — novel count, average length, latest activity.
            placeholders = ",".join("?" for _ in matches)
            try:
                row = con.execute(
                    "SELECT COUNT(*) AS novel_count, "
                    "       AVG(total_words) AS avg_words, "
                    "       MAX(last_seen_date) AS latest "
                    "FROM novels WHERE platform IN (" + placeholders + ")",
                    matches,
                ).fetchone()
                if row and (row["novel_count"] or 0) > 0:
                    out["novel_count"] = int(row["novel_count"])
                    if row["avg_words"]:
                        out["avg_words"] = int(row["avg_words"])
                    if row["latest"]:
                        out["latest"] = str(row["latest"])
            except sqlite3.OperationalError:
                pass
            # 主分类 distribution — what's actually published on this platform.
            try:
                rows = con.execute(
                    "SELECT main_category AS k, COUNT(*) AS n FROM novels "
                    "WHERE platform IN (" + placeholders + ") "
                    "AND main_category IS NOT NULL AND main_category != '' "
                    "GROUP BY main_category ORDER BY n DESC LIMIT 8",
                    matches,
                ).fetchall()
                out["main_categories"] = [
                    {"label": r["k"], "count": int(r["n"])} for r in rows if r["k"]
                ]
            except sqlite3.OperationalError:
                out["main_categories"] = []
            # Tag frequency — restrict to this category when the project has one,
            # otherwise platform-wide.
            try:
                if category:
                    rows = con.execute(
                        "SELECT t.tag_name AS tag, COUNT(*) AS n "
                        "FROM novels n "
                        "JOIN novel_tag_map m ON m.novel_uid = n.novel_uid "
                        "JOIN tags t ON t.tag_id = m.tag_id "
                        "WHERE n.platform IN (" + placeholders + ") "
                        "AND n.main_category = ? "
                        "GROUP BY t.tag_name "
                        "ORDER BY n DESC LIMIT 15",
                        matches + [category],
                    ).fetchall()
                else:
                    rows = con.execute(
                        "SELECT t.tag_name AS tag, COUNT(*) AS n "
                        "FROM novels n "
                        "JOIN novel_tag_map m ON m.novel_uid = n.novel_uid "
                        "JOIN tags t ON t.tag_id = m.tag_id "
                        "WHERE n.platform IN (" + placeholders + ") "
                        "GROUP BY t.tag_name "
                        "ORDER BY n DESC LIMIT 15",
                        matches,
                    ).fetchall()
                out["top_tags"] = [
                    {"label": r["tag"], "count": int(r["n"])}
                    for r in rows if r["tag"]
                ]
            except sqlite3.OperationalError:
                out["top_tags"] = []
            # Opening-chapter sample size (so the LLM knows how grounded
            # the upstream NLP is). Pure count, no body fetched.
            try:
                row = con.execute(
                    "SELECT COUNT(*) AS n FROM first_n_chapters fc "
                    "JOIN novels n ON n.novel_uid = fc.novel_uid "
                    "WHERE n.platform IN (" + placeholders + ") "
                    "AND fc.chapter_num = 1 "
                    "AND length(fc.chapter_content) > 300",
                    matches,
                ).fetchone()
                if row and (row["n"] or 0) > 0:
                    out["opening_sample_count"] = int(row["n"])
            except sqlite3.OperationalError:
                pass
    except sqlite3.OperationalError:
        return out
    return out


def _format_dist(items: list[dict] | None, n: int = _TOP_N_DIST) -> str:
    """Render a list of ``{"label","count"}`` dicts as
    ``"a 5 · b 3 · c 2"``. Empty list / missing fields → ``""``."""
    if not isinstance(items, list) or not items:
        return ""
    bits: list[str] = []
    for it in items[:n]:
        if not isinstance(it, dict):
            continue
        lab = str(it.get("label") or it.get("tag") or it.get("category") or "").strip()
        cnt = it.get("count")
        if not lab:
            continue
        if cnt is None:
            bits.append(lab)
        else:
            try:
                bits.append(f"{lab} {int(cnt)}")
            except (TypeError, ValueError):
                bits.append(lab)
    return " · ".join(bits)


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
                line = _format_dist(items)
                if line:
                    parts.append(clip(f"{label}：{line}", _CAP_PER_SUBSECTION))

    # Trend analysis (analysis_run_v4 cache) — top tags / categories /
    # opportunities that the 基础特征提取 tab shows. Real cached data
    # from /api/analysis/run — same pipeline the user is looking at.
    trend = _load_analysis_run_cache(db_path, platform)
    if trend:
        head = "市场趋势（"
        if trend.get("start_date") and trend.get("end_date"):
            head += f"{trend['start_date']} → {trend['end_date']}"
        head += "）"
        parts.append(head)
        tags = trend.get("tag_rollup") or []
        if isinstance(tags, list) and tags:
            tag_line = _format_dist(
                [{"label": t.get("tag") or t.get("tag_u"),
                  "count": int(t.get("appearances") or 0)}
                 for t in tags if isinstance(t, dict)],
                n=8,
            )
            if tag_line:
                parts.append(clip(f"热门标签：{tag_line}", _CAP_PER_SUBSECTION))
        cats = trend.get("cat_rollup") or []
        if isinstance(cats, list) and cats:
            cat_line = _format_dist(
                [{"label": c.get("category") or c.get("cat_u"),
                  "count": int(c.get("appearances") or 0)}
                 for c in cats if isinstance(c, dict)],
                n=6,
            )
            if cat_line:
                parts.append(clip(f"热门分类：{cat_line}", _CAP_PER_SUBSECTION))
        opps = trend.get("opportunities") or []
        if isinstance(opps, list) and opps:
            opp_line = "、".join(
                str(o.get("tag") or o.get("tag_u") or "").strip()
                for o in opps[:5] if isinstance(o, dict)
            )
            if opp_line.strip("、"):
                parts.append(clip(f"机会词：{opp_line}", _CAP_PER_SUBSECTION))

    # Crawler-DB direct aggregation — the deepest fallback. Even when
    # nothing's been cached or extracted in the project DB, the 市场数据库
    # itself can answer "what does this platform actually publish, and
    # what are the popular tags?" right now.
    matches = _data_driven_platform_matches(db_path, platform)
    if matches:
        crawler_db = _crawler_db_path()
        if crawler_db:
            agg = _load_crawler_aggregates(crawler_db, matches, category)
            if agg.get("novel_count"):
                bits = [f"{agg['novel_count']} 部作品"]
                if agg.get("avg_words"):
                    bits.append(f"平均 {agg['avg_words']:,} 字")
                if agg.get("opening_sample_count"):
                    bits.append(f"开篇样本 {agg['opening_sample_count']} 章")
                parts.append("市场基底（来自市场数据库）：" + " · ".join(bits))
                mc_line = _format_dist(agg.get("main_categories"), n=6)
                if mc_line:
                    parts.append(clip(f"在售主分类分布：{mc_line}", _CAP_PER_SUBSECTION))
                tag_line = _format_dist(agg.get("top_tags"), n=10)
                if tag_line:
                    parts.append(clip(f"高频标签：{tag_line}", _CAP_PER_SUBSECTION))

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


def diagnose(project_id: str) -> dict:
    """Step-by-step report on what the loader sees for ``project_id``.

    Returns a JSON-serializable dict listing the project's resolved
    platform / category, every stored platform identifier found in
    each data source, the matched aliases, and per-source flags telling
    whether ``_build_from_market_data`` was able to read content. Use
    this to debug "loader says 未注入 but I have data" — the report
    reveals which source the project's platform string failed to match.
    """
    try:
        from ui.backend.app.services.project_paths import get_db_path
        db_path = get_db_path()
    except Exception as e:
        return {"error": f"could not resolve project db: {e}"}
    crawler_db = _crawler_db_path()

    platform, category = _resolve_project_platform_category(db_path, project_id)
    stored = _scan_stored_platforms(db_path, crawler_db)
    matches = _data_driven_platform_matches(db_path, platform, crawler_db)

    profile_body = _load_active_profile(db_path, platform, category) if platform else ""
    aggregated = _load_aggregated_stats(db_path, platform, category) if platform else None
    opening_nlp = _load_opening_nlp_cache(db_path, platform) if platform else None
    trend = _load_analysis_run_cache(db_path, platform) if platform else None
    crawler_agg = (
        _load_crawler_aggregates(crawler_db, matches, category)
        if (crawler_db and matches) else {}
    )

    rendered = ""
    if platform:
        body = profile_body or _build_from_market_data(db_path, platform, category)
        if body:
            rendered = section(_TITLE, body)

    return {
        "project_id":          project_id,
        "project_db_path":     db_path,
        "crawler_db_path":     crawler_db or "(not configured / file missing)",
        "project_platform":    platform,
        "project_category":    category,
        "stored_platforms":    stored,
        "matched_aliases":     matches,
        "sources": {
            "platform_profiles":          {"present": bool(profile_body),
                                            "preview": (profile_body or "")[:160]},
            "category_aggregated_stats":  {"present": bool(aggregated),
                                            "keys": sorted((aggregated or {}).keys())[:8]},
            "opening_nlp_cache":          {"present": bool(opening_nlp),
                                            "sample_count": (opening_nlp or {}).get("sample_count")},
            "analysis_run_v4_cache":      {"present": bool(trend),
                                            "tag_rollup_n": len((trend or {}).get("tag_rollup") or [])},
            "crawler_db_aggregates":      {"present": bool(crawler_agg),
                                            "novel_count": crawler_agg.get("novel_count"),
                                            "main_categories": len(crawler_agg.get("main_categories") or []),
                                            "top_tags": len(crawler_agg.get("top_tags") or [])},
        },
        "rendered_length": len(rendered),
        "rendered_preview": rendered[:600],
    }
