"""
/api/data — CRUD endpoints for creative data.

Storage backend by collection:
  - characters / worldbook / project_memory  → SQLite (project_store)
  - projects + everything else (editor doc, chat history,
    reference_injection, knowledge_injection, calibration,
    preferences, storyline, versions)                → JSON files
    in ``{data_dir}/{collection}/``.

The SQLite-backed collections were migrated in
docs/SCHEMA_REDESIGN.md. Remaining JSON collections will be migrated
in subsequent commits — the wire shape stays identical so the
frontend never sees the cut-over.
"""
from __future__ import annotations
import json, time, uuid, os
from datetime import datetime
from pathlib import Path
from typing import Any
from fastapi import APIRouter, HTTPException, Body
from ..services import project_store
from ..services.project_paths import get_db_path
from ..settings import settings

router = APIRouter(prefix="/data", tags=["data"])


def _db() -> str:
    """Resolve the active novels.db path (honors test mode)."""
    return get_db_path()

def _data_dir() -> Path:
    d = settings.get_data_path()
    d.mkdir(parents=True, exist_ok=True); return d
def _col(name: str) -> Path:
    d = _data_dir() / name; d.mkdir(parents=True, exist_ok=True); return d
def _rj(p: Path) -> dict:
    return json.loads(p.read_text("utf-8")) if p.exists() else {}
def _wj(p: Path, d: Any):
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2), "utf-8")
def _nid() -> str:
    return f"{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"

import re as _re

def _safe_id(val: str) -> str:
    """Sanitize user-supplied IDs to prevent path traversal."""
    # Strip path separators and parent directory references
    sanitized = _re.sub(r'[/\\]', '_', val.strip())
    sanitized = sanitized.replace('..', '_')
    if not sanitized:
        sanitized = "default"
    return sanitized

def _list(c: str, *, filter_key: str = "", filter_value: str = "") -> list[dict]:
    items = []
    for f in sorted(_col(c).glob("*.json")):
        try:
            item = json.loads(f.read_text("utf-8"))
            if filter_key and filter_value and item.get(filter_key) != filter_value:
                continue
            items.append(item)
        except (json.JSONDecodeError, OSError): pass
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
    """Compute word_count and chapter_count from editor data (DB-backed)."""
    pid = proj.get("id", "default")
    try:
        ed = project_store.get_editor_doc(_db(), pid)
        total_words = 0
        total_chapters = 0
        for v in ed.get("volumes", []) or []:
            for ch in v.get("chapters", []) or []:
                total_chapters += 1
                content = ch.get("content", "")
                total_words += ch.get("word_count", 0) or (
                    len(content) - content.count(" ") - content.count("\n")
                )
        proj["word_count"] = total_words
        proj["chapter_count"] = total_chapters
    except Exception:
        pass
    return proj

# ═══ Projects (DB-backed via project_store) ═══
@router.get("/projects")
def list_projects(page: int = 0, size: int = 0):
    items = [_enrich_project(p) for p in project_store.list_projects(_db())]
    total = len(items)
    if size > 0:
        start = page * size
        items = items[start:start + size]
    return {"items": items, "total": total}

@router.post("/projects")
def create_project(body: dict = Body(...)):
    body = dict(body)
    body.setdefault("name", "新项目")
    return project_store.upsert_project(_db(), body)

@router.get("/projects/{pid}")
def get_project(pid: str):
    item = project_store.get_project(_db(), pid)
    if not item:
        raise HTTPException(404, f"not found: projects/{pid}")
    return _enrich_project(item)

@router.put("/projects/{pid}")
def update_project(pid: str, body: dict = Body(...)):
    body = dict(body)
    body["id"] = pid
    return project_store.upsert_project(_db(), body)

@router.delete("/projects/{pid}")
def delete_project(pid: str):
    project_store.delete_project(_db(), pid)
    return {"ok": True}

# ═══ Characters (DB-backed via project_store) ═══
@router.get("/characters")
def list_characters(project_id: str | None = None, page: int = 0, size: int = 0):
    items = project_store.list_characters(_db(), project_id)
    total = len(items)
    if size > 0:
        start = page * size
        items = items[start:start + size]
    return {"items": items, "total": total}

@router.post("/characters")
def create_character(body: dict = Body(...)):
    body = dict(body)
    body.setdefault("name", "新角色")
    body.setdefault("role", "配角")
    body.setdefault("project_id", "default")
    project_store.ensure_project_row(_db(), body["project_id"])
    try:
        return project_store.upsert_character(_db(), body)
    except ValueError as e:
        raise HTTPException(409 if "已存在" in str(e) else 400, detail=str(e))

