"""
/api/data — JSON-file-based CRUD for creative data.
All data stored in {repo_root}/data/{collection}/.
"""
from __future__ import annotations
import json
import time
import uuid
from pathlib import Path
from typing import Any
from fastapi import APIRouter, HTTPException, Body

from ..settings import settings

router = APIRouter(prefix="/data", tags=["data"])

def _data_dir() -> Path:
    d = settings.repo_root / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d

def _collection_dir(name: str) -> Path:
    d = _data_dir() / name
    d.mkdir(parents=True, exist_ok=True)
    return d

def _read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text("utf-8"))

def _write_json(path: Path, data: Any):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")

def _new_id() -> str:
    return f"{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"

# ─── Generic collection helpers ───

def _list_items(collection: str) -> list[dict]:
    d = _collection_dir(collection)
    items = []
    for f in sorted(d.glob("*.json")):
        try:
            items.append(json.loads(f.read_text("utf-8")))
        except Exception:
            pass
    return items

def _get_item(collection: str, item_id: str) -> dict:
    p = _collection_dir(collection) / f"{item_id}.json"
    if not p.exists():
        raise HTTPException(404, f"{collection} item not found: {item_id}")
    return json.loads(p.read_text("utf-8"))

def _save_item(collection: str, item_id: str, data: dict) -> dict:
    data["id"] = item_id
    data["updated_at"] = time.time()
    _write_json(_collection_dir(collection) / f"{item_id}.json", data)
    return data

def _delete_item(collection: str, item_id: str):
    p = _collection_dir(collection) / f"{item_id}.json"
    if p.exists():
        p.unlink()

# ═══════════════════════════════════════════
# Characters
# ═══════════════════════════════════════════
@router.get("/characters")
def list_characters():
    return {"items": _list_items("characters")}

@router.post("/characters")
def create_character(body: dict = Body(...)):
    cid = _new_id()
    body["id"] = cid
    body.setdefault("name", "新角色")
    body.setdefault("role", "配角")
    body.setdefault("personality", "")
    body.setdefault("background", "")
    body.setdefault("speech_style", "")
    body.setdefault("tags", [])
    body.setdefault("quant_params", {})
    body["created_at"] = time.time()
    return _save_item("characters", cid, body)

@router.get("/characters/{cid}")
def get_character(cid: str):
    return _get_item("characters", cid)

@router.put("/characters/{cid}")
def update_character(cid: str, body: dict = Body(...)):
    return _save_item("characters", cid, body)

@router.delete("/characters/{cid}")
def delete_character(cid: str):
    _delete_item("characters", cid)
    return {"ok": True}

# ═══════════════════════════════════════════
# World Book
# ═══════════════════════════════════════════
@router.get("/worldbook")
def list_worldbook():
    return {"items": _list_items("worldbook")}

@router.post("/worldbook")
def create_worldbook_entry(body: dict = Body(...)):
    eid = _new_id()
    body["id"] = eid
    body.setdefault("category", "力量体系")
    body.setdefault("title", "新条目")
    body.setdefault("content", "")
    body.setdefault("tags", [])
    body["created_at"] = time.time()
    return _save_item("worldbook", eid, body)

@router.get("/worldbook/{eid}")
def get_worldbook_entry(eid: str):
    return _get_item("worldbook", eid)

@router.put("/worldbook/{eid}")
def update_worldbook_entry(eid: str, body: dict = Body(...)):
    return _save_item("worldbook", eid, body)

@router.delete("/worldbook/{eid}")
def delete_worldbook_entry(eid: str):
    _delete_item("worldbook", eid)
    return {"ok": True}

# ═══════════════════════════════════════════
# Editor (volumes + chapters)
# Single-file storage: data/editor/project.json
# ═══════════════════════════════════════════
def _editor_path() -> Path:
    d = _collection_dir("editor")
    return d / "project.json"

@router.get("/editor")
def get_editor_data():
    p = _editor_path()
    if not p.exists():
        return {"volumes": []}
    return json.loads(p.read_text("utf-8"))

@router.put("/editor")
def save_editor_data(body: dict = Body(...)):
    body["saved_at"] = time.time()
    _write_json(_editor_path(), body)
    return {"ok": True, "saved_at": body["saved_at"]}

# ═══════════════════════════════════════════
# Settings
# Single-file storage: data/settings.json
# ═══════════════════════════════════════════
def _settings_path() -> Path:
    return _data_dir() / "settings.json"

@router.get("/settings")
def get_settings():
    p = _settings_path()
    if not p.exists():
        return _default_settings()
    data = json.loads(p.read_text("utf-8"))
    # Merge defaults for any missing keys
    defaults = _default_settings()
    for k, v in defaults.items():
        if k not in data:
            data[k] = v
    return data

@router.put("/settings")
def save_settings(body: dict = Body(...)):
    body["saved_at"] = time.time()
    _write_json(_settings_path(), body)
    return {"ok": True}

def _default_settings() -> dict:
    return {
        "theme": "light",
        "auto_save": True,
        "auto_save_interval": 30,
        "cost_confirm": True,
        "export_format": "txt",
        "providers": {
            "openai": {"enabled": False, "api_key_set": False},
            "anthropic": {"enabled": False, "api_key_set": False},
            "deepseek": {"enabled": False, "api_key_set": False},
            "ollama": {"enabled": False, "base_url": "http://localhost:11434"},
            "vllm": {"enabled": False, "base_url": "http://localhost:8000"},
        },
        "model_preset": "balanced",
    }
