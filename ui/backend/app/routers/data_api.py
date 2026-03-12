"""
/api/data — JSON-file-based CRUD for creative data.
All data stored in {repo_root}/data/{collection}/.
"""
from __future__ import annotations
import json, time, uuid, os
from pathlib import Path
from typing import Any
from fastapi import APIRouter, HTTPException, Body
from ..settings import settings

router = APIRouter(prefix="/data", tags=["data"])

def _data_dir() -> Path:
    d = settings.data_dir if settings.data_dir else settings.repo_root / "data"
    d.mkdir(parents=True, exist_ok=True); return d
def _col(name: str) -> Path:
    d = _data_dir() / name; d.mkdir(parents=True, exist_ok=True); return d
def _rj(p: Path) -> dict:
    return json.loads(p.read_text("utf-8")) if p.exists() else {}
def _wj(p: Path, d: Any):
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), "utf-8")
def _nid() -> str:
    return f"{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"

def _list(c: str) -> list[dict]:
    items = []
    for f in sorted(_col(c).glob("*.json")):
        try: items.append(json.loads(f.read_text("utf-8")))
        except: pass
    return items

def _get(c: str, id: str) -> dict:
    p = _col(c) / f"{id}.json"
    if not p.exists(): raise HTTPException(404, f"not found: {c}/{id}")
    return json.loads(p.read_text("utf-8"))

def _save(c: str, id: str, d: dict) -> dict:
    d["id"] = id; d["updated_at"] = time.time()
    _wj(_col(c) / f"{id}.json", d); return d

def _del(c: str, id: str):
    p = _col(c) / f"{id}.json"
    if p.exists(): p.unlink()

# ═══ Projects ═══
def _enrich_project(proj: dict) -> dict:
    """Compute word_count and chapter_count from editor data."""
    pid = proj.get("id", "default")
    ep = _col("editor") / f"{pid}.json"
    if ep.exists():
        try:
            ed = json.loads(ep.read_text("utf-8"))
            total_words = 0
            total_chapters = 0
            for v in ed.get("volumes", []):
                for ch in v.get("chapters", []):
                    total_chapters += 1
                    content = ch.get("content", "")
                    total_words += ch.get("word_count", 0) or len(content.replace(" ", "").replace("\n", ""))
            proj["word_count"] = total_words
            proj["chapter_count"] = total_chapters
        except Exception:
            pass
    return proj

@router.get("/projects")
def list_projects():
    return {"items": [_enrich_project(p) for p in _list("projects")]}
@router.post("/projects")
def create_project(body: dict = Body(...)):
    pid = _nid()
    body.update({"id": pid, "name": body.get("name", "新项目"), "genre": body.get("genre", ""), "description": body.get("description", ""), "created_at": time.time()})
    return _save("projects", pid, body)
@router.get("/projects/{pid}")
def get_project(pid: str): return _get("projects", pid)
@router.put("/projects/{pid}")
def update_project(pid: str, body: dict = Body(...)): return _save("projects", pid, body)
@router.delete("/projects/{pid}")
def delete_project(pid: str): _del("projects", pid); return {"ok": True}

# ═══ Characters ═══
@router.get("/characters")
def list_characters(project_id: str | None = None):
    items = _list("characters")
    if project_id: items = [i for i in items if i.get("project_id") == project_id]
    return {"items": items}
@router.post("/characters")
def create_character(body: dict = Body(...)):
    cid = _nid()
    defaults = {"id": cid, "name": "新角色", "role": "配角",
        "project_id": "", "personality": "", "background": "", "speech_style": "",
        "tags": [], "layer_b": {}, "relationships": [], "created_at": time.time()}
    for k, v in defaults.items():
        body.setdefault(k, v)
    return _save("characters", cid, body)
@router.get("/characters/{cid}")
def get_character(cid: str): return _get("characters", cid)
@router.put("/characters/{cid}")
def update_character(cid: str, body: dict = Body(...)): return _save("characters", cid, body)
@router.delete("/characters/{cid}")
def delete_character(cid: str): _del("characters", cid); return {"ok": True}

