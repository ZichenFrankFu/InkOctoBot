"""Platform market directive loader.

Composes a single ``## 平台风格基线`` block from the real extracted
features the user has produced in 市场特征提取. By design this loader
covers BOTH 基础特征 and 高级特征 — there is no longer a separate
``market_overview`` block. Inputs:

  · 高级特征  — ``platform_profiles`` row (the most recent active one
                for the project's platform / category). Six subsections
                are rendered independently so a noisy one can't drown
                the rest:
                  - 平台综述           (profile_summary)
                  - 风格基线           (style_baseline)
                  - 节奏指南           (pacing_guidance)
                  - 招牌叙事手法       (signature_devices_description)
                  - 专有名词           (neologism_step2_json — renamed
                                        from "生造词Step2" per UX request)
                  - 行文风格           (style_dimensions_json A1-G2)
  · 基础特征  — ``compute_cache[opening_nlp:<platform>]`` — the same
                payload the 基础特征 tab visualizes. Rendered via
                ``opening_stats.render_stats_for_prompt`` so the prompt's
                wording matches the tab (标点 / 词性 / 情感 / 高频词).

Crawler-DB direct aggregation and the ``analysis_run_v4`` trend cache
are intentionally NOT injected: by the time the user has settled on a
book project, the market basis and trend exploration belong on the
extraction tabs, not in every chapter prompt.

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
_CAP_PER_SUBSECTION = 320      # chars — default for short subsections
# 行文风格 七组 (A1-G2) 信息密度最大, 直接关系 writer 风格落地, 单独给
# 更高的预算上限 —— 用户明确要求 "增加行文风格 token".
_CAP_STYLE_DIMENSIONS = 1200
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

    Two-pass matching:
    1. **Canonical alias bridge** — both the project value and each
       stored value are normalised through
       ``platform_aliases.canonicalize_platform`` (qidian ↔ 起点,
       fanqie ↔ 番茄, 起点中文网 → 起点, …). When the canonical forms
       agree, the stored value is treated as an exact match. This is
       what bridges crawler-side English slugs to project-side Chinese
       labels.
    2. **Substring fallback** — when the alias map doesn't cover a
       platform we still do the original case-insensitive substring
       match (exact > project ⊃ stored > stored ⊃ project) so newly-added
       platforms work without touching the alias table.

    Returns ``[]`` when nothing overlaps; the loader then surfaces a
    "no real data for this platform" hint to the user.
    """
    if not (project_platform or "").strip():
        return []
    from ui.backend.app.services.platform_aliases import canonicalize_platform
    pp_canon = canonicalize_platform(project_platform).lower()
    pp = project_platform.strip().lower()
    if not crawler_db:
        crawler_db = _crawler_db_path()
    stored = _scan_stored_platforms(db_path, crawler_db)

    alias_hits: list[str] = []
    exact: list[str] = []
    project_contains: list[str] = []
    stored_contains: list[str] = []
    for v in stored:
        vl = v.lower()
        v_canon = canonicalize_platform(v).lower()
        # 1. Canonical alias match — bridges qidian ↔ 起点中文网.
        if pp_canon and v_canon and pp_canon == v_canon:
            alias_hits.append(v)
            continue
        # 2. Substring fallback — original three tiers.
        if vl == pp:
            exact.append(v)
        elif vl in pp:
            project_contains.append(v)
        elif pp in vl:
            stored_contains.append(v)
    return alias_hits + exact + project_contains + stored_contains


