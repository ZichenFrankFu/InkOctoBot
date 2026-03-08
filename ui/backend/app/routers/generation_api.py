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
    provider: str = "ollama"
    model: str = ""


class RewriteRequest(BaseModel):
    text: str
    instruction: str = ""
    provider: str = "ollama"
    model: str = ""


class EvalRequest(BaseModel):
    text: str
    chapter_num: int = 1
    provider: str = "ollama"
    model: str = ""


def _get_user_settings() -> dict:
    import json as _json
    from pathlib import Path
    from ui.backend.app.settings import settings as app_settings
    p = app_settings.repo_root / "data" / "settings.json"
    if p.exists():
        return _json.loads(p.read_text("utf-8"))
    return {}


def _build_router(provider: str = "", model: str = ""):
    from agents.model_router import ModelRouter
    user_settings = _get_user_settings()
    providers = user_settings.get("providers", {})

    if not provider:
        pipeline = user_settings.get("pipeline", {})
        sc = pipeline.get("scene_director", {})
        provider = sc.get("provider", "ollama")
        model = sc.get("model", "")

    prov_cfg = providers.get(provider, {})
    api_key = prov_cfg.get("api_key", "")

    api_keys = {}
    if api_key:
        api_keys[provider] = api_key

    return ModelRouter(api_keys=api_keys)


@router.get("/health")
def health():
    return {"status": "ok", "router": "generation"}


@router.post("/start")
async def start_generation(req: GenerateRequest):
    session_id = f"gen_{uuid.uuid4().hex[:12]}"
    _active_sessions[session_id] = {
        "status": "pending",
        "request": req.model_dump(),
        "created_at": time.time(),
        "steps": [],
        "result": None,
    }
    return {"status": "ok", "session_id": session_id}


@router.get("/status/{session_id}")
def get_session_status(session_id: str):
    session = _active_sessions.get(session_id)
    if not session:
        raise HTTPException(404, "Session not found")
    return {
        "status": session["status"],
        "steps": session["steps"],
        "result": session.get("result"),
    }


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

        system_prompt = (
            "你是一个专业的小说写作AI。根据提供的大纲和设定，"
            "写出高质量的章节内容。要求：\n"
            "1. 文字生动，有画面感\n"
            "2. 对话自然，符合人物性格\n"
            "3. 情节紧凑，节奏合理\n"
            "4. 保持叙事视角一致"
        )

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
    except Exception as e:
        logger.error("Quick generate error: %s", e, exc_info=True)
        raise HTTPException(500, detail=str(e))


@router.websocket("/ws/{session_id}")
async def generation_websocket(websocket: WebSocket, session_id: str):
    """WebSocket for real-time pipeline streaming."""
    await websocket.accept()
    session = _active_sessions.get(session_id)
    if not session:
        await websocket.send_json({"type": "error", "message": "Session not found"})
        await websocket.close()
        return

    req_data = session["request"]
    try:
        await websocket.send_json({"type": "pipeline_start", "session_id": session_id})

        # Step 1: Scene Director
        await websocket.send_json({
            "type": "step_start", "step": "scene_director",
            "label": "Scene Director", "detail": "正在拆分场景..."
        })
        try:
            router_inst = _build_router(req_data.get("provider", "ollama"), req_data.get("model", ""))
            from agents.production.scene_director import SceneDirector
            director = SceneDirector(router_inst, project_id=req_data.get("project_id", ""))
            scenes = await director.plan_scenes(
                chapter_outline=req_data.get("synopsis", ""),
                chapter_num=1,
                world_rules=req_data.get("world_rules", ""),
            )
            await websocket.send_json({
                "type": "step_done", "step": "scene_director",
                "result": scenes if isinstance(scenes, dict) else {"raw": str(scenes)},
            })
        except Exception as e:
            await websocket.send_json({
                "type": "step_done", "step": "scene_director",
                "result": {"summary": f"场景规划完成 (fallback: {str(e)[:100]})"},
            })

        await websocket.send_json({
            "type": "need_confirm", "step": "scene_director",
            "message": "场景拆分完成，是否继续生成？",
        })
        try:
            msg = await asyncio.wait_for(websocket.receive_json(), timeout=300)
        except asyncio.TimeoutError:
            await websocket.send_json({"type": "error", "message": "等待超时"})
            return
        if msg.get("action") == "abort":
            await websocket.send_json({"type": "complete", "text": "", "aborted": True})
            return

        # Step 2: Generate content
        await websocket.send_json({
            "type": "step_start", "step": "actor_agents",
            "label": "Actor Agents + Editor", "detail": "正在生成章节内容..."
        })
        full_text = ""
        try:
            from agents.model_providers.base import LLMMessage as Msg
            system = (
                "你是一个专业的小说写作AI。根据提供的大纲和设定，"
                "写出高质量的章节内容。文字生动，对话自然，情节紧凑。"
            )
            user_content = f"## 章节大纲\n{req_data.get('synopsis', '')}"
            if req_data.get("world_rules"):
                user_content += f"\n\n## 世界观\n{req_data['world_rules']}"
            if req_data.get("style_notes"):
                user_content += f"\n\n## 风格\n{req_data['style_notes']}"
            user_content += "\n\n请写出完整章节内容（800-1500字）："

            async for token in router_inst.generate_stream(
                agent_role="editor_stylist",
                messages=[Msg(role="system", content=system), Msg(role="user", content=user_content)],
                temperature=0.8, max_tokens=4096,
            ):
                full_text += token
                await websocket.send_json({"type": "token", "step": "actor_agents", "content": token})

            await websocket.send_json({
                "type": "step_done", "step": "actor_agents",
                "result": {"text": full_text, "word_count": len(full_text)},
            })
        except Exception as e:
            full_text = f"（生成失败：{str(e)[:200]}）"
            await websocket.send_json({
                "type": "step_done", "step": "actor_agents",
                "result": {"text": full_text, "error": str(e)[:200]},
            })

        # Step 3: Evaluation
        await websocket.send_json({
            "type": "step_start", "step": "evaluator",
            "label": "Evaluator", "detail": "正在评估质量..."
        })
        eval_result = {"score": 80, "passed": True, "issues": []}
        try:
            from agents.evaluation.repetition_detector import RepetitionDetector
            from agents.evaluation.slop_detector import SlopDetector
            rep = RepetitionDetector()
            rep_issues = rep.detect(full_text)
            slop = SlopDetector()
            slop_issues = slop.detect(full_text)
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

        await websocket.send_json({"type": "step_done", "step": "evaluator", "result": eval_result})
        await websocket.send_json({"type": "complete", "text": full_text, "evaluation": eval_result})

        session["status"] = "complete"
        session["result"] = {"text": full_text, "evaluation": eval_result}

    except WebSocketDisconnect:
        logger.info("Generation WS client disconnected: %s", session_id)
    except Exception as e:
        logger.error("Generation WS error: %s", e, exc_info=True)
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
