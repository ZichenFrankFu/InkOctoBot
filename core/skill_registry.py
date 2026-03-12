"""
Skill discovery, registration, and hot-reload.

Inspired by OpenClaw's SKILL.md declarative discovery:
- Startup: scans agents/*/skills/ directories
- Runtime: watches agents/learned_skills/ for hot-reload
- All skills registered in a unified registry, queryable by name/tag
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agents.base_skill import BaseSkill, SkillMeta

logger = logging.getLogger("inkoctobot.core.skill_registry")


class SkillRegistry:
    """Central registry for all Skills (built-in and learned)."""

    def __init__(self) -> None:
        self._skills: dict[str, BaseSkill] = {}
        self._observer: Any = None  # watchdog observer (lazy)

    # ── Discovery ────────────────────────────────────────────────

    def scan_directory(self, base_path: Path) -> int:
        """Scan for SKILL.md + skill.py pairs; return count of loaded skills."""
        loaded = 0
        for skill_md in base_path.rglob("SKILL.md"):
            skill_py = skill_md.parent / "skill.py"
            if skill_py.exists():
                try:
                    skill = self._load_skill(skill_py)
                    self.register(skill)
                    loaded += 1
                except Exception as e:
                    logger.error(
                        "Failed to load skill from %s: %s", skill_py, e,
                    )
        logger.info("Scanned %s — loaded %d skills", base_path, loaded)
        return loaded

    def scan_all(self, agents_dir: Path | None = None) -> int:
        """Scan all standard skill directories."""
        if agents_dir is None:
            agents_dir = Path(__file__).resolve().parent.parent / "agents"
        return self.scan_directory(agents_dir)

    # ── Registration ─────────────────────────────────────────────

    def register(self, skill: BaseSkill) -> None:
        meta = skill.meta()
        if meta.name in self._skills:
            logger.warning("Overwriting skill: %s", meta.name)
        self._skills[meta.name] = skill
        logger.info(
            "Registered skill: %s (%s) v%s",
            meta.name, meta.display_name, meta.version,
        )

    def unregister(self, name: str) -> None:
        self._skills.pop(name, None)

    # ── Query ────────────────────────────────────────────────────

    def get(self, name: str) -> BaseSkill:
        """Get a skill by name. Raises KeyError if not found."""
        if name not in self._skills:
            raise KeyError(
                f"Skill '{name}' not found. "
                f"Available: {list(self._skills.keys())}"
            )
        return self._skills[name]

    def find_by_tags(self, tags: list[str]) -> list[BaseSkill]:
        """Find skills matching any of the given tags."""
        return [
            s for s in self._skills.values()
            if any(t in s.meta().tags for t in tags)
        ]

    def list_all(self) -> list[SkillMeta]:
        """List all registered skill metadata."""
        return [s.meta() for s in self._skills.values()]

    def has(self, name: str) -> bool:
        return name in self._skills

    @property
    def count(self) -> int:
        return len(self._skills)

    # ── Hot-reload (learned skills) ──────────────────────────────

    def watch_learned_skills(self, path: Path) -> None:
        """Start watching a directory for new/modified skills."""
        if self._observer is not None:
            # Already watching — skip duplicate start
            return
        try:
            from watchdog.observers import Observer
            from watchdog.events import FileSystemEventHandler

            class _Handler(FileSystemEventHandler):
                def __init__(self, registry: SkillRegistry):
                    self._registry = registry

                def on_created(self, event: Any) -> None:
                    if event.src_path.endswith("skill.py"):
                        p = Path(event.src_path)
                        skill_md = p.parent / "SKILL.md"
                        if skill_md.exists():
                            try:
                                skill = self._registry._load_skill(p)
                                self._registry.register(skill)
                                logger.info("Hot-loaded skill: %s", p)
                            except Exception as e:
                                logger.error("Hot-load failed: %s: %s", p, e)

            path.mkdir(parents=True, exist_ok=True)
            self._observer = Observer()
            self._observer.schedule(_Handler(self), str(path), recursive=True)
            self._observer.start()
            logger.info("Watching for learned skills: %s", path)
        except ImportError:
            logger.warning(
                "watchdog not installed — learned skill hot-reload disabled"
            )
        except RuntimeError:
            logger.warning(
                "Observer thread already started — skipping duplicate start"
            )

    def stop_watching(self) -> None:
        if self._observer is not None:
            self._observer.stop()
            self._observer.join()
            self._observer = None

    # ── Internal ─────────────────────────────────────────────────

    @staticmethod
    def _load_skill(skill_py: Path) -> BaseSkill:
        """Dynamically load a skill.py module and return its Skill() instance."""
        module_name = f"skill_{skill_py.parent.name}"
        spec = importlib.util.spec_from_file_location(module_name, skill_py)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load spec for {skill_py}")
        module = importlib.util.module_from_spec(spec)
        # Register in sys.modules so inspect.getfile() can resolve the source
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        skill_cls = getattr(module, "Skill", None)
        if skill_cls is None:
            raise AttributeError(
                f"{skill_py} must export a class named 'Skill'"
            )
        return skill_cls()
