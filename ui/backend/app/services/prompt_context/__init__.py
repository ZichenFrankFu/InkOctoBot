"""RAG context builder package.

A read-only, no-LLM assembly path shared by 正文创作 generation
(``/api/generation/quick-generate``) and the prompt preview / copy flow.
Pure and defensive: every loader is wrapped so a missing collection,
empty DB or test mode degrades to ``""`` rather than raising.

Public API:
- ``build_generation_context``     — every RAG block keyed by block name
- ``build_rag_digest``              — single concatenated grounding text
- ``creation_context_manifest``     — RAG categories + items for the UI
- ``single_agent_vars``             — variable dict for single_agent template
- ``build_referenced_materials_block`` — chapter-tab linked refs
- ``load_chapter_fields``           — read chapter local fields
- ``active_writing_skill_names``    — display names of active learned skills
- ``creation_default_skills``       — built-in skills for the creation mode
- ``load_project_memory_block``     — project-memory loader public block
"""
from .builder import (
    build_generation_context,
    build_rag_digest,
    creation_context_manifest,
    single_agent_vars,
)
from .references import build_referenced_materials_block
from .chapter_fields import load_chapter_fields
from .skills_block import (
    active_learned_skills,
    active_writing_skill_names,
    creation_default_skills,
)
from .loaders.project_memory import public_block as load_project_memory_block

__all__ = [
    "build_generation_context",
    "build_rag_digest",
    "creation_context_manifest",
    "single_agent_vars",
    "build_referenced_materials_block",
    "load_chapter_fields",
    "active_learned_skills",
    "active_writing_skill_names",
    "creation_default_skills",
    "load_project_memory_block",
]
