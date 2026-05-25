"""Reference library routes — split from the original 4425-line
``reference_api.py``.

Each sub-module owns one concern's endpoints and is independently
testable. The package aggregates them all into a single ``router``
which the parent reference_api.py mounts so the original public URL
namespace (``/api/references/*``) is preserved without touching main.py.

Currently extracted (9 sub-routers):
  - works          /works CRUD, /upload, /files
                                          (parent entity for everything)
  - inspirations   /inspirations         (idea snippets)
  - entries        /entries              (per-work content slices)
  - links          /links                (project ⇄ reference-work)
  - stats          /stats, /stats/genres (aggregates)
  - lora           /lora/{train,status}  (fine-tuning trigger)
  - index          /search, /works/{id}/index/* (vector index + search)
  - patterns       /chapter_patterns, /author_note_keywords,
                   /garbled_patterns     (user-tunable regex)
  - web_search     /web_search/capability + /works/{id}/ai_complete

Pending (still in reference_api.py until next split pass):
  - preprocess + chapter editing  (largest remaining: ~1800 lines)
  - segments + chunks extract     (~870 lines)
  - analysis writer + prompts     (~820 lines)
"""
from fastapi import APIRouter

from .entries import router as _entries_router
from .index import router as _index_router
from .inspirations import router as _inspirations_router
from .links import router as _links_router
from .lora import router as _lora_router
from .patterns import router as _patterns_router
from .stats import router as _stats_router
from .web_search import router as _web_search_router
from .works import router as _works_router

# All sub-routers share the same /references prefix, applied by
# reference_api.py when it mounts this aggregator.
router = APIRouter()
router.include_router(_works_router)
router.include_router(_inspirations_router)
router.include_router(_entries_router)
router.include_router(_links_router)
router.include_router(_stats_router)
router.include_router(_lora_router)
router.include_router(_index_router)
router.include_router(_patterns_router)
router.include_router(_web_search_router)
