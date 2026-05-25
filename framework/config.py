"""
Unified configuration loader.

Loads YAML config files with environment variable overrides and validation.
Supersedes the root-level config.py thin compatibility layer.
"""
from __future__ import annotations

import os
import sys
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("inkoctobot.core.config")

# ------------------------------------------------------------------
# BASE_DIR: source tree vs PyInstaller bundle
# ------------------------------------------------------------------
if hasattr(sys, "_MEIPASS"):
    if os.name == "nt":
        BASE_DIR = Path(
            os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        ) / "InkOctoBot"
    else:
        BASE_DIR = Path.home() / ".local" / "share" / "InkOctoBot"
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    _CONFIG_DIR = Path(sys._MEIPASS) / "config"
else:
    BASE_DIR = Path(__file__).resolve().parent.parent
    _CONFIG_DIR = BASE_DIR / "config"


def _load_yaml(filename: str) -> dict[str, Any]:
    """Load a YAML file from the config directory; return {} if missing."""
    path = _CONFIG_DIR / filename
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _env_override(yaml_val: Any, env_key: str) -> Any:
    """Return the environment variable value if set, else the YAML value."""
    return os.environ.get(env_key, yaml_val)


class AppConfig:
    """Centralized, validated application configuration."""

    def __init__(self, config_dir: str | Path | None = None):
        if config_dir is not None:
            self._config_dir = Path(config_dir)
        else:
            self._config_dir = _CONFIG_DIR

        self._raw: dict[str, dict[str, Any]] = {}
        self._load_all()

    def _load_all(self) -> None:
        for name in ["app_config", "models", "paths", "websites"]:
            self._raw[name] = self._load(f"{name}.yaml")

    def _load(self, filename: str) -> dict[str, Any]:
        path = self._config_dir / filename
        if not path.exists():
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    # --- Convenience accessors ---

    @property
    def app(self) -> dict[str, Any]:
        return self._raw.get("app_config", {})

    @property
    def models(self) -> dict[str, Any]:
        return self._raw.get("models", {})

    @property
    def paths(self) -> dict[str, Any]:
        return self._raw.get("paths", {})

    @property
    def project_defaults(self) -> dict[str, Any]:
        return self.app.get("project", {})

    @property
    def memory_config(self) -> dict[str, Any]:
        return self.app.get("memory", {})

    @property
    def evaluation_config(self) -> dict[str, Any]:
        return self.app.get("evaluation", {})

    @property
    def cost_config(self) -> dict[str, Any]:
        return self.app.get("cost", {})

    # --- Database paths ---

    @property
    def app_db_path(self) -> Path:
        rel = self.paths.get("database", {}).get("path", "data/novels.db")
        return BASE_DIR / rel

    @property
    def crawler_db_path(self) -> Path:
        rel = self.paths.get("crawler_database", {}).get(
            "path", "data/InkOctoBot_Crawler.db"
        )
        return BASE_DIR / rel

    @property
    def config_dir(self) -> Path:
        return self._config_dir

    def get(self, section: str, key: str, default: Any = None) -> Any:
        return self._raw.get(section, {}).get(key, default)

    def reload(self) -> None:
        self._load_all()
        logger.info("Configuration reloaded from %s", self._config_dir)


# Module-level singleton (lazy)
_instance: AppConfig | None = None


def get_config() -> AppConfig:
    """Return the global AppConfig singleton."""
    global _instance
    if _instance is None:
        _instance = AppConfig()
    return _instance
