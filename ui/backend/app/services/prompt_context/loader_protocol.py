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

    def to_block_provider(
        self, allocated_budget: int | None = None,
    ) -> "_LoaderBlockProviderAdapter":
        """Return a ``BlockProvider``-compatible adapter (roadmap Stage 3 Task 3.1).

        Lets PromptComposer consume LoaderPlan directly instead of
        maintaining a parallel block hierarchy. The adapter:

        - ``name``    = ``self.block_id``
        - ``role``    = mapped from ``priority_tier`` (4→system,
                        3→context, 2→context, 1→user_content) — same
                        mapping the builder's diagnostics
                        ``_SECTION_GROUPS`` uses
        - ``render(ctx)`` = ``self.render(allocated_budget or self.target)``
                            — the BlockProvider context arg is ignored
                            because LoaderPlan already captured the data
                            it needs at plan() time
        """
        return _LoaderBlockProviderAdapter(
            plan=self,
            allocated_budget=allocated_budget if allocated_budget is not None
                              else self.target,
        )


def _tier_to_role(priority_tier: int) -> str:
    if priority_tier == 4:
        return "system"
    if priority_tier == 1:
        return "user_content"
    return "context"


@dataclass
class _LoaderBlockProviderAdapter:
    """Stateless adapter: makes a LoaderPlan satisfy the BlockProvider
    protocol. Lives here (not in prompt_composer.py) to keep the
    LoaderPlan owner the canonical source of truth — adding a new
    tier mapping or attribute is a one-place edit."""

    plan: "LoaderPlan"
    allocated_budget: int

    @property
    def name(self) -> str:
        return self.plan.block_id

    @property
    def role(self) -> str:
        # PromptComposer recognizes 'user_content' and 'context'.
        # Map system → context (system-level info still goes into the
        # context window via BaseAgent.invoke(context=...)).
        role = _tier_to_role(self.plan.priority_tier)
        return role if role == "user_content" else "context"

    def render(self, ctx: object = None) -> str:
        """Render the block at the captured budget. The ``ctx`` arg is
        the PromptComposer ``PromptContext`` — ignored here because
        LoaderPlan already snapshotted its data at plan() time."""
        try:
            return self.plan.render(self.allocated_budget) or ""
        except Exception:
            return ""


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
