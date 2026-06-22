"""Backend mirror of ``ui/frontend/src/utils/platforms.ts``.

The frontend's project form stores ``project.platform`` as the human
label (``"起点中文网"``), while the crawler DB / market extractor
write rows keyed by their own short form (``"起点"`` or ``"qidian"``).
Without a translation layer, the platform_market loader (and any other
loader joining on platform name) silently misses the data the user
expects to flow into the prompt.

``platform_candidates(name)`` returns every name we should try when
looking up rows for ``name`` — the literal value first (so an exact
crawler-store value still wins), then the canonical id / label / each
alias. Callers iterate the list and stop at the first hit; ordering
matters only to keep the most likely match first for performance.
"""
from __future__ import annotations


# Mirror PLATFORM_PROFILES from utils/platforms.ts. Keep both halves in
# sync — adding a platform here without updating the frontend (or vice
# versa) silently loses the alias chain.
_PLATFORM_PROFILES: list[dict] = [
    {
        "id": "qidian",
        "label": "起点中文网",
        "aliases": ["起点", "qidian", "起点中文"],
    },
    {
        "id": "fanqie",
        "label": "番茄小说",
        "aliases": ["番茄", "fanqie", "tomato"],
    },
    {
        "id": "other",
        "label": "其他",
        "aliases": ["other"],
    },
]


def _profile_for(name: str) -> dict | None:
    """Find the platform profile whose id / label / any alias matches
    ``name`` (case-insensitive). Returns ``None`` for unknown values."""
    norm = (name or "").strip().lower()
    if not norm:
        return None
    for p in _PLATFORM_PROFILES:
        if p["label"].lower() == norm or p["id"] == norm:
            return p
        if any((a or "").lower() == norm for a in p["aliases"]):
            return p
    # Substring fallback — same heuristic as the frontend's
    # platformProfile so e.g. "起点5月榜" still resolves to 起点中文网.
    for p in _PLATFORM_PROFILES:
        if p["id"] == "other":
            continue
        if p["label"].lower() in norm:
            return p
        if any(a and a.lower() in norm for a in p["aliases"]):
            return p
    return None


def platform_candidates(name: str) -> list[str]:
    """Return every name worth trying when querying the crawler /
    market-extractor tables for platform ``name``.

    The literal value comes first so an exact stored match is preferred;
    the rest of the chain is de-duplicated while preserving insertion
    order. Falls back to ``[name]`` when the platform isn't in the table
    so unknown values still get one query attempt.
    """
    literal = (name or "").strip()
    out: list[str] = []
    seen: set[str] = set()

    def _push(v: str) -> None:
        v = (v or "").strip()
        if not v or v in seen:
            return
        out.append(v)
        seen.add(v)

    if literal:
        _push(literal)
    profile = _profile_for(literal)
    if profile:
        _push(profile["id"])
        _push(profile["label"])
        for alias in profile["aliases"]:
            _push(alias)
    return out


def canonical_platform_id(name: str) -> str:
    """Return the canonical short id (``qidian`` / ``fanqie`` / ...) for
    ``name``, falling back to the literal value when unknown.

    Used by code paths that need ONE canonical key (e.g. the market_brief
    cache, the per-platform RAG suffix) instead of a candidate list.
    """
    profile = _profile_for(name or "")
    if profile:
        return profile["id"]
    return (name or "").strip()