@router.get("/characters/{cid}")
def get_character(cid: str):
    item = project_store.get_character(_db(), cid)
    if not item:
        raise HTTPException(404, f"not found: characters/{cid}")
    return item

@router.put("/characters/{cid}")
def update_character(cid: str, body: dict = Body(...)):
    body = dict(body)
    body["id"] = cid
    try:
        return project_store.upsert_character(_db(), body)
    except ValueError as e:
        raise HTTPException(409 if "已存在" in str(e) else 400, detail=str(e))

@router.delete("/characters/{cid}")
def delete_character(cid: str):
    project_store.delete_character(_db(), cid)
    return {"ok": True}


# ═══ World Book (DB-backed via project_store) ═══
@router.get("/worldbook")
def list_worldbook(project_id: str | None = None, page: int = 0, size: int = 0):
    items = project_store.list_worldbook(_db(), project_id)
    total = len(items)
    if size > 0:
        start = page * size
        items = items[start:start + size]
    return {"items": items, "total": total}

@router.post("/worldbook")
def create_worldbook_entry(body: dict = Body(...)):
    body = dict(body)
    body.setdefault("title", "新条目")
    body.setdefault("category", "力量体系")
    body.setdefault("project_id", "default")
    project_store.ensure_project_row(_db(), body["project_id"])
    try:
        return project_store.upsert_worldbook(_db(), body)
    except ValueError as e:
        raise HTTPException(409 if "已存在" in str(e) else 400, detail=str(e))

@router.get("/worldbook/{eid}")
def get_worldbook_entry(eid: str):
    item = project_store.get_worldbook(_db(), eid)
    if not item:
        raise HTTPException(404, f"not found: worldbook/{eid}")
    return item

@router.put("/worldbook/{eid}")
def update_worldbook_entry(eid: str, body: dict = Body(...)):
    body = dict(body)
    body["id"] = eid
    try:
        return project_store.upsert_worldbook(_db(), body)
    except ValueError as e:
        raise HTTPException(409 if "已存在" in str(e) else 400, detail=str(e))

@router.delete("/worldbook/{eid}")
def delete_worldbook_entry(eid: str):
    project_store.delete_worldbook(_db(), eid)
    return {"ok": True}

# ═══ Writing Knowledge (DB-backed via project_store; cross-project library) ═══
@router.get("/writing_knowledge")
def list_writing_knowledge(domain: str | None = None, page: int = 0, size: int = 0):
    items = project_store.list_writing_knowledge(_db(), domain)
    total = len(items)
    if size > 0:
        start = page * size
        items = items[start:start + size]
    return {"items": items, "total": total}

@router.post("/writing_knowledge")
def create_writing_knowledge(body: dict = Body(...)):
    body = dict(body)
    body.setdefault("title", "新知识条目")
    body.setdefault("domain", "其他")
    try:
        return project_store.upsert_writing_knowledge(_db(), body)
    except ValueError as e:
        raise HTTPException(409 if "已存在" in str(e) else 400, detail=str(e))

@router.get("/writing_knowledge/{kid}")
def get_writing_knowledge(kid: str):
    item = project_store.get_writing_knowledge(_db(), kid)
    if not item:
        raise HTTPException(404, f"not found: writing_knowledge/{kid}")
    return item

@router.put("/writing_knowledge/{kid}")
def update_writing_knowledge(kid: str, body: dict = Body(...)):
    body = dict(body)
    body["id"] = kid
    try:
        return project_store.upsert_writing_knowledge(_db(), body)
    except ValueError as e:
        raise HTTPException(409 if "已存在" in str(e) else 400, detail=str(e))

@router.delete("/writing_knowledge/{kid}")
def delete_writing_knowledge(kid: str):
    project_store.delete_writing_knowledge(_db(), kid)
    return {"ok": True}

# ═══ Reference Injection (DB-backed via project_store; blob scope) ═══
@router.get("/reference_injection/{project_id}")
def get_reference_injection(project_id: str):
    data = project_store.get_blob(
        _db(), project_id, "reference_injection",
        default={"project_id": project_id, "selections": {}},
    )
    data.setdefault("project_id", project_id)
    data.setdefault("selections", {})
    return data

@router.put("/reference_injection/{project_id}")
def save_reference_injection(project_id: str, body: dict = Body(...)):
    selections = body.get("selections", {})
    project_store.save_blob(_db(), project_id, "reference_injection", {
        "project_id": project_id,
        "selections": selections if isinstance(selections, dict) else {},
        "updated_at": time.time(),
    })
    return {"ok": True}