def _load_active_profile_row(
    db_path: str, platform: str, category: str,
) -> dict | None:
    """Return the latest non-superseded ``platform_profiles`` row for the
    project's (platform, category), as a dict. Used to render the six
    advanced-feature subsections the user explicitly listed
    (profile_summary / style_baseline / pacing_guidance /
    signature_devices_description / neologism_step2_json /
    style_dimensions_json).

    Lookup tiers — exact match → platform-wide ``category=''`` (what the
    manual-submit path writes) → any row for the platform. Every tier is
    tried against each alias produced by ``_data_driven_platform_matches``.
    Returns ``None`` when no row matches; the caller then degrades to
    rendering only basic features.

    Uses ``SELECT *`` so DBs on an older schema (missing newer columns
    like ``style_dimensions_json`` / ``neologism_step2_json`` / the
    ``superseded_by_profile_id`` column) still resolve a row instead of
    blowing up the entire SELECT and silently returning None.
    """
    matches = _data_driven_platform_matches(db_path, platform)
    # Emergency fallback: if the scanner missed the project's platform
    # string (rare schema-skew edge case), still try the verbatim value.
    # The downstream SELECT uses ``WHERE platform = ?`` which is exact,
    # so adding the project string itself can only RECOVER a missed
    # match — never introduce a wrong one.
    if (platform or "").strip() and platform not in matches:
        matches = list(matches) + [platform.strip()]
    if not matches:
        return None
    # Detect available columns once so we can build a WHERE clause that
    # only references columns the schema actually has.
    try:
        with sqlite3.connect(db_path) as con:
            cols = {
                r[1] for r in con.execute("PRAGMA table_info(platform_profiles)")
            }
    except sqlite3.OperationalError:
        return None
    if not cols:
        return None
    extra_where = ""
    if "superseded_by_profile_id" in cols:
        extra_where += " AND superseded_by_profile_id IS NULL"
    if "confidence_label" in cols:
        extra_where += (
            " AND (confidence_label IS NULL OR confidence_label != 'low')"
        )
    order_clause = (
        " ORDER BY profile_version DESC"
        if "profile_version" in cols else ""
    )
    has_category = "category" in cols
    base_select = (
        "SELECT * FROM platform_profiles WHERE platform = ?" + extra_where
    )
    candidates: list[tuple[str, tuple]] = []
    for plat_alias in matches:
        if category and has_category:
            candidates.append((
                base_select + " AND category = ?" + order_clause + " LIMIT 1",
                (plat_alias, category),
            ))
        if has_category:
            candidates.append((
                base_select
                + " AND (category = '' OR category IS NULL)"
                + order_clause + " LIMIT 1",
                (plat_alias,),
            ))
        candidates.append((
            base_select + order_clause + " LIMIT 1",
            (plat_alias,),
        ))
    try:
        with sqlite3.connect(db_path) as con:
            con.row_factory = sqlite3.Row
            for sql, params in candidates:
                try:
                    row = con.execute(sql, params).fetchone()
                except sqlite3.OperationalError as e:
                    logger.debug(
                        "platform_directive: SELECT failed for alias %r: %s",
                        params, e,
                    )
                    continue
                if row:
                    return dict(row)
    except sqlite3.OperationalError as e:
        logger.debug("platform_directive: profile_row connect failed: %s", e)
        return None
    logger.debug(
        "platform_directive: no active profile for platform=%r category=%r "
        "(data-driven matches: %s, available cols: %s)",
        platform, category, matches, sorted(cols))
    return None


def _load_active_profile(db_path: str, platform: str, category: str) -> str:
    """Back-compat shim — returns the legacy single-string body. New code
    should use ``_load_active_profile_row`` directly so each subsection
    can be rendered with its own cap. Kept for older callers / tests."""
    row = _load_active_profile_row(db_path, platform, category)
    if not row:
        return ""
    payload = (row.get("loader_payload") or "").strip()
    if payload:
        return payload
    parts: list[str] = []
    summary = (row.get("profile_summary") or "").strip()
    if summary:
        parts.append(summary)
    devices = (row.get("signature_devices_description") or "").strip()
    if devices:
        parts.append(f"代表手法：{devices}")
    style = _coerce_payload_field(row.get("style_baseline"))
    if style:
        parts.append(f"风格基线：{style}")
    pacing = _coerce_payload_field(row.get("pacing_guidance"))
    if pacing:
        parts.append(f"节奏指南：{pacing}")
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
    # Emergency fallback: try the verbatim project platform too, in case
    # the scanner missed it (e.g. compute_cache rows written before the
    # opening_nlp key prefix existed).
    if (platform or "").strip() and platform not in matches:
        matches = list(matches) + [platform.strip()]
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


