"""ChapterContext — the canonical "chapter being generated" object.

Single source of truth for everything the generation pipeline (single
agent or multi agent) needs about a chapter:

  - Identifiers (project_id / chapter_id / chapter_num / generation_mode)
  - 14-loader output (loader_blocks + sections + diagnostics)
  - TruthBundle (the 7-file truth-files snapshot)
  - User inputs (on_stage_entities / pov_character / scene_types /
    user_special_requirements)

Two operations:

    context = await ChapterContext.build(req)   # builds RAG + truth bundle
    version_id = await context.commit(content, audit_result=...)
                                                # persists text_versions
                                                # + fires post-commit

Replaces the ad-hoc ``req_data["world_rules"]`` string folding +
``_persist_and_trigger_post_commit`` helper pattern that existed in
generation_api.py — that path stays for back-compat, but new code
should go through ChapterContext.
"""
from .chapter_context import (
    BuildRequest,
    ChapterContext,
    CommitBlocked,
)

__all__ = ["BuildRequest", "ChapterContext", "CommitBlocked"]
