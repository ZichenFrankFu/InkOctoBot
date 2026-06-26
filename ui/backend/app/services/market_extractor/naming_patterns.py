"""取名规律统计 —— 按题材分桶、按来源作品热度/排名加权的命名画像（spec §5）。

从人名库统计作者取名规律，输出**每题材一份加权画像**：
- 姓氏加权分布
- 名字用字加权分布（分首字 / 末字 / 全部位置）
- 名长结构（单名 / 双名 / 复姓占比）
- 生僻字使用率
- 名字字对的典型组合

要点（spec §5）：
- 权重 = 来源作品在「题材×平台」内的热度/排名百分位（前排作品权重大）。
- 统计**按 book 去重**（库本就一名一行），并对**单本贡献设上限**（每本最多贡献
  ``_PER_BOOK_CAP`` 个有效名权重），防止角色多的爆款独霸分布。
- **非标准名**（昵称/异常名）排除出规律分布，避免污染；单名/复姓仍计入「名长结构」。
- UI 展示**加权值**而非原始出现次数。
- 定位：**描述性**的市场命名惯例，**不作**成功公式或打分依据。
"""
from __future__ import annotations

import logging
import sqlite3
from collections import defaultdict

logger = logging.getLogger("inkoctobot.market_extractor.naming_patterns")

_PER_BOOK_CAP = 8.0          # 单本最多贡献的有效名权重（防爆款独霸）
_DISCLAIMER = "描述性的市场命名惯例，仅供参考，不作为成功公式或打分依据。"


def _common_name_chars() -> frozenset[str]:
    try:
        from . import wordlists as _wl
        return _wl.load_name_chars()
    except Exception:
        return frozenset()


def _percentile_weights(books: dict[str, dict]) -> dict[str, float]:
    """每本书的显著度权重（0.2..1.0）：按 rank/heat 在桶内的百分位，前排权重大。
    缺 rank/heat 的书给中位权重。"""
    if not books:
        return {}
    heats = sorted(b["heat"] for b in books.values() if b.get("heat") is not None)
    ranks = sorted(b["rank"] for b in books.values() if b.get("rank") is not None)

    def _pct(sorted_vals: list, v, *, higher_better: bool) -> float:
        if not sorted_vals or v is None:
            return 0.5
        # v 的位次百分位。
        below = sum(1 for x in sorted_vals if x < v)
        p = below / len(sorted_vals)
        return p if higher_better else (1.0 - p)

    out: dict[str, float] = {}
    for wid, b in books.items():
        hp = _pct(heats, b.get("heat"), higher_better=True)
        rp = _pct(ranks, b.get("rank"), higher_better=False)
        # 有 rank 用 rank 为主、heat 为辅；只有 heat 用 heat；都没有 → 0.5。
        if b.get("rank") is not None and b.get("heat") is not None:
            prom = 0.6 * rp + 0.4 * hp
        elif b.get("rank") is not None:
            prom = rp
        elif b.get("heat") is not None:
            prom = hp
        else:
            prom = 0.5
        out[wid] = 0.2 + 0.8 * prom      # 即便低显著度也留一点底权重
    return out


def _top(weighted: dict[str, float], k: int) -> list[dict]:
    total = sum(weighted.values())
    items = sorted(weighted.items(), key=lambda x: -x[1])[:k]
    return [{"key": kk, "weight": round(w, 3),
             "pct": round(w / total, 4) if total else 0.0} for kk, w in items]


