"""InkOctoBot 人名识别预训练器 —— 独立后端 + UI。

依照现有市场数据库（爬虫库）对全部小说开篇正文跑 LTP NER，预训练出一个干净的人名库
（中文/日文/西方 + 性别），供用户 review / 编辑 / 取名 / 导入导出。首次运行=全量预训练，
之后=只处理新增小说（增量）。整个 folder 自包含，可整体搬到 InkOctoBot_Crawler。

启动：python app.py [--crawler-db 路径] [--port 8765]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from core import (
    name_generator as ng,
    name_library as nl,
    name_refresh as nr,
    ner_backend as nb,
    paths,
    schema,
    wordlists as wl,
)

BASE = Path(__file__).resolve().parent
_CONFIG = paths.data_dir() / "config.json"


# ── 配置（爬虫库路径）：data/config.json，UI 可改 ──
def _load_config() -> dict:
    try:
        return json.loads(_CONFIG.read_text("utf-8"))
    except Exception:
        return {}


def _save_config(cfg: dict) -> None:
    _CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")


def crawler_db() -> str:
    """市场数据库（爬虫库，只读）地址：config > env > 空。"""
    import os
    return (_load_config().get("crawler_db")
            or os.environ.get("NAME_TRAINER_CRAWLER_DB") or "")


def name_db_path() -> str:
    """人名数据库（读写）地址：config > env > 本工具自带 data/name_library.db。
    指向 InkOctoBot 的库即可两边共用同一份人名库（schema 幂等、不动其它表）。"""
    import os
    return (_load_config().get("name_db")
            or os.environ.get("NAME_TRAINER_NAME_DB") or paths.name_db_path())


def project_db() -> str:
    p = name_db_path()
    Path(p).expanduser().parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(p) as con:
        schema.ensure_name_tables(con)
    return p


app = FastAPI(title="InkOctoBot 人名识别预训练器")


@app.get("/")
def index():
    return FileResponse(str(BASE / "static" / "index.html"))


# ── 配置 ──
@app.get("/api/config")
def get_config():
    cdb, ndb = crawler_db(), name_db_path()
    return {"crawler_db": cdb, "crawler_exists": bool(cdb and Path(cdb).expanduser().exists()),
            "name_db": ndb, "name_db_exists": Path(ndb).expanduser().exists()}


@app.post("/api/config")
def set_config(body: dict = Body(...)):
    cfg = _load_config()
    if "crawler_db" in body:
        cfg["crawler_db"] = (body.get("crawler_db") or "").strip()
    if "name_db" in body:
        cfg["name_db"] = (body.get("name_db") or "").strip()
    _save_config(cfg)
    return get_config()


# ── NER 后端状态 ──
@app.get("/api/ner-status")
def ner_status():
    return nb.backend_status()


# ── 预训练 / 增量刷新 ──
@app.post("/api/pretrain")
def pretrain(body: dict = Body(default={})):
    cdb = crawler_db()
    if not cdb or not Path(cdb).exists():
        raise HTTPException(400, "未配置可用的市场数据库（爬虫库）路径")
    platform = (body.get("platform") or "").strip() or None
    started = nr.refresh_in_background(project_db(), cdb, platform=platform)
    return {"ok": True, "started": started, "status": nr.status(project_db())}


@app.get("/api/pretrain-status")
def pretrain_status():
    return nr.status(project_db())


# ── 人名库 ──
@app.get("/api/names")
def names(q: str = Query(default=""), kind: str = Query(default=""),
          limit: int = Query(default=100, le=20000), offset: int = Query(default=0, ge=0),
          order: str = Query(default="name")):
    return nl.search_names(project_db(), q, limit=limit, offset=offset, order=order, kind=kind)


@app.get("/api/stats")
def stats():
    return nl.library_stats(project_db())


@app.post("/api/names/add")
def add_name(body: dict = Body(...)):
    fn = (body.get("full_name") or "").strip()
    if not nl.is_valid_name(fn):
        raise HTTPException(400, "full_name 需为 2-8 个汉字")
    row = nl.add_name(project_db(), fn, source="user", dedupe_fragments=False)
    return {"ok": True, "name": row}


@app.post("/api/names/edit")
def edit_name(body: dict = Body(...)):
    nid = (body.get("name_id") or "").strip()
    if not nid:
        raise HTTPException(400, "name_id required")
    kw = {}
    for k in ("full_name", "example_sentence", "name_kind", "gender"):
        if k in body:
            kw[k] = str(body[k])
    try:
        row = nl.edit_name(project_db(), nid, **kw)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "name": row}


@app.post("/api/names/remove")
def remove_name(body: dict = Body(...)):
    fn = (body.get("full_name") or "").strip()
    removed = nl.remove_name(project_db(), fn)
    if removed and body.get("blocklist", True) and nl.is_valid_name(fn):
        try:
            wl.add_word("name_blocklist", fn)
        except Exception:
            pass
    return {"ok": True, "removed": removed}


@app.post("/api/reclassify")
def reclassify():
    return {"ok": True, "updated": nl.rebuild_derived(project_db())}


@app.post("/api/clear")
def clear():
    return {"ok": True, **nl.clear_all(project_db())}


# ── 导入 / 导出 ──
@app.get("/api/export")
def export(format: str = Query(default="json")):
    records = nl.export_all(project_db())
    if format == "txt":
        body = "\n".join(r["full_name"] for r in records) + ("\n" if records else "")
        return PlainTextResponse(body, headers={
            "Content-Disposition": 'attachment; filename="name_library.txt"'})
    return PlainTextResponse(
        json.dumps(records, ensure_ascii=False, indent=1), media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="name_library.json"'})


@app.post("/api/export-to-path")
def export_to_path(body: dict = Body(...)):
    raw = (body.get("path") or "").strip()
    if not raw:
        raise HTTPException(400, "请填写导出路径")
    fmt = "txt" if (body.get("format") == "txt") else "json"
    p = Path(raw).expanduser()
    if p.is_dir() or raw.endswith(("/", "\\")) or not p.suffix:
        p = p / f"name_library.{fmt}"
    records = nl.export_all(project_db())
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "txt":
            p.write_text("\n".join(r["full_name"] for r in records) + "\n", "utf-8")
        else:
            p.write_text(json.dumps(records, ensure_ascii=False, indent=1), "utf-8")
    except OSError as e:
        raise HTTPException(400, f"写入失败：{e}")
    return {"ok": True, "path": str(p), "count": len(records)}


@app.post("/api/import")
def import_names(body: dict = Body(...)):
    content = body.get("content")
    records: list[dict] = []
    if isinstance(content, list):
        records = [r for r in content if isinstance(r, dict)]
    elif isinstance(content, str):
        txt = content.strip()
        if txt.startswith("[") or txt.startswith("{"):
            try:
                parsed = json.loads(txt)
                records = [r for r in (parsed if isinstance(parsed, list) else [parsed])
                           if isinstance(r, dict)]
            except Exception:
                records = []
        if not records:
            records = [{"full_name": s.strip()} for s in txt.splitlines()
                       if s.strip() and not s.startswith("#")]
    if not records:
        raise HTTPException(400, "未解析到任何人名（支持导出的 JSON 或每行一个全名的 txt）")
    return {"ok": True, **nl.import_records(project_db(), records)}


# ── 取名 ──
@app.post("/api/generate")
def generate(body: dict = Body(default={})):
    return ng.generate_names(
        project_db(), kind=(body.get("kind") or "chinese"),
        gender=(body.get("gender") or ""), surname=(body.get("surname") or ""),
        category=(body.get("category") or ""), count=int(body.get("count") or 20))


# ── 性别用字 / 黑名单 词表 ──
_WL_OK = {"male_name_chars", "female_name_chars", "name_blocklist", "surnames", "given_names"}


@app.get("/api/wordlist")
def wordlist(list: str = Query(...), q: str = Query(default="")):
    if list not in _WL_OK:
        raise HTTPException(400, f"unknown list: {list!r}")
    return wl.list_words(list, q or None)


@app.post("/api/wordlist/add")
def wordlist_add(body: dict = Body(...)):
    lst, word = (body.get("list") or ""), (body.get("word") or "").strip()
    if lst not in _WL_OK or not word:
        raise HTTPException(400, "list/word required")
    return {"ok": True, "added": wl.add_word(lst, word)}


@app.post("/api/wordlist/remove")
def wordlist_remove(body: dict = Body(...)):
    lst, word = (body.get("list") or ""), (body.get("word") or "").strip()
    if lst not in _WL_OK or not word:
        raise HTTPException(400, "list/word required")
    return {"ok": True, "removed": wl.remove_word(lst, word)}


app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")


def main() -> None:
    ap = argparse.ArgumentParser(description="InkOctoBot 人名识别预训练器")
    ap.add_argument("--crawler-db", default="", help="市场数据库(爬虫库) sqlite 路径")
    ap.add_argument("--name-db", default="", help="人名数据库 sqlite 路径（默认 data/name_library.db）")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    if args.crawler_db or args.name_db:
        cfg = _load_config()
        if args.crawler_db:
            cfg["crawler_db"] = args.crawler_db
        if args.name_db:
            cfg["name_db"] = args.name_db
        _save_config(cfg)
    url = f"http://{args.host}:{args.port}/"
    if not args.no_browser:
        import threading
        import webbrowser
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"\n  人名识别预训练器已启动 → {url}\n  人名库: {paths.name_db_path()}\n")
    import uvicorn
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
