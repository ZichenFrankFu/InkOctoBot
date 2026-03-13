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

# ═══ Version History ═══
def _versions_path(project_id: str) -> Path:
    d = _col("versions"); return d / f"{project_id}.json"

@router.get("/versions")
def get_versions(project_id: str = "default", chapter_id: str = ""):
    p = _versions_path(project_id)
    if not p.exists():
        return {"versions": []}
    data = json.loads(p.read_text("utf-8"))
    versions = data.get("versions", [])
    if chapter_id:
        versions = [v for v in versions if v.get("chapter_id") == chapter_id]
    return {"versions": versions}

@router.post("/versions")
def save_version(body: dict = Body(...)):
    pid = body.get("project_id", "default")
    version = body.get("version", {})
    p = _versions_path(pid)
    data = json.loads(p.read_text("utf-8")) if p.exists() else {"versions": []}
    versions = data.get("versions", [])
    versions.append(version)
    # Keep last 50 versions per chapter
    by_ch: dict[str, list] = {}
    for v in versions:
        ch = v.get("chapter_id", "")
        by_ch.setdefault(ch, []).append(v)
    trimmed = []
    for ch_versions in by_ch.values():
        trimmed.extend(ch_versions[-50:])
    _wj(p, {"versions": trimmed, "saved_at": time.time()})
    return {"ok": True, "count": len(trimmed)}

@router.delete("/versions/{version_id}")
def delete_version(version_id: str, project_id: str = "default"):
    p = _versions_path(project_id)
    if not p.exists():
        return {"ok": False, "error": "not found"}
    data = json.loads(p.read_text("utf-8"))
    versions = data.get("versions", [])
    original_count = len(versions)
    versions = [v for v in versions if v.get("version_id") != version_id]
    if len(versions) == original_count:
        return {"ok": False, "error": "version not found"}
    _wj(p, {"versions": versions, "saved_at": time.time()})
    return {"ok": True, "remaining": len(versions)}

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


# ═══ Preferences / Activity History ═══
def _pref_path(project_id: str) -> Path:
    d = _col("preferences"); return d / f"{project_id}.json"

@router.get("/preferences")
def get_preferences(project_id: str = "default"):
    """Load saved preference entries."""
    p = _pref_path(project_id)
    if not p.exists():
        return {"entries": [], "summary": ""}
    data = json.loads(p.read_text("utf-8"))
    return {"entries": data.get("entries", []), "summary": data.get("summary", "")}

