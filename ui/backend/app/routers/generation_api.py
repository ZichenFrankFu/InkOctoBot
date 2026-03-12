"""
/api/generation — Creative Writing Pipeline execution via real agent pipeline.

Provides both synchronous and streaming (WebSocket) generation endpoints
that connect to the actual SceneDirector -> ActorAgents -> EditorWriter -> Evaluator pipeline.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

router = APIRouter(prefix="/api/generation", tags=["generation"])
logger = logging.getLogger("inkoctobot.ui.backend.generation_api")

_active_sessions: dict[str, dict[str, Any]] = {}


# ═══ Intelligence Integration Helpers ═══
# These connect the existing-but-disconnected modules together.

def _get_db_path() -> str:
    """Resolve the project database path."""
    try:
        from ui.backend.app.settings import settings as app_settings
        from ui.backend.app.utils import load_repo_config, get_db_path
        repo_cfg = load_repo_config(app_settings.repo_root)
        return get_db_path(repo_cfg, app_settings.repo_root)
    except Exception:
        from ui.backend.app.settings import settings as app_settings
        return str(app_settings.repo_root / "data" / "novels.db")


def _load_user_style_preferences(project_id: str, db_path: str) -> str:
    """Load accumulated user style preferences from EditAnalyzer (A4: feedback loop)."""
    try:
        import sqlite3
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT preference_type, description, confidence
                   FROM user_style_preferences
                   WHERE project_id=? AND confidence>=0.3
                   ORDER BY confidence DESC LIMIT 20""",
                (project_id,),
            ).fetchall()
        if not rows:
            return ""
        parts = ["[用户写作偏好（从历史修改中学习）]"]
        by_type: dict[str, list[str]] = {}
        for r in rows:
            by_type.setdefault(r["preference_type"], []).append(
                f"- {r['description']} (置信度: {r['confidence']:.0%})"
            )
        type_labels = {"style": "风格偏好", "content": "内容偏好", "pacing": "节奏偏好"}
        for ptype, items in by_type.items():
            parts.append(f"\n### {type_labels.get(ptype, ptype)}")
            parts.extend(items[:8])
        return "\n".join(parts)
    except Exception as e:
        logger.debug("Load user preferences skipped: %s", e)
        return ""


def _load_reference_style(project_id: str, db_path: str) -> str:
    """Load style info from linked reference works (B1: reference style injection)."""
    try:
        from rag.reference_db import ReferenceDB
        ref_db = ReferenceDB(db_path)
        links = ref_db.get_project_links(project_id)
        if not links:
            return ""
        parts = ["[参考作品风格]"]
        for link in links[:5]:
            work = ref_db.get_work(link["ref_id"])
            if not work:
                continue
            title = work.get("title", "")
            style_fp = work.get("style_fingerprint_json")
            why_like = work.get("user_why_i_like", "")
            dimension = link.get("dimension", "")
            part = f"- 《{title}》"
            if dimension:
                part += f"（参考维度: {dimension}）"
            if why_like:
                part += f"\n  喜欢原因: {why_like[:200]}"
            if style_fp:
                try:
                    fp = json.loads(style_fp) if isinstance(style_fp, str) else style_fp
                    if fp.get("avg_sentence_length"):
                        part += f"\n  风格指纹: 平均句长{fp['avg_sentence_length']:.0f}字"
                    if fp.get("dialogue_ratio"):
                        part += f", 对话比{fp['dialogue_ratio']:.0%}"
                except Exception:
                    pass
            parts.append(part)
        return "\n".join(parts)
    except Exception as e:
        logger.debug("Load reference style skipped: %s", e)
        return ""


def _load_unresolved_foreshadowing(project_id: str, db_path: str, chapter_num: int) -> str:
    """Load unresolved foreshadowing for context injection (B2: foreshadowing tracking)."""
    try:
        from rag.memory.episodic_timeline import EpisodicTimeline
        timeline = EpisodicTimeline(db_path)
        unresolved = timeline.get_unresolved_foreshadowing(project_id)
        if not unresolved:
            return ""
        parts = ["[未回收伏笔提醒]"]
        overdue = []
        upcoming = []
        for f in unresolved:
            gap = chapter_num - f.get("chapter_num", 0)
            desc = f"第{f['chapter_num']}章: {f['description']}"
            if gap > 10:
                overdue.append(f"⚠️ {desc} (已超{gap}章未回收)")
            elif gap > 5:
                upcoming.append(f"💡 {desc} (距今{gap}章)")
        if overdue:
            parts.append("\n### 超期伏笔（建议尽快回收）")
            parts.extend(overdue[:5])
        if upcoming:
            parts.append("\n### 近期伏笔（可考虑回收）")
            parts.extend(upcoming[:5])
        return "\n".join(parts) if len(parts) > 1 else ""
    except Exception as e:
        logger.debug("Load foreshadowing skipped: %s", e)
        return ""


def _build_chapter_hook_constraint(chapter_num: int) -> str:
    """Generate chapter-end hook constraint (D2: hook generation)."""
    return (
        "\n\n【章末钩子】本章结尾必须设置阅读钩子，吸引读者继续阅读下一章。"
        "\n钩子类型可选：悬念型（留下未解之谜）、反转型（最后一刻的意外）、"
        "情感型（强烈的情绪冲击）、伏笔型（暗示即将到来的重大事件）。"
        "\n钩子应自然融入情节，不要刻意突兀。"
    )


def _build_shuangdian_guidance(chapter_num: int, total_chapters: int = 0) -> str:
    """Generate shuangdian (satisfaction point) rhythm guidance (D1)."""
    # Every 3-5 chapters should have a small shuangdian, every 10-15 chapters a big one
    guidance = ""
    if chapter_num % 3 == 0:
        guidance = (
            "\n【爽点节奏提示】本章建议安排一个小爽点（如: 小规模打脸、获得小宝物、"
            "解决一个悬念、实力小突破等），保持读者的阅读兴奋感。"
        )
    if chapter_num % 10 == 0:
        guidance = (
            "\n【爽点节奏提示】本章建议安排一个大爽点（如: 大规模公开打脸、重大实力暴露、"
            "获得顶级宝物、大谜团揭晓等），这是节奏高潮章节。"
        )
    return guidance


async def _run_chapter_complete_hook(
    memory_manager: Any,
    router_inst: Any,
    project_id: str,
    chapter_num: int,
    chapter_text: str,
    scene_result: dict,
) -> None:
    """Post-generation hook: update memory system (A2: memory integration)."""
    try:
        # Generate chapter summary using LLM
        from models.base import LLMMessage
        summary_resp = await router_inst.generate(
            agent_role="evaluator",
            messages=[
                LLMMessage(role="system", content="你是一个小说章节摘要生成器。"),
                LLMMessage(role="user", content=(
                    f"请为以下第{chapter_num}章生成简短摘要（100-200字），"
                    f"并提取关键事件和角色状态变化。\n\n{chapter_text[:3000]}\n\n"
                    "请以JSON格式输出：\n"
                    '{"summary": "摘要", "key_events": ["事件1"], '
                    '"character_states": {"角色名": "状态"}, '
                    '"foreshadowing": [{"type": "planted|resolved", "description": "描述"}]}'
                )),
            ],
            temperature=0.3,
            max_tokens=1000,
        )

        # Parse summary
        import re
        summary_data: dict = {}
        try:
            json_match = re.search(r'\{[\s\S]*\}', summary_resp.content)
            if json_match:
                summary_data = json.loads(json_match.group())
        except Exception:
            summary_data = {"summary": summary_resp.content[:200]}

        summary = summary_data.get("summary", chapter_text[:200])
        key_events = summary_data.get("key_events", [])
        char_states = summary_data.get("character_states", {})

        # Build timeline events from foreshadowing data
        events: list[dict] = []
        for fs in summary_data.get("foreshadowing", []):
            events.append({
                "event_type": "foreshadowing",
                "description": fs.get("description", ""),
                "foreshadow_status": "planted" if fs.get("type") == "planted" else "resolved",
            })
        for ke in key_events:
            events.append({"event_type": "plot_event", "description": ke})

        # Update all memory layers
        await memory_manager.on_chapter_complete(
            chapter_num=chapter_num,
            chapter_text=chapter_text,
            summary=summary,
            key_events=key_events,
            character_states=char_states,
            events=events,
        )
        logger.info("Memory system updated for chapter %d: %d events, %d char states",
                     chapter_num, len(events), len(char_states))
    except Exception as e:
        logger.warning("Chapter complete hook failed (non-fatal): %s", e)


class GenerateRequest(BaseModel):
    project_id: str = ""
    chapter_id: str = ""
    synopsis: str = ""
    time_setting: str = ""
    location: str = ""
    characters: list[str] = []
    references: list[str] = []
    world_rules: str = ""
    style_notes: str = ""
    system_hint: str = ""
    provider: str = ""
    model: str = ""
    existing_content: str = ""
    chapter_num: int = 1
    character_aliases: dict[str, str] = {}


class RewriteRequest(BaseModel):
    text: str
    instruction: str = ""
    provider: str = ""
    model: str = ""


class EvalRequest(BaseModel):
    text: str
    chapter_num: int = 1
    provider: str = ""
    model: str = ""


def _get_user_settings() -> dict:
    """Load user settings with full defaults for missing keys."""
    import json as _json
    from pathlib import Path
    from ui.backend.app.settings import settings as app_settings
    from ui.backend.app.routers.data_api import _default_settings
    p = app_settings.repo_root / "data" / "settings.json"
    if p.exists():
        data = _json.loads(p.read_text("utf-8"))
    else:
        data = {}
    # Deep-merge defaults so new providers/pipeline roles always appear
    defaults = _default_settings()
    for k, v in defaults.items():
        if k not in data:
            data[k] = v
    # Ensure all default providers exist
    for pname, pdef in defaults.get("providers", {}).items():
        if pname not in data.get("providers", {}):
            data.setdefault("providers", {})[pname] = pdef
    # Ensure all default pipeline roles exist
    for rname, rdef in defaults.get("pipeline", {}).items():
        if rname not in data.get("pipeline", {}):
            data.setdefault("pipeline", {})[rname] = rdef
    return data


