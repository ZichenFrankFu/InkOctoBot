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


class Settings(BaseSettings):
    repo_root: Path = _default_repo_root()
    python_bin: str = sys.executable          # 打包后用 sys.executable 而非 "python"
    allow_outputs_dirname: str = "outputs"


settings = Settings()