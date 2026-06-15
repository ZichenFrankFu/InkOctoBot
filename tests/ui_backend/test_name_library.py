"""人名库 + NER 后端 + 取名规律统计（spec §4/§5）。"""
from __future__ import annotations

import sqlite3

import pytest

from storage.market_extractor_schema import ensure_market_extractor_tables
from ui.backend.app.services.market_extractor import (
    name_library as nl,
    naming_patterns as npat,
    ner_backend,
)


@pytest.fixture
def db(tmp_path):
    p = str(tmp_path / "proj.db")
    con = sqlite3.connect(p)
    ensure_market_extractor_tables(con)
    con.close()
    return p


class TestDerive:
    def test_full_name_is_authoritative_parts_are_derived(self) -> None:
        assert nl.derive_name_parts("李翠翠")["surname"] == "李"
        assert nl.derive_name_parts("李翠翠")["given_name"] == "翠翠"

    def test_compound_surname(self) -> None:
        p = nl.derive_name_parts("上官婉儿")
        assert p["surname"] == "上官" and p["surname_kind"] == "compound"
        assert p["is_compound_surname"] == 1

    def test_single_given_flagged(self) -> None:
        assert nl.derive_name_parts("李白")["is_single_given"] == 1

    def test_nonstandard_nickname(self) -> None:
        p = nl.derive_name_parts("翠翠")        # 叠字、无姓 → 非标准
        assert p["is_nonstandard"] == 1
        assert "叠字" in p["nonstandard_reason"] or "姓氏" in p["nonstandard_reason"]


class TestSeedAndCrud:
    def test_seed_then_search_and_df(self, db) -> None:
        assert nl.seed_if_empty(db) > 0
        assert nl.seed_if_empty(db) == 0          # 幂等
        res = nl.search_names(db, "上官")
        assert res["total"] >= 1
        # add with DF increment
        nl.add_name(db, "测试名", source="user", count_df=True)
        nl.add_name(db, "测试名", source="user", count_df=True)
        row = nl.search_names(db, "测试名")["items"][0]
        assert row["book_df"] == 2                # DF 按 book 累计
        assert nl.remove_name(db, "测试名") is True

    def test_cached_sets_and_invalidation(self, db) -> None:
        nl.seed_if_empty(db)
        full, given = nl.cached_name_sets(db)
        assert "诸葛亮" in full
        nl.add_name(db, "韩立", source="user")
        full2, _ = nl.cached_name_sets(db)
        assert "韩立" in full2                     # 缓存随写入失效重建

    def test_rebuild_derived(self, db) -> None:
        nl.seed_if_empty(db)
        assert nl.rebuild_derived(db) > 0


class TestNerBackend:
    def test_degrades_to_jieba_without_ltp(self) -> None:
        info = ner_backend.detect_ner_backend(refresh=True)
        # 本环境无 LTP/torch → jieba 兜底
        assert info.backend == "jieba"
        assert info.uses_ltp is False
        assert "jieba" in info.reason or "LTP" in info.reason

    def test_extract_per_jieba(self) -> None:
        names = ner_backend.extract_per_names(["李慕白对陈玄说道，张三丰在一旁微笑。"])
        # jieba nr 至少能识出部分人名（具体取决于词典，断言非异常 + 列表）
        assert isinstance(names, list)


class TestNamingPatterns:
    def _populate(self, db) -> None:
        # 两本书：爆款(rank1,heat高,角色多) + 普通(rank50,heat低)。
        for i in range(12):
            nl.add_name(db, f"萧{chr(0x708e + i)}", source="ltp_ner",
                        work_id="hit", work_title="爆款", category="玄幻",
                        platform="qidian", rank=1, heat=9000, count_df=True)
        for fn in ["王林", "韩立", "方源"]:
            nl.add_name(db, fn, source="ltp_ner", work_id="normal",
                        work_title="普通", category="玄幻", platform="qidian",
                        rank=50, heat=100, count_df=True)

    def test_per_book_cap_prevents_domination(self, db) -> None:
        self._populate(db)
        out = npat.compute_for_category(db, "玄幻", platform="qidian")
        assert out["available"] is True
        assert out["book_count"] == 2
        # 单本封顶：爆款贡献被限，普通书的姓氏（王/韩/方）也能进分布
        surnames = {s["key"] for s in out["surname_distribution"]}
        assert surnames & {"王", "韩", "方"}
        # 展示加权值
        assert all("weight" in s and "pct" in s for s in out["surname_distribution"])
        # 名长结构 + 描述性声明
        assert "name_length_structure" in out
        assert "成功公式" in out["note"]

    def test_categories_listing(self, db) -> None:
        self._populate(db)
        cats = npat.list_categories(db)
        assert any(c["category"] == "玄幻" for c in cats)
