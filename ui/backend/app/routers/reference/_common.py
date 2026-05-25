"""Shared helpers for the reference/ sub-routers.

The original ``reference_api.py`` had a single private ``_db()`` helper
+ a couple of shared constants used across many endpoints. Centralizing
them here prevents each sub-router from re-implementing the same
config-resolution dance.
"""
from __future__ import annotations

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


def db() -> ReferenceDB:
    """Open the reference DB respecting test mode / config overrides.

    Replaces the old private ``_db()`` in reference_api.py. Test mode
    points at ``data_test/novels.db``; otherwise we honor the repo's
    paths.yaml; otherwise default to ``data/novels.db``.
    """
    if settings.test_mode and settings.data_dir:
        return ReferenceDB(str(settings.data_dir / "novels.db"))
    try:
        repo_cfg = load_repo_config(settings.repo_root)
        return ReferenceDB(get_db_path(repo_cfg, settings.repo_root))
    except FileNotFoundError:
        return ReferenceDB(str(settings.repo_root / "data" / "novels.db"))