# ═══ World Book ═══
@router.get("/worldbook")
def list_worldbook(project_id: str | None = None):
    items = _list("worldbook")
    if project_id: items = [i for i in items if i.get("project_id") == project_id]
    return {"items": items}
@router.post("/worldbook")
def create_worldbook_entry(body: dict = Body(...)):
    eid = _nid()
    body.update({"id": eid, "category": body.get("category", "力量体系"), "title": body.get("title", "新条目"),
        "content": "", "tags": [], "project_id": body.get("project_id", ""), "created_at": time.time()})
    return _save("worldbook", eid, body)
@router.get("/worldbook/{eid}")
def get_worldbook_entry(eid: str): return _get("worldbook", eid)
@router.put("/worldbook/{eid}")
def update_worldbook_entry(eid: str, body: dict = Body(...)): return _save("worldbook", eid, body)
@router.delete("/worldbook/{eid}")
def delete_worldbook_entry(eid: str): _del("worldbook", eid); return {"ok": True}

# ═══ Editor ═══
def _editor_path(project_id: str = "default") -> Path:
    d = _col("editor"); return d / f"{project_id}.json"
@router.get("/editor")
def get_editor_data(project_id: str = "default"):
    p = _editor_path(project_id)
    return json.loads(p.read_text("utf-8")) if p.exists() else {"volumes": []}
@router.put("/editor")
def save_editor_data(body: dict = Body(...)):
    pid = body.pop("project_id", "default")
    body["saved_at"] = time.time()
    _wj(_editor_path(pid), body); return {"ok": True, "saved_at": body["saved_at"]}

# ═══ Chat History ═══
def _chat_path(project_id: str, scope: str) -> Path:
    d = _col("chat_history"); return d / f"{project_id}_{scope}.json"

@router.get("/chat_history")
def get_chat_history(project_id: str = "default", scope: str = "pipeline"):
    """Load persistent chat history. scope: pipeline|character_ai|studio"""
    p = _chat_path(project_id, scope)
    if not p.exists():
        return {"messages": []}
    data = json.loads(p.read_text("utf-8"))
    return {"messages": data.get("messages", [])}

@router.put("/chat_history")
def save_chat_history(body: dict = Body(...)):
    """Save chat messages. body: {project_id, scope, messages}"""
    pid = body.get("project_id", "default")
    scope = body.get("scope", "pipeline")
    messages = body.get("messages", [])
    _wj(_chat_path(pid, scope), {
        "project_id": pid,
        "scope": scope,
        "messages": messages,
        "saved_at": time.time(),
    })
    return {"ok": True, "count": len(messages)}

@router.delete("/chat_history")
def clear_chat_history(project_id: str = "default", scope: str = "pipeline"):
    p = _chat_path(project_id, scope)
    if p.exists():
        p.unlink()
    return {"ok": True}

# ═══ Calibration ═══
def _calibration_path(project_id: str) -> Path:
    d = _col("calibration"); return d / f"{project_id}.json"

@router.get("/calibration/{project_id}")
def get_calibration(project_id: str):
    p = _calibration_path(project_id)
    if not p.exists():
        return {"history": [], "style_params": {}, "confirmed": False}
    return json.loads(p.read_text("utf-8"))

@router.put("/calibration/{project_id}")
def save_calibration(project_id: str, body: dict = Body(...)):
    body["project_id"] = project_id
    body["saved_at"] = time.time()
    _wj(_calibration_path(project_id), body)
    return {"ok": True}


# ═══ Storyline ═══
def _storyline_path(project_id: str = "default") -> Path:
    d = _col("storylines"); return d / f"{project_id}.json"
@router.get("/storyline")
def get_storyline(project_id: str = "default"):
    p = _storyline_path(project_id)
    return json.loads(p.read_text("utf-8")) if p.exists() else {"nodes": [], "edges": []}
@router.put("/storyline")
def save_storyline(body: dict = Body(...)):
    pid = body.pop("project_id", "default")
    body["saved_at"] = time.time()
    _wj(_storyline_path(pid), body); return {"ok": True}

