"""
World Book Manager — manages world-building rules and settings.

README §3.2: Structured storage for world settings with AI consistency
checking between entries.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("inkoctobot.knowledge.world_book")


class WorldBookManager:
    """Manages world-building entries stored in YAML."""

    def __init__(self, project_dir: str | Path):
        self.project_dir = Path(project_dir)
        self.wb_path = self.project_dir / "world_book.yaml"

    def _load(self) -> dict[str, Any]:
        if not self.wb_path.exists():
            return {"categories": {}, "rules": [], "metadata": {}}
        with open(self.wb_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {"categories": {}, "rules": [], "metadata": {}}

    def _save(self, data: dict[str, Any]) -> None:
        self.wb_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.wb_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

    def add_entry(self, category: str, title: str, content: str, **fields: Any) -> dict:
        """Add a world-building entry under a category."""
        wb = self._load()
        cats = wb.setdefault("categories", {})
        entries = cats.setdefault(category, [])
        entry = {
            "title": title,
            "content": content,
            "tags": fields.get("tags", []),
            "priority": fields.get("priority", "normal"),
        }
        entries.append(entry)
        self._save(wb)
        return entry

    def get_category(self, category: str) -> list[dict]:
        wb = self._load()
        return wb.get("categories", {}).get(category, [])

    def list_categories(self) -> list[str]:
        wb = self._load()
        return list(wb.get("categories", {}).keys())

    def get_all_entries(self) -> dict[str, list[dict]]:
        return self._load().get("categories", {})

    def add_rule(self, rule: str, rule_type: str = "hard") -> None:
        """Add a world-building rule."""
        wb = self._load()
        rules = wb.setdefault("rules", [])
        rules.append({"rule": rule, "type": rule_type})
        self._save(wb)

    def get_rules(self) -> list[dict]:
        return self._load().get("rules", [])

    def get_summary(self, max_length: int = 3000) -> str:
        """Build a text summary of the world book for LLM context."""
        wb = self._load()
        parts = ["[世界书摘要]"]

        rules = wb.get("rules", [])
        if rules:
            parts.append("\n## 世界观规则")
            for r in rules:
                parts.append(f"- [{r.get('type', 'hard')}] {r['rule']}")

        categories = wb.get("categories", {})
        for cat_name, entries in categories.items():
            parts.append(f"\n## {cat_name}")
            for entry in entries:
                parts.append(f"### {entry.get('title', '')}")
                content = entry.get("content", "")
                if len(content) > 500:
                    content = content[:500] + "..."
                parts.append(content)

        text = "\n".join(parts)
        if len(text) > max_length:
            text = text[:max_length] + "\n...[已截断]"
        return text

    def update_entry(self, category: str, title: str, **fields: Any) -> bool:
        wb = self._load()
        entries = wb.get("categories", {}).get(category, [])
        for entry in entries:
            if entry.get("title") == title:
                entry.update({k: v for k, v in fields.items() if v is not None})
                self._save(wb)
                return True
        return False

    def delete_entry(self, category: str, title: str) -> bool:
        wb = self._load()
        entries = wb.get("categories", {}).get(category, [])
        original_len = len(entries)
        wb["categories"][category] = [e for e in entries if e.get("title") != title]
        if len(wb["categories"][category]) < original_len:
            self._save(wb)
            return True
        return False
