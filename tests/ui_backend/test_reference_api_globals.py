"""Smoke test for reference_api globals that the preprocess endpoints depend on.

The reference_api.py file was split into the reference/ sub-package
incrementally. Several private helpers (``_load_author_keywords``,
``_db``, ``_MEDIA_ZH``, ``_SERIAL_STATUS_VALUES``) moved into
``reference/_common.py`` and ``reference/patterns.py``; the remaining
endpoints in reference_api.py still call those names.

When the user hit ``GET /works/{id}/preprocess/aside_paragraphs`` in
production they got ``NameError: name '_load_author_keywords' is not
defined`` because the re-import line was missing. This test guards
against that regression: every helper the not-yet-extracted endpoints
need MUST be in the reference_api module namespace.
"""
from __future__ import annotations

from ui.backend.app.routers import reference_api


REQUIRED_GLOBALS = [
    "_load_author_keywords",   # used by preprocess_start /
                               # preprocess_aside_paragraphs /
                               # rename_chapter (3 call sites)
    "_db",
    "_MEDIA_ZH",
    "_SERIAL_STATUS_VALUES",
]


def test_reference_api_required_globals_present():
    missing = [n for n in REQUIRED_GLOBALS if not hasattr(reference_api, n)]
    assert not missing, (
        f"reference_api lost helpers needed by un-extracted endpoints: "
        f"{missing}. Re-import them from ui.backend.app.routers.reference.*"
    )


def test_load_author_keywords_is_callable():
    # Specifically the one that hit the user.
    fn = getattr(reference_api, "_load_author_keywords")
    assert callable(fn)
    # And it returns a list (default: empty when no settings.json key set).
    result = fn()
    assert isinstance(result, list)
