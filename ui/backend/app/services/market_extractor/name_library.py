"""人名库 —— 全名为权威记录，姓/名为可重算派生字段（spec 语言学文本特征 §4/§5）。

数据结构（spec §5）：
- 以**全名**为权威记录（唯一键）。
- 姓氏、名字字符为**派生字段**：按姓氏表 + 复姓识别从全名重算，可随姓氏表更新重建。
- 对**昵称 / 复姓 / 单名**等非标准名打标记（``is_nonstandard`` / ``is_compound_surname``
  / ``is_single_given``），以免污染后续取名规律统计。
- 每条附元数据：来源作品、来源作品热度/排名、出现的去重书数 ``book_df``
  （按 book 去重、不按 snapshot 统计）—— 管理界面用 DF 作可信度信号。

入库来源：种子库（seed）/ LTP NER（ltp_ner）/ jieba nr（jieba_nr）/ 用户（user）。
NER 仅对按 book 去重的新增书运行（见 name_refresh.py），已处理书用 name_extraction_state
跳过。
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger("inkoctobot.market_extractor.name_library")

_SEED_FILE = Path(__file__).resolve().parent / "resources" / "name_library" / "seed_names.txt"
_CJK_NAME = re.compile(r"^[一-鿿]{2,8}$")


def _surnames() -> frozenset[str]:
    try:
        from . import wordlists as _wl
        return _wl.load_surnames()
    except Exception:
        return frozenset()


# ─────────── 全名 → 派生字段 ───────────


def derive_name_parts(full_name: str, surnames: frozenset[str] | None = None) -> dict[str, Any]:
    """从全名重算姓/名 + 标准性标记。姓氏表更新后可对全库重跑此函数重建派生字段。"""
    surnames = surnames if surnames is not None else _surnames()
    fn = (full_name or "").strip()
    length = len(fn)
    surname = ""
    kind = "unknown"
    # 复姓优先（2-3 字），再单字姓。
    for k in (3, 2):
        if length > k and fn[:k] in surnames:
            surname, kind = fn[:k], "compound"
            break
    if not surname and length >= 2 and fn[:1] in surnames:
        surname, kind = fn[:1], "single"
    given = fn[len(surname):] if surname else fn

    is_compound = kind == "compound"
    is_single_given = bool(surname) and len(given) == 1
    reasons: list[str] = []
    if kind == "unknown":
        reasons.append("无法识别姓氏")
    if length == 2 and fn[0] == fn[1]:
        reasons.append("叠字昵称")
    if length < 2 or length > 4:
        reasons.append("名长异常")
    if surname and not given:
        reasons.append("缺名")
    is_nonstandard = bool(reasons)
    return {
        "full_name": fn,
        "surname": surname,
        "given_name": given,
        "surname_kind": kind,
        "name_length": length,
        "is_compound_surname": int(is_compound),
        "is_single_given": int(is_single_given),
        "is_nonstandard": int(is_nonstandard),
        "nonstandard_reason": "、".join(reasons),
    }


def is_valid_name(full_name: str) -> bool:
    return bool(_CJK_NAME.match((full_name or "").strip()))


# ─────────── 进程内缓存（剔名 / jieba userdict 用） ───────────

_cache_lock = threading.Lock()
_name_cache: dict[str, tuple[frozenset[str], frozenset[str]]] = {}


def cached_name_sets(db_path: str) -> tuple[frozenset[str], frozenset[str]]:
    """(全名集合, 名字片段集合) —— 供高频词剔名 + jieba userdict 注入，按 db 缓存。"""
    with _cache_lock:
        hit = _name_cache.get(db_path)
        if hit is not None:
            return hit
    full: set[str] = set()
    given: set[str] = set()
    try:
        with sqlite3.connect(db_path) as con:
            for r in con.execute(
                "SELECT full_name, given_name FROM person_name_library"
            ).fetchall():
                if r[0]:
                    full.add(r[0])
                if r[1] and len(r[1]) >= 2:
                    given.add(r[1])
    except sqlite3.OperationalError:
        pass    # 表还没建（首次）→ 空集
    result = (frozenset(full), frozenset(given))
    with _cache_lock:
        _name_cache[db_path] = result
    return result


def invalidate_cache(db_path: str | None = None) -> None:
    with _cache_lock:
        if db_path is None:
            _name_cache.clear()
        else:
            _name_cache.pop(db_path, None)


# ─────────── CRUD ───────────


def _ensure(con: sqlite3.Connection) -> None:
    from storage.market_extractor_schema import ensure_market_extractor_tables
    ensure_market_extractor_tables(con)


def add_name(
    db_path: str, full_name: str, *, source: str = "user",
    work_id: str = "", work_title: str = "", platform: str = "",
    category: str = "", rank: int | None = None, heat: float | None = None,
    count_df: bool = False,
) -> dict | None:
    """新增/更新一条全名记录（派生字段自动重算）。``count_df=True`` 时把 book_df +1
    （仅在「确属一本新书的去重名」时调用）。返回写入的行。"""
    fn = (full_name or "").strip()
    if not is_valid_name(fn):
        return None
    parts = derive_name_parts(fn)
    with sqlite3.connect(db_path) as con:
        _ensure(con)
        con.row_factory = sqlite3.Row
        existing = con.execute(
            "SELECT * FROM person_name_library WHERE full_name = ?", (fn,)
        ).fetchone()
        if existing:
            df = existing["book_df"] + (1 if count_df else 0)
            con.execute(
                "UPDATE person_name_library SET book_df = ?, updated_at = CURRENT_TIMESTAMP, "
                "source_work_id = COALESCE(NULLIF(?, ''), source_work_id), "
                "source_work_title = COALESCE(NULLIF(?, ''), source_work_title), "
                "source_work_rank = COALESCE(?, source_work_rank), "
                "source_work_heat = COALESCE(?, source_work_heat) "
                "WHERE full_name = ?",
                (df, work_id, work_title, rank, heat, fn),
            )
            con.commit()
            row = con.execute("SELECT * FROM person_name_library WHERE full_name = ?", (fn,)).fetchone()
            invalidate_cache(db_path)
            return dict(row)
        nid = f"pn_{uuid.uuid4().hex[:12]}"
        con.execute(
            "INSERT INTO person_name_library "
            "(name_id, full_name, surname, given_name, surname_kind, name_length, "
            " is_compound_surname, is_single_given, is_nonstandard, nonstandard_reason, "
            " source, source_work_id, source_work_title, source_platform, source_category, "
            " source_work_rank, source_work_heat, book_df) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (nid, fn, parts["surname"], parts["given_name"], parts["surname_kind"],
             parts["name_length"], parts["is_compound_surname"], parts["is_single_given"],
             parts["is_nonstandard"], parts["nonstandard_reason"], source, work_id,
             work_title, platform, category, rank, heat, 1 if count_df else 0),
        )
        con.commit()
        row = con.execute("SELECT * FROM person_name_library WHERE full_name = ?", (fn,)).fetchone()
    invalidate_cache(db_path)
    return dict(row)


def remove_name(db_path: str, full_name: str) -> bool:
    fn = (full_name or "").strip()
    with sqlite3.connect(db_path) as con:
        _ensure(con)
        cur = con.execute("DELETE FROM person_name_library WHERE full_name = ?", (fn,))
        con.commit()
    invalidate_cache(db_path)
    return cur.rowcount > 0


def clear_all(db_path: str) -> dict:
    """清空人名库：删除所有全名记录 + NER 处理台账（下次刷新/分析会重新抽取，并按需
    重新灌入静态种子库）。返回删除数。"""
    with sqlite3.connect(db_path) as con:
        _ensure(con)
        removed = con.execute("SELECT COUNT(*) FROM person_name_library").fetchone()[0]
        con.execute("DELETE FROM person_name_library")
        con.execute("DELETE FROM name_extraction_state")
        con.commit()
    invalidate_cache(db_path)
    logger.info("cleared person_name_library (%d names removed)", removed)
    return {"removed": int(removed or 0)}


def update_flags(db_path: str, full_name: str, **flags: Any) -> bool:
    """手动改标记（如把某条标/取消标为非标准）。仅允许改标记类字段。"""
    allowed = {"is_nonstandard", "is_compound_surname", "is_single_given", "nonstandard_reason"}
    sets = {k: v for k, v in flags.items() if k in allowed}
    if not sets:
        return False
    with sqlite3.connect(db_path) as con:
        _ensure(con)
        clause = ", ".join(f"{k} = ?" for k in sets)
        cur = con.execute(
            f"UPDATE person_name_library SET {clause}, updated_at = CURRENT_TIMESTAMP "
            "WHERE full_name = ?",
            list(sets.values()) + [fn := full_name.strip()],
        )
        con.commit()
    invalidate_cache(db_path)
    return cur.rowcount > 0


def search_names(
    db_path: str, q: str = "", *, limit: int = 200, offset: int = 0,
    only_nonstandard: bool | None = None, order: str = "df",
) -> dict:
    """搜索 + 分页。``order`` ∈ {df, name, recent}。DF 作可信度信号一并返回。"""
    q = (q or "").strip()
    where = []
    params: list = []
    if q:
        where.append("(full_name LIKE ? OR surname LIKE ? OR given_name LIKE ?)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if only_nonstandard is True:
        where.append("is_nonstandard = 1")
    elif only_nonstandard is False:
        where.append("is_nonstandard = 0")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    order_sql = {
        "df": "book_df DESC, full_name",
        "name": "full_name",
        "recent": "updated_at DESC",
    }.get(order, "book_df DESC, full_name")
    with sqlite3.connect(db_path) as con:
        _ensure(con)
        con.row_factory = sqlite3.Row
        total = con.execute(
            f"SELECT COUNT(*) AS c FROM person_name_library{where_sql}", params
        ).fetchone()["c"]
        rows = con.execute(
            f"SELECT * FROM person_name_library{where_sql} ORDER BY {order_sql} "
            "LIMIT ? OFFSET ?",
            params + [limit, offset],
        ).fetchall()
    return {
        "total": total,
        "items": [dict(r) for r in rows],
        "offset": offset,
        "limit": limit,
        "truncated": offset + len(rows) < total,
    }


def library_stats(db_path: str) -> dict:
    with sqlite3.connect(db_path) as con:
        _ensure(con)
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT COUNT(*) AS total, "
            " SUM(is_nonstandard) AS nonstandard, "
            " SUM(is_compound_surname) AS compound, "
            " SUM(is_single_given) AS single_given, "
            " SUM(CASE WHEN book_df >= 2 THEN 1 ELSE 0 END) AS df_ge2 "
            "FROM person_name_library"
        ).fetchone()
        by_source = {
            r["source"]: r["c"] for r in con.execute(
                "SELECT source, COUNT(*) AS c FROM person_name_library GROUP BY source"
            ).fetchall()
        }
    return {
        "total": row["total"] or 0,
        "nonstandard": row["nonstandard"] or 0,
        "compound_surname": row["compound"] or 0,
        "single_given": row["single_given"] or 0,
        "df_ge2": row["df_ge2"] or 0,
        "by_source": by_source,
    }


# ─────────── 种子库 ───────────


def _read_seed_names() -> list[str]:
    if not _SEED_FILE.exists():
        return []
    names: list[str] = []
    for line in _SEED_FILE.read_text("utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        for tok in re.split(r"[\s,，]+", s):
            tok = tok.strip()
            if is_valid_name(tok):
                names.append(tok)
    return names


def seed_if_empty(db_path: str) -> int:
    """库为空时灌入打包种子人名库，保证 day-1 覆盖。返回写入条数。"""
    with sqlite3.connect(db_path) as con:
        _ensure(con)
        n = con.execute("SELECT COUNT(*) FROM person_name_library").fetchone()[0]
    if n > 0:
        return 0
    seed = _read_seed_names()
    surnames = _surnames()
    written = 0
    with sqlite3.connect(db_path) as con:
        for fn in dict.fromkeys(seed):     # 去重保序
            parts = derive_name_parts(fn, surnames)
            nid = f"pn_{uuid.uuid4().hex[:12]}"
            try:
                con.execute(
                    "INSERT OR IGNORE INTO person_name_library "
                    "(name_id, full_name, surname, given_name, surname_kind, name_length, "
                    " is_compound_surname, is_single_given, is_nonstandard, nonstandard_reason, "
                    " source, book_df) VALUES (?,?,?,?,?,?,?,?,?,?, 'seed', 0)",
                    (nid, fn, parts["surname"], parts["given_name"], parts["surname_kind"],
                     parts["name_length"], parts["is_compound_surname"],
                     parts["is_single_given"], parts["is_nonstandard"], parts["nonstandard_reason"]),
                )
                written += 1
            except sqlite3.IntegrityError:
                pass
        con.commit()
    invalidate_cache(db_path)
    logger.info("seeded person_name_library with %d names", written)
    return written


def rebuild_derived(db_path: str) -> int:
    """姓氏表更新后，对全库重算姓/名/标记派生字段（全名权威记录不动）。"""
    surnames = _surnames()
    updated = 0
    with sqlite3.connect(db_path) as con:
        _ensure(con)
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT full_name FROM person_name_library").fetchall()
        for r in rows:
            p = derive_name_parts(r["full_name"], surnames)
            con.execute(
                "UPDATE person_name_library SET surname=?, given_name=?, surname_kind=?, "
                "name_length=?, is_compound_surname=?, is_single_given=?, is_nonstandard=?, "
                "nonstandard_reason=? WHERE full_name=?",
                (p["surname"], p["given_name"], p["surname_kind"], p["name_length"],
                 p["is_compound_surname"], p["is_single_given"], p["is_nonstandard"],
                 p["nonstandard_reason"], r["full_name"]),
            )
            updated += 1
        con.commit()
    invalidate_cache(db_path)
    return updated
