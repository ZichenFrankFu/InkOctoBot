"""
/api/generation — Film pipeline execution via real agent pipeline.

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


class GenerateRequest(BaseModel):
    project_id: str = ""
    chapter_id: str = ""
    synopsis: str = ""
    time_setting: str = ""
    location: str = ""
    characters: list[str] = []
    world_rules: str = ""
    style_notes: str = ""
    system_hint: str = ""
    provider: str = ""
    model: str = ""


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
        from agents.model_providers.base import ProviderConfig
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
        from agents.model_providers.ollama_provider import OllamaProvider
        return OllamaProvider(cfg)
    elif ptype == "deepseek":
        from agents.model_providers.deepseek_provider import DeepSeekProvider
        return DeepSeekProvider(cfg)
    elif ptype == "openai":
        from agents.model_providers.openai_provider import OpenAIProvider
        return OpenAIProvider(cfg)
    elif ptype == "anthropic":
        from agents.model_providers.anthropic_provider import AnthropicProvider
        return AnthropicProvider(cfg)
    elif ptype == "gemini":
        from agents.model_providers.gemini_provider import GeminiProvider
        return GeminiProvider(cfg)
    elif ptype == "vllm":
        from agents.model_providers.vllm_provider import VLLMProvider
        return VLLMProvider(cfg)
    else:
        from agents.model_providers.ollama_provider import OllamaProvider
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
        from agents.model_providers.base import LLMMessage
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


async def _wait_for_confirm_bg(session_id: str, step: str, message: str, timeout_s: int = 600):
    """Emit need_confirm and block until user responds (or auto-continue after timeout)."""
    session = _active_sessions.get(session_id)
    if not session:
        return None
    evt = asyncio.Event()
    session["confirm_event"] = evt
    session["confirm_data"] = None
    session["waiting_confirm"] = True
    session["current_step"] = step
    _emit(session_id, {"type": "need_confirm", "step": step, "message": message})
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
        scene_result = {}
        try:
            from agents.production.scene_director import SceneDirector
            director = SceneDirector(router_inst, project_id=req_data.get("project_id", ""))
            # Build character info string for scene planning
            _chars = req_data.get("characters", [])
            _char_cards_str = "\n".join(f"- {c}" for c in _chars) if _chars else ""
            _location = req_data.get("location", "")
            _time_setting = req_data.get("time_setting", "")
            _outline = req_data.get("synopsis", "")
            if _location:
                _outline += f"\n场景地点：{_location}"
            if _time_setting:
                _outline += f"\n时间设定：{_time_setting}"
            scenes = await director.plan_scenes(
                chapter_outline=_outline,
                chapter_num=1,
                world_rules=req_data.get("world_rules", ""),
                character_cards=_char_cards_str,
            )
            scene_result = scenes if isinstance(scenes, dict) else {"raw": str(scenes)}
        except Exception as e:
            logger.error("Scene director error: %s", e, exc_info=True)
            scene_result = {"summary": f"场景规划失败：{str(e)[:200]}", "error": str(e)[:200]}
        scene_prompt = json.dumps({
            "chapter_outline": req_data.get("synopsis", "")[:500],
            "world_rules": req_data.get("world_rules", "")[:300],
            "chapter_num": 1,
        }, ensure_ascii=False, indent=2)
        scene_result_with_prompt = dict(scene_result) if isinstance(scene_result, dict) else {"raw": str(scene_result)}
        scene_result_with_prompt["prompt_sent"] = f"SceneDirector.plan_scenes({scene_prompt})"
        _emit(session_id, {"type": "step_done", "step": "scene_director", "result": scene_result_with_prompt})

        # Emit handoff: show what Scene Director outputs → Actor Agents receives
        scene_summary = ""
        if isinstance(scene_result, dict):
            scene_summary = scene_result.get("summary", scene_result.get("raw", ""))
            if not scene_summary:
                scene_summary = json.dumps(scene_result, ensure_ascii=False, indent=2)[:800]
        _emit(session_id, {
            "type": "handoff", "from": "Scene Director", "to": "Actor Agents",
            "content": f"场景指令已生成：\n{scene_summary[:500]}",
        })

        confirm = await _wait_for_confirm_bg(session_id, "scene_director", "场景拆分完成，是否继续生成？")
        if confirm is None:
            return

        # ── Step 2: Actor Agents (via SceneSimulator) ────────
        session["current_step"] = "actor_agents"
        characters = req_data.get("characters", [])
        _emit(session_id, {
            "type": "step_start", "step": "actor_agents",
            "label": "Actor Agents",
            "detail": f"正在为 {len(characters)} 个角色生成表演记录...",
        })
        full_text = ""
        actor_prompt_sent = ""
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

            memory = MemoryManager(db_path=db_path, router=router_inst)
            simulator = SceneSimulator(
                router_inst, memory,
                project_id=req_data.get("project_id", ""),
            )

            # Ensure scene_result has 'characters' list
            if isinstance(scene_result, dict) and "characters" not in scene_result:
                scene_result["characters"] = characters

            # Build character cards (minimal — names only for now)
            character_cards = {c: "" for c in characters}

            actor_prompt_sent = (
                f"SceneSimulator.simulate_scene(\n"
                f"  scene_plan={json.dumps(scene_result, ensure_ascii=False, indent=2)[:1500]},\n"
                f"  characters={characters},\n"
                f"  mode='parallel'\n"
                f")"
            )

            sim_result = await simulator.simulate_scene(
                scene_plan=scene_result,
                character_cards=character_cards,
                chapter_num=1,
                style_profile=req_data.get("style_notes", ""),
                constraints=req_data.get("world_rules", ""),
                mode="parallel",
            )

            full_text = sim_result.get("combined", "")
            performances = sim_result.get("performances", {})

            # Emit token-like updates so UI shows the performance record
            if full_text:
                _emit(session_id, {"type": "token", "step": "actor_agents", "content": full_text})

            _emit(session_id, {
                "type": "step_done", "step": "actor_agents",
                "result": {
                    "text": full_text,
                    "word_count": len(full_text),
                    "performances": {k: v[:500] for k, v in performances.items()},
                    "actor_count": len(performances),
                    "prompt_sent": actor_prompt_sent,
                },
            })
        except Exception as e:
            logger.error("Actor agents error: %s", e, exc_info=True)
            full_text = f"（表演记录生成失败：{str(e)[:200]}）"
            _emit(session_id, {
                "type": "step_done", "step": "actor_agents",
                "result": {"text": full_text, "error": str(e)[:200], "prompt_sent": actor_prompt_sent},
            })

        # Emit handoff: Actor Agents → Editor-Writer
        _emit(session_id, {
            "type": "handoff", "from": "Actor Agents", "to": "Editor-Writer",
            "content": f"角色表演记录已生成（{len(full_text)}字，{len(characters)}个角色），将传递给编辑转化为正文。",
        })

        confirm = await _wait_for_confirm_bg(session_id, "actor_agents", "角色对话生成完成，是否继续编辑润色？")
        if confirm is None:
            return

        # ── Step 3: Editor-Writer ───────────────────────────
        session["current_step"] = "editor_writer"
        _emit(session_id, {
            "type": "step_start", "step": "editor_writer",
            "label": "Editor-Writer", "detail": "正在进行文学风格化与润色...",
        })
        edited_text = full_text
        editor_prompt_sent = ""
        try:
            from agents.model_providers.base import LLMMessage as Msg
            edit_system = (
                "你是一个专业的小说编辑兼作家。请将以下角色表演记录转化为完整的小说正文：\n"
                "1. 将节拍标记、角色动作描写转化为流畅的叙事\n"
                "2. 保留对话内容，使用中文引号「」\n"
                "3. 将内心独白自然融入叙述\n"
                "4. 将氛围描写转化为环境和气氛的文学描写\n"
                "5. 保持原始的情节和角色互动\n"
                "直接输出小说正文，不要保留任何结构化标记。"
            )
            edit_user = f"请将以下角色表演记录转化为小说正文：\n\n{full_text}"
            editor_prompt_sent = json.dumps(
                [{"role": "system", "content": edit_system}, {"role": "user", "content": edit_user[:1000]}],
                ensure_ascii=False, indent=2,
            )
            edit_resp = await router_inst.generate(
                agent_role="editor_stylist",
                messages=[
                    Msg(role="system", content=edit_system),
                    Msg(role="user", content=edit_user),
                ],
                temperature=0.6, max_tokens=4096,
            )
            edited_text = edit_resp.content or full_text
            _emit(session_id, {
                "type": "step_done", "step": "editor_writer",
                "result": {"text": edited_text, "word_count": len(edited_text), "prompt_sent": editor_prompt_sent},
            })
        except Exception as e:
            _emit(session_id, {
                "type": "step_done", "step": "editor_writer",
                "result": {"text": edited_text, "error": str(e)[:200]},
            })

        # Emit handoff: Editor-Writer → Evaluator
        _emit(session_id, {
            "type": "handoff", "from": "Editor-Writer", "to": "Evaluator",
            "content": f"润色后的文稿（{len(edited_text)}字）已传递给评估器进行质量检查。",
        })

        confirm = await _wait_for_confirm_bg(session_id, "editor_writer", "编辑润色完成，是否继续质量评估？")
        if confirm is None:
            return

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

            # --- Detector 3: LLM-based comprehensive evaluation ---
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

            eval_result = {
                "score": final_score,
                "passed": final_score >= 60,
                "issues": issues,
                "process": process_log,
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
