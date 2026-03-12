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