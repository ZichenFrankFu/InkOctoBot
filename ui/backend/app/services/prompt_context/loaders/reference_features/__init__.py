"""Reference-work feature helpers (LOADER_SPEC Loader 3).

Each module exposes ``load_for_work(work, **kwargs) -> str`` and
``score_by_outline(work, outline_vec) -> float``. The main reference
loader composes them; the helpers don't know about each other.
"""
from . import characters, plot, worldview, text_features, exemplars

__all__ = ["characters", "plot", "worldview", "text_features", "exemplars"]
