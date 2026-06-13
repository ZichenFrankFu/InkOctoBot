"""Platform category taxonomy — modular config (resources/taxonomy/*.json).

Externalizes the 起点 大分类 / 副分类 taxonomy that used to be hardcoded in
the frontend. The backend now classifies market-panel rows with it so the
UI is taxonomy-agnostic (reads ``panel`` items' ``parent`` field instead of
a hardcoded sub→main map). Cached per process.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parent / "resources" / "taxonomy"


@lru_cache(maxsize=1)
def load_qidian() -> dict:
    """{'main_categories': [...], 'sub_to_main': {副分类: 大分类}}."""
    try:
        blob = json.loads((_ROOT / "qidian.json").read_text("utf-8"))
        return {
            "main_categories": list(blob.get("main_categories") or []),
            "sub_to_main": dict(blob.get("sub_to_main") or {}),
        }
    except Exception:
        return {"main_categories": [], "sub_to_main": {}}


def qidian_main_set() -> frozenset[str]:
    return frozenset(load_qidian()["main_categories"])


def qidian_sub_to_main() -> dict[str, str]:
    return load_qidian()["sub_to_main"]
