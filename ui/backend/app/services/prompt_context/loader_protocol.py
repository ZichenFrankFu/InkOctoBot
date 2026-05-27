"""Loader plan protocol — shared between the builder and every loader.

Each loader exposes two callables:

- ``plan(...) -> LoaderPlan | None`` performs the data lookups (DB
  queries, embedding round-trips, etc.) and returns a small struct
  carrying:

    * the *natural* character length the loader would emit at no cap,
    * its priority tier (1-4, lower wins under pressure),
    * the (min, target, max) character budget triple, and
    * a render closure that takes a final allocated budget and produces
      the actual block string.

  ``plan()`` returns ``None`` when the loader is inactive (no data,
  user excluded, required input missing). Inactive loaders consume
  zero budget and don't appear in the rendered output.

- ``load(...) -> str`` is the back-compat one-shot entry; it calls
  ``plan()`` and renders at the spec target budget. New code should
  call ``plan()`` and let the builder allocate.

The same closure used in ``render`` is responsible for honouring
``budget`` via ``clip()`` — natural_length is the upper-bound on what
``render`` will emit at ``budget >= natural_length``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


@dataclass
class LoaderPlan:
    """One plan returned by a loader's ``plan(...)`` entry."""

    block_id: str
    natural_length: int
    minimum: int
    target: int
    maximum: int
    priority_tier: int
    render: Callable[[int], str]
    # Optional debug payload — loaders can stash anything that helps
    # surface what they DID query (row counts, top-k cutoffs, etc.).
    debug: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Normalize: min ≤ target ≤ max; natural ≥ 0.
        self.minimum = max(0, int(self.minimum))
        self.target = max(self.minimum, int(self.target))
        self.maximum = max(self.target, int(self.maximum))
        self.natural_length = max(0, int(self.natural_length))
        if self.priority_tier < 1:
            self.priority_tier = 1
        if self.priority_tier > 4:
            self.priority_tier = 4


# Header overhead for ``section()`` output ("\n\n## TITLE\n").
_SECTION_OVERHEAD = 6


def make_plan(
    block_id: str,
    title: str,
    body: str,
    *,
    debug: dict | None = None,
) -> "LoaderPlan | None":
    """Compose a ``LoaderPlan`` from a fully-formatted body string.

    ``body`` should be the loader's rendered content *without* the
    ``\\n\\n## title\\n`` wrapper — ``render(budget)`` re-applies the
    section header and clips the body to ``budget - section overhead``.

    Returns ``None`` when ``body`` is empty (inactive loader).
    """
    body = (body or "").strip()
    if not body:
        return None
    # Local import — avoid circular dependency at module load.
    from .budget_allocator import LOADER_BUDGETS
    from .utils import clip, section

    cfg = LOADER_BUDGETS[block_id]
    overhead = len(title) + _SECTION_OVERHEAD
    natural = overhead + len(body)

    def render(budget: int) -> str:
        return section(title, clip(body, max(0, budget - overhead)))

    return LoaderPlan(
        block_id=block_id,
        natural_length=natural,
        minimum=cfg["min"], target=cfg["target"], maximum=cfg["max"],
        priority_tier=cfg["tier"],
        render=render,
        debug=debug or {},
    )
