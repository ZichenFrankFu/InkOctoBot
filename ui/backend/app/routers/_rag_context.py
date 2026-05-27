"""Compatibility shim — the real implementation lives in
``ui.backend.app.services.prompt_context``.

This file is kept so the many callers of e.g.
``from ui.backend.app.routers._rag_context import build_generation_context``
keep working without churn. New code should import from the services
package directly.

v3.1: ``build_skills_block`` and ``_load_writing_skills`` re-exports
were dropped — the new ``loaders/skills.py`` owns prompt-side skill
injection. Callers that need a plain skill-name list should use
``active_writing_skill_names()``.
"""
from __future__ import annotations

from ui.backend.app.services.prompt_context import (
    active_learned_skills,
    active_writing_skill_names,
    build_generation_context,
    build_referenced_materials_block,
    build_rag_digest,
    creation_context_manifest,
    creation_default_skills,
    load_chapter_fields,
    load_project_memory_block,
    single_agent_vars,
)

__all__ = [
    "build_generation_context",
    "build_rag_digest",
    "build_referenced_materials_block",
    "creation_context_manifest",
    "load_chapter_fields",
    "load_project_memory_block",
    "single_agent_vars",
    "active_writing_skill_names",
    "active_learned_skills",
    "creation_default_skills",
]
