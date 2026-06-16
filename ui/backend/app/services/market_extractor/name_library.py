"""人名库 —— 全名为权威记录，姓/名为可重算派生字段（spec 语言学文本特征 §4/§5）。

数据结构（spec §5）：
- 以**全名**为权威记录（唯一键）。
- 姓氏、名字字符为**派生字段**：按姓氏表 + 复姓识别从全名重算，可随姓氏表更新重建。
- 对**昵称 / 复姓 / 单名**等非标准名打标记（``is_nonstandard`` / ``is_compound_surname``
  / ``is_single_given``），以免污染后续取名规律统计。
- 每条附元数据：来源作品、来源作品热度/排名、出现的去重书数 ``book_df``
  （按 book 去重统计，仅作内部元数据，不作识别指标、不在界面排序/展示）。

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


def _wl_set(loader: str) -> frozenset[str]:
    try:
        from . import wordlists as _wl
        return getattr(_wl, loader)()
    except Exception:
        return frozenset()


_NICK_PREFIX = ("小", "阿", "老")     # 小明 / 阿强 / 老李 …
# 称谓后缀：姓/名 + 这些字 = 昵称（王总 / 李叔 / 统子哥 / 张姐）。这些字几乎不作中文
# 名字的收尾字，故可据此判昵称而不误伤真名。
_NICK_SUFFIX = ("哥", "姐", "叔", "姨", "伯", "婶", "嫂", "爷", "总", "董", "爹", "妈", "爸")

# 组织/门派/宗族后缀：以这些字结尾的实体几乎都是机构/势力而非个人（唐门/宗门/丐帮/
# 刘氏/慕容世家…）。只收极少作中文名收尾字的，避免误伤（不含 国/堂/宗/山/林/江… 等
# 可入名的字）。抽名时据此剔除 LTP 误标。
_ORG_PLACE_TAIL = frozenset(
    "门派帮会殿宫府寺庙观族氏团盟教营院局厅省市县区乡镇司")


def _is_western(fn: str, western: frozenset[str], translit: frozenset[str],
                surnames: frozenset[str], blocklist: frozenset[str]) -> bool:
    """西方名判定（收紧）：词典命中 / 含间隔号「·」/（保守）长度≥3 且**全部**为音译字
    且非中文姓氏开头。**不再用「音译字占比」启发**——那会把「查克拉」「金丹」这类网文
    名词误判成西方名（金/丹/克/拉 都在音译字表里）。"""
    if fn in western:
        return True
    if "·" in fn or "・" in fn:           # 乔治·马丁 这类强信号
        return True
    if len(fn) >= 3 and fn not in blocklist and fn[0] not in surnames:
        if all(ch in translit for ch in fn):   # 整名皆音译字（查克拉因「查」不在表→不命中）
            return True
    return False


def _jp_prefix(fn: str, jp_surnames: frozenset[str]) -> str:
    for k in (4, 3, 2):                   # 复姓优先（长谷川/长宗我部…）
        if len(fn) > k and fn[:k] in jp_surnames:
            return fn[:k]
    return ""


def _is_nickname(fn: str) -> bool:
    """昵称形态（已下线昵称分类 —— 抽名时据此**剔除**，不入库）：叠字 / 小阿老前缀 /
    称谓后缀（王总/李叔/统子哥/张姐）。"""
    if len(fn) == 2 and fn[0] == fn[1]:   # 叠字：翠翠 / 婷婷
        return True
    if 2 <= len(fn) <= 3 and fn[0] in _NICK_PREFIX:  # 小明 / 阿强 / 老李
        return True
    if 2 <= len(fn) <= 4 and fn[-1] in _NICK_SUFFIX:  # 王总 / 李叔 / 统子哥 / 张姐
        return True
    return False


# ─────────── 性别启发（字启发式；可手动覆盖、字表可增删）───────────
_JP_MALE_TAIL = frozenset("郎朗夫雄也介彦之吾太树斗健直辉武洋丸藏")
_JP_FEMALE_TAIL = frozenset("子美惠奈香菜花枝代穗乃织音叶纱莉爱")
_WEST_MALE = frozenset({
    "乔治", "约翰", "大卫", "汤姆", "杰克", "威廉", "亨利", "查理", "彼得", "迈克",
    "詹姆斯", "本杰明", "卢卡斯", "亚瑟", "爱德华", "哈利", "罗恩", "杰森", "瑞恩",
    "凯文", "布莱恩", "史蒂夫", "马克", "保罗", "安东尼", "丹尼尔", "马修", "约瑟夫",
    "亚历山大", "尼古拉斯", "维克托", "奥斯卡", "雨果", "路易", "菲利普", "文森特",
    "卡尔", "弗兰克", "诺亚", "伊森", "西奥多", "阿尔伯特", "欧文", "雷蒙德"})
_WEST_FEMALE = frozenset({
    "玛丽", "爱丽丝", "莉莉", "安娜", "苏菲", "艾玛", "伊莎贝拉", "奥利维亚", "夏洛特",
    "艾娃", "米娅", "罗丝", "贝拉", "赫敏", "凯特", "露西", "薇薇安", "索菲亚", "艾米丽",
    "艾米", "莉莉安", "格蕾丝", "克莱尔", "黛西", "珍妮", "莎拉", "丽贝卡", "艾琳",
    "玛格丽特", "维多利亚", "伊丽莎白", "凯瑟琳", "朱莉娅", "娜塔莉", "露娜", "黛安娜",
    "卡罗琳", "海伦", "琳达", "雪莉", "温迪", "莫妮卡", "蕾切尔", "妮可", "珊曼莎"})


def infer_gender(full_name: str, name_kind: str, given_name: str = "") -> str:
    """字启发式判性别：返回 'male' / 'female' / ''（中性/未知）。中文按名字用字统计、
    日文按收尾字、西方按常见男女名词典。仅作建议，用户可手动覆盖。"""
    fn = (full_name or "").strip()
    if name_kind == "western":
        if fn in _WEST_MALE:
            return "male"
        if fn in _WEST_FEMALE:
            return "female"
        return ""
    if name_kind == "japanese":
        if fn and fn[-1] in _JP_FEMALE_TAIL:
            return "female"
        if fn and fn[-1] in _JP_MALE_TAIL:
            return "male"
        return ""
    # 中文：名字用字男女计数（多者胜，平局/无命中→中性）。
    given = given_name or fn
    male = _wl_set("load_male_name_chars")
    female = _wl_set("load_female_name_chars")
    m = sum(1 for c in given if c in male)
    f = sum(1 for c in given if c in female)
    if m > f:
        return "male"
    if f > m:
        return "female"
    return ""


# ─────────── 全名 → 派生字段 + 分类（中文/日文/西方）───────────


def derive_name_parts(full_name: str, surnames: frozenset[str] | None = None) -> dict[str, Any]:
    """从全名重算姓/名 + **分类** name_kind（chinese/japanese/western）+ 性别启发。
    分类顺序：西方 → 日文 → 中文，避免「乔治」被误拆成中文姓+名（昵称已下线）。
    姓氏/名字表更新后可对全库重跑本函数重建派生字段。"""
    surnames = surnames if surnames is not None else _surnames()
    western = _wl_set("load_western_names")
    jp_surnames = _wl_set("load_japanese_surnames")
    translit = _wl_set("load_translit_chars")
    blocklist = _wl_set("load_name_blocklist")
    fn = (full_name or "").strip()
    length = len(fn)

    surname = ""
    surname_kind = "unknown"
    name_kind = "chinese"

    if _is_western(fn, western, translit, surnames, blocklist):
        name_kind, surname_kind = "western", "western"
        given = fn
    elif (jp := _jp_prefix(fn, jp_surnames)):
        name_kind, surname_kind, surname = "japanese", "japanese", jp
        given = fn[len(jp):]
    else:
        # 中文：复姓优先（2-3 字），再单字姓。
        for k in (3, 2):
            if length > k and fn[:k] in surnames:
                surname, surname_kind = fn[:k], "compound"
                break
        if not surname and length >= 2 and fn[:1] in surnames:
            surname, surname_kind = fn[:1], "single"
        given = fn[len(surname):] if surname else fn

    gender = infer_gender(fn, name_kind, given)
    is_compound = surname_kind == "compound"
    is_single_given = name_kind == "chinese" and bool(surname) and len(given) == 1
    # is_nonstandard 仅用于「中文取名规律」排除：非中文名或无法识别姓氏的中文名都排除。
    is_nonstandard = name_kind != "chinese" or surname_kind == "unknown"
    return {
        "full_name": fn,
        "surname": surname,
        "given_name": given,
        "surname_kind": surname_kind,
        "name_kind": name_kind,
        "gender": gender,
        "name_length": length,
        "is_compound_surname": int(is_compound),
        "is_single_given": int(is_single_given),
        "is_nonstandard": int(is_nonstandard),
        "nonstandard_reason": "" if name_kind == "chinese" and surname_kind != "unknown"
        else {"western": "西方名", "japanese": "日文名"}.get(name_kind, "无法识别姓氏"),
    }


def is_valid_name(full_name: str) -> bool:
    return bool(_CJK_NAME.match((full_name or "").strip()))


def is_plausible_person_name(full_name: str) -> bool:
    """抽名质量闸（仅用于 LTP 自动抽取，手动添加不受限）：在 ``is_valid_name`` 基础上
    再剔除 LTP 的常见误报 —— ①命中网文「设定/物品/称谓」黑名单（金丹/甲胄/查克拉/启禀…）；
    ②以组织/门派/宗族后缀结尾（唐门/宗门/丐帮/刘氏…）；③昵称形态（叠字/小阿老/王总李叔，
    昵称已下线、不入库）。宁可漏，不要脏。"""
    fn = (full_name or "").strip()
    if not is_valid_name(fn):
        return False
    if fn in _wl_set("load_name_blocklist"):
        return False
    if len(fn) >= 2 and fn[-1] in _ORG_PLACE_TAIL:
        return False
    if _is_nickname(fn):
        return False
    return True


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
    count_df: bool = False, example_sentence: str = "", alias_of: str = "",
) -> dict | None:
    """新增/更新一条全名记录（派生字段自动重算）。``count_df=True`` 时把 book_df +1
    （仅在「确属一本新书的去重名」时调用）。返回写入的行。"""
    fn = (full_name or "").strip()
    if not is_valid_name(fn):
        return None
    parts = derive_name_parts(fn)
    ex = (example_sentence or "").strip()[:300]
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
                "source_work_heat = COALESCE(?, source_work_heat), "
                "source_category = COALESCE(NULLIF(?, ''), source_category), "
                "source_platform = COALESCE(NULLIF(?, ''), source_platform), "
                "example_sentence = COALESCE(NULLIF(example_sentence, ''), NULLIF(?, '')) "
                "WHERE full_name = ?",
                (df, work_id, work_title, rank, heat, category, platform, ex, fn),
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
            " source_work_rank, source_work_heat, book_df, example_sentence, name_kind, "
            " gender, alias_of) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (nid, fn, parts["surname"], parts["given_name"], parts["surname_kind"],
             parts["name_length"], parts["is_compound_surname"], parts["is_single_given"],
             parts["is_nonstandard"], parts["nonstandard_reason"], source, work_id,
             work_title, platform, category, rank, heat, 1 if count_df else 0, ex,
             parts["name_kind"], parts["gender"], (alias_of or "").strip()),
        )
        con.commit()
        row = con.execute("SELECT * FROM person_name_library WHERE full_name = ?", (fn,)).fetchone()
    invalidate_cache(db_path)
    return dict(row)


def edit_name(db_path: str, name_id: str, *, full_name: str | None = None,
              example_sentence: str | None = None,
              is_nonstandard: int | None = None,
              name_kind: str | None = None, alias_of: str | None = None,
              gender: str | None = None) -> dict | None:
    """手动编辑一条人名库条目。改全名时自动重算姓/名/分类/性别派生字段；可手动覆盖
    分类 name_kind（中文/日文/西方）与性别 gender（male/female/''）。"""
    sets: list[str] = []
    params: list = []
    if full_name is not None:
        fn = full_name.strip()
        if not is_valid_name(fn):
            raise ValueError("full_name 需为 2-8 个汉字")
        p = derive_name_parts(fn)
        sets += ["full_name=?", "surname=?", "given_name=?", "surname_kind=?",
                 "name_length=?", "is_compound_surname=?", "is_single_given=?",
                 "is_nonstandard=?", "nonstandard_reason=?", "name_kind=?", "gender=?"]
        params += [fn, p["surname"], p["given_name"], p["surname_kind"], p["name_length"],
                   p["is_compound_surname"], p["is_single_given"], p["is_nonstandard"],
                   p["nonstandard_reason"], p["name_kind"], p["gender"]]
    if name_kind is not None and name_kind in ("chinese", "japanese", "western"):
        sets.append("name_kind=?")
        params.append(name_kind)
    if gender is not None and gender in ("male", "female", ""):
        sets.append("gender=?")             # 手动标性别（覆盖启发式）
        params.append(gender)
    if alias_of is not None:
        sets.append("alias_of=?")
        params.append(alias_of.strip()[:32])
    if example_sentence is not None:
        sets.append("example_sentence=?")
        params.append(example_sentence.strip()[:300])
    if is_nonstandard is not None:
        sets.append("is_nonstandard=?")
        params.append(int(is_nonstandard))
    if not sets:
        return None
    with sqlite3.connect(db_path) as con:
        _ensure(con)
        con.row_factory = sqlite3.Row
        try:
            con.execute(
                f"UPDATE person_name_library SET {', '.join(sets)}, "
                "updated_at=CURRENT_TIMESTAMP WHERE name_id=?", params + [name_id])
            con.commit()
        except sqlite3.IntegrityError:
            raise ValueError("该全名已存在于人名库")
        row = con.execute("SELECT * FROM person_name_library WHERE name_id=?", (name_id,)).fetchone()
    invalidate_cache(db_path)
    return dict(row) if row else None


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
    only_nonstandard: bool | None = None, order: str = "name", kind: str = "",
) -> dict:
    """搜索 + 分页。``kind`` ∈ {chinese,japanese,western,nickname,''}。``order`` ∈
    {name, recent}（不再按 DF 排序 —— 人名识别不用 DF 作指标）。"""
    q = (q or "").strip()
    where = []
    params: list = []
    if q:
        where.append("(full_name LIKE ? OR surname LIKE ? OR given_name LIKE ?)")
        params += [f"%{q}%", f"%{q}%", f"%{q}%"]
    if kind:
        where.append("name_kind = ?")
        params.append(kind)
    if only_nonstandard is True:
        where.append("is_nonstandard = 1")
    elif only_nonstandard is False:
        where.append("is_nonstandard = 0")
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    order_sql = {
        "name": "full_name",
        "recent": "updated_at DESC, full_name",
    }.get(order, "full_name")
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
            " SUM(is_single_given) AS single_given "
            "FROM person_name_library"
        ).fetchone()
        by_source = {
            r["source"]: r["c"] for r in con.execute(
                "SELECT source, COUNT(*) AS c FROM person_name_library GROUP BY source"
            ).fetchall()
        }
        by_kind = {
            r["name_kind"]: r["c"] for r in con.execute(
                "SELECT name_kind, COUNT(*) AS c FROM person_name_library GROUP BY name_kind"
            ).fetchall()
        }
        by_gender = {
            (r["gender"] or "unknown"): r["c"] for r in con.execute(
                "SELECT gender, COUNT(*) AS c FROM person_name_library GROUP BY gender"
            ).fetchall()
        }
    return {
        "total": row["total"] or 0,
        "nonstandard": row["nonstandard"] or 0,
        "compound_surname": row["compound"] or 0,
        "single_given": row["single_given"] or 0,
        "by_gender": by_gender,
        "by_source": by_source,
        "by_kind": by_kind,
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
                    " name_kind, gender, source, book_df) VALUES (?,?,?,?,?,?,?,?,?,?,?,?, 'seed', 0)",
                    (nid, fn, parts["surname"], parts["given_name"], parts["surname_kind"],
                     parts["name_length"], parts["is_compound_surname"],
                     parts["is_single_given"], parts["is_nonstandard"], parts["nonstandard_reason"],
                     parts["name_kind"], parts["gender"]),
                )
                written += 1
            except sqlite3.IntegrityError:
                pass
        con.commit()
    invalidate_cache(db_path)
    logger.info("seeded person_name_library with %d names", written)
    return written


# ─────────── 导入 / 导出（预训练好的清理库可整体迁移、备份、复用）───────────

_EXPORT_FIELDS = ("full_name", "name_kind", "gender", "alias_of", "source",
                  "example_sentence", "source_category", "source_work_title")


def export_all(db_path: str) -> list[dict]:
    """导出全部人名为可移植记录（含分类/别名/例句/题材），供备份或迁移到别处。
    全名权威、姓/名派生字段不导（导入端按姓氏表重算）。"""
    with sqlite3.connect(db_path) as con:
        _ensure(con)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"SELECT {', '.join(_EXPORT_FIELDS)} FROM person_name_library "
            "ORDER BY name_kind, full_name"
        ).fetchall()
    return [dict(r) for r in rows]


def import_records(db_path: str, records: list[dict], *,
                   default_source: str = "import") -> dict:
    """导入人名记录（全名权威，姓/名按本机姓氏表重算）。已存在的全名按需补 DF/例句/
    分类（不重复计）。``records`` 每条至少含 ``full_name``；其余字段（name_kind/alias_of/
    example_sentence/source_category）若给则覆盖。返回 {added, updated, skipped}。"""
    with sqlite3.connect(db_path) as con:
        _ensure(con)
        existing = {r[0] for r in con.execute(
            "SELECT full_name FROM person_name_library").fetchall()}
    added = updated = skipped = 0
    for rec in records or []:
        fn = (str(rec.get("full_name") or "")).strip()
        if not is_valid_name(fn):
            skipped += 1
            continue
        src = (str(rec.get("source") or "")).strip() or default_source
        existed = fn in existing
        existing.add(fn)
        row = add_name(
            db_path, fn, source=src,
            work_title=str(rec.get("source_work_title") or ""),
            category=str(rec.get("source_category") or ""),
            example_sentence=str(rec.get("example_sentence") or ""),
            alias_of=str(rec.get("alias_of") or ""),
        )
        if not row:
            skipped += 1
            continue
        # 导入端尊重导出时的人工分类/性别（如把「乔治」固定为西方名、手动标的性别）。
        kind = (str(rec.get("name_kind") or "")).strip()
        gender = (str(rec.get("gender") or "")).strip()
        if kind in ("chinese", "japanese", "western") or gender in ("male", "female"):
            try:
                edit_name(db_path, row["name_id"],
                          name_kind=kind if kind in ("chinese", "japanese", "western") else None,
                          gender=gender if gender in ("male", "female") else None)
            except Exception:
                pass
        updated += 1 if existed else 0
        added += 0 if existed else 1
    invalidate_cache(db_path)
    return {"added": added, "updated": updated, "skipped": skipped}


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
                "nonstandard_reason=?, name_kind=? WHERE full_name=?",
                (p["surname"], p["given_name"], p["surname_kind"], p["name_length"],
                 p["is_compound_surname"], p["is_single_given"], p["is_nonstandard"],
                 p["nonstandard_reason"], p["name_kind"], r["full_name"]),
            )
            updated += 1
        con.commit()
    invalidate_cache(db_path)
    return updated
