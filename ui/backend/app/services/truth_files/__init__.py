"""TruthFilesIntegration — single canonical interface to Truth Files.

Wraps the lower-level helpers in
``agents/production/storyland_state_integration.py`` (which stay
backward-compatible for any existing caller) behind a class with the
3-method contract from roadmap 2026 Q2 Stage 2:

    bundle = TruthFilesIntegration.build_bundle(chapter_id, chapter_num)
    audit  = await TruthFilesIntegration.apply_settlement(chapter_id, content)
    ok     = TruthFilesIntegration.check_audit_gate(chapter_id)

The class is what ChapterContext / multi-agent /start should depend on;
single-writer endpoint and any other legacy caller can keep using the
raw functions until they migrate.
"""
from .integration import (
    AuditResult,
    TruthBundle,
    TruthFilesIntegration,
)

__all__ = ["AuditResult", "TruthBundle", "TruthFilesIntegration"]