# ═══ Knowledge Injection (DB-backed via project_store; blob scope) ═══
@router.get("/knowledge_injection/{project_id}")
def get_knowledge_injection(project_id: str):
    data = project_store.get_blob(
        _db(), project_id, "knowledge_injection",
        default={"project_id": project_id, "knowledge_ids": []},
    )
    data.setdefault("project_id", project_id)
    data.setdefault("knowledge_ids", [])
    return data

@router.put("/knowledge_injection/{project_id}")
def save_knowledge_injection(project_id: str, body: dict = Body(...)):
    ids = body.get("knowledge_ids", [])
    project_store.save_blob(_db(), project_id, "knowledge_injection", {
        "project_id": project_id,
        "knowledge_ids": ids if isinstance(ids, list) else [],
        "updated_at": time.time(),
    })
    return {"ok": True}

# ═══ Project Memory (DB-backed via project_store) ═══
@router.get("/project_memory/{project_id}")
def get_project_memory(project_id: str):
    return project_store.get_project_memory(_db(), project_id)

@router.put("/project_memory/{project_id}")
def save_project_memory(project_id: str, body: dict = Body(...)):
    mems = body.get("memories", [])
    if not isinstance(mems, list):
        mems = []
    project_store.ensure_project_row(_db(), project_id)
    project_store.replace_project_memory(_db(), project_id, mems)
    return {"ok": True}

@router.post("/project_memory/{project_id}")
def add_project_memory(project_id: str, body: dict = Body(...)):
    content = (body.get("content") or "").strip()
    if not content:
        raise HTTPException(400, detail="记忆内容不能为空")
    project_store.ensure_project_row(_db(), project_id)
    try:
        return project_store.add_project_memory(
            _db(), project_id, content,
            category=body.get("category", "note"),
            source=body.get("source", "manual"),
        )
    except ValueError as e:
        raise HTTPException(400, detail=str(e))

@router.delete("/project_memory/{project_id}/{memory_id}")
def delete_project_memory(project_id: str, memory_id: str):
    project_store.delete_project_memory_entry(_db(), project_id, memory_id)
    return {"ok": True}

# ═══ Foreshadowing (legacy) — reads pending_hooks from Truth Files ═══
# The legacy data/foreshadowing/<pid>.json file is gone in v2; this
# endpoint now serves the canonical pending_hooks rows reshaped to the
# old {items:[...]} envelope so the existing 大纲 tab keeps working.
# Writes are accepted but a deprecation warning is logged — new code
# should emit HookDelta via TruthFileStore.apply_deltas.

import logging as _fs_logging
_fs_log = _fs_logging.getLogger("inkoctobot.routers.foreshadowing_legacy")

@router.get("/foreshadowing/{project_id}")
def get_foreshadowing(project_id: str):
    return {
        "project_id": project_id,
        "items": project_store.list_foreshadowing_legacy(_db(), project_id),
        "source": "pending_hooks",
    }

@router.put("/foreshadowing/{project_id}")
def save_foreshadowing(project_id: str, body: dict = Body(...)):
    """Deprecated: foreshadowing should be emitted via TruthFileStore.

    Kept as a no-op so legacy frontend save calls don't 404. The count
    of items the client tried to save is logged so we can spot whether
    anything is still hitting this path.
    """
    items = body.get("items", []) or []
    _fs_log.warning(
        "deprecated PUT /data/foreshadowing/%s ignored (items=%d) — "
        "emit HookDelta via TruthFileStore.apply_deltas instead",
        project_id, len(items),
    )
    return {"ok": True, "deprecated": True}

# ═══ Editor (DB-backed: project_blobs + chapters table) ═══
@router.get("/editor")
def get_editor_data(project_id: str = "default"):
    return project_store.get_editor_doc(_db(), project_id)

@router.put("/editor")
def save_editor_data(body: dict = Body(...)):
    body = dict(body)
    pid = body.pop("project_id", "default")
    res = project_store.save_editor_doc(_db(), pid, body)
    return {"ok": True, "saved_at": res["saved_at"]}

# ═══ Chat History (DB-backed via project_store) ═══
@router.get("/chat_history")
def get_chat_history(project_id: str = "default", scope: str = "pipeline"):
    """Load persistent chat history. scope: pipeline|character_ai|studio|outline_chat|..."""
    return {"messages": project_store.get_chat_history(_db(), project_id, scope)}

