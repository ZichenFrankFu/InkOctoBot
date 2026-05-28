# ui/backend/app/settings.py
from __future__ import annotations

import os
import sys
from pathlib import Path

from pydantic_settings import BaseSettings


def _default_repo_root() -> Path:
    """
    优先级:
    1. 环境变量 WN_REPO_ROOT（launcher.py 设置的）
    2. PyInstaller _MEIPASS
    3. 源码态：settings.py 往上 3 层
    """
    env = os.environ.get("WN_REPO_ROOT")
    if env:
        return Path(env)
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[3]


def _default_data_dir() -> Path | None:
    """If WN_DATA_DIR is set (test mode), override the data directory."""
    env = os.environ.get("WN_DATA_DIR")
    return Path(env) if env else None


class Settings(BaseSettings):
    repo_root: Path = _default_repo_root()
    data_dir: Path | None = _default_data_dir()
    test_mode: bool = os.environ.get("WN_TEST_MODE", "") == "1"
    python_bin: str = sys.executable          # 打包后用 sys.executable 而非 "python"
    allow_outputs_dirname: str = "outputs"

    def get_data_path(self, *parts: str) -> Path:
        """Resolve a path under the data directory, respecting test mode override."""
        base = self.data_dir if self.data_dir else self.repo_root / "data"
        return base.joinpath(*parts) if parts else base


settings = Settings()


# ── embedding settings helpers (EMBEDDING_SPEC § 3) ────────────────
#
# The active model + language mode live in ``data/settings.json``
# alongside the legacy ``embedding_backend`` key. These helpers keep
# the JSON read/write path centralized so the EmbeddingService and
# the embedding API endpoints don't duplicate it.

import json as _json


_DEFAULT_LANGUAGE_MODE = "zh"
_DEFAULT_MODEL_KEY = "bge-base-zh"


def _embedding_settings_path() -> Path:
    return settings.get_data_path("settings.json")


def _read_embedding_settings_blob() -> dict:
    p = _embedding_settings_path()
    if not p.exists():
        return {}
    try:
        return _json.loads(p.read_text("utf-8"))
    except Exception:
        return {}


def get_embedding_language_mode() -> str:
    blob = _read_embedding_settings_blob()
    mode = str(blob.get("embedding_language_mode") or _DEFAULT_LANGUAGE_MODE)
    return mode if mode in ("zh", "en") else _DEFAULT_LANGUAGE_MODE


def get_embedding_model_key() -> str:
    blob = _read_embedding_settings_blob()
    return str(blob.get("embedding_model_key") or _DEFAULT_MODEL_KEY)


def set_embedding_settings(
    *, language_mode: str | None = None, model_key: str | None = None,
) -> dict:
    """Persist embedding settings to ``data/settings.json``. Missing
    keys keep their existing value. Returns the post-update blob."""
    p = _embedding_settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    blob = _read_embedding_settings_blob()
    if language_mode is not None:
        blob["embedding_language_mode"] = language_mode
    if model_key is not None:
        blob["embedding_model_key"] = model_key
    p.write_text(_json.dumps(blob, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "embedding_language_mode": blob.get(
            "embedding_language_mode", _DEFAULT_LANGUAGE_MODE,
        ),
        "embedding_model_key": blob.get(
            "embedding_model_key", _DEFAULT_MODEL_KEY,
        ),
    }