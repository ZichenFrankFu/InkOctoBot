"""On-stage character card loader.

Renders the base character card PLUS the most-relevant
``dynamic_snapshots`` entry (chapter-linked override of personality /
background / speech_style / Layer B / hidden identities).

Snapshot selection:
- If ``chapter_num > 0`` and any snapshot has a parseable chapter number
  ≤ ``chapter_num``, the latest such snapshot wins.
- Otherwise, the last snapshot in the array wins.
- If the character has no snapshots, only the base card is rendered.

Which characters get loaded is controlled by ``names`` (the user-edited
"on-stage characters" field on the chapter), further filterable by the
``exclude`` set populated from the UI's manifest de-selection panel.
"""
from __future__ import annotations

import logging
import re

from ..budgets import BUDGETS
from ..utils import clip, section

logger = logging.getLogger("inkoctobot.services.prompt_context.character_cards")


_LAYER_B_LABELS = {
    "loss_aversion":       "损失厌恶",
    "risk_aversion_gain":  "收益侧风险厌恶",
    "risk_aversion_loss":  "损失侧风险厌恶",
    "impulse_probability": "冲动概率",
    "social_frequency":    "社交频率",
    "time_discount":       "时间贴现",
}

_VALUE_WEIGHT_LABELS = {
    "survival": "生存", "power": "权力", "love": "情感",
    "loyalty":  "忠诚", "justice": "正义", "freedom": "自由",
}


def _parse_chapter_num(s: str) -> int | None:
    """Pull the first integer out of a free-form chapter marker.

    "第5章" → 5, "Chapter 12" → 12, "三年后" → None.
    """
    if not s:
        return None
    m = re.search(r"\d+", str(s))
    return int(m.group(0)) if m else None


def _pick_snapshot(snaps: list[dict], chapter_num: int) -> dict | None:
    """Choose the snapshot whose chapter is closest to (but not after)
    ``chapter_num``. Falls back to the latest snapshot when no parseable
    chapter numbers exist.
    """
    if not snaps:
        return None
    if chapter_num > 0:
        candidates = []
        for s in snaps:
            n = _parse_chapter_num(s.get("chapter", ""))
            if n is not None and n <= chapter_num:
                candidates.append((n, s))
        if candidates:
            candidates.sort(key=lambda t: t[0])
            return candidates[-1][1]
    return snaps[-1]


def _format_layer_b(lb: dict) -> str:
    """Compact one-liner of the quantitative decision parameters."""
    if not isinstance(lb, dict) or not lb:
        return ""
    bits: list[str] = []
    for key, label in _LAYER_B_LABELS.items():
        if key in lb and lb[key] is not None:
            try:
                bits.append(f"{label} {float(lb[key]):.2g}")
            except (TypeError, ValueError):
                continue
    vw = lb.get("value_weights")
    if isinstance(vw, dict) and vw:
        weights = []
        for vk, vv in vw.items():
            try:
                label = _VALUE_WEIGHT_LABELS.get(vk, vk)
                weights.append(f"{label} {float(vv):.0%}")
            except (TypeError, ValueError):
                continue
        if weights:
            bits.append("价值权重：" + "、".join(weights))
    return "；".join(bits)


def _format_hidden_identities(items: list[dict]) -> list[str]:
    """One bullet line per hidden identity (snapshot-scoped)."""
    if not isinstance(items, list):
        return []
    out: list[str] = []
    for h in items:
        if not isinstance(h, dict):
            continue
        name = (h.get("name") or "").strip()
        if not name:
            continue
        revealed = [
            str(r).strip() for r in (h.get("revealed_to") or [])
            if str(r or "").strip()
        ]
        notes = (h.get("notes") or "").strip()
        line = f"  - 化名「{name}」"
        if revealed:
            line += f"（已知真相：{'、'.join(revealed)}）"
        else:
            line += "（无人知晓真实身份）"
        if notes:
            line += f"；{notes}"
        out.append(line)
    return out


def load(
    project_id: str,
    names: list[str],
    exclude: set | None = None,
    chapter_num: int = 0,
) -> str:
    """Build deep character cards for the chapter's on-stage characters.

    ``names`` comes from the chapter's user-edited on-stage list.
    ``exclude`` is the manifest-driven per-character de-selection set.
    ``chapter_num`` selects the appropriate ``dynamic_snapshots`` entry.
    """
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

            snap = _pick_snapshot(c.get("dynamic_snapshots") or [], chapter_num)
            # Snapshot values override base fields when non-empty;
            # missing snapshot fields fall back to the base card.
            personality = (snap or {}).get("personality") or c.get("personality")
            background = (snap or {}).get("background") or c.get("background")
            speech_style = (snap or {}).get("speech_style") or c.get("speech_style")
            layer_b = (snap or {}).get("layer_b") or c.get("layer_b")
            hidden = (snap or {}).get("hidden_identities") or []

            lines = [f"【{name}】" + (f"（{c['role']}）" if c.get("role") else "")]
            if c.get("appearance"):
                lines.append(f"  外貌：{c['appearance']}")
            if personality:
                lines.append(f"  性格：{personality}")
            if background:
                lines.append(f"  背景：{background}")
            if speech_style:
                lines.append(f"  说话风格：{speech_style}")
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
            lb_txt = _format_layer_b(layer_b or {})
            if lb_txt:
                lines.append(f"  决策倾向（Layer B）：{lb_txt}")
            hidden_lines = _format_hidden_identities(hidden)
            if hidden_lines:
                lines.append("  隐藏身份 / 化名：")
                lines.extend(hidden_lines)
            if snap and (snap.get("chapter") or "").strip():
                lines.append(f"  ↑ 上述性格 / 背景 / 决策参数 / 化名取自快照「{snap['chapter']}」")
            cards.append("\n".join(lines))
        if not cards:
            return ""
        return section("出场角色档案", clip("\n\n".join(cards), BUDGETS["character_cards"]))
    except Exception as e:
        logger.debug("character cards skipped: %s", e)
        return ""
