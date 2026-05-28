"""Reference works loader (LOADER_SPEC Loader 3).

Five features per linked reference work: characters / plot / worldview /
text_features / exemplars. Each one supports an explicit-pick path
(user toggles it on in the reference-injection panel) and an
auto-pick path (top-K by outline similarity).

The user's per-project config lives in the ``reference_injection`` blob.
V1 shape: ``{selections: {ref_id: {characters: bool, settings: bool,
plot: bool, rhythm: bool}}}``. V2 (this loader) shape:

  {
    "version": 2,
    "explicit_selections": {
      <ref_id>: {
        "characters": bool, "plot": bool, "worldview": bool,
        "text_features": bool, "exemplars": bool
      }
    },
    "auto_top_k":  {"characters": 2, "plot": 2, "worldview": 2,
                     "text_features": 1, "exemplars": 3},
    "min_relevance": 0.3
  }

V1 blobs are upgraded in-memory on read (settings→worldview,
rhythm→text_features, exemplars=False default). The on-disk blob is
left alone until the next PUT — the reference-injection API does the
canonical write.

Each per-feature helper module (loaders/reference_features/<name>.py)
exposes ``load_for_work(work) -> str`` and ``score_by_outline(work,
outline_vec) -> float``. The main loader assembles the rendered output
grouped by work.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ..loader_protocol import LoaderPlan, make_plan
from ..utils import clip, embed_sync, section
from .reference_features import (
    characters as feat_characters,
    plot as feat_plot,
    worldview as feat_worldview,
    text_features as feat_text_features,
    exemplars as feat_exemplars,
)

logger = logging.getLogger("inkoctobot.services.prompt_context.reference")


FEATURES = ("characters", "plot", "worldview", "text_features", "exemplars")

_DEFAULT_TOP_K = {
    "characters": 2, "plot": 2, "worldview": 2,
    "text_features": 1, "exemplars": 3,
}

# V1 → V2 feature-name remap. V1 used different labels for two features.
_V1_FEATURE_MAP = {
    "characters": "characters",
    "plot":       "plot",
    "settings":   "worldview",
    "rhythm":     "text_features",
}


def upgrade_reference_injection(blob: dict) -> dict:
    """Return a V2 view of a possibly-V1 reference_injection blob.

    Idempotent — V2 blobs round-trip unchanged. Missing fields are
    filled with defaults so downstream code doesn't have to check.
    """
    blob = dict(blob or {})
    if int(blob.get("version", 1)) >= 2:
        # Already V2 — just ensure defaults.
        out = dict(blob)
        out.setdefault("explicit_selections", {})
        out.setdefault("auto_top_k", dict(_DEFAULT_TOP_K))
        out.setdefault("min_relevance", 0.3)
        return out

    # V1 → V2 conversion.
    v1_selections = blob.get("selections") or {}
    explicit: dict[str, dict[str, bool]] = {}
    for ref_id, feats in (v1_selections.items() if isinstance(v1_selections, dict) else []):
        if not isinstance(feats, dict):
            continue
        upgraded: dict[str, bool] = {f: False for f in FEATURES}
        for v1_key, v2_key in _V1_FEATURE_MAP.items():
            if feats.get(v1_key):
                upgraded[v2_key] = True
        explicit[ref_id] = upgraded
    return {
        "version": 2,
        "explicit_selections": explicit,
        "auto_top_k": dict(_DEFAULT_TOP_K),
        "min_relevance": 0.3,
    }


def _load_injection_blob(project_id: str) -> dict:
    try:
        from ui.backend.app.services import project_store
        from ui.backend.app.services.project_paths import get_db_path

        data = project_store.get_blob(
            get_db_path(), project_id, "reference_injection",
        )
        return upgrade_reference_injection(data or {})
    except Exception as e:
        logger.debug("reference_injection load skipped: %s", e)
        return upgrade_reference_injection({})


# ─────────── feature dispatcher ───────────


def _feature_module(feature: str):
    return {
        "characters":    feat_characters,
        "plot":          feat_plot,
        "worldview":     feat_worldview,
        "text_features": feat_text_features,
        "exemplars":     feat_exemplars,
    }.get(feature)


def _load_feature(feature: str, work: dict, **kwargs) -> str:
    mod = _feature_module(feature)
    if mod is None:
        return ""
    try:
        return mod.load_for_work(work, **kwargs) or ""
    except Exception as e:
        logger.debug("feature %s load_for_work failed: %s", feature, e)
        return ""


def _score_feature(feature: str, work: dict, outline_vec: list[float]) -> float:
    mod = _feature_module(feature)
    if mod is None:
        return float("-inf")
    try:
        return float(mod.score_by_outline(work, outline_vec))
    except Exception as e:
        logger.debug("feature %s score_by_outline failed: %s", feature, e)
        return float("-inf")


# ─────────── main loader ───────────


def _list_works(db_path: str, project_id: str) -> list[dict]:
    try:
        from knowledge.reference_db import ReferenceDB
        ref_db = ReferenceDB(db_path)
        links = ref_db.get_project_links(project_id)
        seen: set[str] = set()
        out: list[dict] = []
        for link in links:
            ref_id = link.get("ref_id")
            if not ref_id or ref_id in seen:
                continue
            seen.add(ref_id)
            work = ref_db.get_work(ref_id)
            if work:
                out.append(work)
        return out
    except Exception as e:
        logger.debug("list_works failed: %s", e)
        return []


_BLOCK = "reference"
_TITLE = "参考作品综合"


def plan(
    project_id: str,
    db_path: str,
    chapter_outline: str = "",
    *,
    scene_types: list[str] | None = None,
    exclude: set | None = None,
) -> LoaderPlan | None:
    body = _build_body(project_id, db_path, chapter_outline,
                         scene_types=scene_types, exclude=exclude)
    return make_plan(_BLOCK, _TITLE, body)


def load(
    project_id: str,
    db_path: str,
    chapter_outline: str = "",
    *,
    scene_types: list[str] | None = None,
    exclude: set | None = None,
) -> str:
    p = plan(project_id, db_path, chapter_outline,
              scene_types=scene_types, exclude=exclude)
    return p.render(p.target) if p else ""


def _build_body(
    project_id: str, db_path: str, chapter_outline: str,
    *, scene_types: list[str] | None = None, exclude: set | None = None,
) -> str:
    """Build the dual-path reference block.

    For each of the 5 features:
      1. Every work whose explicit_selections[ref_id][feature]=True gets
         the feature rendered for that work (mandatory inclusion).
      2. If the explicit set is smaller than auto_top_k[feature], top up
         from the remaining works by outline-similarity score, ignoring
         scores below ``min_relevance``.

    Output is grouped by work (one block per work that contributed any
    feature) plus a final exemplars section across all works.
    """
    cfg = _load_injection_blob(project_id)
    explicit = cfg.get("explicit_selections") or {}
    top_k = cfg.get("auto_top_k") or dict(_DEFAULT_TOP_K)
    min_relevance = float(cfg.get("min_relevance") or 0.3)

    works = _list_works(db_path, project_id)
    if not works:
        return ""

    excl = exclude or set()
    works = [w for w in works if w.get("ref_id") not in excl]
    if not works:
        return ""

    # Compute the outline vector once. If embedding is unavailable, we
    # can still serve the explicit picks (auto-pick degrades to "no
    # additions").
    outline_vec: list[float] = []
    if chapter_outline.strip():
        out = embed_sync([chapter_outline])
        if out and out[0]:
            outline_vec = out[0]

    # blocks_by_work[ref_id] = {feature: rendered_text}
    blocks_by_work: dict[str, dict[str, str]] = {}
    explicit_count: dict[str, int] = {f: 0 for f in FEATURES}

    for feature in FEATURES:
        explicit_picks = [
            wid for wid, feats in explicit.items()
            if isinstance(feats, dict) and feats.get(feature)
        ]
        # Explicit path — must include.
        for ref_id in explicit_picks:
            work = next((w for w in works if w.get("ref_id") == ref_id), None)
            if work is None:
                continue
            text = _load_feature(
                feature, work,
                chapter_outline=chapter_outline,
                scene_types=scene_types,
                db_path=db_path,
            )
            if text:
                blocks_by_work.setdefault(ref_id, {})[feature] = text
                explicit_count[feature] += 1

        # Auto path — top up to the budget.
        k = int(top_k.get(feature, _DEFAULT_TOP_K.get(feature, 2)))
        remaining = max(0, k - explicit_count[feature])
        if remaining <= 0 or not outline_vec:
            continue
        candidates = [
            w for w in works
            if w.get("ref_id") not in set(explicit_picks)
        ]
        scored = [
            (_score_feature(feature, w, outline_vec), w)
            for w in candidates
        ]
        scored.sort(key=lambda t: t[0], reverse=True)
        for score, w in scored[:remaining]:
            if score < min_relevance:
                break
            text = _load_feature(
                feature, w,
                chapter_outline=chapter_outline,
                scene_types=scene_types,
                db_path=db_path,
            )
            if text:
                blocks_by_work.setdefault(w["ref_id"], {})[feature] = text

    if not blocks_by_work:
        return ""

    # Render grouped by work. Order: works that have explicit picks
    # first (in their explicit_selections order), then auto-added.
    explicit_order = list(explicit.keys())
    ref_id_order = (
        [w for w in explicit_order if w in blocks_by_work]
        + [w for w in blocks_by_work if w not in explicit_order]
    )

    parts: list[str] = []
    feature_labels = {
        "characters":    "角色原型",
        "plot":          "剧情结构",
        "worldview":     "世界设定",
        "text_features": "文本特征",
        "exemplars":     "风格样例",
    }
    for ref_id in ref_id_order:
        feats = blocks_by_work.get(ref_id) or {}
        if not feats:
            continue
        work = next((w for w in works if w.get("ref_id") == ref_id), None)
        title = (work or {}).get("title") or ref_id
        # Note which features were explicit so the user can tell.
        explicit_tags = [
            feature_labels[f] for f in FEATURES
            if (explicit.get(ref_id) or {}).get(f)
            and f in feats
        ]
        tag_line = (
            f"（用户显式选定：{'、'.join(explicit_tags)}）"
            if explicit_tags else "（自动匹配）"
        )
        block_lines = [f"### 《{title}》{tag_line}"]
        for feature in FEATURES:
            text = feats.get(feature)
            if not text:
                continue
            block_lines.append("")
            block_lines.append(f"**{feature_labels[feature]}**：{text}")
        parts.append("\n".join(block_lines))

    return "\n\n".join(parts)