def _render_advanced_subsections(row: dict) -> list[str]:
    """Render the six 高级特征 subsections from a ``platform_profiles``
    row. Each subsection is emitted as ``### 子段标题\\n<body>`` so the
    prompt has stable in-block anchors, and each body is clipped to
    ``_CAP_PER_SUBSECTION`` chars independently — a noisy single field
    can't crowd out the others.

    Returns ``[]`` when the row carries nothing useful for any subsection;
    the caller then falls back to rendering only 基础特征.
    """
    parts: list[str] = []

    def _emit(title: str, body: str) -> None:
        body = (body or "").strip()
        if not body:
            return
        parts.append(f"### {title}\n{clip(body, _CAP_PER_SUBSECTION)}")

    _emit("平台综述", (row.get("profile_summary") or "").strip())
    _emit("风格基线", _coerce_payload_field(row.get("style_baseline")))
    _emit("节奏指南", _coerce_payload_field(row.get("pacing_guidance")))
    _emit("招牌叙事手法",
          (row.get("signature_devices_description") or "").strip())

    # 专有名词 (formerly 生造词Step2) — proper_nouns / person_names /
    # place_names / common_chars / naming_patterns from the JSON blob.
    neo = _safe_json(row.get("neologism_step2_json")) or {}
    if isinstance(neo, dict):
        neo_bits: list[str] = []
        for key, label in [
            ("proper_nouns",     "专有名词"),
            ("person_names",     "人名"),
            ("place_names",      "地名"),
            ("common_chars",     "常见字"),
        ]:
            vals = neo.get(key) or []
            if isinstance(vals, list) and vals:
                neo_bits.append(
                    f"{label}：" + "、".join(str(v) for v in vals[:10] if v)
                )
        nm_pat = (neo.get("naming_patterns") or "").strip()
        if nm_pat:
            neo_bits.append(f"构词模式：{nm_pat}")
        if neo_bits:
            _emit("专有名词", "；".join(neo_bits))

    # 行文风格 — the seven (A-G) dimension groups. Render each group as
    # one line; field labels match the UI's ``DIM_FIELD_LABELS`` so a
    # writer who has clicked through the extraction tab sees the same
    # vocabulary in the prompt.
    dims = _safe_json(row.get("style_dimensions_json")) or {}
    if isinstance(dims, dict) and dims:
        group_labels = {
            "A_protagonist": "A·主角", "B_social": "B·社会",
            "C_world":       "C·世界", "D_hook":   "D·钩子",
            "E_style":       "E·风格", "F_info":   "F·信息",
            "G_pacing":      "G·节奏",
        }
        field_labels = {
            "A1_appearance":       "登场",
            "A2_image":            "形象",
            "A3_cheat":            "金手指",
            "A4_agency":           "主动性",
            "A5_drive":            "驱动",
            "B2_ensemble":         "聚焦",
            "C1_type":             "类型",
            "C2_unfold":           "铺展",
            "C3_contrast":         "突破点",
            "D1_opening_hook":     "开篇钩",
            "D2_early_payoff":     "前期爽",
            "D3_chapter_end_hooks": "章末钩",
            "E1_writing_style":    "风格",
            "E2_emotion":          "情绪",
            "F1_disclosure":       "信息揭露",
            "F2_volume_concept":   "卷一目标",
            "G1_rhythm":           "节奏",
            "G2_early_strategy":   "前5章策略",
        }
        group_lines: list[str] = []
        for gkey, glabel in group_labels.items():
            grp = dims.get(gkey)
            if not isinstance(grp, dict):
                continue
            field_bits: list[str] = []
            for fkey, fval in grp.items():
                # B1_network is deliberately dropped from the prompt —
                # it lists specific characters & relationships from the
                # source works, which over-fits the writer's prompt to
                # those reference books instead of a general baseline.
                if fkey == "B1_network":
                    continue
                txt = _coerce_payload_field(fval)
                if not txt:
                    continue
                flabel = field_labels.get(fkey, fkey)
                field_bits.append(f"{flabel}：{txt}")
            if field_bits:
                group_lines.append(f"{glabel} — " + "；".join(field_bits))
        if group_lines:
            # 行文风格 用单独的高预算 (用户要求): 默认 320 字不够装下 6 组
            # × 平均 80 字 = 480 字, 截下来只剩 3 组. 提到 1200 字基本能保住
            # 所有组都进 prompt.
            body_text = "\n".join(group_lines)
            parts.append(
                f"### 行文风格\n{clip(body_text, _CAP_STYLE_DIMENSIONS)}"
            )

    # Legacy-profile fallback: an older extraction may have populated
    # ONLY ``loader_payload`` (the LLM-written 600-1200字 baseline
    # paragraph) without filling the six structured fields above. Surface
    # the payload as a single "整段画像" subsection so those projects
    # don't regress to 未注入 in RAG预览 just because their schema is older.
    # Allowed budget: 2 × _CAP_PER_SUBSECTION (~640 chars) to keep prompt
    # tight while preserving most of the writing baseline.
    if not parts:
        legacy = (row.get("loader_payload") or "").strip()
        if legacy:
            parts.append(
                f"### 整段画像\n{clip(legacy, _CAP_PER_SUBSECTION * 2)}"
            )

    return parts