class _SimpleRouter:
    """Router that resolves provider+model per agent role from user settings."""

    def __init__(self, user_settings: dict, fallback_provider: str = "", fallback_model: str = ""):
        self._settings = user_settings
        self._providers_cfg = user_settings.get("providers", {})
        self._pipeline = user_settings.get("pipeline", {})
        self._fallback_provider = fallback_provider
        self._fallback_model = fallback_model
        self._provider_cache: dict[str, Any] = {}  # keyed by "provider:model"

    def _resolve(self, agent_role: str) -> tuple[str, str, dict]:
        """Return (provider, model, prov_cfg) for the given agent role."""
        role_cfg = self._pipeline.get(agent_role, {})
        provider = role_cfg.get("provider", "") or self._fallback_provider
        model = role_cfg.get("model", "") or self._fallback_model
        prov_cfg = self._providers_cfg.get(provider, {})
        # If model still empty, try the provider's model list
        if provider and not model:
            models = prov_cfg.get("models", [])
            if models:
                model = models[0]
        return provider, model, prov_cfg

    def _get_provider(self, provider: str, model: str, prov_cfg: dict):
        cache_key = f"{provider}:{model}"
        if cache_key in self._provider_cache:
            return self._provider_cache[cache_key]
        from models.base import ProviderConfig
        cfg = ProviderConfig(
            provider_type=provider,
            model_name=model,
            base_url=prov_cfg.get("base_url") or None,
            api_key=prov_cfg.get("api_key") or None,
        )
        inst = _make_provider_instance(cfg)
        self._provider_cache[cache_key] = inst
        return inst

    async def generate(self, *, agent_role: str, messages, temperature=None, max_tokens=None, **kw):
        provider, model, prov_cfg = self._resolve(agent_role)
        if not model:
            raise ValueError(f"角色 '{agent_role}' 未配置模型。请在「设置→Pipeline 配置」中分配。")
        inst = self._get_provider(provider, model, prov_cfg)
        return await inst.generate(messages, temperature=temperature, max_tokens=max_tokens, **kw)

    async def invoke(self, *, role: str, prompt: str, max_tokens: int = 4096, temperature: float = 0.7) -> str:
        """Simple prompt-in, text-out API used by BaseSkill.execute()."""
        from models.base import LLMMessage
        messages = [LLMMessage(role="user", content=prompt)]
        resp = await self.generate(agent_role=role, messages=messages, temperature=temperature, max_tokens=max_tokens)
        return resp.content

    async def generate_stream(self, *, agent_role: str, messages, temperature=None, max_tokens=None, **kw):
        provider, model, prov_cfg = self._resolve(agent_role)
        if not model:
            raise ValueError(f"角色 '{agent_role}' 未配置模型。请在「设置→Pipeline 配置」中分配。")
        inst = self._get_provider(provider, model, prov_cfg)
        async for token in inst.generate_stream(messages, temperature=temperature, max_tokens=max_tokens, **kw):
            yield token


def _make_provider_instance(cfg):
    """Instantiate a provider from a ProviderConfig."""
    ptype = cfg.provider_type
    if ptype == "ollama":
        from models.ollama_provider import OllamaProvider
        return OllamaProvider(cfg)
    elif ptype == "deepseek":
        from models.deepseek_provider import DeepSeekProvider
        return DeepSeekProvider(cfg)
    elif ptype == "openai":
        from models.openai_provider import OpenAIProvider
        return OpenAIProvider(cfg)
    elif ptype == "anthropic":
        from models.anthropic_provider import AnthropicProvider
        return AnthropicProvider(cfg)
    elif ptype == "gemini":
        from models.gemini_provider import GeminiProvider
        return GeminiProvider(cfg)
    elif ptype == "vllm":
        from models.vllm_provider import VLLMProvider
        return VLLMProvider(cfg)
    else:
        from models.ollama_provider import OllamaProvider
        return OllamaProvider(cfg)


def _build_router(provider: str = "", model: str = ""):
    """Build a router from user settings.

    If explicit provider+model given, uses that as fallback.
    Otherwise resolves a fallback from the first configured enabled provider.
    Each agent_role call will look up its own pipeline assignment first.
    """
    user_settings = _get_user_settings()
    providers_cfg = user_settings.get("providers", {})
    pipeline = user_settings.get("pipeline", {})

    # Find a fallback provider+model (used only when a role has no assignment)
    fb_provider = provider
    fb_model = model

    if not fb_provider or not fb_model:
        # Try pipeline config
        for role_key in ("scene_director", "editor_stylist", "actor_default"):
            role_cfg = pipeline.get(role_key, {})
            p = role_cfg.get("provider", "")
            m = role_cfg.get("model", "")
            if p and m:
                fb_provider = fb_provider or p
                fb_model = fb_model or m
                break

    if not fb_provider or not fb_model:
        # Scan enabled providers
        for pname, pcfg in providers_cfg.items():
            if pcfg.get("enabled") and pcfg.get("models"):
                fb_provider = fb_provider or pname
                fb_model = fb_model or pcfg["models"][0]
                break

    if not fb_provider or not fb_model:
        # Auto-detect Ollama as last resort
        ollama_cfg = providers_cfg.get("ollama", {})
        try:
            import httpx
            base = ollama_cfg.get("base_url", "http://localhost:11434")
            resp = httpx.get(f"{base}/api/tags", timeout=5)
            if resp.status_code == 200:
                ollama_models = [m["name"] for m in resp.json().get("models", [])]
                if ollama_models:
                    fb_provider = "ollama"
                    fb_model = ollama_models[0]
        except Exception:
            pass

    if not fb_model:
        raise ValueError(
            "未找到可用的 AI 模型。请在「设置」页面中启用一个模型供应商并配置模型。"
        )

    return _SimpleRouter(user_settings, fb_provider, fb_model)


@router.get("/health")
def health():
    return {"status": "ok", "router": "generation"}


@router.post("/start")
async def start_generation(req: GenerateRequest):
    session_id = f"gen_{uuid.uuid4().hex[:12]}"
    _active_sessions[session_id] = {
        "status": "running",
        "request": req.model_dump(),
        "created_at": time.time(),
        "events": [],           # list of dicts — full event log
        "result": None,
        "text": "",             # accumulated generated text
        "current_step": "",
        "waiting_confirm": False,
        "confirm_event": None,  # asyncio.Event for confirm signal
        "confirm_data": None,   # data from user confirm
        "task": None,           # background asyncio.Task
    }
    # Start pipeline as background task
    task = asyncio.create_task(_run_pipeline_background(session_id))
    _active_sessions[session_id]["task"] = task
    return {"status": "ok", "session_id": session_id}


@router.get("/status/{session_id}")
def get_session_status(session_id: str):
    session = _active_sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return {
        "status": session["status"],
        "current_step": session.get("current_step", ""),
        "waiting_confirm": session.get("waiting_confirm", False),
        "event_count": len(session.get("events", [])),
        "result": session.get("result"),
    }


@router.get("/events/{session_id}")
def get_session_events(session_id: str, after: int = 0):
    """Get events after a given index. Used for polling."""
    session = _active_sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    events = session.get("events", [])
    return {
        "status": session["status"],
        "events": events[after:],
        "total": len(events),
    }


@router.post("/confirm/{session_id}")
async def confirm_session(session_id: str, body: dict):
    """Send a confirm/abort signal to a waiting pipeline."""
    session = _active_sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    if not session.get("waiting_confirm"):
        return {"status": "ok", "message": "not waiting"}
    session["confirm_data"] = body
    evt = session.get("confirm_event")
    if evt:
        evt.set()
    return {"status": "ok"}


@router.post("/stop/{session_id}")
async def stop_session(session_id: str):
    """Stop/abort a running pipeline session."""
    session = _active_sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    # Signal abort via confirm mechanism
    session["confirm_data"] = {"action": "abort"}
    evt = session.get("confirm_event")
    if evt:
        evt.set()
    # Cancel the background task
    task = session.get("task")
    if task and not task.done():
        task.cancel()
    session["status"] = "complete"
    _emit(session_id, {"type": "complete", "text": session.get("text", ""), "aborted": True})
    return {"status": "ok", "message": "Pipeline stopped"}


@router.post("/scene-plan")
async def generate_scene_plan(req: GenerateRequest):
    try:
        router_inst = _build_router(req.provider, req.model)
        from agents.production.scene_director import SceneDirector
        director = SceneDirector(router_inst, project_id=req.project_id)
        result = await director.plan_scenes(
            chapter_outline=req.synopsis,
            chapter_num=1,
            world_rules=req.world_rules,
            constraints=req.style_notes,
        )
        return {"status": "ok", "scenes": result}
    except Exception as e:
        logger.error("Scene plan error: %s", e, exc_info=True)
        raise HTTPException(500, detail=str(e))


@router.post("/rewrite")
async def rewrite_text(req: RewriteRequest):
    try:
        router_inst = _build_router(req.provider, req.model)
        from agents.production.editor_writer import EditorWriter
        editor = EditorWriter(router_inst, project_id="rewrite")
        result = await editor.targeted_rewrite(
            original_text=req.text,
            instruction=req.instruction,
        )
        return {"status": "ok", "rewritten": result}
    except Exception as e:
        logger.error("Rewrite error: %s", e, exc_info=True)
        raise HTTPException(500, detail=str(e))


