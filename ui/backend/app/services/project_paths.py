"""Project path resolution.

The project data directory location depends on test mode / PyInstaller
bundling / explicit overrides. This module is the single place callers
should ask "where is the database for this project?".
"""
from __future__ import annotations

import logging

logger = logging.getLogger("inkoctobot.services.project_paths")


def get_db_path() -> str:
    """Resolve the path to the project database (``novels.db``).

    Priority (highest wins):
      1. Test mode + ``WN_DATA_DIR`` → ``<data_dir>/novels.db`` — this
         used to be ignored, which made every test-mode caller (and
         every debug endpoint) read from the live ``data/novels.db``
         instead of the isolated ``data_test/novels.db``.
      2. ``config.py:DATABASE['path']`` via paths.yaml.
      3. Default ``<repo>/data/novels.db``.
    """
    from ui.backend.app.settings import settings as app_settings

    # 1. Test mode override — single most important fix point. Without
    # this, /api/debug/* endpoints in --test mode read live data and
    # silently report "table missing" because they were looking at the
    # wrong database file all along.
    if app_settings.test_mode and app_settings.data_dir:
        return str(app_settings.data_dir / "novels.db")

    # 2. paths.yaml
    try:
        from ui.backend.app.utils import load_repo_config, get_db_path as _resolve
        repo_cfg = load_repo_config(app_settings.repo_root)
        return _resolve(repo_cfg, app_settings.repo_root)
    except Exception as e:
        logger.debug("falling back to default db path: %s", e)
        return str(app_settings.repo_root / "data" / "novels.db")
