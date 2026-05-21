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
from typing import Any, Optional

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


def _skill_public_dict(skill, deactivated: set[str]) -> dict[str, Any]:
    """Build a skill's public dict: execution meta + Claude SKILL.md manifest.

    The Claude-format ``SKILL.md`` manifest, when present, is authoritative
    for the skill's ``description``; its markdown body is exposed as
    ``skill_md``. ``is_basic`` marks built-in skills as read-only (only
    learned skills may be edited or deleted).
    """
    meta = skill.meta()
    info = _meta_to_dict(meta)
    info["agent_domain"] = _skill_domain(skill)
    is_learned = "learned_skills" in (
        getattr(type(skill), "__module__", "") or ""
    )
    info["is_learned"] = is_learned
    info["is_basic"] = not is_learned
    info["active"] = meta.name not in deactivated
    manifest = getattr(skill, "manifest", None)
    if manifest is not None:
        if manifest.description:
            info["description"] = manifest.description
        info["skill_md"] = manifest.body
    else:
        info["skill_md"] = ""
    return info


def _render_claude_skill_md(name: str, display: str, desc: str) -> str:
    """Render a Claude Agent Skill SKILL.md (minimal name+description frontmatter)."""
    import yaml
    fm = yaml.safe_dump(
        {"name": name, "description": desc},
        allow_unicode=True, default_flow_style=False, sort_keys=False,
    )
    return (
        f"---\n{fm}---\n\n"
        f"# {display}\n\n"
        f"## 说明\n\n{desc}\n\n"
        f"## 输入\n\n"
        f"- `text`（string，必填）：技能的输入文本\n\n"
        f"## 输出\n\n"
        f"```json\n"
        f'{{ "result": "技能生成的文本结果" }}\n'
        f"```\n"
    )


@router.get("")
def list_skills():
    """List all registered skills with metadata."""
    registry = _get_registry()
    deactivated = _get_deactivated()
    skills = [
        _skill_public_dict(skill, deactivated)
        for skill in registry._skills.values()
    ]
    return {"skills": skills, "total": len(skills)}


@router.get("/tags")
def list_tags():
    """List all unique tags across registered skills."""
    registry = _get_registry()
    tags: set[str] = set()
    for meta in registry.list_all():
        tags.update(meta.tags)
    return {"tags": sorted(tags)}