def _render_basic_features(db_path: str, platform: str) -> str:
    """Render 基础特征 via ``opening_stats.render_stats_for_prompt``.

    The cached payload's ``spec_stats`` field is exactly what the 基础特征
    tab visualizes; rendering it with the shared helper keeps the prompt
    wording in sync with the UI (标点密度 / 词性分布 / 情感占比 / 高频词 /
    生造词Step1 候选).

    Legacy fallback: pre-``spec_stats`` cache rows still expose
    ``word_count_summary`` / ``dialogue_ratio`` / ``sentence_length`` /
    ``first_sentence_types`` / ``end_hook_types`` at the top level. When
    the new field is missing we fall back to those so existing projects
    don't show 未注入 just because their cache is older.

    Returns ``""`` when no ``opening_nlp`` cache row matches.
    """
    nlp = _load_opening_nlp_cache(db_path, platform)
    if not nlp:
        return ""
    spec = nlp.get("spec_stats") or {}
    if isinstance(spec, dict) and spec.get("available"):
        try:
            from ui.backend.app.services.market_extractor.opening_stats import (
                render_stats_for_prompt,
            )
        except Exception as e:
            logger.debug("opening_stats import failed: %s", e)
            return _render_legacy_basic_features(nlp)
        body = render_stats_for_prompt(spec).strip()
        if body:
            return clip(body, _CAP_PER_SUBSECTION * 2)
    # spec_stats absent or unavailable → degrade to the legacy fields the
    # cache always has so the loader still emits something useful.
    return _render_legacy_basic_features(nlp)