# ═══ Settings ═══
def _settings_path() -> Path: return _data_dir() / "settings.json"
@router.get("/settings")
def get_settings():
    p = _settings_path()
    data = json.loads(p.read_text("utf-8")) if p.exists() else {}
    defaults = _default_settings()
    for k, v in defaults.items():
        if k not in data:
            data[k] = v
    # Deep-merge providers so new ones (e.g. gemini) always appear
    for pname, pdef in defaults.get("providers", {}).items():
        if pname not in data.get("providers", {}):
            data.setdefault("providers", {})[pname] = pdef
    # Deep-merge pipeline roles
    for rname, rdef in defaults.get("pipeline", {}).items():
        if rname not in data.get("pipeline", {}):
            data.setdefault("pipeline", {})[rname] = rdef
    # Remove deprecated providers
    providers = data.get("providers", {})
    for dep in _DEPRECATED_PROVIDERS:
        providers.pop(dep, None)
    return data
@router.put("/settings")
def save_settings(body: dict = Body(...)):
    # Strip deprecated providers before saving
    providers = body.get("providers", {})
    for dep in _DEPRECATED_PROVIDERS:
        providers.pop(dep, None)
    body["saved_at"] = time.time(); _wj(_settings_path(), body); return {"ok": True}

# ═══ Local Models ═══
@router.get("/local_models")
def list_local_models():
    mdir = settings.repo_root / "models"
    if not mdir.exists(): return {"models": []}
    models = []
    for f in sorted(mdir.iterdir()):
        if f.is_file() and f.suffix in (".gguf", ".bin", ".safetensors", ".pt"):
            models.append({"name": f.stem, "file": f.name, "size_mb": round(f.stat().st_size / 1048576, 1)})
        elif f.is_dir():
            total = sum(ff.stat().st_size for ff in f.rglob("*") if ff.is_file())
            models.append({"name": f.name, "file": f.name, "size_mb": round(total / 1048576, 1), "is_dir": True})
    return {"models": models}

_DEPRECATED_PROVIDERS = {"vllm", "local"}

def _default_settings() -> dict:
    return {
        "theme": "dark", "auto_save": True, "auto_save_interval": 30,
        "cost_confirm": True, "export_format": "txt",
        "providers": {
            "openai": {"enabled": False, "api_key": "", "models": ["gpt-4o", "gpt-4o-mini"]},
            "anthropic": {"enabled": False, "api_key": "", "models": ["claude-sonnet-4-5-20250929", "claude-haiku-4-5-20251001"]},
            "deepseek": {"enabled": False, "api_key": "", "models": ["deepseek-chat", "deepseek-reasoner"]},
            "gemini": {"enabled": False, "api_key": "", "models": ["gemini-2.0-flash", "gemini-2.5-pro-preview-06-05"]},
            "ollama": {"enabled": True, "base_url": "http://localhost:11434", "models": []},
            "volcengine": {"enabled": False, "api_key": "", "base_url": "https://ark.cn-beijing.volces.com/api/v3", "models": ["doubao-pro-32k", "doubao-lite-32k"]},
            "baidu_qianfan": {"enabled": False, "api_key": "", "models": ["ernie-4.0-8k", "ernie-3.5-8k"]},
            "aliyun_bailian": {"enabled": False, "api_key": "", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "models": ["qwen-max", "qwen-plus", "qwen-turbo"]},
            "grok": {"enabled": False, "api_key": "", "models": ["grok-2", "grok-2-mini"]},
        },
        "pipeline": {
            "scene_planner": {"provider": "ollama", "model": "", "compare_models": []},
            "scene_director": {"provider": "ollama", "model": "", "compare_models": []},
            "actor_default": {"provider": "ollama", "model": "", "compare_models": []},
            "actor_protagonist": {"provider": "ollama", "model": "", "compare_models": []},
            "editor_stylist": {"provider": "ollama", "model": "", "compare_models": []},
            "editor_agent": {"provider": "ollama", "model": "", "compare_models": []},
            "evaluator": {"provider": "ollama", "model": "", "compare_models": []},
            "analyzer": {"provider": "ollama", "model": "", "compare_models": []},
        },
    }