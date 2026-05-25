"""Shared helpers for the reference/ sub-routers.

The original ``reference_api.py`` had a single private ``_db()`` helper
+ a couple of shared constants used across many endpoints. Centralizing
them here prevents each sub-router from re-implementing the same
config-resolution dance.
"""
from __future__ import annotations

import re

from knowledge.reference_db import ReferenceDB
from ...settings import settings
from ...utils import load_repo_config, get_db_path


# Allowed enum values for ReferenceWork.serial_status (used by works.py).
SERIAL_STATUS_VALUES = frozenset({"ongoing", "completed", "hiatus", "unknown"})

# Localized media type labels — used by prompts (web_search, chronicle,
# plot_outline) so the LLM sees the genre in Chinese.
MEDIA_TYPE_ZH = {
    "web_novel": "网文小说", "literature": "文学作品", "poetry": "诗歌作品",
    "film": "电影", "anime": "动漫", "tv_series": "电视剧", "other": "作品",
}

# Editable analysis field whitelist — used by /works/{id}/analysis PUT.
ANALYSIS_FIELDS = frozenset({
    "style_fingerprint_json",
    "narrative_structure_json",
    "extracted_characters_json",
    "rhythm_template_json",
    "plot_outline_json",
    "settings_json",
    "rhythm_json",
})


def strip_json_blob(raw: str) -> str:
    """Pull a JSON object out of a string that may contain markdown fences.

    Shared by web_search.py (ai_complete) and analysis_writer.py
    (plot_outline/summarize) — both call LLMs that sometimes wrap
    their JSON in ```json ... ``` despite explicit instructions not to.
    """
    s = (raw or "").strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, re.DOTALL)
    if fence:
        s = fence.group(1).strip()
    a = s.find("{")
    b = s.rfind("}")
    if 0 <= a < b:
        s = s[a:b + 1]
    return s


def _data_dir():
    """Resolve the active data directory (honors test mode override)."""
    if settings.test_mode and settings.data_dir:
        return settings.data_dir
    return settings.repo_root / "data"


def reference_db_path() -> str:
    """Resolve ``data/reference.db`` (or test-mode equivalent).

    During the rename transition this falls back to ``novels.db`` when
    a legacy ``reference.db`` doesn't exist yet — so existing installs
    keep working until they migrate.
    """
    d = _data_dir()
    new = d / "reference.db"
    legacy = d / "novels.db"
    return str(new if new.exists() or not legacy.exists() else legacy)


def idea_db_path() -> str:
    """Resolve ``data/idea.db`` (or test-mode equivalent).

    During the rename transition this falls back to ``novels.db`` when
    a legacy ``idea.db`` doesn't exist yet.
    """
    d = _data_dir()
    new = d / "idea.db"
    legacy = d / "novels.db"
    return str(new if new.exists() or not legacy.exists() else legacy)


def db() -> ReferenceDB:
    """Open the reference DB (``data/reference.db``).

    Replaces the old private ``_db()`` in reference_api.py. Test mode
    points at ``data_test/reference.db``; otherwise we honor the
    repo's paths.yaml; otherwise default to ``data/reference.db``.
    """
    return ReferenceDB(reference_db_path())


def idea_db():
    """Open the IdeaDB for the inspirations / 灵感库."""
    from knowledge.idea_db import IdeaDB
    return IdeaDB(idea_db_path())
