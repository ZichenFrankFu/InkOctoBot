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

    def test_nickname_kind(self) -> None:
        p = nl.derive_name_parts("翠翠")        # 叠字 → 昵称
        assert p["name_kind"] == "nickname"
        assert p["is_nonstandard"] == 1        # 昵称排除出中文取名规律

    def test_western_not_split_as_chinese(self) -> None:
        p = nl.derive_name_parts("乔治")        # 西方名，不可拆成 姓乔+名治
        assert p["name_kind"] == "western" and p["surname"] == ""

    def test_fu_surname_split(self) -> None:
        p = nl.derive_name_parts("傅红雪")      # 傅 是姓
        assert p["name_kind"] == "chinese" and p["surname"] == "傅" and p["given_name"] == "红雪"

    def test_japanese_kind(self) -> None:
        p = nl.derive_name_parts("山田太郎")
        assert p["name_kind"] == "japanese" and p["surname"] == "山田"


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

    def test_example_sentence_stored(self, db) -> None:
        nl.add_name(db, "李翠翠", source="ltp_ner", example_sentence="李翠翠笑着走来。")
        row = nl.search_names(db, "李翠翠")["items"][0]
        assert row["example_sentence"] == "李翠翠笑着走来。"

    def test_edit_name_rederives(self, db) -> None:
        row = nl.add_name(db, "王芳", source="user")
        edited = nl.edit_name(db, row["name_id"], full_name="欧阳芳",
                              example_sentence="欧阳芳点头。")
        assert edited["full_name"] == "欧阳芳"
        assert edited["surname"] == "欧阳" and edited["is_compound_surname"] == 1
        assert edited["example_sentence"] == "欧阳芳点头。"


class TestNerBackend:
    def test_no_ltp_degrades_to_seed_only(self) -> None:
        info = ner_backend.detect_ner_backend(refresh=True)
        # 本环境无 LTP/torch → seed-only（不抽名，仅静态种子库剔名）；不退 jieba。
        assert info.backend == "seed"
        assert info.uses_ltp is False
        # 真实 import 报错被暴露出来（不再吞掉），便于前端诊断。
        assert "ltp_import_error" in info.to_dict()

    def test_extract_returns_empty_without_ltp(self) -> None:
        # 用户明确要求：不用 jieba 做人名识别。无 LTP 时宁可返回空，也不冒充人名。
        ner_backend.detect_ner_backend(refresh=True)
        names = ner_backend.extract_per_names(
            ["李慕白对萧炎说道，韩立在一旁修炼，王林冷笑一声。"])
        assert names == []
        assert ner_backend.last_method() == "seed"


class TestTokenizerCompat:
    """新版 transformers 移除 batch_encode_plus → LTP NER 崩；验证兼容补丁。"""

    def test_shim_adds_batch_encode_plus(self, monkeypatch) -> None:
        import sys
        import types

        # 造一个「缺 batch_encode_plus」的 fast tokenizer 基类（模拟新版 transformers）。
        calls = {}

        class FakeBase:
            is_fast = True

            def __call__(self, batch, **kwargs):     # __call__ 仍可用
                calls["args"] = (batch, kwargs)
                return {"ok": True}

        fake_tub = types.ModuleType("transformers.tokenization_utils_base")
        fake_tub.PreTrainedTokenizerBase = FakeBase
        fake_root = types.ModuleType("transformers")
        fake_root.PreTrainedTokenizerBase = FakeBase
        monkeypatch.setitem(sys.modules, "transformers", fake_root)
        monkeypatch.setitem(sys.modules, "transformers.tokenization_utils_base", fake_tub)

        monkeypatch.setattr(ner_backend, "_tok_patched", False)
        assert not hasattr(FakeBase, "batch_encode_plus")
        ner_backend._ensure_tokenizer_compat()
        # 补丁挂上后：batch_encode_plus 委托给 __call__（fast 下产出 .encodings）。
        assert hasattr(FakeBase, "batch_encode_plus")
        out = FakeBase().batch_encode_plus(["李慕白说道"], max_length=512)
        assert out == {"ok": True}
        assert calls["args"][0] == ["李慕白说道"]
        assert calls["args"][1]["max_length"] == 512


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
