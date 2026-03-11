"""Tests for core.config."""
from pathlib import Path
from core.config import AppConfig


class TestAppConfig:
    def test_load_from_project_root(self):
        """Should load config from default config/ directory."""
        cfg = AppConfig()
        assert cfg.config_dir.exists()

    def test_app_db_path(self):
        cfg = AppConfig()
        assert "novels.db" in str(cfg.app_db_path) or "data" in str(cfg.app_db_path)

    def test_get_missing_returns_default(self):
        cfg = AppConfig()
        result = cfg.get("nonexistent", "key", "default_val")
        assert result == "default_val"

    def test_reload(self):
        cfg = AppConfig()
        cfg.reload()  # Should not raise
