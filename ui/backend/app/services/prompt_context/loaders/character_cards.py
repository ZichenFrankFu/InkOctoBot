"""On-stage character card loader."""
from __future__ import annotations

import logging

from ..budgets import BUDGETS
from ..utils import clip, section

logger = logging.getLogger("inkoctobot.services.prompt_context.character_cards")


def load(project_id: str, names: list[str], exclude: set | None = None) -> str:
    """Build deep character cards for the chapter's on-stage characters."""
    if not names:
        return ""
    try:
        from ui.backend.app.services import project_store
        from ui.backend.app.services.project_paths import get_db_path

        rows = project_store.list_characters(get_db_path(), project_id)
        by_name = {r.get("name", ""): r for r in rows}
        cards: list[str] = []
        for name in names:
            if exclude and name in exclude:
                continue
            c = by_name.get(name)
            if not c:
                continue
            lines = [f"【{name}】" + (f"（{c['role']}）" if c.get("role") else "")]
            if c.get("appearance"):
                lines.append(f"  外貌：{c['appearance']}")
            if c.get("personality"):
                lines.append(f"  性格：{c['personality']}")
            if c.get("background"):
                lines.append(f"  背景：{c['background']}")
            if c.get("speech_style"):
                lines.append(f"  说话风格：{c['speech_style']}")
            rels = c.get("relationships") or []
            if isinstance(rels, list) and rels:
                rel_txt = "；".join(
                    f"{r.get('target_name', '')}"
                    + (f"（{r.get('label')}）" if r.get("label") else "")
                    for r in rels[:6]
                    if r.get("target_name")
                )
                if rel_txt:
                    lines.append(f"  关系：{rel_txt}")
            cards.append("\n".join(lines))
        if not cards:
            return ""
        return section("出场角色档案", clip("\n\n".join(cards), BUDGETS["character_cards"]))
    except Exception as e:
        logger.debug("character cards skipped: %s", e)
        return ""
