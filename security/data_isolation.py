"""
Data Isolation — project-level data isolation and cleanup.

README §5.1: Each project's data is stored in its own directory
under data/projects/{project_id}/. Supports one-click cleanup.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

logger = logging.getLogger("inkoctobot.security.data_isolation")


class DataIsolation:
    """Manages project-level data isolation."""

    def __init__(self, data_dir: str | Path = "data/projects"):
        self.data_dir = Path(data_dir)

    def create_project_dir(self, project_id: str) -> Path:
        """Create isolated project directory structure."""
        proj_dir = self.data_dir / project_id
        subdirs = ["chapters", "characters", "volumes", "exports", "lora"]
        for sub in subdirs:
            (proj_dir / sub).mkdir(parents=True, exist_ok=True)
        logger.info("Created project directory: %s", proj_dir)
        return proj_dir

    def project_exists(self, project_id: str) -> bool:
        return (self.data_dir / project_id).is_dir()

    def get_project_path(self, project_id: str) -> Path:
        return self.data_dir / project_id

    def list_projects(self) -> list[str]:
        """List all project IDs."""
        if not self.data_dir.exists():
            return []
        return [d.name for d in self.data_dir.iterdir() if d.is_dir()]

    def get_project_size(self, project_id: str) -> dict[str, Any]:
        """Get project disk usage."""
        proj_dir = self.data_dir / project_id
        if not proj_dir.exists():
            return {"exists": False}
        total = sum(f.stat().st_size for f in proj_dir.rglob("*") if f.is_file())
        file_count = sum(1 for f in proj_dir.rglob("*") if f.is_file())
        return {
            "exists": True,
            "total_bytes": total,
            "total_mb": round(total / 1024 / 1024, 2),
            "file_count": file_count,
        }

    def cleanup_project(self, project_id: str, *, keep_exports: bool = True) -> bool:
        """Delete a project's data. Optionally keep exports."""
        proj_dir = self.data_dir / project_id
        if not proj_dir.exists():
            return False
        if keep_exports:
            exports = proj_dir / "exports"
            temp_exports = proj_dir.parent / f"_exports_{project_id}"
            if exports.exists():
                shutil.move(str(exports), str(temp_exports))
            shutil.rmtree(proj_dir)
            if temp_exports.exists():
                proj_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(temp_exports), str(proj_dir / "exports"))
        else:
            shutil.rmtree(proj_dir)
        logger.info("Cleaned up project: %s", project_id)
        return True

    def export_project(self, project_id: str, output_path: str | Path) -> Path:
        """Export a project as a zip archive."""
        proj_dir = self.data_dir / project_id
        if not proj_dir.exists():
            raise FileNotFoundError(f"Project {project_id} not found")
        output = Path(output_path)
        shutil.make_archive(str(output.with_suffix("")), "zip", str(proj_dir))
        return output.with_suffix(".zip")