@router.post("/evaluate")
async def evaluate_text(req: EvalRequest):
    try:
        router_inst = _build_router(req.provider, req.model)
        from agents.evaluation.evaluator import Evaluator
        evaluator = Evaluator(router_inst, project_id="eval")
        result = await evaluator.evaluate(text=req.text, chapter_num=req.chapter_num)
        return {"status": "ok", "evaluation": result}
    except Exception as e:
        logger.error("Evaluate error: %s", e, exc_info=True)
        raise HTTPException(500, detail=str(e))


@router.post("/quick-generate")
async def quick_generate(req: GenerateRequest):
    """Single-step generation: synopsis -> full chapter text."""
    try:
        from models.base import LLMMessage
        router_inst = _build_router(req.provider, req.model)

        system_prompt = req.system_hint if req.system_hint else (
            "你是一个专业的小说写作AI。根据提供的大纲和设定，"
            "写出高质量的章节内容。要求：\n"
            "1. 文字生动，有画面感\n"
            "2. 对话自然，符合人物性格\n"
            "3. 情节紧凑，节奏合理\n"
            "4. 保持叙事视角一致"
        )

        # When system_hint is set, it's a conversational/studio mode
        if req.system_hint:
            parts = [req.synopsis]
        else:
            parts = [f"## 章节大纲\n{req.synopsis}"]
            if req.time_setting:
                parts.append(f"## 时间\n{req.time_setting}")
            if req.location:
                parts.append(f"## 地点\n{req.location}")
            if req.characters:
                parts.append(f"## 出场角色\n{', '.join(req.characters)}")
            if req.world_rules:
                parts.append(f"## 世界观设定\n{req.world_rules}")
            if req.style_notes:
                parts.append(f"## 风格要求\n{req.style_notes}")
            parts.append("\n请根据以上信息，写出完整的章节内容（800-1500字）：")

        messages = [
            LLMMessage(role="system", content=system_prompt),
            LLMMessage(role="user", content="\n\n".join(parts)),
        ]

        response = await router_inst.generate(
            agent_role="editor_stylist",
            messages=messages,
            temperature=0.8,
            max_tokens=4096,
        )

        return {
            "status": "ok",
            "text": response.content,
            "model": response.model,
            "tokens": {
                "input": response.input_tokens,
                "output": response.output_tokens,
            },
        }
    except ValueError as e:
        logger.error("Quick generate config error: %s", e)
        raise HTTPException(500, detail=str(e))
    except Exception as e:
        msg = str(e)
        logger.error("Quick generate error: %s", e, exc_info=True)
        if "404" in msg and "localhost:11434" in msg:
            detail = (
                "Ollama 模型未找到。请确认：\n"
                "1. Ollama 服务正在运行（ollama serve）\n"
                "2. 在「设置→模型供应商」中点击「自动检测」以发现可用模型\n"
                "3. 在「设置→Pipeline 配置」中为各角色分配模型\n"
                f"原始错误：{msg[:200]}"
            )
        elif "Connect" in msg or "refused" in msg.lower():
            detail = (
                "无法连接到模型服务。请在「设置→模型供应商」中配置并启用一个可用的供应商（Ollama / OpenAI / Gemini 等）。"
            )
        else:
            detail = msg
        raise HTTPException(500, detail=detail)


def _emit(session_id: str, event: dict):
    """Append an event to the session log."""
    session = _active_sessions.get(session_id)
    if session:
        event["ts"] = time.time()
        session["events"].append(event)


def _format_scene_plan_readable(scene_plan: dict) -> str:
    """Convert scene plan JSON to human-readable natural language text."""
    if not isinstance(scene_plan, dict):
        return str(scene_plan)
    scenes = scene_plan.get("scenes", [])
    if not scenes:
        if scene_plan.get("summary"):
            return scene_plan["summary"]
        if scene_plan.get("raw"):
            return str(scene_plan["raw"])
        return str(scene_plan)

    parts = []
    for i, scene in enumerate(scenes):
        lines = [f"━━━ 场景 {i + 1}{': ' + scene.get('location', '') if scene.get('location') else ''} ━━━"]
        if scene.get("time"):
            lines.append(f"时间：{scene['time']}")
        if scene.get("characters"):
            chars = scene["characters"]
            lines.append(f"出场：{', '.join(chars) if isinstance(chars, list) else chars}")
        if scene.get("summary"):
            lines.append(f"\n【概要】{scene['summary']}")
        if scene.get("beats"):
            lines.append("\n【节拍】")
            for bi, b in enumerate(scene["beats"]):
                lines.append(f"  {bi + 1}. {b}")
        if scene.get("character_instructions"):
            lines.append("\n【角色指令】")
            for name, inst in scene["character_instructions"].items():
                lines.append(f"  {name}:")
                if isinstance(inst, dict):
                    if inst.get("emotional_state"):
                        lines.append(f"    情绪: {inst['emotional_state']}")
                    if inst.get("secret_goal"):
                        lines.append(f"    暗线: {inst['secret_goal']}")
                    if inst.get("must"):
                        lines.append(f"    必须: {'; '.join(inst['must'])}")
                    if inst.get("must_not"):
                        lines.append(f"    禁止: {'; '.join(inst['must_not'])}")
                else:
                    lines.append(f"    {inst}")
        if scene.get("narrator_instructions"):
            lines.append(f"\n【旁白指引】{scene['narrator_instructions']}")
        parts.append("\n".join(lines))

    output = "\n\n".join(parts)
    if scene_plan.get("chapter_arc"):
        output += f"\n\n【本章情绪弧线】{scene_plan['chapter_arc']}"
    return output


def _format_evaluation_readable(eval_result: dict) -> str:
    """Convert evaluation result to human-readable natural language."""
    score = eval_result.get("score", 0)
    lines = [f"综合评分：{score}/100"]
    if eval_result.get("dimension_scores"):
        dim_labels = {
            "constraint_satisfaction": "约束满足", "consistency": "一致性",
            "knowledge_isolation": "知识隔离", "repetition": "重复度",
            "ai_flavor": "AI味", "narrative_quality": "叙事质量",
        }
        lines.append("\n分维度评分：")
        for dim, s in eval_result["dimension_scores"].items():
            lines.append(f"  {dim_labels.get(dim, dim)}: {s}/100")
    if eval_result.get("issues"):
        lines.append("\n发现的问题：")
        for issue in eval_result["issues"]:
            sev_icon = {"high": "[严重]", "medium": "[中等]", "low": "[轻微]"}.get(issue.get("severity", "low"), "")
            lines.append(f"  {sev_icon} [{issue.get('type', '')}] {issue.get('description', '')}")
            if issue.get("suggestion"):
                lines.append(f"    建议：{issue['suggestion']}")
    if eval_result.get("strengths"):
        lines.append("\n亮点：")
        for s in eval_result["strengths"]:
            lines.append(f"  - {s}")
    return "\n".join(lines)


async def _wait_for_confirm_bg(session_id: str, step: str, message: str, timeout_s: int = 600):
    """Block until user responds via follow_up choice or chat (or auto-continue after timeout).
    NOTE: The caller should emit a 'follow_up' event BEFORE calling this so the UI shows options.
    This function no longer emits 'need_confirm' — the follow_up options serve as the confirmation UI.
    """
    session = _active_sessions.get(session_id)
    if not session:
        return None
    evt = asyncio.Event()
    session["confirm_event"] = evt
    session["confirm_data"] = None
    session["waiting_confirm"] = True
    session["current_step"] = step
    # No need_confirm event — the follow_up options already provide the UI
    try:
        await asyncio.wait_for(evt.wait(), timeout=timeout_s)
    except asyncio.TimeoutError:
        # Auto-continue on timeout
        session["confirm_data"] = {"action": "continue"}
    session["waiting_confirm"] = False
    session["confirm_event"] = None
    data = session.get("confirm_data", {})
    if data and data.get("action") == "abort":
        _emit(session_id, {"type": "complete", "text": "", "aborted": True})
        session["status"] = "complete"
        return None
    return data or {"action": "continue"}


