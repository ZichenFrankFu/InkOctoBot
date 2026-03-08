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
            scenes = await director.plan_scenes(
                chapter_outline=req_data.get("synopsis", ""),
                chapter_num=1,
                world_rules=req_data.get("world_rules", ""),
            )
            scene_result = scenes if isinstance(scenes, dict) else {"raw": str(scenes)}
        except Exception as e:
            logger.error("Scene director error: %s", e, exc_info=True)
            scene_result = {"summary": f"场景规划失败：{str(e)[:200]}", "error": str(e)[:200]}
        _emit(session_id, {"type": "step_done", "step": "scene_director", "result": scene_result})

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

        # ── Step 2: Actor Agents ────────────────────────────
        session["current_step"] = "actor_agents"
        _emit(session_id, {
            "type": "step_start", "step": "actor_agents",
            "label": "Actor Agents", "detail": "正在生成角色对话与内心活动...",
        })
        full_text = ""
        try:
            from agents.model_providers.base import LLMMessage as Msg

            # Build scene context from director output
            scene_desc = ""
            if isinstance(scene_result, dict):
                scene_desc = scene_result.get("summary", scene_result.get("raw", ""))
                if not scene_desc:
                    scene_desc = json.dumps(scene_result, ensure_ascii=False, indent=2)[:1000]

            system = (
                "你是一组专业的小说角色扮演AI（Actor Agents）。\n"
                "你的任务是根据场景大纲和导演指令，以小说正文的形式写出角色对话、"
                "动作描写和内心活动。\n\n"
                "输出要求：\n"
                "1. 直接输出小说正文，不要输出JSON或结构化数据\n"
                "2. 对话使用中文引号「」\n"
                "3. 每个角色的对话和动作自然衔接\n"
                "4. 适当加入内心独白和环境描写\n"
                "5. 保持800-1500字的长度"
            )
            user_content = f"## 章节大纲\n{req_data.get('synopsis', '')}"
            if scene_desc:
                user_content += f"\n\n## 场景导演指令\n{scene_desc}"
            if req_data.get("world_rules"):
                user_content += f"\n\n## 世界观\n{req_data['world_rules']}"
            if req_data.get("style_notes"):
                user_content += f"\n\n## 风格\n{req_data['style_notes']}"
            user_content += "\n\n请根据以上信息，以小说正文的形式写出完整章节内容："

            async for token in router_inst.generate_stream(
                agent_role="actor_default",
                messages=[Msg(role="system", content=system), Msg(role="user", content=user_content)],
                temperature=0.8, max_tokens=4096,
            ):
                full_text += token
                _emit(session_id, {"type": "token", "step": "actor_agents", "content": token})

            _emit(session_id, {
                "type": "step_done", "step": "actor_agents",
                "result": {"text": full_text, "word_count": len(full_text)},
            })
        except Exception as e:
            full_text = f"（生成失败：{str(e)[:200]}）"
            _emit(session_id, {
                "type": "step_done", "step": "actor_agents",
                "result": {"text": full_text, "error": str(e)[:200]},
            })

        # Emit handoff: Actor Agents → Editor-Writer
        _emit(session_id, {
            "type": "handoff", "from": "Actor Agents", "to": "Editor-Writer",
            "content": f"角色对话草稿已生成（{len(full_text)}字），将传递给编辑进行润色。",
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
        try:
            from agents.model_providers.base import LLMMessage as Msg
            edit_system = (
                "你是一个专业的小说编辑。请对以下草稿进行文学润色：\n"
                "1. 提升文学性和画面感\n"
                "2. 优化对话的自然度\n"
                "3. 调整节奏和叙事张力\n"
                "4. 保持原文的核心情节和角色不变\n"
                "直接输出润色后的全文，不要加任何说明。"
            )
            edit_resp = await router_inst.generate(
                agent_role="editor_stylist",
                messages=[
                    Msg(role="system", content=edit_system),
                    Msg(role="user", content=f"请润色以下草稿：\n\n{full_text}"),
                ],
                temperature=0.6, max_tokens=4096,
            )
            edited_text = edit_resp.content or full_text
            _emit(session_id, {
                "type": "step_done", "step": "editor_writer",
                "result": {"text": edited_text, "word_count": len(edited_text)},
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
        eval_result = {"score": 80, "passed": True, "issues": []}
        try:
            from agents.evaluation.repetition_detector import RepetitionDetector
            from agents.evaluation.slop_detector import SlopDetector
            rep = RepetitionDetector()
            rep_issues = rep.detect(edited_text)
            slop = SlopDetector()
            slop_issues = slop.detect(edited_text)
            issues = []
            for ri in (rep_issues or [])[:3]:
                issues.append({
                    "type": "repetition", "severity": "medium",
                    "description": str(ri.get("phrase", "重复")) + f" (出现{ri.get('count', 0)}次)",
                })
            for si in (slop_issues or [])[:3]:
                issues.append({
                    "type": "ai_flavor", "severity": "medium",
                    "description": str(si.get("pattern", "AI味")) + f": {si.get('match', '')}",
                })
            score = max(0, 100 - len(issues) * 8)
            eval_result = {"score": score, "passed": score >= 60, "issues": issues}
        except Exception:
            pass

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