@router.post("/preferences/analyze")
def analyze_preferences(body: dict = Body(...)):
    """Gather user activity from chat histories and return as entries."""
    pid = body.get("project_id", "default")
    chat_dir = _col("chat_history")
    entries: list[dict] = []

    # Scan all chat history files for this project
    for fp in sorted(chat_dir.glob(f"{pid}_*.json")):
        try:
            data = json.loads(fp.read_text("utf-8"))
            scope = data.get("scope", fp.stem.replace(f"{pid}_", ""))
            scope_label = {
                "studio_trending": "热点讨论",
                "studio_brainstorm": "头脑风暴",
                "studio_calibration": "风格校准",
                "studio_preferences": "偏好记忆",
                "pipeline": "Pipeline对话",
            }.get(scope, scope)
            for msg in data.get("messages", []):
                role = msg.get("role", "")
                content = msg.get("content", "")
                ts = msg.get("timestamp", 0)
                if not content or not content.strip():
                    continue
                if role == "user":
                    action = f"用户输入 ({scope_label})"
                elif role == "assistant":
                    action = f"AI回复 ({scope_label})"
                else:
                    continue
                entries.append({
                    "id": msg.get("id", str(uuid.uuid4())[:8]),
                    "timestamp": _ts_fmt(ts),
                    "action": action,
                    "detail": content[:500],
                    "scope": scope,
                    "role": role,
                })
        except Exception:
            continue

    # Also include outline chats (per chapter)
    for fp in sorted(chat_dir.glob(f"{pid}_outline_chat_*.json")):
        try:
            data = json.loads(fp.read_text("utf-8"))
            for msg in data.get("messages", []):
                role = msg.get("role", "")
                content = msg.get("content", "")
                ts = msg.get("timestamp", 0)
                if not content.strip():
                    continue
                entries.append({
                    "id": msg.get("id", str(uuid.uuid4())[:8]),
                    "timestamp": _ts_fmt(ts),
                    "action": f"{'用户输入' if role == 'user' else 'AI回复'} (大纲助手)",
                    "detail": content[:500],
                    "scope": "outline_chat",
                    "role": role,
                })
        except Exception:
            continue

    # Sort by timestamp descending
    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)

    # Generate summary
    user_entries = [e for e in entries if e.get("role") == "user"]
    summary = f"共收集到 {len(entries)} 条交互记录（其中用户输入 {len(user_entries)} 条）。"
    if user_entries:
        scopes = set(e.get("scope", "") for e in user_entries)
        summary += f" 涉及 {len(scopes)} 个对话场景。"

    # If no entries found (e.g. test mode), provide mock data
    if not entries:
        is_test = os.environ.get("WN_TEST_MODE") == "1"
        if is_test:
            entries = [
                {"id": "mock_1", "timestamp": "2026-03-13 10:00", "action": "用户输入 (头脑风暴)", "detail": "帮我构思一个玄幻小说的核心设定和卖点", "scope": "studio_brainstorm", "role": "user"},
                {"id": "mock_2", "timestamp": "2026-03-13 10:01", "action": "AI回复 (头脑风暴)", "detail": "关于玄幻小说的核心设定，我建议从以下几个方向思考：1. 独特的修炼体系...", "scope": "studio_brainstorm", "role": "assistant"},
                {"id": "mock_3", "timestamp": "2026-03-13 10:05", "action": "用户输入 (头脑风暴)", "detail": "帮我设计三个有辨识度的配角", "scope": "studio_brainstorm", "role": "user"},
                {"id": "mock_4", "timestamp": "2026-03-13 10:06", "action": "AI回复 (头脑风暴)", "detail": "好的，以下是三个各具特色的配角设计：\n1. 柳无痕——温文尔雅的毒师...", "scope": "studio_brainstorm", "role": "assistant"},
                {"id": "mock_5", "timestamp": "2026-03-13 09:50", "action": "用户输入 (风格校准)", "detail": "我想要偏向轻松幽默的文风", "scope": "studio_calibration", "role": "user"},
                {"id": "mock_6", "timestamp": "2026-03-13 09:30", "action": "用户输入 (热点讨论)", "detail": "玄幻题材目前市场竞争大吗？", "scope": "studio_trending", "role": "user"},
                {"id": "mock_7", "timestamp": "2026-03-13 09:31", "action": "AI回复 (热点讨论)", "detail": "玄幻题材确实竞争激烈，但仍有细分赛道机会...", "scope": "studio_trending", "role": "assistant"},
                {"id": "mock_8", "timestamp": "2026-03-13 09:20", "action": "用户输入 (大纲助手)", "detail": "根据这一章的定位，帮我生成详细的章节大纲", "scope": "outline_chat", "role": "user"},
            ]
            summary = "（测试模式）共收集到 8 条模拟交互记录（其中用户输入 5 条）。涉及 4 个对话场景。"

    # Persist
    _wj(_pref_path(pid), {"entries": entries, "summary": summary, "saved_at": time.time()})
    return {"entries": entries, "summary": summary}

@router.delete("/preferences/{entry_id}")
def delete_preference_entry(entry_id: str, project_id: str = "default"):
    p = _pref_path(project_id)
    if not p.exists():
        return {"ok": False, "error": "not found"}
    data = json.loads(p.read_text("utf-8"))
    entries = data.get("entries", [])
    entries = [e for e in entries if e.get("id") != entry_id]
    data["entries"] = entries
    _wj(p, data)
    return {"ok": True, "remaining": len(entries)}

def _ts_fmt(ts) -> str:
    """Format a timestamp (ms or s) to readable string."""
    if not ts:
        return ""
    try:
        t = float(ts)
        if t > 1e12:  # milliseconds
            t /= 1000
        import datetime
        return datetime.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)


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
        "theme": "dark", "auto_save": True, "auto_save_interval": 30, "max_backup_versions": 10,
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
            "editor_writer": {"provider": "ollama", "model": "", "compare_models": []},
            "editor_agent": {"provider": "ollama", "model": "", "compare_models": []},
            "evaluator": {"provider": "ollama", "model": "", "compare_models": []},
            "analyzer": {"provider": "ollama", "model": "", "compare_models": []},
        },
    }