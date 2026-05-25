"""Project path resolution.

The project data directory location depends on test mode / PyInstaller
bundling / explicit overrides. This module is the single place callers
should ask "where is the database for this project?".
"""
from __future__ import annotations

import logging

logger = logging.getLogger("inkoctobot.services.project_paths")


def get_db_path() -> str:
    """Resolve the path to the project database (``data/novels.db``).

    Honors ``WN_DATA_DIR`` / test-mode overrides via the
    ``ui.backend.app.settings`` singleton and the repo-config helpers in
    ``ui.backend.app.utils``. Falls back to a sensible default if config
    parsing fails so the app remains usable.
    """
    from ui.backend.app.settings import settings as app_settings
    try:
        from ui.backend.app.utils import load_repo_config, get_db_path as _resolve
        repo_cfg = load_repo_config(app_settings.repo_root)
        return _resolve(repo_cfg, app_settings.repo_root)
    except Exception as e:
        logger.debug("falling back to default db path: %s", e)
        return str(app_settings.repo_root / "data" / "novels.db")