@router.put("/chat_history")
def save_chat_history(body: dict = Body(...)):
    """Save chat messages. body: {project_id, scope, messages}"""
    pid = body.get("project_id", "default")
    scope = body.get("scope", "pipeline")
    messages = body.get("messages", []) or []
    project_store.ensure_project_row(_db(), pid)
    project_store.replace_chat_history(_db(), pid, scope, messages)
    return {"ok": True, "count": len(messages)}

@router.delete("/chat_history")
def clear_chat_history(project_id: str = "default", scope: str = "pipeline"):
    project_store.clear_chat_history(_db(), project_id, scope)
    return {"ok": True}

# ═══ Version History (DB-backed via project_store; text_versions table) ═══
@router.get("/versions")
def get_versions(project_id: str = "default", chapter_id: str = ""):
    return {"versions": project_store.list_versions(
        _db(), project_id, chapter_id,
    )}

@router.post("/versions")
def save_version(body: dict = Body(...)):
    pid = body.get("project_id", "default")
    version = body.get("version") or {}
    project_store.ensure_project_row(_db(), pid)
    if not version.get("chapter_id"):
        raise HTTPException(400, detail="version.chapter_id is required")
    try:
        saved = project_store.insert_version(_db(), pid, version)
    except ValueError as e:
        raise HTTPException(400, detail=str(e))
    # Match legacy response shape; the trim-to-50 policy is enforced
    # by the FK cascade + retention logic above the API if needed.
    return {"ok": True, "version_id": saved["version_id"]}

@router.delete("/versions/{version_id}")
def delete_version(version_id: str, project_id: str = "default"):
    project_store.delete_version(_db(), version_id)
    return {"ok": True}

# ═══ Calibration (DB-backed via project_store; blob scope) ═══
@router.get("/calibration/{project_id}")
def get_calibration(project_id: str):
    return project_store.get_blob(
        _db(), project_id, "calibration",
        default={"history": [], "style_params": {}, "confirmed": False},
    )

@router.put("/calibration/{project_id}")
def save_calibration(project_id: str, body: dict = Body(...)):
    body = dict(body)
    body["project_id"] = project_id
    body["saved_at"] = time.time()
    project_store.save_blob(_db(), project_id, "calibration", body)
    return {"ok": True}


# ═══ Preferences / Activity History ═══
def _pref_path(project_id: str) -> Path:
    d = _col("preferences"); return d / f"{_safe_id(project_id)}.json"

@router.get("/preferences")
def get_preferences(project_id: str = "default"):
    """Load saved preference entries."""
    p = _pref_path(project_id)
    if not p.exists():
        return {"entries": [], "summary": "", "extracted_memories": []}
    data = json.loads(p.read_text("utf-8"))
    return {"entries": data.get("entries", []), "summary": data.get("summary", ""), "extracted_memories": data.get("extracted_memories", [])}

@router.post("/preferences/analyze")
def analyze_preferences(body: dict = Body(...)):
    """Gather user activity from chat histories and return as entries."""
    pid = _safe_id(body.get("project_id", "default"))
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
                if role != "user":
                    continue  # Only show user inputs
                action = f"用户输入 ({scope_label})"
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
                if not content.strip() or role != "user":
                    continue  # Only show user inputs
                entries.append({
                    "id": msg.get("id", str(uuid.uuid4())[:8]),
                    "timestamp": _ts_fmt(ts),
                    "action": "用户输入 (大纲助手)",
                    "detail": content[:500],
                    "scope": "outline_chat",
                    "role": role,
                })
        except Exception:
            continue

    # Filter out previously deleted entries
    p = _pref_path(pid)
    deleted_ids: set = set()
    if p.exists():
        try:
            old_data = json.loads(p.read_text("utf-8"))
            deleted_ids = set(old_data.get("deleted_ids", []))
        except Exception:
            pass
    entries = [e for e in entries if e.get("id") not in deleted_ids]

    # Sort by timestamp descending
    entries.sort(key=lambda e: e.get("timestamp", ""), reverse=True)

    # Generate summary
    user_entries = entries  # all entries are user inputs now
    summary = f"共收集到 {len(entries)} 条用户交互记录。"
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
        # Also provide mock extracted memories in test mode
        user_entries = [e for e in entries if e.get("role") == "user"]

    # Extract interaction memories from user entries
    extracted_memories = _extract_memories(user_entries, pid)

    # Persist
    _wj(_pref_path(pid), {"entries": entries, "summary": summary, "extracted_memories": extracted_memories, "deleted_ids": list(deleted_ids), "saved_at": time.time()})
    return {"entries": entries, "summary": summary, "extracted_memories": extracted_memories}

