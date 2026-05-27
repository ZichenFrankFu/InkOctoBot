"""Platform market directive loader (placeholder).

Planned design: ingest the openings (chapters 2–5) of many works from the
market database, extract per-platform "opening features" and "platform
work characteristics" (recurring hooks, scene structures, tone, pacing,
POV choices, etc.), and inject the user's target-platform profile as a
craft-grounded directive for chapter generation.

Implementation status: TODO. Until the extraction pipeline lands, this
loader returns ``""`` so the prompt remains uncluttered. The contract
(``load(project_id, exclude=None) -> str``) matches the other loaders
so callers don't need to change when the real implementation arrives.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("inkoctobot.services.prompt_context.platform_market")


def load(project_id: str, exclude: set | None = None) -> str:
    """Placeholder — see module docstring for the planned design."""
    return ""
