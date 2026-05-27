"""Platform market directive loader (placeholder).

Planned design: ingest the openings (chapters 2–5) of many works from
the market database, extract per-platform "opening features" and
"platform work characteristics" (recurring hooks, scene structures,
tone, pacing, POV choices, etc.), and inject the user's target-platform
profile as a craft-grounded directive for chapter generation.

Implementation status: TODO. Until the extraction pipeline lands,
``plan()`` returns ``None`` so the loader is treated as inactive (zero
budget). ``load()`` returns ``""`` for the same reason — preserves the
contract every other v3 loader uses.
"""
from __future__ import annotations

import logging

from ..loader_protocol import LoaderPlan

logger = logging.getLogger("inkoctobot.services.prompt_context.platform_market")


def plan(project_id: str, exclude: set | None = None) -> LoaderPlan | None:
    """Inactive placeholder — see module docstring for the planned design."""
    return None


def load(project_id: str, exclude: set | None = None) -> str:
    """Back-compat thin wrapper. Always returns ``""``."""
    return ""
