"""
/api/skills — Skill registry management and execution.

Exposes the Agent-Skill framework to the UI: list, inspect, test, and
execute registered skills.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/skills", tags=["skills"])
logger = logging.getLogger("inkoctobot.ui.backend.skill_api")

# Singleton registry instance (lazy-init)
_registry = None


def _get_registry():
    global _registry
    if _registry is None:
        from core.skill_registry import SkillRegistry
        _registry = SkillRegistry()
        _registry.scan_all()
        # Start watching learned skills
        agents_dir = Path(__file__).resolve().parents[4] / "agents"
        learned_dir = agents_dir / "learned_skills"
        _registry.watch_learned_skills(learned_dir)
    return _registry


def _meta_to_dict(meta) -> dict[str, Any]:
    """Convert SkillMeta dataclass to a serializable dict."""
    d = asdict(meta)
    return d


def _skill_domain(skill) -> str:
    """Determine the agent domain from the skill's module path."""
    mod = type(skill).__module__ or ""
    # Module names like "skill_shuangdian_extract" from dynamic import
    # Fall back to checking the skill's file path if available
    try:
        import inspect
        src = inspect.getfile(type(skill))
        parts = Path(src).parts
        # Look for agents/<domain>/skills/<name>/skill.py
        if "agents" in parts:
            idx = parts.index("agents")
            if idx + 1 < len(parts):
                domain = parts[idx + 1]
                if domain not in ("base_skill.py", "base_agent.py", "__init__.py"):
                    return domain
    except Exception:
        pass
    return "unknown"


@router.get("")
def list_skills():
    """List all registered skills with metadata."""
    registry = _get_registry()
    skills = []
    for skill in registry._skills.values():
        meta = skill.meta()
        info = _meta_to_dict(meta)
        info["agent_domain"] = _skill_domain(skill)
        info["is_learned"] = "learned_skills" in (
            getattr(type(skill), "__module__", "") or ""
        )
        skills.append(info)
    return {"skills": skills, "total": len(skills)}


@router.get("/tags")
def list_tags():
    """List all unique tags across registered skills."""
    registry = _get_registry()
    tags: set[str] = set()
    for meta in registry.list_all():
        tags.update(meta.tags)
    return {"tags": sorted(tags)}


@router.get("/learned")
def list_learned_skills():
    """List skills from the learned_skills directory."""
    registry = _get_registry()
    learned = []
    for skill in registry._skills.values():
        domain = _skill_domain(skill)
        if domain == "learned_skills":
            meta = skill.meta()
            info = _meta_to_dict(meta)
            info["agent_domain"] = domain
            info["is_learned"] = True
            learned.append(info)
    return {"skills": learned, "total": len(learned)}


@router.get("/{name}")
def get_skill(name: str):
    """Get detailed info for a single skill, including SKILL.md content."""
    registry = _get_registry()
    if not registry.has(name):
        raise HTTPException(404, f"Skill '{name}' not found")
    skill = registry.get(name)
    meta = skill.meta()
    info = _meta_to_dict(meta)
    info["agent_domain"] = _skill_domain(skill)
    info["is_learned"] = "learned_skills" in (
        getattr(type(skill), "__module__", "") or ""
    )

    # Read SKILL.md if available
    skill_md_content = ""
    try:
        import inspect
        src = inspect.getfile(type(skill))
        skill_md = Path(src).parent / "SKILL.md"
        if skill_md.exists():
            skill_md_content = skill_md.read_text("utf-8")
    except Exception:
        pass

    info["skill_md"] = skill_md_content
    return info


class SkillExecuteRequest(BaseModel):
    name: str
    inputs: dict[str, Any] = {}
    provider: str = ""
    model: str = ""


@router.post("/execute")
async def execute_skill(req: SkillExecuteRequest):
    """Execute a skill by name with given inputs."""
    registry = _get_registry()
    if not registry.has(req.name):
        raise HTTPException(404, f"Skill '{req.name}' not found")

    skill = registry.get(req.name)
    meta = skill.meta()

    t0 = time.time()
    try:
        # Build router for LLM-based skills
        from ui.backend.app.routers.generation_api import _build_router
        router_inst = _build_router(req.provider, req.model)

        result = await skill.execute(req.inputs, model_router=router_inst)
        elapsed_ms = int((time.time() - t0) * 1000)

        return {
            "status": "ok",
            "skill_name": meta.name,
            "result": result,
            "execution_time_ms": elapsed_ms,
        }
    except TypeError:
        # Some skills (rule-based) override execute with different signatures
        try:
            result = await skill.execute(req.inputs)
            elapsed_ms = int((time.time() - t0) * 1000)
            return {
                "status": "ok",
                "skill_name": meta.name,
                "result": result,
                "execution_time_ms": elapsed_ms,
            }
        except Exception as e:
            raise HTTPException(500, f"Skill execution error: {str(e)[:300]}")
    except ValueError as e:
        # Model not configured
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error("Skill execute error (%s): %s", req.name, e, exc_info=True)
        raise HTTPException(500, f"Skill execution error: {str(e)[:300]}")