@router.get("/agents")
def list_agents():
    """List all agents with their associated skills, grouped by domain."""
    agents_dir = Path(__file__).resolve().parents[4] / "agents"
    registry = _get_registry()
    deactivated = _get_deactivated()

    AGENT_DOMAINS = {
        "planner": {"label": "规划", "description": "故事规划与架构设计"},
        "production": {"label": "生产", "description": "内容创作与场景执行"},
        "evaluation": {"label": "评估", "description": "质量评估与一致性检查"},
        "analysis": {"label": "分析", "description": "风格分析与特征提取"},
        "feature_extraction": {"label": "特征提取", "description": "编年史、信息密度、爽点、钩子、开篇模式等特征抽取技能"},
        "constraints": {"label": "约束", "description": "约束消歧与规则执行"},
        "learned_skills": {"label": "自学习", "description": "通过使用自动习得的技能"},
    }

    # Map skill name -> skill directory parent name for agent matching
    def _skill_info(meta):
        return {
            "name": meta.name,
            "display_name": meta.display_name,
            "active": meta.name not in deactivated,
            "is_learned": False,
        }

    result = []
    for domain, info in AGENT_DOMAINS.items():
        domain_dir = agents_dir / domain
        if not domain_dir.is_dir():
            continue

        # Collect all domain skills with their file paths
        domain_skills_map: dict[str, dict] = {}
        for skill in registry._skills.values():
            if _skill_domain(skill) == domain:
                meta = skill.meta()
                si = _skill_info(meta)
                si["is_learned"] = domain == "learned_skills"
                # Determine which subdirectory this skill lives in
                try:
                    import inspect
                    src = Path(inspect.getfile(type(skill)))
                    # e.g. agents/evaluation/skills/quality_score/skill.py
                    # parent = quality_score, parent.parent = skills
                    skill_subdir = src.parent.name  # e.g. "quality_score"
                    si["_subdir"] = skill_subdir
                except Exception:
                    si["_subdir"] = ""
                domain_skills_map[meta.name] = si

        # Find agents and map skills to each agent
        agents_list = []
        # Scan for agent classes (BaseAgent subclasses + other agent-like classes)
        for py in sorted(domain_dir.glob("*.py")):
            if py.name.startswith("__"):
                continue
            try:
                text = py.read_text("utf-8", errors="ignore")
            except Exception:
                continue
            # Extract agent_name or class name
            agent_name = ""
            class_name = ""
            agent_desc = ""
            for line in text.splitlines():
                if "agent_name" in line and "=" in line and not line.strip().startswith("#"):
                    val = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if val and val != "base":
                        agent_name = val
                if line.strip().startswith("class ") and ":" in line:
                    class_name = line.strip().split("class ", 1)[1].split("(")[0].split(":")[0].strip()
                if '"""' in line and not agent_desc:
                    agent_desc = line.strip().strip('"').strip()
            if not agent_name:
                # Use snake_case of filename for non-BaseAgent classes
                if class_name:
                    agent_name = py.stem
                else:
                    continue

            agents_list.append({
                "name": agent_name,
                "class_name": class_name,
                "skills": [],
            })

        # Map skills to agents by naming convention
        assigned_skills: set[str] = set()
        for agent_info in agents_list:
            aname = agent_info["name"]
            for sname, sinfo in domain_skills_map.items():
                if sname in assigned_skills:
                    continue  # Already assigned to another agent
                subdir = sinfo.get("_subdir", "")
                skill_name_lower = sname.lower()
                # Match by: skill subdir contains agent name fragment, or vice versa
                # e.g. actor_agent ↔ actor_perform, editor_writer ↔ editor_write
                abase = aname.replace("_agent", "").replace("agent_", "")
                sbase = subdir.replace("_", "")
                abase_clean = abase.replace("_", "")
                # Also try matching against the skill's registered name
                sname_clean = skill_name_lower.replace("_", "")
                if (abase_clean and (
                    (sbase and (
                        abase_clean in sbase or sbase in abase_clean
                        or abase.split("_")[0] == subdir.split("_")[0]
                    ))
                    or abase_clean in sname_clean or sname_clean in abase_clean
                    or (abase.split("_")[0] and abase.split("_")[0] in skill_name_lower.split("_"))
                )):
                    skill_copy = {k: v for k, v in sinfo.items() if not k.startswith("_")}
                    agent_info["skills"].append(skill_copy)
                    assigned_skills.add(sname)

        # Unassigned skills go as domain-level
        unassigned = []
        for sname, sinfo in domain_skills_map.items():
            if sname not in assigned_skills:
                unassigned.append({k: v for k, v in sinfo.items() if not k.startswith("_")})

        # If domain has skills but no agents, create a synthetic domain-level agent
        # so the skills remain visible in the UI
        if not agents_list and domain_skills_map:
            agents_list.append({
                "name": domain,
                "class_name": f"{domain.title()}Domain",
                "skills": [
                    {k: v for k, v in sinfo.items() if not k.startswith("_")}
                    for sinfo in domain_skills_map.values()
                ],
            })
            # All skills are now assigned to the synthetic agent
            unassigned = []

        result.append({
            "domain": domain,
            "label": info["label"],
            "description": info["description"],
            "agents": [
                {"name": a["name"], "class_name": a["class_name"], "skills": a["skills"]}
                for a in agents_list
            ],
            "unassigned_skills": unassigned,
        })

    return {"agents": result}


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
    deactivated = _get_deactivated()
    learned = [
        _skill_public_dict(skill, deactivated)
        for skill in registry._skills.values()
        if _skill_domain(skill) == "learned_skills"
    ]
    return {"skills": learned, "total": len(learned)}


