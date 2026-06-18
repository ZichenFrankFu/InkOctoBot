"""Self-contained data dir for the standalone name pre-trainer.

All generated state (name_library.db, wordlist overlays, refresh marker)
lives under ``<folder>/data/`` so the whole tool is portable — copy the
folder anywhere and it keeps its own library.
"""
from __future__ import annotations

import os
from pathlib import Path


def base_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    env = os.environ.get("NAME_TRAINER_DATA_DIR")
    d = Path(env) if env else base_dir() / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def name_db_path() -> str:
    """人名库 sqlite 路径（项目库角色）。"""
    return str(data_dir() / "name_library.db")
