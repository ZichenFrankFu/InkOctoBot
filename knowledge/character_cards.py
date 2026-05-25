"""
Character Card Manager — Layer A (natural language descriptions).

README §3.1: Manages character cards with personality, speech style,
relationship networks, canonical scenario-response examples, and growth trajectory.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("inkoctobot.knowledge.character_cards")


class CharacterCardManager:
    """Manages character cards stored as YAML files per project."""

    def __init__(self, project_dir: str | Path):
        self.chars_dir = Path(project_dir) / "characters"
        self.chars_dir.mkdir(parents=True, exist_ok=True)

    def create_character(self, name: str, **fields: Any) -> dict[str, Any]:
        """Create a new character card."""
        card: dict[str, Any] = {
            "name": name,
            "aliases": fields.get("aliases", []),
            "role": fields.get("role", "supporting"),
            "personality": fields.get("personality", ""),
            "appearance": fields.get("appearance", ""),
            "speech_style": fields.get("speech_style", ""),
            "background": fields.get("background", ""),
            "motivations": fields.get("motivations", []),
            "relationships": fields.get("relationships", {}),
            "canonical_responses": fields.get("canonical_responses", []),
            "growth_trajectory": fields.get("growth_trajectory", ""),
            "tags": fields.get("tags", []),
            "notes": fields.get("notes", ""),
        }
        self._save(name, card)
        return card

    def get_character(self, name: str) -> dict[str, Any] | None:
        path = self._path(name)
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)

    def update_character(self, name: str, **fields: Any) -> dict[str, Any] | None:
        card = self.get_character(name)
        if not card:
            return None
        card.update({k: v for k, v in fields.items() if v is not None})
        self._save(name, card)
        return card

    def delete_character(self, name: str) -> bool:
        path = self._path(name)
        if path.exists():
            path.unlink()
            return True
        return False

    def list_characters(self) -> list[dict[str, Any]]:
        cards = []
        for p in sorted(self.chars_dir.glob("*.yaml")):
            with open(p, "r", encoding="utf-8") as f:
                card = yaml.safe_load(f)
                if card:
                    cards.append(card)
        return cards

    def get_character_prompt(self, name: str) -> str:
        """Build a natural-language prompt section for a character."""
        card = self.get_character(name)
        if not card:
            return ""
        parts = [f"角色: {card.get('name', name)}"]
        if card.get("role"):
            parts.append(f"定位: {card['role']}")
        if card.get("personality"):
            parts.append(f"性格: {card['personality']}")
        if card.get("appearance"):
            parts.append(f"外貌: {card['appearance']}")
        if card.get("speech_style"):
            parts.append(f"说话风格: {card['speech_style']}")
        if card.get("background"):
            parts.append(f"背景: {card['background']}")
        if card.get("motivations"):
            parts.append(f"动机: {', '.join(card['motivations'])}")
        if card.get("relationships"):
            rels = [f"{k}: {v}" for k, v in card["relationships"].items()]
            parts.append(f"人际关系: {'; '.join(rels)}")
        if card.get("canonical_responses"):
            parts.append("典型反应:")
            for cr in card["canonical_responses"][:5]:
                if isinstance(cr, dict):
                    parts.append(f"  情境: {cr.get('scenario', '')} → 反应: {cr.get('response', '')}")
                else:
                    parts.append(f"  - {cr}")
        return "\n".join(parts)

    def get_all_prompts(self) -> dict[str, str]:
        """Get prompt text for all characters."""
        return {c["name"]: self.get_character_prompt(c["name"]) for c in self.list_characters()}

    def _path(self, name: str) -> Path:
        safe_name = name.replace("/", "_").replace("\\", "_")
        return self.chars_dir / f"{safe_name}.yaml"

    def _save(self, name: str, card: dict) -> None:
        with open(self._path(name), "w", encoding="utf-8") as f:
            yaml.dump(card, f, allow_unicode=True, default_flow_style=False, sort_keys=False)