def compute_for_category(
    db_path: str, category: str, *, platform: str | None = None, top_k: int = 20,
) -> dict:
    """单题材加权命名画像。空 / "" / "全部" / "all" → 跨题材聚合（全部样本）。"""
    where: list[str] = []
    params: list = []
    cat_label = (category or "").strip()
    # Empty / 全部 / all → no category filter; everything else → exact match.
    if cat_label and cat_label.lower() != "all" and cat_label != "全部":
        where.append("source_category = ?")
        params.append(cat_label)
    if platform:
        where.append("source_platform = ?")
        params.append(platform)
    where_sql = " AND ".join(where) if where else "1=1"
    with sqlite3.connect(db_path) as con:
        from . import name_library
        name_library._ensure(con)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"SELECT full_name, surname, given_name, surname_kind, name_length, "
            f"is_compound_surname, is_single_given, is_nonstandard, source_work_id, "
            f"source_work_rank, source_work_heat FROM person_name_library "
            f"WHERE {where_sql}",
            params,
        ).fetchall()
    if not rows:
        return {"available": False, "category": cat_label or "全部",
                "platform": platform or "all",
                "reason": "该题材人名库为空" if cat_label else "人名库为空",
                "note": _DISCLAIMER}

    # 每本书的 rank/heat（按 book 去重；同书多名取一致元数据）。
    books: dict[str, dict] = {}
    names_per_book: dict[str, int] = defaultdict(int)
    for r in rows:
        wid = r["source_work_id"] or "__unknown__"
        books.setdefault(wid, {"rank": r["source_work_rank"], "heat": r["source_work_heat"]})
        if not r["is_nonstandard"]:
            names_per_book[wid] += 1
    prom = _percentile_weights(books)

    def _name_weight(r) -> float:
        wid = r["source_work_id"] or "__unknown__"
        base = prom.get(wid, 0.5)
        n = names_per_book.get(wid, 1) or 1
        return base * min(1.0, _PER_BOOK_CAP / n)    # 单本贡献封顶

    common_chars = _common_name_chars()
    surname_w: dict[str, float] = defaultdict(float)
    first_w: dict[str, float] = defaultdict(float)
    last_w: dict[str, float] = defaultdict(float)
    all_char_w: dict[str, float] = defaultdict(float)
    pair_w: dict[str, float] = defaultdict(float)
    struct_w = {"single_given": 0.0, "double_given": 0.0, "compound_surname": 0.0, "other": 0.0}
    rare_num = 0.0
    rare_den = 0.0
    standard_count = 0
    weighted_total = 0.0

    for r in rows:
        # 名长结构：单名/复姓也算（它们是结构维度，不算"非标准污染"）。
        w = _name_weight(r)
        if r["is_compound_surname"]:
            struct_w["compound_surname"] += w
        if r["is_single_given"]:
            struct_w["single_given"] += w
        elif r["given_name"] and len(r["given_name"]) == 2:
            struct_w["double_given"] += w
        elif not r["is_compound_surname"]:
            struct_w["other"] += w

        if r["is_nonstandard"]:
            continue                       # 规律分布排除非标准名
        standard_count += 1
        weighted_total += w
        if r["surname"]:
            surname_w[r["surname"]] += w
        given = r["given_name"] or ""
        for i, ch in enumerate(given):
            all_char_w[ch] += w
            if i == 0:
                first_w[ch] += w
            if i == len(given) - 1:
                last_w[ch] += w
            rare_den += w
            if ch not in common_chars:
                rare_num += w
        for i in range(len(given) - 1):
            pair_w[given[i:i + 2]] += w

    return {
        "available": standard_count > 0,
        "category": category,
        "platform": platform or "all",
        "name_count": standard_count,
        "book_count": len(books),
        "weighted_total": round(weighted_total, 2),
        "surname_distribution": _top(surname_w, top_k),
        "given_char_distribution": {
            "first": _top(first_w, top_k),
            "last": _top(last_w, top_k),
            "all": _top(all_char_w, top_k),
        },
        "name_length_structure": {
            k: (round(v / sum(struct_w.values()), 4) if sum(struct_w.values()) else 0.0)
            for k, v in struct_w.items()
        },
        "rare_char_rate": round(rare_num / rare_den, 4) if rare_den else 0.0,
        "typical_char_pairs": _top(pair_w, top_k),
        "note": _DISCLAIMER,
    }


def list_categories(db_path: str, *, platform: str | None = None) -> list[dict]:
    """人名库里有数据的题材（按名数排序），供 UI 选题材。"""
    where = "WHERE source_category != ''"
    params: list = []
    if platform:
        where += " AND source_platform = ?"
        params.append(platform)
    with sqlite3.connect(db_path) as con:
        from . import name_library
        name_library._ensure(con)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            f"SELECT source_category cat, COUNT(*) n, "
            f"SUM(CASE WHEN is_nonstandard=0 THEN 1 ELSE 0 END) std "
            f"FROM person_name_library {where} GROUP BY source_category ORDER BY n DESC",
            params,
        ).fetchall()
    return [{"category": r["cat"], "name_count": r["n"], "standard_count": r["std"]}
            for r in rows if r["cat"]]
