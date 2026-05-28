"""Stage 4 — typed Request/Response schemas for the 4 production agents.

Before this stage the pipeline passed ``dict[str, Any]`` between
SceneDirector → SceneSimulator → Writer → Evaluator. That worked at
runtime but made it impossible to:

  * swap an agent's input shape without grepping every call site
  * validate that the LLM output dict matches what downstream expects
  * surface a typed snapshot of agent I/O in observability tables

These pydantic models are the source of truth for each agent's
contract. All fields tolerate ``extra="allow"`` so LLM dicts with
extra keys (or future fields) don't fail validation — the contracts
are additive, not enforcing.

Adoption strategy: each agent's legacy method (``plan_scenes``,
``assemble_chapter``, etc.) is unchanged. A typed wrapper method
``*_typed(request: <X>Request) -> <X>Response`` lives alongside it,
so call sites can migrate one by one.
"""
from __future__ import annotations

from .scene_director import (
    SceneDirectorRequest,
    SceneDirectorResponse,
    ScenePlanItem,
    CharacterInstruction,
    KnowledgeBoundary,
)
from .scene_simulator import (
    SceneSimulatorRequest,
    SceneSimulatorResponse,
    SceneSimulationResult,
)
from .writer import (
    WriterAssembleRequest,
    WriterWriteRequest,
    WriterResponse,
)
from .evaluator import (
    EvaluatorRequest,
    EvaluatorResponse,
    EvaluationIssue,
    DimensionScores,
)


__all__ = [
    # SceneDirector
    "SceneDirectorRequest", "SceneDirectorResponse",
    "ScenePlanItem", "CharacterInstruction", "KnowledgeBoundary",
    # SceneSimulator
    "SceneSimulatorRequest", "SceneSimulatorResponse",
    "SceneSimulationResult",
    # Writer
    "WriterAssembleRequest", "WriterWriteRequest", "WriterResponse",
    # Evaluator
    "EvaluatorRequest", "EvaluatorResponse",
    "EvaluationIssue", "DimensionScores",
]