@router.get("/{name}")
def get_skill(name: str):
    """Get detailed info for a single skill, including its SKILL.md body."""
    registry = _get_registry()
    if not registry.has(name):
        raise HTTPException(404, f"Skill '{name}' not found")
    skill = registry.get(name)
    return _skill_public_dict(skill, _get_deactivated())


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
    prompt = req.prompt_template or "请根据以下输入生成内容：\\n\\n{text}"

    # Write SKILL.md (Claude Agent Skill format — name + description only)
    (skill_dir / "SKILL.md").write_text(
        _render_claude_skill_md(req.name, display, desc), "utf-8"
    )

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


class SkillUpdateRequest(BaseModel):
    display_name: str = ""
    description: str = ""
    tags: list[str] = []
    prompt_template: str = ""
    model_role: str = ""
    temperature: float | None = None
    max_tokens: int | None = None


@router.put("/{name}")
def update_skill(name: str, req: SkillUpdateRequest):
    """Update a learned skill's SKILL.md and skill.py metadata."""
    registry = _get_registry()
    if not registry.has(name):
        raise HTTPException(404, f"Skill '{name}' not found")

    skill = registry.get(name)
    domain = _skill_domain(skill)
    if domain != "learned_skills":
        raise HTTPException(403, "Only learned skills can be modified")

    try:
        import inspect
        src = inspect.getfile(type(skill))
        skill_dir = Path(src).parent
    except Exception:
        raise HTTPException(500, "Cannot locate skill source")

    # Update SKILL.md (Claude Agent Skill format — name + description only)
    skill_md_path = skill_dir / "SKILL.md"
    meta = skill.meta()
    display = req.display_name or meta.display_name
    desc = req.description or meta.description
    tags = req.tags if req.tags else meta.tags
    model_role = req.model_role or meta.model_role
    temperature = req.temperature if req.temperature is not None else meta.temperature
    max_tokens = req.max_tokens if req.max_tokens is not None else meta.max_tokens

    skill_md_path.write_text(
        _render_claude_skill_md(name, display, desc), "utf-8"
    )

    # Update skill.py
    prompt = req.prompt_template or "请根据以下输入生成内容：\\n\\n{text}"
    skill_py_content = f'''"""Auto-generated learned skill: {display}"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from agents.base_skill import BaseSkill, SkillMeta


class Skill(BaseSkill):
    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="{name}",
            display_name="{display}",
            description="""{desc}""",
            version="{meta.version}",
            model_role="{model_role}",
            tags={tags},
            temperature={temperature},
            max_tokens={max_tokens},
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
    (skill_dir / "skill.py").write_text(skill_py_content, "utf-8")

    # Reload the skill in registry
    try:
        new_skill = registry._load_skill(skill_dir / "skill.py")
        registry.register(new_skill)
    except Exception as e:
        logger.error("Failed to reload skill %s: %s", name, e)

    return {"status": "ok", "name": name}


@router.delete("/{name}")
def delete_skill(name: str):
    """Delete a learned skill entirely."""
    registry = _get_registry()
    if not registry.has(name):
        raise HTTPException(404, f"Skill '{name}' not found")

    skill = registry.get(name)
    domain = _skill_domain(skill)
    if domain != "learned_skills":
        raise HTTPException(403, "Only learned skills can be deleted")

    try:
        import inspect
        import shutil
        src = inspect.getfile(type(skill))
        skill_dir = Path(src).parent
        shutil.rmtree(skill_dir, ignore_errors=True)
    except Exception as e:
        logger.error("Failed to delete skill files for %s: %s", name, e)

    registry.unregister(name)
    return {"status": "ok", "name": name}


# ── Deactivated skills tracking ──

def _deactivated_path() -> Path:
    from ..settings import settings
    d = settings.get_data_path("skill_learning_log")
    d.mkdir(parents=True, exist_ok=True)
    return d / "deactivated.json"


def _get_deactivated() -> set[str]:
    p = _deactivated_path()
    if not p.exists():
        return set()
    try:
        data = json.loads(p.read_text("utf-8"))
        return set(data.get("deactivated", []))
    except Exception:
        return set()


def _save_deactivated(names: set[str]) -> None:
    p = _deactivated_path()
    p.write_text(json.dumps({"deactivated": sorted(names)}, ensure_ascii=False), "utf-8")


@router.post("/{name}/toggle")
def toggle_skill(name: str):
    """Toggle a skill's active/deactivated state."""
    registry = _get_registry()
    if not registry.has(name):
        # Check if it's deactivated
        deactivated = _get_deactivated()
        if name in deactivated:
            deactivated.discard(name)
            _save_deactivated(deactivated)
            return {"status": "ok", "name": name, "active": True}
        raise HTTPException(404, f"Skill '{name}' not found")

    deactivated = _get_deactivated()
    if name in deactivated:
        deactivated.discard(name)
        active = True
    else:
        deactivated.add(name)
        active = False
    _save_deactivated(deactivated)
    return {"status": "ok", "name": name, "active": active}


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
    d = settings.get_data_path("skill_learning_log")
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


# ═══ Cross-work comparison → draft skill ═══════════════════

class CompareWorksRequest(BaseModel):
    ref_ids: list[str]
    focus: str = "all"           # 'all' | 'characters' | 'plot' | 'rhythm' | 'settings'
    instruction: str = ""        # optional user steer
    prompt_override: Optional[str] = None  # per-call ephemeral override


_COMPARE_PROMPT_DEFAULT = """你是创作技巧分析师。下面是 {n} 部参考作品的提取数据。
请对比它们的 **{focus}** 特征，找出**共同模式**和**显著差异**，并提炼出一条可作为创作技巧 (Skill) 的洞察。

要求：
1. 先客观陈述共同模式（2-3 句），再陈述差异点（2-3 句），最后给出可操作的写作建议。
2. 把它包装成一个**可重用的 Skill** — 一段 prompt 模板，里面带 {{user_input}} 占位符让后续调用可以传入新场景。

{instruction_block}

返回严格 JSON（不要 markdown 包装）：
{{
  "name": "snake_case_skill_name",
  "display_name": "可读名称",
  "description": "≤ 100 字描述这个 skill 解决什么问题",
  "prompt_template": "完整 prompt 模板，包含 {{user_input}} 占位符",
  "tags": ["对比", "学习", ...]
}}

作品数据：
{bundle}
"""


@router.post("/compare_works")
async def compare_works(body: CompareWorksRequest):
    """Pull each work's extracted features, ask the AI to find common
    patterns + differences, return a draft Skill object that the client
    can review and then save via the existing /api/skills/create."""
    from rag.reference_db import ReferenceDB
    from ui.backend.app.settings import settings as _app_settings
    from models.router import ModelRouter

    if not body.ref_ids:
        raise HTTPException(400, "请至少选择一部作品")
    if len(body.ref_ids) > 8:
        raise HTTPException(400, "一次最多对比 8 部作品")

    try:
        try:
            from ui.backend.app.utils import load_repo_config, get_db_path
            repo_cfg = load_repo_config(_app_settings.repo_root)
            db_path = get_db_path(repo_cfg, _app_settings.repo_root)
        except FileNotFoundError:
            db_path = str(_app_settings.repo_root / "data" / "novels.db")
        rdb = ReferenceDB(db_path)
    except Exception as e:
        raise HTTPException(500, f"打开参考库失败: {e}")

    works: list[dict] = []
    for rid in body.ref_ids:
        w = rdb.get_work(rid)
        if not w:
            raise HTTPException(404, f"参考作品不存在: {rid}")
        works.append(w)

    def _pj(s: Optional[str]) -> Any:
        if not s:
            return None
        try:
            return json.loads(s)
        except Exception:
            return None

    def _focused(w: dict) -> dict:
        """Build a compact JSON bundle for one work, scoped to focus."""
        out: dict[str, Any] = {
            "title": w.get("title"),
            "creator": w.get("creator"),
            "genre": w.get("genre"),
            "media_type": w.get("media_type"),
        }
        if body.focus in ("plot", "all"):
            out["plot"] = _pj(w.get("plot_outline_json"))
        if body.focus in ("characters", "all"):
            out["characters"] = (_pj(w.get("extracted_characters_json")) or [])[:15]
        if body.focus in ("settings", "all"):
            out["settings"] = (_pj(w.get("settings_json")) or [])[:15]
        if body.focus in ("rhythm", "all"):
            rh = _pj(w.get("rhythm_json")) or {}
            out["rhythm"] = {
                "coverage": rh.get("coverage"),
                "opening_pattern": rh.get("opening_pattern"),
                "climax_positions": rh.get("climax_positions"),
                "shuangdian": rh.get("shuangdian"),
                "pacing_segments": rh.get("pacing_segments"),
                # chapter_features can be huge; sample evenly
                "chapter_features_sample": (rh.get("chapter_features") or [])[::5],
            }
        if body.focus in ("style", "all"):
            out["style_fingerprint"] = _pj(w.get("style_fingerprint_json"))
        return out

    bundle = [{"ref_id": w["ref_id"], **_focused(w)} for w in works]
    bundle_str = json.dumps(bundle, ensure_ascii=False, indent=2)
    # Cap bundle size to keep prompt tractable
    if len(bundle_str) > 60_000:
        bundle_str = bundle_str[:60_000] + "\n[...截断]"

    instruction_block = (
        f"额外指示：{body.instruction.strip()}" if body.instruction.strip() else ""
    )
    focus_zh = {
        "all": "整体（剧情/角色/设定/节奏/风格）",
        "plot": "剧情大纲", "characters": "角色塑造",
        "settings": "世界观设定", "rhythm": "叙事节奏",
        "style": "语言风格",
    }.get(body.focus, body.focus)

    base = body.prompt_override or _COMPARE_PROMPT_DEFAULT
    prompt = base.format(
        n=len(works), focus=focus_zh,
        instruction_block=instruction_block,
        bundle=bundle_str,
    )

    try:
        router_inst = ModelRouter()
        raw = await router_inst.invoke(
            role="reference_extractor", prompt=prompt,
            max_tokens=2048, temperature=0.4,
        )
    except Exception as e:
        raise HTTPException(502, f"AI 对比调用失败: {e}")

    # Strip markdown fences if present
    import re as _re
    s = (raw or "").strip()
    fence = _re.match(r"^```(?:json)?\s*(.*?)\s*```$", s, _re.DOTALL)
    if fence:
        s = fence.group(1).strip()
    a, b = s.find("{"), s.rfind("}")
    if 0 <= a < b:
        s = s[a:b+1]
    try:
        draft = json.loads(s)
        if not isinstance(draft, dict):
            raise ValueError("not an object")
    except Exception as e:
        raise HTTPException(502, f"AI 返回的 JSON 无法解析: {e}; raw={raw[:200]!r}")

    # Default work-set tag for traceability
    tags = list(draft.get("tags") or [])
    if "对比" not in tags: tags.insert(0, "对比")
    if "自学习" not in tags: tags.append("自学习")

    return {
        "draft": {
            "name": str(draft.get("name") or f"compare_{int(time.time())}"),
            "display_name": str(draft.get("display_name") or "作品对比"),
            "description": str(draft.get("description") or ""),
            "prompt_template": str(draft.get("prompt_template") or ""),
            "tags": tags,
        },
        "source_works": [
            {"ref_id": w["ref_id"], "title": w.get("title")} for w in works
        ],
        "focus": body.focus,
    }
