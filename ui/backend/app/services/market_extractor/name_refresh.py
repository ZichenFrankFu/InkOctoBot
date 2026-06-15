"""人名库刷新 —— LTP NER 抽 PER 入库，按 book 去重、增量处理（spec §4）。

机制：
- NER 只对**市场数据库中按 book 去重的新增书**（novel_uid 未处理过，或开篇 content_hash
  变化）运行；rank snapshot 更新与已处理过的书一律跳过（name_extraction_state 台账）。
- 每本新书抽 PER 人名实体，**书内去重**后并入人名库，对每个去重名把 ``book_df`` +1
  （按 book 不按 snapshot）。并带上来源作品的标题/题材/平台/排名/热度做元数据。
- 触发：用户手动刷新（API），或每天一次打开基础特征提取时自动刷新（maybe_daily_refresh）。
- 后端选择与降级由 ner_backend 负责（GPU→CPU→jieba 兜底）。

人名库在**项目库**（project db），开篇章节在**爬虫库**（crawler db），两库路径分开传入。
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from . import name_library, ner_backend

logger = logging.getLogger("inkoctobot.market_extractor.name_refresh")

_OPENING_CHAPTERS = 5          # 每本书取前 N 章开篇做 NER
_DEFAULT_LIMIT = 500           # 单次刷新最多处理多少本新书（控耗时）
_DAILY_SECONDS = 24 * 3600

_refresh_lock = threading.Lock()
_refreshing = False

# 进度状态（供 UI 进度条；后台线程更新、状态接口读取）。
_progress_lock = threading.Lock()
_progress: dict = {"running": False, "total": 0, "done": 0, "names": 0,
                   "backend": "", "phase": "idle", "message": ""}


def _set_progress(**kw) -> None:
    with _progress_lock:
        _progress.update(kw)


def _bump_progress(done: int = 0, names: int = 0) -> None:
    with _progress_lock:
        _progress["done"] += done
        _progress["names"] += names


def _get_progress() -> dict:
    with _progress_lock:
        p = dict(_progress)
    p["pct"] = round(p["done"] / p["total"] * 100, 1) if p["total"] else (100.0 if not p["running"] else 0.0)
    return p


def _marker_path() -> Path:
    try:
        from ...runtime_paths import data_dir
        return data_dir() / "name_lib_last_refresh.txt"
    except Exception:
        return Path("/tmp/name_lib_last_refresh.txt")


def _read_last_refresh() -> float:
    p = _marker_path()
    try:
        return float(p.read_text("utf-8").strip())
    except Exception:
        return 0.0


def _write_last_refresh(ts: float) -> None:
    try:
        _marker_path().write_text(str(ts), "utf-8")
    except Exception:
        pass


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _processed_state(project_db: str) -> dict[str, str]:
    """novel_uid → content_hash 已处理台账。"""
    out: dict[str, str] = {}
    with sqlite3.connect(project_db) as con:
        name_library._ensure(con)
        for r in con.execute("SELECT novel_uid, content_hash FROM name_extraction_state").fetchall():
            out[str(r[0])] = str(r[1] or "")
    return out


def _fetch_new_books(
    crawler_db: str, processed: dict[str, str], *, platform: str | None, limit: int,
) -> list[dict]:
    """爬虫库里按 book 去重的新增书：novel_uid 未处理，或开篇 content_hash 变化。"""
    with sqlite3.connect(crawler_db) as con:
        con.row_factory = sqlite3.Row
        if not _table_exists(con, "first_n_chapters"):
            return []
        has_titles = _table_exists(con, "novel_titles")
        has_novels = _table_exists(con, "novels")
        title_join = (" LEFT JOIN novel_titles nt ON nt.novel_uid=fc.novel_uid AND nt.is_primary=1"
                      if has_titles else "")
        title_expr = "nt.title" if has_titles else "NULL"
        nov_join = " LEFT JOIN novels n ON n.novel_uid=fc.novel_uid" if has_novels else ""
        cat_expr = "n.main_category" if has_novels else "NULL"
        plat_expr = "n.platform" if has_novels else "NULL"
        words_expr = "n.total_words" if has_novels else "NULL"
        cond = " WHERE fc.chapter_num <= ? AND fc.chapter_content IS NOT NULL"
        params: list = [_OPENING_CHAPTERS]
        if platform and has_novels:
            cond += " AND n.platform = ?"
            params.append(platform)
        rows = con.execute(
            f"SELECT fc.novel_uid uid, fc.chapter_num cn, fc.chapter_content cc, "
            f"fc.content_hash ch, {title_expr} title, {cat_expr} cat, "
            f"{plat_expr} plat, {words_expr} words "
            f"FROM first_n_chapters fc{nov_join}{title_join}{cond} "
            f"ORDER BY fc.novel_uid, fc.chapter_num",
            params,
        ).fetchall()
        # 热度/排名（若有 rank_entries）：每本取最好排名 + 最大推荐数作热度代理。
        heat_map: dict[str, dict] = {}
        if _table_exists(con, "rank_entries"):
            for r in con.execute(
                "SELECT novel_uid uid, MIN(rank) best_rank, MAX(total_recommend) heat "
                "FROM rank_entries GROUP BY novel_uid"
            ).fetchall():
                heat_map[str(r["uid"])] = {"rank": r["best_rank"], "heat": r["heat"]}

    # 聚合到 book：拼前 N 章正文 + 用 ch1 content_hash 作书指纹。
    books: dict[str, dict] = {}
    for r in rows:
        uid = str(r["uid"])
        b = books.setdefault(uid, {
            "novel_uid": uid, "texts": [], "ch1_hash": "",
            "title": r["title"], "category": r["cat"], "platform": r["plat"],
            "words": r["words"],
        })
        b["texts"].append(str(r["cc"] or ""))
        if int(r["cn"] or 0) == 1:
            b["ch1_hash"] = str(r["ch"] or "")
    import hashlib
    new_books: list[dict] = []
    for uid, b in books.items():
        # 优先用爬虫库的 ch1 content_hash；缺失则对开篇文本取稳定指纹（跨重启一致，
        # 不用内置 hash() 以免每次进程重启都误判为新书重跑）。
        fingerprint = b["ch1_hash"] or hashlib.md5(
            "".join(b["texts"])[:2000].encode("utf-8")).hexdigest()
        if processed.get(uid, "__none__") == fingerprint:
            continue        # 已处理且开篇未变 → 跳过（含 rank snapshot 更新）
        hm = heat_map.get(uid, {})
        b["fingerprint"] = fingerprint
        b["rank"] = hm.get("rank")
        b["heat"] = hm.get("heat") if hm.get("heat") is not None else b.get("words")
        new_books.append(b)
        if len(new_books) >= limit:
            break
    return new_books


def refresh(
    project_db: str, crawler_db: str, *, platform: str | None = None,
    limit: int = _DEFAULT_LIMIT,
) -> dict:
    """对新增书跑 NER，把 PER 人名并入人名库（书内去重、book_df +1）。返回汇总。"""
    global _refreshing
    with _refresh_lock:
        if _refreshing:
            return {"status": "already_running"}
        _refreshing = True
    started = time.time()
    # 手动/每日刷新都重置后端缓存：给 LTP 一次全新加载机会（上轮降级不影响本轮）。
    ner_backend.reset_backend_cache()
    info = ner_backend.detect_ner_backend()
    name_library.seed_if_empty(project_db)   # 静态种子库始终就绪（fallback）
    try:
        # LTP 不可用 → 不抽新名（jieba 错误率高），也不把书标记为已处理（留待 LTP 可
        # 用时再抽）。静态种子人名库继续作为高频词剔名的 fallback。
        if not info.uses_ltp:
            _set_progress(running=False, total=0, done=0, names=0,
                          backend=info.backend, phase="ltp_unavailable",
                          message=info.reason)
            return {"status": "ltp_unavailable", "backend": info.backend,
                    "backend_reason": info.reason, "new_books": 0, "names_added": 0,
                    "elapsed_sec": round(time.time() - started, 1)}
        processed = _processed_state(project_db)
        new_books = _fetch_new_books(crawler_db, processed, platform=platform, limit=limit)
        _set_progress(running=True, total=len(new_books), done=0, names=0,
                      backend=info.backend, phase="running", message="")
        total_names = 0
        for b in new_books:
            try:
                per = ner_backend.extract_per_names(b["texts"])
            except Exception as e:
                logger.warning("NER failed for book %s: %s", b["novel_uid"], e)
                per = []
            cur = ner_backend.detect_ner_backend()
            if not cur.uses_ltp:
                # 运行中 LTP 加载失败被降级 → 停止处理，剩余书留待下次（绝不用 jieba）。
                _set_progress(phase="ltp_failed", message=cur.reason)
                break
            unique = {n for n in per if name_library.is_valid_name(n)}   # 书内去重
            for fn in unique:
                name_library.add_name(
                    project_db, fn, source="ltp_ner",
                    work_id=b["novel_uid"], work_title=b.get("title") or "",
                    platform=b.get("platform") or "", category=b.get("category") or "",
                    rank=b.get("rank"), heat=b.get("heat"), count_df=True,
                )
            total_names += len(unique)
            _record_state(project_db, b["novel_uid"], b["fingerprint"],
                          cur.backend, len(unique))
            _bump_progress(done=1, names=len(unique))
        _write_last_refresh(time.time())
        final = ner_backend.detect_ner_backend()
        return {
            "status": "ok",
            "backend": final.backend,
            "backend_reason": final.reason,
            "new_books": len(new_books),
            "names_added": total_names,
            "elapsed_sec": round(time.time() - started, 1),
        }
    finally:
        _set_progress(running=False)
        with _refresh_lock:
            _refreshing = False


def _record_state(project_db: str, novel_uid: str, fingerprint: str,
                  method: str, n_found: int) -> None:
    with sqlite3.connect(project_db) as con:
        name_library._ensure(con)
        con.execute(
            "INSERT INTO name_extraction_state (novel_uid, content_hash, method, "
            "names_found, processed_at) VALUES (?,?,?,?, CURRENT_TIMESTAMP) "
            "ON CONFLICT(novel_uid) DO UPDATE SET content_hash=excluded.content_hash, "
            "method=excluded.method, names_found=excluded.names_found, "
            "processed_at=CURRENT_TIMESTAMP",
            (novel_uid, fingerprint, method, n_found),
        )
        con.commit()


def refresh_in_background(project_db: str, crawler_db: str, *, platform: str | None = None) -> bool:
    """后台线程跑刷新（不阻塞请求）。已有刷新在跑则返回 False。"""
    with _refresh_lock:
        if _refreshing:
            return False

    def _runner() -> None:
        try:
            refresh(project_db, crawler_db, platform=platform)
        except Exception:
            logger.exception("background name refresh failed")

    threading.Thread(target=_runner, name="name-lib-refresh", daemon=True).start()
    return True


def maybe_daily_refresh(project_db: str, crawler_db: str, *, platform: str | None = None) -> bool:
    """每天一次：距上次刷新 ≥24h 则后台触发一次。返回是否已触发。"""
    if not crawler_db or not Path(crawler_db).exists():
        return False
    if time.time() - _read_last_refresh() < _DAILY_SECONDS:
        return False
    # 先占位，避免并发的多次请求同时各触发一个。
    _write_last_refresh(time.time())
    logger.info("daily name-library refresh kicked off")
    return refresh_in_background(project_db, crawler_db, platform=platform)


def status(project_db: str) -> dict:
    """刷新状态 + 后端信息（供 UI 展示）。"""
    last = _read_last_refresh()
    with _refresh_lock:
        running = _refreshing
    try:
        with sqlite3.connect(project_db) as con:
            name_library._ensure(con)
            processed = con.execute("SELECT COUNT(*) FROM name_extraction_state").fetchone()[0]
    except Exception:
        processed = 0
    return {
        "running": running,
        "last_refresh": (datetime.fromtimestamp(last, tz=timezone.utc).isoformat()
                         if last else None),
        "books_processed": processed,
        "backend": ner_backend.backend_status(),
        "progress": _get_progress(),
    }
