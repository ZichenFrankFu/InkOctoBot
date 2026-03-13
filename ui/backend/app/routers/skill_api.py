"""
/api/skills — Skill registry management and execution.

Exposes the Agent-Skill framework to the UI: list, inspect, test, and
execute registered skills.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/skills", tags=["skills"])
logger = logging.getLogger("inkoctobot.ui.backend.skill_api")

# Singleton registry instance (lazy-init, thread-safe)
_registry = None
_registry_lock = threading.Lock()


def _get_registry():
    global _registry
    if _registry is not None:
        return _registry
    with _registry_lock:
        if _registry is not None:
            return _registry
        from core.skill_registry import SkillRegistry
        reg = SkillRegistry()
        reg.scan_all()
        # Start watching learned skills
        agents_dir = Path(__file__).resolve().parents[4] / "agents"
        learned_dir = agents_dir / "learned_skills"
        reg.watch_learned_skills(learned_dir)
        _registry = reg
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


@router.get("/learning-log")
def get_learning_log():
    """Get the skill learning history log."""
    p = _learning_log_path()
    if not p.exists():
        # Provide mock data in test mode
        if os.environ.get("WN_TEST_MODE") == "1":
            return {"entries": _mock_learning_log()}
        return {"entries": []}
    try:
        data = json.loads(p.read_text("utf-8"))
        return {"entries": data.get("entries", [])}
    except Exception:
        return {"entries": []}


@router.post("/learning-log")
def add_learning_log_entry(body: dict = Body(...)):
    """Record a new skill learning event."""
    p = _learning_log_path()
    data = {"entries": []}
    if p.exists():
        try:
            data = json.loads(p.read_text("utf-8"))
        except Exception:
            pass
    entries = data.get("entries", [])
    entry = {
        "id": f"sl_{uuid.uuid4().hex[:8]}",
        "skill_name": body.get("skill_name", ""),
        "display_name": body.get("display_name", ""),
        "trigger": body.get("trigger", ""),
        "need_description": body.get("need_description", ""),
        "project_id": body.get("project_id", ""),
        "created_at": time.strftime("%Y-%m-%d %H:%M"),
    }
    entries.insert(0, entry)
    data["entries"] = entries[:100]  # keep last 100
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    return {"ok": True, "entry": entry}


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


class SkillCreateRequest(BaseModel):
    name: str
    display_name: str = ""
    description: str = ""
    domain: str = "learned_skills"
    model_role: str = "default"
    tags: list[str] = []
    prompt_template: str = ""


@router.post("/create")
def create_skill(req: SkillCreateRequest):
    """Create a new learned skill from template."""
    agents_dir = Path(__file__).resolve().parents[4] / "agents"
    skill_dir = agents_dir / "learned_skills" / req.name
    if skill_dir.exists():
        raise HTTPException(409, f"Skill '{req.name}' already exists")

    skill_dir.mkdir(parents=True, exist_ok=True)
    display = req.display_name or req.name.replace("_", " ").title()
    desc = req.description or f"Custom skill: {display}"
    tags_str = ", ".join(req.tags) if req.tags else "custom"
    prompt = req.prompt_template or "请根据以下输入生成内容：\\n\\n{text}"

    # Write SKILL.md
    skill_md = f"""---
name: {req.name}
display_name: {display}
version: "1.0"
model_role: {req.model_role}
tags: [{tags_str}]
temperature: 0.7
max_tokens: 2000
permissions: []
---

# {display}

{desc}

## Input
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| text  | str  | yes      | Input text  |

## Output
| Field  | Type | Description    |
|--------|------|----------------|
| result | str  | Generated text |
"""
    (skill_dir / "SKILL.md").write_text(skill_md, "utf-8")

    # Write skill.py
    skill_py = f'''"""Auto-generated learned skill: {display}"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from agents.base_skill import BaseSkill, SkillMeta


class Skill(BaseSkill):
    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="{req.name}",
            display_name="{display}",
            description="""{desc}""",
            version="1.0",
            model_role="{req.model_role}",
            tags={req.tags or ["custom"]},
            temperature=0.7,
            max_tokens=2000,
            input_schema={{"text": {{"type": "str", "required": True}}}},
            output_schema={{"result": {{"type": "str"}}}},
            permissions=[],
        )

    def build_prompt(self, inputs: dict[str, Any]) -> str:
        text = inputs.get("text", "")
        return f"""{prompt.replace('"', chr(92)+'"')}""".replace("{{text}}", text)

    def parse_output(self, raw: str) -> dict[str, Any]:
        return {{"result": raw}}
'''
    (skill_dir / "skill.py").write_text(skill_py, "utf-8")

    # Register in the running registry
    registry = _get_registry()
    try:
        skill = registry._load_skill(skill_dir / "skill.py")
        registry.register(skill)
    except Exception as e:
        logger.error("Failed to register new skill %s: %s", req.name, e)

    return {"status": "ok", "name": req.name, "path": str(skill_dir)}


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


# ── Skill Learning Log (helpers) ──

def _learning_log_path() -> Path:
    from ..settings import settings
    d = Path(settings.data_dir) / "skill_learning_log"
    d.mkdir(parents=True, exist_ok=True)
    return d / "log.json"


def _mock_learning_log() -> list[dict]:
    return [
        {
            "id": "sl_mock_001",
            "skill_name": "style_tone_adjuster",
            "display_name": "风格语气调整器",
            "trigger": "用户多次修改AI生成文本的语气和基调",
            "need_description": "自动检测并调整输出文风以匹配用户偏好的轻松幽默风格",
            "project_id": "test_project_001",
            "created_at": "2026-03-12 14:30",
        },
        {
            "id": "sl_mock_002",
            "skill_name": "dialogue_naturalizer",
            "display_name": "对话自然化处理",
            "trigger": "评估器多次标记对话不自然",
            "need_description": "优化角色对话的口语化程度和个性化表达",
            "project_id": "test_project_001",
            "created_at": "2026-03-11 09:15",
        },
        {
            "id": "sl_mock_003",
            "skill_name": "pacing_optimizer",
            "display_name": "节奏优化器",
            "trigger": "用户反复调整段落长度和场景切换节奏",
            "need_description": "根据场景类型自动调整叙事节奏和段落密度",
            "project_id": "test_project_001",
            "created_at": "2026-03-10 16:45",
        },
    ]