def _render_legacy_basic_features(nlp: dict) -> str:
    """Render the legacy ``opening_nlp`` cache shape (pre-spec_stats) —
    word counts, dialogue ratio, average sentence length, first-sentence /
    end-hook type top-3. Each row is at most one line; the whole block
    fits well under one ``_CAP_PER_SUBSECTION`` budget.
    """
    bits: list[str] = []
    sample = int(nlp.get("sample_count") or 0)
    novels = int(nlp.get("unique_novels") or 0)
    if sample or novels:
        bits.append(f"- 样本：{novels} 部 / {sample} 章")
    wcs = nlp.get("word_count_summary") or {}
    if isinstance(wcs, dict) and wcs.get("mean"):
        bits.append(
            f"- 开篇字数 均 {int(wcs.get('mean') or 0)} "
            f"(min {int(wcs.get('min') or 0)} / max {int(wcs.get('max') or 0)})"
        )
    dr = nlp.get("dialogue_ratio") or {}
    if isinstance(dr, dict) and dr.get("mean") is not None:
        bits.append(f"- 开篇对话占比 均 {float(dr.get('mean') or 0):.1%}")
    sl = nlp.get("sentence_length") or {}
    if isinstance(sl, dict) and sl.get("mean"):
        bits.append(f"- 开篇平均句长 {float(sl.get('mean') or 0):.1f} 字")
    for field, label in [
        ("first_sentence_types", "首句类型"),
        ("end_hook_types",       "尾钩类型"),
    ]:
        items = nlp.get(field) or []
        if not isinstance(items, list) or not items:
            continue
        line_bits: list[str] = []
        for it in items[:3]:
            if not isinstance(it, dict):
                continue
            lab = str(it.get("label") or "").strip()
            cnt = it.get("count")
            if not lab:
                continue
            line_bits.append(f"{lab} {int(cnt)}" if cnt is not None else lab)
        if line_bits:
            bits.append(f"- {label}：" + " · ".join(line_bits))
    body = "\n".join(bits).strip()
    if not body:
        return ""
    return clip(body, _CAP_PER_SUBSECTION * 2)


def _build_from_market_data(
    db_path: str, platform: str, category: str,
) -> str:
    """Compose the platform-style block from real extracted features —
    one ``### 高级特征`` group (six subsections) and one ``### 基础特征``
    block. Each subsection is clipped independently so a noisy field
    can't drown the rest.
    """
    parts: list[str] = []
    row = _load_active_profile_row(db_path, platform, category)
    if row:
        advanced = _render_advanced_subsections(row)
        if advanced:
            parts.extend(advanced)

    basic = _render_basic_features(db_path, platform)
    if basic:
        parts.append(f"### 基础特征（开篇 NLP）\n{basic}")

    return "\n\n".join(parts).strip()


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

    # Always render through the structured builder — six advanced
    # subsections (each independently clipped) + 基础特征 from the
    # opening_nlp cache. The legacy ``loader_payload`` is a single
    # 600-1200字 paragraph that loses subsection structure, so it's no
    # longer surfaced as the primary path.
    payload = _build_from_market_data(db_path, platform, category)
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
    platform / category, every stored platform identifier found, the
    matched aliases, and per-source flags telling whether the structured
    builder was able to read content. Use this to debug "loader says
    未注入 but I have data" — the report reveals which source the
    project's platform string failed to match.
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

    profile_row = (
        _load_active_profile_row(db_path, platform, category) if platform else None
    )
    opening_nlp = _load_opening_nlp_cache(db_path, platform) if platform else None
    body = (
        _build_from_market_data(db_path, platform, category) if platform else ""
    )

    rendered = section(_TITLE, body) if body else ""

    profile_fields_present = []
    if profile_row:
        for f in ("profile_summary", "style_baseline", "pacing_guidance",
                   "signature_devices_description",
                   "neologism_step2_json", "style_dimensions_json"):
            if (profile_row.get(f) or "").strip() if isinstance(profile_row.get(f), str) else profile_row.get(f):
                profile_fields_present.append(f)

    return {
        "project_id":          project_id,
        "project_db_path":     db_path,
        "crawler_db_path":     crawler_db or "(not configured / file missing)",
        "project_platform":    platform,
        "project_category":    category,
        "stored_platforms":    stored,
        "matched_aliases":     matches,
        "sources": {
            "platform_profiles":   {"present": bool(profile_row),
                                     "fields_present": profile_fields_present,
                                     "confidence": (profile_row or {}).get("confidence_label")},
            "opening_nlp_cache":   {"present": bool(opening_nlp),
                                     "sample_count": (opening_nlp or {}).get("sample_count"),
                                     "spec_stats_available":
                                       bool(((opening_nlp or {}).get("spec_stats") or {}).get("available"))},
        },
        "rendered_length": len(rendered),
        "rendered_preview": rendered[:600],
    }
