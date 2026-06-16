"""取名（人名生成）—— 基于人名库的姓氏池 + 名字池，按题材/性别/姓氏约束**重组**出
库里尚不存在的新名，供用户复制给新角色。中文/日文走「姓+名」重组，西方走（带性别的）
常见名采样。库稀疏时回退到打包词表，保证 day-1 可用。
"""
from __future__ import annotations

import random
import re
import sqlite3

from . import name_library, wordlists

_CJK = re.compile(r"^[一-鿿]+$")
_KINDS = ("chinese", "japanese", "western")


def _db_rows(db_path: str, kind: str, category: str):
    where = ["name_kind = ?"]
    params: list = [kind]
    if category:
        where.append("source_category LIKE ?")
        params.append(f"%{category}%")
    try:
        with sqlite3.connect(db_path) as con:
            con.row_factory = sqlite3.Row
            name_library._ensure(con)
            return con.execute(
                f"SELECT full_name, surname, given_name, gender FROM person_name_library "
                f"WHERE {' AND '.join(where)}", params).fetchall()
    except Exception:
        return []


def _given_matches_gender(given: str, kind: str, want: str) -> bool:
    if not want:
        return True
    g = name_library.infer_gender(given, kind, given)
    return g == want or g == ""        # 含中性名（不强行排除）；精确匹配优先在采样层处理


def _cjk_pools(db_path: str, kind: str, gender: str, category: str):
    """(surname_pool, given_pool) —— 库优先，库稀疏时用打包词表兜底。"""
    surnames: list[str] = []
    givens: list[str] = []
    for r in _db_rows(db_path, kind, category):
        if r["surname"] and _CJK.match(r["surname"]):
            surnames.append(r["surname"])
        gv = r["given_name"]
        if gv and 1 <= len(gv) <= 3 and _CJK.match(gv) and _given_matches_gender(gv, kind, gender):
            givens.append(gv)
    # 兜底：中文用百家姓 + 名字词表；日文用日文姓词表（名字仅来自库）。
    if kind == "chinese":
        if len(surnames) < 8:
            surnames += [s for s in wordlists.load_surnames() if len(s) == 1]
        if len(givens) < 12:
            givens += [g for g in wordlists.load_given_names()
                       if 1 <= len(g) <= 3 and _CJK.match(g)
                       and _given_matches_gender(g, kind, gender)
                       and name_library.derive_name_parts(g)["name_kind"] == "chinese"]
    elif kind == "japanese":
        if len(surnames) < 4:
            surnames += [s for s in wordlists.load_japanese_surnames() if len(s) >= 2]
    # 去重保序
    return list(dict.fromkeys(surnames)), list(dict.fromkeys(givens))


def _gen_cjk(db_path, kind, gender, surname, category, count):
    sur_pool, given_pool = _cjk_pools(db_path, kind, gender, category)
    if surname:
        sur_pool = [surname]
    if not sur_pool or not given_pool:
        return [], ("库内该题材/性别的名字样本不足，且词表兜底为空 —— 先刷新/导入人名库，"
                    "或放宽题材/性别约束。")
    existing = _existing(db_path)
    out: list[dict] = []
    seen: set[str] = set()
    # 组合空间 = 姓数 × 名数；上限内尽量产出库里没有的新名。
    budget = max(count * 60, 400)
    while len(out) < count and budget > 0:
        budget -= 1
        s = surname or random.choice(sur_pool)
        g = random.choice(given_pool)
        full = s + g
        if not (2 <= len(full) <= 6) or full in seen:
            continue
        seen.add(full)
        if full in existing:           # 优先新名；新名不够时下一轮放宽
            continue
        out.append({"full_name": full, "surname": s, "given_name": g,
                    "name_kind": kind,
                    "gender": gender or name_library.infer_gender(full, kind, g)})
    # 新名不够 → 允许已存在组合补齐（仍是有效名，只是库里已有）。
    budget = max(count * 40, 200)
    while len(out) < count and budget > 0:
        budget -= 1
        s = surname or random.choice(sur_pool)
        g = random.choice(given_pool)
        full = s + g
        if not (2 <= len(full) <= 6) or full in seen:
            continue
        seen.add(full)
        out.append({"full_name": full, "surname": s, "given_name": g,
                    "name_kind": kind,
                    "gender": gender or name_library.infer_gender(full, kind, g)})
    return out, ""


def _gen_western(db_path, gender, category, count):
    pool: list[str] = []
    if gender == "male":
        pool += list(name_library._WEST_MALE)
    elif gender == "female":
        pool += list(name_library._WEST_FEMALE)
    else:
        pool += list(name_library._WEST_MALE) + list(name_library._WEST_FEMALE)
    # 库里的西方名（按性别过滤）一并纳入。
    for r in _db_rows(db_path, "western", category):
        fn = r["full_name"]
        if fn and (not gender or (r["gender"] or "") == gender):
            pool.append(fn)
    pool = list(dict.fromkeys(pool))
    if not pool:
        return [], "暂无西方名样本 —— 先刷新/导入人名库。"
    random.shuffle(pool)
    out = [{"full_name": fn, "surname": "", "given_name": fn,
            "name_kind": "western",
            "gender": gender or name_library.infer_gender(fn, "western")}
           for fn in pool[:count]]
    return out, ""


def _existing(db_path: str) -> set[str]:
    try:
        with sqlite3.connect(db_path) as con:
            name_library._ensure(con)
            return {r[0] for r in con.execute(
                "SELECT full_name FROM person_name_library").fetchall()}
    except Exception:
        return set()


def generate_names(db_path: str, *, kind: str = "chinese", gender: str = "",
                   surname: str = "", category: str = "", count: int = 20) -> dict:
    """按约束生成一批名字。返回 {names:[{full_name,surname,given_name,name_kind,gender}],
    note}。kind∈中文/日文/西方；gender∈male/female/''；surname/category 可空。"""
    kind = kind if kind in _KINDS else "chinese"
    gender = gender if gender in ("male", "female") else ""
    surname = (surname or "").strip()
    category = (category or "").strip()
    count = max(1, min(int(count or 20), 100))
    if kind == "western":
        names, note = _gen_western(db_path, gender, category, count)
    else:
        names, note = _gen_cjk(db_path, kind, gender, surname, category, count)
    return {"names": names, "note": note, "kind": kind, "gender": gender,
            "surname": surname, "category": category, "count": len(names)}