async def _run_pipeline_background(session_id: str):
    """Run the full pipeline as a background task. Events are logged to the session."""
    session = _active_sessions.get(session_id)
    if not session:
        return
    req_data = session["request"]

    try:
        router_inst = _build_router(req_data.get("provider", ""), req_data.get("model", ""))
        _emit(session_id, {"type": "pipeline_start", "session_id": session_id})

        # ── Step 1: Scene Director ──────────────────────────
        session["current_step"] = "scene_director"
        _emit(session_id, {
            "type": "step_start", "step": "scene_director",
            "label": "Scene Director", "detail": "正在拆分场景...",
        })
        _emit(session_id, {"type": "progress_update", "step": "scene_director", "progress": 10, "detail": "加载角色和世界观数据..."})
        scene_result = {}
        try:
            from agents.production.scene_director import SceneDirector
            director = SceneDirector(router_inst, project_id=req_data.get("project_id", ""))
            # Build character info string for scene planning
            _chars = req_data.get("characters", [])
            _aliases = req_data.get("character_aliases", {})
            _char_card_parts = []
            try:
                from ui.backend.app.routers.data_api import _list as _list_data
                _all_chars = _list_data("characters")
                _pid = req_data.get("project_id", "")
                for _cn in _chars:
                    _display = _aliases.get(_cn, _cn)
                    _info = [_display]
                    for _cd in _all_chars:
                        if _cd.get("name") == _cn and (not _pid or _cd.get("project_id", "") in ("", _pid)):
                            if _cd.get("role"): _info.append(f"({_cd['role']})")
                            if _cd.get("personality"): _info.append(f"- 性格: {_cd['personality'][:100]}")
                            break
                    if _cn != _display:
                        _info.append(f"（作者备注：此角色真实身份为「{_cn}」，但本章中以「{_display}」的身份出场，禁止透露真实身份）")
                    _char_card_parts.append(" ".join(_info[:2]) + ("\n  " + "\n  ".join(_info[2:]) if len(_info) > 2 else ""))
            except Exception:
                _char_card_parts = [f"- {_aliases.get(c, c)}" for c in _chars]
            _char_cards_str = "\n".join(_char_card_parts) if _char_card_parts else ""
            _location = req_data.get("location", "")
            _time_setting = req_data.get("time_setting", "")
            _outline = req_data.get("synopsis", "")
            if _location:
                _outline += f"\n场景地点：{_location}"
            if _time_setting:
                _outline += f"\n时间设定：{_time_setting}"
            _existing_content = req_data.get("existing_content", "")
            if _existing_content:
                _outline += f"\n\n[已有正文（请基于此续写下一部分）]\n{_existing_content[-1000:]}"
            # Build character scope constraint to prevent memory leak
            _chapter_num = req_data.get("chapter_num", 1)
            _scope_constraints = req_data.get("world_rules", "")
            if _chars:
                _display_chars = [_aliases.get(c, c) for c in _chars]
                _scope_constraints += (
                    f"\n\n【严格限制】本章（第{_chapter_num}章）仅有以下角色出场：{', '.join(_display_chars)}。"
                    f"\n禁止引入或提及任何不在上述列表中的角色。"
                    f"\n禁止引用其他章节的剧情或角色。"
                    f"\n场景中的 characters 数组只能包含上述角色名。"
                )
                if _aliases:
                    _scope_constraints += (
                        f"\n\n【隐藏身份】以下角色在本章中使用化名，禁止在正文中透露真实身份："
                    )
                    for _real, _alias in _aliases.items():
                        _scope_constraints += f"\n  - 「{_alias}」（请始终使用此称呼，不要使用真名）"

            # D2: Inject chapter-end hook constraint (non-fatal)
            try:
                _scope_constraints += _build_chapter_hook_constraint(_chapter_num)
            except Exception:
                pass

            # D1: Inject shuangdian rhythm guidance (non-fatal)
            try:
                _shuangdian = _build_shuangdian_guidance(_chapter_num)
                if _shuangdian:
                    _scope_constraints += _shuangdian
            except Exception:
                pass

            # B2: Load unresolved foreshadowing for SceneDirector awareness (non-fatal)
            _memory_ctx = ""
            try:
                _db_path = _get_db_path()
                _proj_id = req_data.get("project_id", "")
                _foreshadow_ctx = _load_unresolved_foreshadowing(_proj_id, _db_path, _chapter_num)
                if _foreshadow_ctx:
                    _memory_ctx += f"\n\n{_foreshadow_ctx}"
            except Exception:
                pass

            # Build memory context from chapter buffer if available (non-fatal)
            try:
                _db_path = _get_db_path()
                _proj_id = req_data.get("project_id", "")
                from rag.memory.manager import MemoryManager as _MM
                _mem = _MM(db_path=_db_path)
                _mem.set_project(_proj_id)
                _mem_text = _mem.get_context_for_scene_director(_chapter_num)
                if _mem_text:
                    _memory_ctx = _mem_text + _memory_ctx
            except Exception:
                pass

            # Truncate constraints and memory context to prevent LLM overload
            _MAX_CONSTRAINTS_LEN = 3000
            _MAX_MEMORY_LEN = 4000
            if len(_scope_constraints) > _MAX_CONSTRAINTS_LEN:
                logger.warning("Constraints truncated: %d -> %d chars",
                               len(_scope_constraints), _MAX_CONSTRAINTS_LEN)
                _scope_constraints = _scope_constraints[:_MAX_CONSTRAINTS_LEN]
            if len(_memory_ctx) > _MAX_MEMORY_LEN:
                logger.warning("Memory context truncated: %d -> %d chars",
                               len(_memory_ctx), _MAX_MEMORY_LEN)
                _memory_ctx = _memory_ctx[:_MAX_MEMORY_LEN]

            _emit(session_id, {"type": "progress_update", "step": "scene_director", "progress": 40, "detail": "AI 正在规划场景结构..."})
            scenes = await director.plan_scenes(
                chapter_outline=_outline,
                chapter_num=_chapter_num,
                memory_context=_memory_ctx,
                world_rules="",
                character_cards=_char_cards_str,
                constraints=_scope_constraints,
            )
            scene_result = scenes if isinstance(scenes, dict) else {"raw": str(scenes)}
            _emit(session_id, {"type": "progress_update", "step": "scene_director", "progress": 90, "detail": "解析场景计划..."})
        except Exception as e:
            logger.warning("Scene director first attempt failed: %s — retrying with minimal context", e)
            # Retry with minimal context (no memory/foreshadowing, only core constraints)
            try:
                _core_constraints = req_data.get("world_rules", "")
                if _chars:
                    _display_chars_fb = [_aliases.get(c, c) for c in _chars]
                    _core_constraints += (
                        f"\n\n【严格限制】本章仅有以下角色出场：{', '.join(_display_chars_fb)}。"
                        f"\n禁止引入不在上述列表中的角色。"
                    )
                scenes = await director.plan_scenes(
                    chapter_outline=_outline,
                    chapter_num=_chapter_num,
                    memory_context="",
                    world_rules="",
                    character_cards=_char_cards_str,
                    constraints=_core_constraints,
                )
                scene_result = scenes if isinstance(scenes, dict) else {"raw": str(scenes)}
                scene_result["_retried"] = True
                logger.info("Scene director retry succeeded")
            except Exception as e2:
                logger.error("Scene director retry also failed: %s", e2, exc_info=True)
                # Build a proper fallback scene plan from the original synopsis
                _display_characters = [_aliases.get(c, c) for c in _chars] if _aliases else _chars
                scene_result = {
                    "scenes": [{
                        "scene_index": 0,
                        "location": req_data.get("location", ""),
                        "time": req_data.get("time_setting", ""),
                        "characters": _display_characters,
                        "summary": req_data.get("synopsis", "")[:500],
                        "beats": [req_data.get("synopsis", "")[:200]],
                        "character_instructions": {},
                        "narrator_instructions": "",
                    }],
                    "chapter_arc": "",
                    "_fallback": True,
                    "_error": str(e2)[:200],
                }
        scene_prompt = json.dumps({
            "chapter_outline": req_data.get("synopsis", "")[:500],
            "world_rules": req_data.get("world_rules", "")[:300],
            "chapter_num": 1,
        }, ensure_ascii=False, indent=2)
        scene_result_with_prompt = dict(scene_result) if isinstance(scene_result, dict) else {"raw": str(scene_result)}
        scene_result_with_prompt["prompt_sent"] = f"SceneDirector.plan_scenes({scene_prompt})"
        # Add human-readable text version
        scene_result_with_prompt["text"] = _format_scene_plan_readable(scene_result)
        _emit(session_id, {"type": "step_done", "step": "scene_director", "result": scene_result_with_prompt})
        _emit(session_id, {"type": "progress_update", "step": "scene_director", "progress": 100, "detail": "场景规划完成"})
        # Emit follow-up options — these serve as the confirmation UI (no separate green button)
        _emit(session_id, {
            "type": "follow_up", "step": "scene_director",
            "message": "场景规划完成，请确认：",
            "options": ["满意，进行下一步", "需要调整场景顺序", "需要增减场景", "需要调整角色安排"],
        })

        confirm = await _wait_for_confirm_bg(session_id, "scene_director", "场景规划完成，请确认")
        if confirm is None:
            return

        # Handoff message appears AFTER user confirms
        _num_scenes = len(scene_result.get("scenes", [])) if isinstance(scene_result, dict) else 0
        _is_fallback = scene_result.get("_fallback", False) if isinstance(scene_result, dict) else False
        _handoff_msg = f"场景拆分完成（{_num_scenes} 个场景），将传递给角色演员进行表演。"
        if _is_fallback:
            _handoff_msg = f"场景规划降级为单场景模式（原因：{scene_result.get('_error', '未知')}），将基于章节大纲继续生成。"
        _emit(session_id, {
            "type": "handoff", "from": "Scene Director", "to": "Actor Agents",
            "content": _handoff_msg,
        })

        # ── Step 2: Actor Agents (via SceneSimulator) ────────
        session["current_step"] = "actor_agents"
        characters = req_data.get("characters", [])
        scene_list = scene_result.get("scenes", []) if isinstance(scene_result, dict) else []
        num_scenes = len(scene_list) if scene_list else 1
        _emit(session_id, {
            "type": "step_start", "step": "actor_agents",
            "label": "Actor Agents",
            "detail": f"正在为 {len(characters)} 个角色 × {num_scenes} 个场景生成表演记录...",
        })
        full_text = ""
        actor_prompt_sent = ""
        sim_result = {}  # Initialize before try block to avoid NameError in editor step
        all_scene_results: list[dict] = []  # Results from each scene
        try:
            from agents.production.scene_simulator import SceneSimulator
            from rag.memory.manager import MemoryManager
            from ui.backend.app.settings import settings as app_settings

            # Resolve DB path
            try:
                from ui.backend.app.utils import load_repo_config, get_db_path
                repo_cfg = load_repo_config(app_settings.repo_root)
                db_path = get_db_path(repo_cfg, app_settings.repo_root)
            except Exception:
                db_path = str(app_settings.repo_root / "data" / "novels.db")

            # Ensure creation tables (information_events etc.) exist in this DB
            try:
                import sqlite3 as _sql
                from database.creation_schema import ensure_creation_tables
                with _sql.connect(db_path) as _tc:
                    ensure_creation_tables(_tc)
            except Exception as _te:
                logger.debug("ensure_creation_tables skipped: %s", _te)

            memory = MemoryManager(db_path=db_path, router=router_inst)
            _proj_id = req_data.get("project_id", "")
            memory.set_project(_proj_id)

            # Pre-load world book entries into semantic memory for RAG retrieval
            try:
                wb_items = _list_data("worldbook") if "_list_data" in dir() else _list("worldbook")
                for wb in wb_items:
                    if wb.get("project_id", "") in ("", _proj_id) and wb.get("content"):
                        memory.store_memory(
                            f"[{wb.get('category', '')}] {wb.get('title', '')}: {wb.get('content', '')}",
                            memory_type="setting", chapter_num=0,
                        )
            except Exception as wb_err:
                logger.debug("World book pre-load skipped: %s", wb_err)

            simulator = SceneSimulator(
                router_inst, memory,
                project_id=_proj_id,
            )

            # Ensure each scene has a characters list; apply aliases to scene character names
            _display_characters = [_aliases.get(c, c) for c in characters] if _aliases else characters
            if isinstance(scene_result, dict):
                for sc in scene_result.get("scenes", []):
                    if not sc.get("characters"):
                        sc["characters"] = _display_characters
                    elif _aliases:
                        sc["characters"] = [_aliases.get(c, c) for c in sc["characters"]]

            # Build character cards from stored data (apply aliases for hidden identity)
            _aliases = req_data.get("character_aliases", {})
            character_cards: dict[str, str] = {}
            try:
                from ui.backend.app.routers.data_api import _list
                all_chars = _list("characters")
                pid = req_data.get("project_id", "")
                for c_name in characters:
                    display_name = _aliases.get(c_name, c_name)
                    card_parts = []
                    for cd in all_chars:
                        if cd.get("name") == c_name and (not pid or cd.get("project_id", "") in ("", pid)):
                            if cd.get("personality"): card_parts.append(f"性格: {cd['personality']}")
                            if cd.get("background"): card_parts.append(f"背景: {cd['background']}")
                            if cd.get("speech_style"): card_parts.append(f"说话风格: {cd['speech_style']}")
                            if cd.get("role"): card_parts.append(f"角色定位: {cd['role']}")
                            break
                    if c_name != display_name:
                        card_parts.append(f"【隐藏身份】本章中以「{display_name}」出场，禁止使用真名「{c_name}」")
                    character_cards[display_name] = "\n".join(card_parts) if card_parts else ""
            except Exception:
                character_cards = {_aliases.get(c, c): "" for c in characters}

            _chapter_num = req_data.get("chapter_num", 1)
            _emit(session_id, {"type": "progress_update", "step": "actor_agents", "progress": 20, "detail": f"准备角色卡... ({len(characters)} 个角色)"})

            # Use simulate_chapter() to iterate through all scenes properly
            if scene_list:
                actor_prompt_sent = (
                    f"SceneSimulator.simulate_chapter(\n"
                    f"  scenes={num_scenes},\n"
                    f"  characters={characters},\n"
                    f"  mode='parallel'\n"
                    f")"
                )
                all_scene_results = await simulator.simulate_chapter(
                    scene_plans=scene_list,
                    character_cards=character_cards,
                    chapter_num=_chapter_num,
                    style_profile=req_data.get("style_notes", ""),
                    constraints=req_data.get("world_rules", ""),
                )
            else:
                # Fallback: single scene from top-level scene_result
                fallback_scene = dict(scene_result) if isinstance(scene_result, dict) else {}
                if not fallback_scene.get("characters"):
                    fallback_scene["characters"] = characters
                actor_prompt_sent = (
                    f"SceneSimulator.simulate_scene(\n"
                    f"  scene_plan=...,\n"
                    f"  characters={characters},\n"
                    f"  mode='parallel'\n"
                    f")"
                )
                single_result = await simulator.simulate_scene(
                    scene_plan=fallback_scene,
                    character_cards=character_cards,
                    chapter_num=_chapter_num,
                    style_profile=req_data.get("style_notes", ""),
                    constraints=req_data.get("world_rules", ""),
                    mode="parallel",
                )
                all_scene_results = [single_result]

            # Combine all scene results into a unified sim_result
            all_performances: dict[str, str] = {}
            all_narrators: list[str] = []
            combined_parts: list[str] = []
            for i, sr in enumerate(all_scene_results):
                perfs = sr.get("performances", {})
                for k, v in perfs.items():
                    key = f"{k}" if len(all_scene_results) == 1 else f"{k}_scene{i+1}"
                    all_performances[key] = v
                if sr.get("narrator"):
                    all_narrators.append(sr["narrator"])
                if sr.get("combined"):
                    combined_parts.append(sr["combined"])

            full_text = "\n\n".join(combined_parts)
            sim_result = {
                "performances": all_performances,
                "narrator": "\n\n".join(all_narrators),
                "combined": full_text,
                "scene_results": all_scene_results,
            }

            _emit(session_id, {"type": "progress_update", "step": "actor_agents", "progress": 80, "detail": "角色表演完成，检查一致性..."})

            # Group-chat style: emit each character's performance as a separate message
            # with the character name as agent_display_name (e.g. "陈明" not "Actor Agents")
            _seen_chars = set()
            for char_name, perf_text in all_performances.items():
                display_name = char_name.split("_scene")[0] if "_scene" in char_name else char_name
                if display_name in _seen_chars:
                    continue  # avoid duplicate per-character messages across scenes
                _seen_chars.add(display_name)
                # Collect all scene performances for this character
                _all_perf_for_char = []
                for k, v in all_performances.items():
                    dn = k.split("_scene")[0] if "_scene" in k else k
                    if dn == display_name:
                        _all_perf_for_char.append(v)
                _combined_perf = "\n\n".join(_all_perf_for_char)
                _emit(session_id, {
                    "type": "step_done", "step": "actor_agents",
                    "agent_display_name": display_name,
                    "result": {"text": _combined_perf[:3000]},
                })

            # Run consistency check → emit warnings as group-chat messages per character
            try:
                from agents.production.actor_agent import ActorAgent
                for char_name, perf_text in all_performances.items():
                    display_name = char_name.split("_scene")[0] if "_scene" in char_name else char_name
                    card = character_cards.get(display_name, "")
                    if card:
                        checker = ActorAgent(router_inst, project_id=req_data.get("project_id", ""),
                                             character_name=display_name, character_card=card)
                        warnings = await checker.check_consistency(perf_text, card, scene_result)
                        for w in warnings:
                            _emit(session_id, {
                                "type": "agent_warning", "step": "actor_agents",
                                "agent": display_name,
                                "message": w.get("message", ""),
                                "options": ["这是故意的，记录原因", "修改指令"],
                            })
            except Exception as _cc_err:
                logger.debug("Consistency check skipped: %s", _cc_err)

            _emit(session_id, {"type": "progress_update", "step": "actor_agents", "progress": 100, "detail": "角色表演完成"})
        except Exception as e:
            logger.error("Actor agents error: %s", e, exc_info=True)
            full_text = f"（表演记录生成失败：{str(e)[:200]}）"
            _emit(session_id, {
                "type": "step_done", "step": "actor_agents",
                "result": {"text": full_text, "error": str(e)[:200], "prompt_sent": actor_prompt_sent},
            })

        # Emit follow-up options for Actor Agents confirmation
        _emit(session_id, {
            "type": "follow_up", "step": "actor_agents",
            "message": "角色表演完成，请确认：",
            "options": ["满意，进行下一步", "需要调整对话风格", "需要修改角色表现", "需要增加/删减内容"],
        })

        confirm = await _wait_for_confirm_bg(session_id, "actor_agents", "角色对话生成完成，请确认")
        if confirm is None:
            return

        # Handoff message appears AFTER user confirms
        _emit(session_id, {
            "type": "handoff", "from": "Actor Agents", "to": "Editor-Writer",
            "content": f"角色表演记录已生成（{len(full_text)}字，{len(characters)}个角色，{num_scenes}个场景），将传递给编辑转化为正文。",
        })

        # ── Step 3: Editor-Writer ───────────────────────────
        session["current_step"] = "editor_writer"
        _emit(session_id, {
            "type": "step_start", "step": "editor_writer",
            "label": "Editor-Writer", "detail": "正在进行文学风格化与润色...",
        })
        _emit(session_id, {"type": "progress_update", "step": "editor_writer", "progress": 10, "detail": "准备素材和风格参数..."})
        edited_text = full_text
        editor_prompt_sent = ""
        try:
            from agents.production.editor_writer import EditorWriter
            editor = EditorWriter(router_inst, project_id=req_data.get("project_id", ""))

            # Build per-scene performance records for the editor
            perf_list: list[str] = []
            narrator_text = ""
            if all_scene_results:
                for i, sr in enumerate(all_scene_results):
                    scene_perfs = sr.get("performances", {})
                    scene_narrator = sr.get("narrator", "")
                    # Combine each scene's performances into one record
                    scene_record_parts = []
                    if scene_narrator:
                        scene_record_parts.append(f"[旁白]\n{scene_narrator}")
                    for char_name, perf in scene_perfs.items():
                        scene_record_parts.append(f"[{char_name}的表演]\n{perf}")
                    if scene_record_parts:
                        perf_list.append("\n\n".join(scene_record_parts))
                narrator_text = (sim_result or {}).get("narrator", "")
            else:
                perf_list = list((sim_result or {}).get("performances", {}).values()) if isinstance(sim_result, dict) else []
                narrator_text = (sim_result or {}).get("narrator", "") if isinstance(sim_result, dict) else ""
            if not perf_list:
                perf_list = [full_text] if full_text else ["（无表演记录）"]

            # Add existing content context and length target to constraints
            _existing = req_data.get("existing_content", "")
            _extra_constraints = []
            if _existing:
                _extra_constraints.append(f"[已有正文（续写请衔接）]\n{_existing[-1500:]}")
            _extra_constraints.append("目标字数：约2000中文字。请保证内容充实完整，不要过短。")
            _editor_aliases = req_data.get("character_aliases", {})
            if _editor_aliases:
                _alias_lines = [f"「{real}」→「{alias}」" for real, alias in _editor_aliases.items()]
                _extra_constraints.append(
                    f"【隐藏身份】以下角色在本章中使用化名，正文中只能使用化名：\n" + "\n".join(_alias_lines)
                )
            _combined_constraints = req_data.get("world_rules", "")
            if _extra_constraints:
                _combined_constraints += "\n" + "\n".join(_extra_constraints)

            # A4: Load user style preferences from EditAnalyzer feedback loop
            _editor_db_path = _get_db_path()
            _editor_proj_id = req_data.get("project_id", "")
            _user_prefs = _load_user_style_preferences(_editor_proj_id, _editor_db_path)

            # B1: Load reference work style info
            _ref_style = _load_reference_style(_editor_proj_id, _editor_db_path)

            # Build enriched style profile
            _style_profile = req_data.get("style_notes", "")
            if _ref_style:
                _style_profile += f"\n\n{_ref_style}"

            # Build memory context for editor
            _editor_memory_ctx = ""
            try:
                _editor_memory_ctx = memory.get_context_for_editor()
            except Exception:
                pass

            editor_prompt_sent = json.dumps({
                "method": "EditorWriter.assemble_chapter",
                "performance_count": len(perf_list),
                "scene_count": len(all_scene_results),
                "narrator_length": len(narrator_text),
                "style_notes": req_data.get("style_notes", "")[:200],
                "has_existing_content": bool(_existing),
                "has_user_preferences": bool(_user_prefs),
                "has_reference_style": bool(_ref_style),
            }, ensure_ascii=False, indent=2)

            edited_text = await editor.assemble_chapter(
                performance_records=perf_list,
                narrator_text=narrator_text,
                chapter_num=req_data.get("chapter_num", 1),
                chapter_title=req_data.get("chapter_title", ""),
                style_profile=_style_profile,
                user_preferences=_user_prefs,
                memory_context=_editor_memory_ctx,
                constraints=_combined_constraints,
            ) or full_text
            _emit(session_id, {
                "type": "step_done", "step": "editor_writer",
                "result": {"text": edited_text, "word_count": len(edited_text), "prompt_sent": editor_prompt_sent},
            })
        except Exception as e:
            _emit(session_id, {
                "type": "step_done", "step": "editor_writer",
                "result": {"text": edited_text, "error": str(e)[:200]},
            })

        # A2: Run chapter-complete memory hook after EditorWriter finishes
        try:
            _chapter_num_for_hook = req_data.get("chapter_num", 1)
            _proj_id_for_hook = req_data.get("project_id", "")
            if edited_text and len(edited_text) > 100 and 'memory' in dir():
                await _run_chapter_complete_hook(
                    memory, router_inst, _proj_id_for_hook,
                    _chapter_num_for_hook, edited_text, scene_result,
                )
                _emit(session_id, {
                    "type": "info", "step": "memory_update",
                    "detail": f"第{_chapter_num_for_hook}章记忆系统已更新（摘要、事件、伏笔）",
                })
        except Exception as mem_err:
            logger.warning("Memory hook skipped: %s", mem_err)

        # Emit follow-up options for Editor-Writer confirmation
        _emit(session_id, {
            "type": "follow_up", "step": "editor_writer",
            "message": "编辑润色完成，请确认：",
            "options": ["满意，进行下一步", "需要调整文风", "需要修改节奏", "需要修改篇幅"],
        })

        confirm = await _wait_for_confirm_bg(session_id, "editor_writer", "编辑润色完成，请确认")
        if confirm is None:
            return

        # Handoff message appears AFTER user confirms
        _emit(session_id, {
            "type": "handoff", "from": "Editor-Writer", "to": "Evaluator",
            "content": f"润色后的文稿（{len(edited_text)}字）已传递给评估器进行质量检查。",
        })

        # ── Step 4: Evaluator ───────────────────────────────
        session["current_step"] = "evaluator"
        _emit(session_id, {
            "type": "step_start", "step": "evaluator",
            "label": "Evaluator", "detail": "正在评估质量...",
        })
        eval_result = {"score": 80, "passed": True, "issues": [], "process": []}
        try:
            from agents.evaluation.repetition_detector import RepetitionDetector
            from agents.evaluation.slop_detector import SlopDetector
            process_log = []
            issues = []

            # --- Detector 1: Repetition ---
            process_log.append({"detector": "RepetitionDetector", "status": "running", "detail": "检测句首重复、短语重复、结构重复..."})
            rep = RepetitionDetector()
            rep_issues = rep.detect(edited_text)
            rep_found = []
            for ri in (rep_issues or [])[:5]:
                desc = str(ri.get("phrase", "重复")) + f" (出现{ri.get('count', 0)}次)"
                rep_found.append(desc)
                issues.append({
                    "type": "repetition", "severity": "medium",
                    "description": desc,
                    "location": ri.get("location", ""),
                })
            process_log.append({
                "detector": "RepetitionDetector", "status": "done",
                "detail": f"发现 {len(rep_found)} 处重复" if rep_found else "未发现明显重复",
                "findings": rep_found,
            })

            # --- Detector 2: Slop ---
            process_log.append({"detector": "SlopDetector", "status": "running", "detail": "检测AI生成痕迹（固定句式、空洞修饰、过度比喻等）..."})
            slop = SlopDetector()
            slop_issues = slop.detect(edited_text)
            slop_found = []
            for si in (slop_issues or [])[:5]:
                desc = str(si.get("pattern", "AI味")) + f": {si.get('match', '')}"
                slop_found.append(desc)
                issues.append({
                    "type": "ai_flavor", "severity": "medium",
                    "description": desc,
                    "location": si.get("match", ""),
                })
            process_log.append({
                "detector": "SlopDetector", "status": "done",
                "detail": f"发现 {len(slop_found)} 处AI味表达" if slop_found else "未发现明显AI痕迹",
                "findings": slop_found,
            })

            # --- Detector 3: Cross-chapter continuity (B2: foreshadowing audit) ---
            try:
                _eval_db_path = _get_db_path()
                _eval_proj_id = req_data.get("project_id", "")
                _eval_chapter_num = req_data.get("chapter_num", 1)
                from rag.memory.episodic_timeline import EpisodicTimeline
                _eval_timeline = EpisodicTimeline(_eval_db_path)
                _unresolved = _eval_timeline.get_unresolved_foreshadowing(_eval_proj_id)
                if _unresolved:
                    process_log.append({"detector": "CrossChapterChecker", "status": "running", "detail": "检查伏笔回收与角色一致性..."})
                    from agents.evaluation.cross_chapter_checker import CrossChapterChecker
                    _cc = CrossChapterChecker(router_inst, project_id=_eval_proj_id)
                    _cc_result = await _cc.audit_foreshadowing(
                        _unresolved, _eval_chapter_num, recent_text=edited_text[:2000],
                    )
                    _overdue = _cc_result.get("overdue", [])
                    for od in _overdue[:3]:
                        issues.append({
                            "type": "foreshadowing", "severity": "medium",
                            "description": f"超期伏笔: {od}" if isinstance(od, str) else f"超期伏笔: {od.get('description', '')}",
                        })
                    process_log.append({
                        "detector": "CrossChapterChecker", "status": "done",
                        "detail": f"发现 {len(_overdue)} 个超期伏笔" if _overdue else "伏笔状态正常",
                        "findings": [str(o)[:100] for o in _overdue[:5]],
                    })
            except Exception as cc_err:
                logger.debug("CrossChapterChecker skipped: %s", cc_err)

            # --- Detector 4: LLM-based comprehensive evaluation ---
            llm_eval = None
            try:
                process_log.append({"detector": "LLM Evaluator", "status": "running", "detail": "使用LLM进行6维度深度评估..."})
                from agents.evaluation.evaluator import Evaluator
                evaluator = Evaluator(router_inst, project_id=req_data.get("project_id", ""))
                llm_eval = await evaluator.evaluate_chapter(
                    edited_text,
                    chapter_num=1,
                    scene_plan=scene_result if isinstance(scene_result, dict) else None,
                    constraints=req_data.get("world_rules", ""),
                )
                # Merge LLM issues
                for li in (llm_eval.get("issues") or [])[:8]:
                    issues.append({
                        "type": li.get("dimension", "llm_eval"),
                        "severity": li.get("severity", "low"),
                        "description": li.get("description", ""),
                        "location": li.get("location", ""),
                        "suggestion": li.get("suggestion", ""),
                    })
                process_log.append({
                    "detector": "LLM Evaluator", "status": "done",
                    "detail": llm_eval.get("summary", f"LLM评分: {llm_eval.get('score', '?')}"),
                    "findings": llm_eval.get("strengths", []),
                    "llm_score": llm_eval.get("score"),
                })
            except Exception as eval_err:
                logger.warning("LLM evaluator failed, using detector-only scores: %s", eval_err)
                process_log.append({"detector": "LLM Evaluator", "status": "skipped", "detail": f"LLM评估跳过: {str(eval_err)[:100]}"})

            # --- Compute final score ---
            detector_score = max(0, 100 - len([i for i in issues if i.get("type") in ("repetition", "ai_flavor")]) * 8)
            if llm_eval and llm_eval.get("score") is not None:
                # Weighted: 40% detector, 60% LLM
                final_score = int(detector_score * 0.4 + llm_eval["score"] * 0.6)
            else:
                final_score = detector_score

            # Build categorized results for explainable evaluation UI
            slop_issues_cat = [i for i in issues if i.get("type") == "ai_flavor"]
            rep_issues_cat = [i for i in issues if i.get("type") == "repetition"]
            other_issues_cat = [i for i in issues if i.get("type") not in ("ai_flavor", "repetition")]
            llm_score_val = llm_eval.get("score", 70) if llm_eval else 70
            categories = [
                {
                    "id": "slop_detection", "name": "AI味检测 (Slop)",
                    "score": max(0, 5 - len(slop_issues_cat)), "max_score": 5,
                    "rationale": f"检测到 {len(slop_issues_cat)} 处AI常见表达模式。" if slop_issues_cat else "未发现明显AI痕迹。",
                    "findings": [i["description"] for i in slop_issues_cat],
                },
                {
                    "id": "repetition", "name": "重复检测",
                    "score": max(0, 5 - len(rep_issues_cat)), "max_score": 5,
                    "rationale": f"发现 {len(rep_issues_cat)} 处重复表达。" if rep_issues_cat else "句式变化丰富，无明显重复。",
                    "findings": [i["description"] for i in rep_issues_cat],
                },
                {
                    "id": "narrative_consistency", "name": "叙事一致性",
                    "score": max(0, 5 - len(other_issues_cat)), "max_score": 5,
                    "rationale": f"发现 {len(other_issues_cat)} 处叙事问题。" if other_issues_cat else "叙事逻辑通顺。",
                    "findings": [i["description"] for i in other_issues_cat[:5]],
                },
                {
                    "id": "foreshadowing", "name": "伏笔一致性",
                    "score": max(0, 5 - len([i for i in issues if i.get("type") == "foreshadowing"])),
                    "max_score": 5,
                    "rationale": (
                        f"发现 {len([i for i in issues if i.get('type') == 'foreshadowing'])} 个超期伏笔需要注意。"
                        if any(i.get("type") == "foreshadowing" for i in issues)
                        else "伏笔线索与前文保持一致。"
                    ),
                    "findings": [i["description"] for i in issues if i.get("type") == "foreshadowing"],
                },
                {
                    "id": "literary_quality", "name": "文学质量",
                    "score": min(5, max(1, llm_score_val // 20)), "max_score": 5,
                    "rationale": llm_eval.get("summary", "语言质量评估完成。") if llm_eval else "使用规则检测器评估。",
                    "findings": llm_eval.get("strengths", [])[:5] if llm_eval else [],
                },
                {
                    "id": "llm_evaluation", "name": "LLM 深度评估",
                    "score": min(5, max(1, llm_score_val // 20)), "max_score": 5,
                    "rationale": llm_eval.get("summary", "LLM评估未运行。") if llm_eval else "LLM评估跳过，仅使用规则检测。",
                    "findings": [f"+ {s}" for s in (llm_eval.get("strengths", []) if llm_eval else [])],
                },
            ]
            eval_result = {
                "score": final_score,
                "passed": final_score >= 60,
                "issues": issues,
                "process": process_log,
                "categories": categories,
                "strengths": llm_eval.get("strengths", []) if llm_eval else [],
                "summary": llm_eval.get("summary", "") if llm_eval else "",
            }
        except Exception as e:
            logger.error("Evaluation error: %s", e, exc_info=True)
            eval_result["process"] = [{"detector": "error", "status": "error", "detail": str(e)[:200]}]

        _emit(session_id, {"type": "step_done", "step": "evaluator", "result": eval_result})
        _emit(session_id, {"type": "complete", "text": edited_text, "evaluation": eval_result})

        session["status"] = "complete"
        session["text"] = edited_text
        session["result"] = {"text": edited_text, "evaluation": eval_result}

    except Exception as e:
        logger.error("Pipeline background error: %s", e, exc_info=True)
        _emit(session_id, {"type": "error", "message": str(e)[:300]})
        session["status"] = "error"


@router.websocket("/ws/{session_id}")
async def generation_websocket(websocket: WebSocket, session_id: str):
    """WebSocket that streams events from a running background pipeline.

    Clients can disconnect and reconnect — the pipeline keeps running.
    On connect, all past events are replayed, then new events are streamed live.
    """
    await websocket.accept()
    session = _active_sessions.get(session_id)
    if not session:
        await websocket.send_json({"type": "error", "message": "Session not found"})
        await websocket.close()
        return

    cursor = 0  # index into session["events"]
    try:
        while True:
            events = session.get("events", [])
            # Send any new events
            while cursor < len(events):
                await websocket.send_json(events[cursor])
                cursor += 1

            # Check if pipeline is done
            if session["status"] in ("complete", "error"):
                break

            # Check for incoming messages (confirm/abort)
            try:
                msg = await asyncio.wait_for(websocket.receive_json(), timeout=0.3)
                if msg:
                    # Forward confirm to background task
                    session["confirm_data"] = msg
                    evt = session.get("confirm_event")
                    if evt:
                        evt.set()
            except asyncio.TimeoutError:
                pass
    except WebSocketDisconnect:
        logger.info("WS client disconnected from %s (pipeline continues)", session_id)
    except Exception as e:
        logger.error("WS relay error: %s", e)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# ═══ A/B Compare Engine ═══

class ABCompareRequest(BaseModel):
    prompt: str
    system_prompt: str = ""
    models: list[dict] = []  # [{"provider": "openai", "model": "gpt-4o"}, ...]
    agent_role: str = "editor_stylist"
    temperature: float = 0.7
    max_tokens: int = 2000

_ab_sessions: dict[str, dict[str, Any]] = {}

@router.post("/ab/compare")
async def ab_compare(req: ABCompareRequest):
    """Run same prompt through multiple models and return side-by-side results."""
    if not req.models or len(req.models) < 2:
        raise HTTPException(400, "至少需要2个模型进行比较")
    if len(req.models) > 4:
        raise HTTPException(400, "最多支持4个模型同时比较")

    session_id = f"ab_{uuid.uuid4().hex[:12]}"
    _ab_sessions[session_id] = {"status": "running", "results": {}, "errors": {}, "request": req.model_dump()}

    async def _run_one(provider: str, model: str) -> tuple[str, dict]:
        label = f"{provider}/{model}"
        try:
            r = _build_router(provider, model)
            from models.base import LLMMessage as Msg
            msgs = []
            if req.system_prompt:
                msgs.append(Msg(role="system", content=req.system_prompt))
            msgs.append(Msg(role="user", content=req.prompt))
            t0 = time.time()
            resp = await r.generate(
                agent_role=req.agent_role, messages=msgs,
                temperature=req.temperature, max_tokens=req.max_tokens,
            )
            elapsed = time.time() - t0
            return label, {
                "content": resp.content,
                "model": resp.model,
                "tokens": resp.total_tokens,
                "elapsed_s": round(elapsed, 2),
                "provider": provider,
            }
        except Exception as e:
            return label, {"error": str(e)[:300], "provider": provider, "model": model}

    tasks = [_run_one(m["provider"], m["model"]) for m in req.models]
    results_list = await asyncio.gather(*[t for t in tasks], return_exceptions=True)

    results = {}
    errors = {}
    for item in results_list:
        if isinstance(item, Exception):
            errors["unknown"] = str(item)[:300]
            continue
        label, data = item
        if "error" in data:
            errors[label] = data["error"]
        else:
            results[label] = data

    response = {
        "session_id": session_id,
        "prompt_preview": req.prompt[:200],
        "results": results,
        "errors": errors,
        "model_count": len(req.models),
    }
    _ab_sessions[session_id] = {"status": "complete", **response}
    return response

# ═══ A4: EditAnalyzer feedback endpoint ═══

class EditFeedbackRequest(BaseModel):
    project_id: str
    chapter_num: int = 0
    original_text: str
    edited_text: str

@router.post("/edit-feedback")
async def analyze_user_edit(req: EditFeedbackRequest):
    """Analyze user edits to learn style preferences (A4: feedback loop)."""
    try:
        router_inst = _build_router("", "")
        from agents.evaluation.edit_analyzer import EditAnalyzer
        db_path = _get_db_path()
        analyzer = EditAnalyzer(
            router_inst, project_id=req.project_id, db_path=db_path,
        )
        result = await analyzer.analyze_edit(
            req.original_text, req.edited_text,
            chapter_num=req.chapter_num,
        )
        return {"status": "ok", "analysis": result}
    except Exception as e:
        logger.error("Edit feedback error: %s", e)
        raise HTTPException(500, detail=str(e)[:300])


@router.get("/edit-preferences/{project_id}")
async def get_learned_preferences(project_id: str):
    """Get accumulated style preferences learned from user edits."""
    try:
        db_path = _get_db_path()
        prefs = _load_user_style_preferences(project_id, db_path)
        return {"status": "ok", "preferences": prefs}
    except Exception as e:
        raise HTTPException(500, detail=str(e)[:300])


# ═══ A3: ChapterPlanner auto-outline endpoint ═══

class AutoOutlineRequest(BaseModel):
    project_id: str
    chapter_num: int
    volume_outline: str = ""
    provider: str = ""
    model: str = ""

@router.post("/auto-outline")
async def auto_outline(req: AutoOutlineRequest):
    """Generate chapter outline automatically using ChapterPlanner + memory context (A3)."""
    try:
        router_inst = _build_router(req.provider, req.model)
        db_path = _get_db_path()

        # Load memory context for planning
        previous_summary = ""
        character_states = ""
        unresolved_threads = ""
        try:
            from rag.memory.manager import MemoryManager
            mem = MemoryManager(db_path=db_path)
            mem.set_project(req.project_id)

            # Get previous chapter summaries
            buffer_text = mem.chapter_buffer.get_buffer_text(req.project_id)
            if buffer_text:
                previous_summary = buffer_text

            # Get unresolved foreshadowing
            foreshadowing = mem.get_unresolved_foreshadowing()
            if foreshadowing:
                unresolved_threads = "\n".join(
                    f"- 第{f['chapter_num']}章: {f['description']}"
                    for f in foreshadowing[:10]
                )
        except Exception as mem_err:
            logger.debug("Memory context for auto-outline skipped: %s", mem_err)

        # Get character states from most recent chapter
        try:
            from rag.memory.chapter_buffer import ChapterBuffer
            cb = ChapterBuffer(db_path)
            summaries = cb.get_active_summaries(req.project_id)
            if summaries:
                latest = summaries[-1]
                if latest.get("character_states"):
                    cs = latest["character_states"]
                    if isinstance(cs, dict):
                        character_states = "\n".join(f"- {k}: {v}" for k, v in cs.items())
                    elif isinstance(cs, str):
                        character_states = cs
        except Exception:
            pass

        # D1: Add shuangdian guidance to constraints
        constraints = _build_shuangdian_guidance(req.chapter_num)

        from agents.planner.chapter_planner import ChapterPlanner
        planner = ChapterPlanner(router=router_inst, project_id=req.project_id)
        result = await planner.plan_chapter(
            req.volume_outline, req.chapter_num,
            previous_summary=previous_summary,
            character_states=character_states,
            unresolved_threads=unresolved_threads,
            constraints=constraints,
        )
        return {"status": "ok", "chapter_plan": result}
    except Exception as e:
        logger.error("Auto outline error: %s", e)
        raise HTTPException(500, detail=str(e)[:300])


# ═══ A1: Batch generation endpoint ═══

class BatchGenerateRequest(BaseModel):
    project_id: str = ""
    chapter_range: list[int] = []  # [start, end] inclusive
    volume_outline: str = ""
    provider: str = ""
    model: str = ""
    style_notes: str = ""
    world_rules: str = ""

_batch_sessions: dict[str, dict[str, Any]] = {}

@router.post("/batch/start")
async def batch_generate_start(req: BatchGenerateRequest):
    """Start batch generation of multiple chapters (A1)."""
    if len(req.chapter_range) != 2:
        raise HTTPException(400, "chapter_range must be [start, end]")
    start, end = req.chapter_range
    if start > end or end - start > 20:
        raise HTTPException(400, "Invalid range (max 20 chapters)")

    session_id = f"batch_{uuid.uuid4().hex[:12]}"
    _batch_sessions[session_id] = {
        "status": "running",
        "request": req.model_dump(),
        "chapters": list(range(start, end + 1)),
        "completed": [],
        "current_chapter": start,
        "results": {},
        "errors": {},
    }

    asyncio.create_task(_run_batch_pipeline(session_id))
    return {"status": "started", "session_id": session_id, "chapter_count": end - start + 1}


@router.get("/batch/status/{session_id}")
async def batch_status(session_id: str):
    """Get batch generation progress."""
    session = _batch_sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Batch session not found")
    return {
        "status": session["status"],
        "current_chapter": session.get("current_chapter"),
        "completed": session.get("completed", []),
        "total": len(session.get("chapters", [])),
        "errors": session.get("errors", {}),
    }


@router.post("/batch/stop/{session_id}")
async def batch_stop(session_id: str):
    """Stop batch generation."""
    session = _batch_sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Batch session not found")
    session["status"] = "stopped"
    return {"status": "stopped"}


async def _run_batch_pipeline(session_id: str):
    """Run pipeline for each chapter sequentially with memory carry-over."""
    session = _batch_sessions.get(session_id)
    if not session:
        return
    req_data = session["request"]

    try:
        router_inst = _build_router(req_data.get("provider", ""), req_data.get("model", ""))
        db_path = _get_db_path()
        project_id = req_data.get("project_id", "")

        from rag.memory.manager import MemoryManager
        memory = MemoryManager(db_path=db_path, router=router_inst)
        memory.set_project(project_id)

        # Ensure tables exist
        try:
            import sqlite3 as _sql
            from database.creation_schema import ensure_creation_tables
            with _sql.connect(db_path) as _tc:
                ensure_creation_tables(_tc)
        except Exception:
            pass

        for chapter_num in session["chapters"]:
            if session["status"] == "stopped":
                break

            session["current_chapter"] = chapter_num
            try:
                # Step 1: Auto-generate chapter outline
                from agents.planner.chapter_planner import ChapterPlanner
                planner = ChapterPlanner(router=router_inst, project_id=project_id)

                previous_summary = memory.chapter_buffer.get_buffer_text(project_id)
                unresolved = memory.get_unresolved_foreshadowing()
                unresolved_text = "\n".join(
                    f"- 第{f['chapter_num']}章: {f['description']}" for f in unresolved[:10]
                ) if unresolved else ""

                chapter_plan = await planner.plan_chapter(
                    req_data.get("volume_outline", ""), chapter_num,
                    previous_summary=previous_summary,
                    unresolved_threads=unresolved_text,
                    constraints=_build_shuangdian_guidance(chapter_num),
                )

                synopsis = chapter_plan.get("summary", "")
                characters = chapter_plan.get("scene_sketches", [{}])[0].get("characters", []) if chapter_plan.get("scene_sketches") else []

                # Step 2: SceneDirector
                from agents.production.scene_director import SceneDirector
                director = SceneDirector(router_inst, project_id=project_id)
                _batch_constraints = req_data.get("world_rules", "")
                try:
                    _batch_constraints += _build_chapter_hook_constraint(chapter_num)
                    _batch_constraints += _build_shuangdian_guidance(chapter_num)
                except Exception:
                    pass
                _batch_memory_ctx = ""
                try:
                    foreshadow_ctx = _load_unresolved_foreshadowing(project_id, db_path, chapter_num)
                    _batch_memory_ctx = memory.get_context_for_scene_director(chapter_num)
                    if foreshadow_ctx:
                        _batch_memory_ctx += f"\n\n{foreshadow_ctx}"
                except Exception:
                    pass
                # Truncate to prevent overload
                if len(_batch_constraints) > 3000:
                    _batch_constraints = _batch_constraints[:3000]
                if len(_batch_memory_ctx) > 4000:
                    _batch_memory_ctx = _batch_memory_ctx[:4000]

                try:
                    scene_result = await director.plan_scenes(
                        chapter_outline=synopsis,
                        chapter_num=chapter_num,
                        memory_context=_batch_memory_ctx,
                        constraints=_batch_constraints,
                    )
                except Exception as sd_err:
                    logger.warning("Batch scene director failed for ch%d: %s — retrying minimal", chapter_num, sd_err)
                    try:
                        scene_result = await director.plan_scenes(
                            chapter_outline=synopsis,
                            chapter_num=chapter_num,
                            memory_context="",
                            constraints=req_data.get("world_rules", ""),
                        )
                    except Exception as sd_err2:
                        logger.error("Batch scene director retry failed for ch%d: %s", chapter_num, sd_err2)
                        scene_result = {
                            "scenes": [{
                                "scene_index": 0,
                                "location": "",
                                "time": "",
                                "characters": characters,
                                "summary": synopsis[:500],
                                "beats": [synopsis[:200]],
                                "character_instructions": {},
                                "narrator_instructions": "",
                            }],
                            "chapter_arc": "",
                        }

                # Step 3: SceneSimulator
                from agents.production.scene_simulator import SceneSimulator
                simulator = SceneSimulator(router_inst, memory, project_id=project_id)
                scene_list = scene_result.get("scenes", [])
                char_cards: dict[str, str] = {c: "" for c in characters}
                all_scene_results = await simulator.simulate_chapter(
                    scene_plans=scene_list if scene_list else [scene_result],
                    character_cards=char_cards,
                    chapter_num=chapter_num,
                    style_profile=req_data.get("style_notes", ""),
                    constraints=constraints,
                )

                # Combine performances
                perf_list = []
                narrator_text = ""
                for sr in all_scene_results:
                    scene_parts = []
                    if sr.get("narrator"):
                        scene_parts.append(f"[旁白]\n{sr['narrator']}")
                    for cn, perf in sr.get("performances", {}).items():
                        scene_parts.append(f"[{cn}的表演]\n{perf}")
                    if scene_parts:
                        perf_list.append("\n\n".join(scene_parts))
                    narrator_text += sr.get("narrator", "")

                # Step 4: EditorWriter
                from agents.production.editor_writer import EditorWriter
                editor = EditorWriter(router_inst, project_id=project_id)
                user_prefs = _load_user_style_preferences(project_id, db_path)
                ref_style = _load_reference_style(project_id, db_path)
                style_profile = req_data.get("style_notes", "")
                if ref_style:
                    style_profile += f"\n\n{ref_style}"

                edited_text = await editor.assemble_chapter(
                    performance_records=perf_list or ["（无表演记录）"],
                    narrator_text=narrator_text,
                    chapter_num=chapter_num,
                    style_profile=style_profile,
                    user_preferences=user_prefs,
                    memory_context=memory.get_context_for_editor(),
                    constraints=constraints,
                )

                # A2: Update memory for next chapter
                await _run_chapter_complete_hook(
                    memory, router_inst, project_id,
                    chapter_num, edited_text, scene_result,
                )

                session["results"][chapter_num] = {
                    "text": edited_text,
                    "word_count": len(edited_text),
                    "synopsis": synopsis,
                }
                session["completed"].append(chapter_num)

            except Exception as ch_err:
                logger.error("Batch chapter %d error: %s", chapter_num, ch_err)
                session["errors"][chapter_num] = str(ch_err)[:300]

        session["status"] = "complete" if session["status"] != "stopped" else "stopped"
    except Exception as e:
        logger.error("Batch pipeline error: %s", e, exc_info=True)
        session["status"] = "error"
        session["errors"]["global"] = str(e)[:300]


@router.get("/ab/history")
async def ab_history():
    """List recent A/B comparison sessions."""
    items = []
    for sid, data in list(_ab_sessions.items())[-20:]:
        items.append({
            "session_id": sid,
            "status": data.get("status"),
            "model_count": data.get("model_count", 0),
            "prompt_preview": data.get("prompt_preview", ""),
        })
    return {"items": items}

@router.get("/ab/result/{session_id}")
async def ab_result(session_id: str):
    """Get a specific A/B comparison result."""
    if session_id not in _ab_sessions:
        raise HTTPException(404, "A/B比较会话不存在")
    return _ab_sessions[session_id]