def _extract_memories(user_entries: list[dict], project_id: str) -> list[dict]:
    """Extract key creative preferences/memories from user interaction history."""
    # Load existing extracted memories to preserve user edits
    p = _pref_path(project_id)
    existing: list[dict] = []
    if p.exists():
        try:
            existing = json.loads(p.read_text("utf-8")).get("extracted_memories", [])
        except Exception:
            pass
    existing_ids = {m.get("id") for m in existing}

    # Simple heuristic extraction: look for user messages that express preferences
    preference_keywords = [
        "喜欢", "想要", "偏好", "风格", "希望", "不要", "倾向", "调整",
        "更多", "更少", "增加", "减少", "节奏", "基调", "语气", "文风",
    ]
    memories: list[dict] = list(existing)  # Keep existing ones
    for entry in user_entries:
        detail = entry.get("detail", "")
        if not detail or len(detail) < 8:
            continue
        # Check if it expresses a preference
        if any(kw in detail for kw in preference_keywords):
            mem_id = f"mem_{entry.get('id', '')}"
            if mem_id in existing_ids:
                continue  # Don't overwrite user-edited memories
            scope = entry.get("scope", "")
            source_label = {
                "studio_trending": "热点讨论",
                "studio_brainstorm": "头脑风暴",
                "studio_calibration": "风格校准",
                "studio_preferences": "偏好记忆",
                "outline_chat": "大纲助手",
            }.get(scope, scope)
            memories.append({
                "id": mem_id,
                "content": detail[:300],
                "source": source_label,
                "timestamp": entry.get("timestamp", ""),
            })
    # Deduplicate and limit
    seen = set()
    unique = []
    for m in memories:
        if m["id"] not in seen:
            seen.add(m["id"])
            unique.append(m)
    return unique[:50]


@router.delete("/preferences/memory/{memory_id}")
def delete_extracted_memory(memory_id: str, project_id: str = "default"):
    """Delete a single extracted memory."""
    p = _pref_path(project_id)
    if not p.exists():
        return {"ok": False, "error": "not found"}
    data = json.loads(p.read_text("utf-8"))
    memories = data.get("extracted_memories", [])
    memories = [m for m in memories if m.get("id") != memory_id]
    data["extracted_memories"] = memories
    _wj(p, data)
    return {"ok": True, "remaining": len(memories)}


@router.put("/preferences/memory/{memory_id}")
def update_extracted_memory(memory_id: str, body: dict = Body(...)):
    """Update a single extracted memory's content."""
    project_id = body.get("project_id", "default")
    new_content = body.get("content", "")
    p = _pref_path(project_id)
    if not p.exists():
        return {"ok": False, "error": "not found"}
    data = json.loads(p.read_text("utf-8"))
    memories = data.get("extracted_memories", [])
    for m in memories:
        if m.get("id") == memory_id:
            m["content"] = new_content
            break
    data["extracted_memories"] = memories
    _wj(p, data)
    return {"ok": True}


@router.delete("/preferences/{entry_id}")
def delete_preference_entry(entry_id: str, project_id: str = "default"):
    p = _pref_path(project_id)
    if not p.exists():
        return {"ok": False, "error": "not found"}
    data = json.loads(p.read_text("utf-8"))
    entries = data.get("entries", [])
    # Find the entry to archive before removing
    removed = [e for e in entries if e.get("id") == entry_id]
    entries = [e for e in entries if e.get("id") != entry_id]
    data["entries"] = entries
    # Track deleted IDs so re-analyze doesn't bring them back
    deleted_ids = set(data.get("deleted_ids", []))
    deleted_ids.add(entry_id)
    data["deleted_ids"] = list(deleted_ids)
    # Archive the full entry data (soft delete) so it's preserved but not used by editAnalyzer
    deleted_entries = data.get("deleted_entries", [])
    for e in removed:
        e["deleted_at"] = datetime.now().isoformat()
        deleted_entries.append(e)
    data["deleted_entries"] = deleted_entries
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
        return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(ts)


# ═══ Storyline (DB-backed via project_store) ═══
@router.get("/storyline")
def get_storyline(project_id: str = "default"):
    return project_store.get_storyline(_db(), project_id)

@router.put("/storyline")
def save_storyline(body: dict = Body(...)):
    body = dict(body)
    pid = body.pop("project_id", "default")
    project_store.ensure_project_row(_db(), pid)
    project_store.replace_storyline(
        _db(), pid,
        body.get("nodes") or [],
        body.get("edges") or [],
    )
    return {"ok": True}

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