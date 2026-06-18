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

    def test_nickname_kind_removed(self) -> None:
        # 昵称分类已下线：不再产出 nickname 这个 name_kind。
        assert nl.derive_name_parts("翠翠")["name_kind"] != "nickname"

    def test_japanese_without_known_surname(self) -> None:
        # 无已知日文姓前缀，但有强日文信号（太郎/之介）→ 日文名，不留在中文区。
        assert nl.derive_name_parts("太郎")["name_kind"] == "japanese"
        assert nl.derive_name_parts("健一郎")["name_kind"] == "japanese"
        assert nl.derive_name_parts("龙之介")["name_kind"] == "japanese"
        # 中文姓开头的 郎 名不误判（武大郎）。
        assert nl.derive_name_parts("武大郎")["name_kind"] == "chinese"

    def test_western_two_char_translit(self) -> None:
        # 词典外的 2 字纯音译名也判西方（汉斯/丽莎），不再漏进中文区。
        assert nl.derive_name_parts("汉斯")["name_kind"] == "western"
        assert nl.derive_name_parts("丽莎")["name_kind"] == "western"
        # 金丹（金 是中文姓）/查克拉（查 非音译字）仍不误判西方。
        assert nl.derive_name_parts("金丹")["name_kind"] == "chinese"
        assert nl.derive_name_parts("查克拉")["name_kind"] == "chinese"

    def test_gender_inference(self) -> None:
        assert nl.derive_name_parts("李伟")["gender"] == "male"
        assert nl.derive_name_parts("王芳婷")["gender"] == "female"
        assert nl.derive_name_parts("山田太郎")["gender"] == "male"     # 日文收尾字
        assert nl.derive_name_parts("乔治")["gender"] == "male"          # 西方词典
        assert nl.derive_name_parts("玛丽")["gender"] == "female"

    def test_western_not_split_as_chinese(self) -> None:
        p = nl.derive_name_parts("乔治")        # 西方名，不可拆成 姓乔+名治
        assert p["name_kind"] == "western" and p["surname"] == ""

    def test_fu_surname_split(self) -> None:
        p = nl.derive_name_parts("傅红雪")      # 傅 是姓
        assert p["name_kind"] == "chinese" and p["surname"] == "傅" and p["given_name"] == "红雪"

    def test_japanese_kind(self) -> None:
        p = nl.derive_name_parts("山田太郎")
        assert p["name_kind"] == "japanese" and p["surname"] == "山田"

    def test_nickname_forms_rejected_not_stored(self) -> None:
        # 昵称已下线：称谓/叠字/小阿老形态在抽名质量闸被剔除，不入库。
        for n in ("王总", "李叔", "统子哥", "张姐", "翠翠", "小明", "老李"):
            assert nl.is_plausible_person_name(n) is False, n

    def test_translit_noun_not_western(self) -> None:
        # 「金丹」「查克拉」是网文名词，不应再被音译占比误判成西方名（旧 bug）。
        assert nl.derive_name_parts("金丹")["name_kind"] != "western"
        assert nl.derive_name_parts("查克拉")["name_kind"] != "western"
        # 真·西方名仍判对（词典命中）。
        assert nl.derive_name_parts("乔治")["name_kind"] == "western"


class TestPlausibleNameGate:
    """LTP 抽名质量闸：剔除设定/物品/门派/动词类误报（spec 修复项）。"""

    def test_rejects_org_and_noun_false_positives(self) -> None:
        for bad in ("唐门", "宗门", "刘氏", "甲胄", "金丹", "查克拉", "启禀", "少年", "师兄"):
            assert nl.is_plausible_person_name(bad) is False, bad

    def test_accepts_real_names(self) -> None:
        for good in ("李慕白", "韩立", "萧炎", "傅红雪", "上官婉儿"):
            assert nl.is_plausible_person_name(good) is True, good


class TestImportExport:
    def test_roundtrip_preserves_kind_gender_example(self, db) -> None:
        nl.add_name(db, "李慕白", source="ltp_ner",
                    example_sentence="李慕白说道。", category="玄幻")
        row = nl.add_name(db, "欧阳菲", source="user")
        nl.edit_name(db, row["name_id"], gender="female")     # 手动标性别
        exported = nl.export_all(db)
        assert any(r["full_name"] == "欧阳菲" and r["gender"] == "female"
                   for r in exported)

        # 灌进新库，分类/性别/例句/题材应原样保留。
        import sqlite3 as _sql
        from storage.market_extractor_schema import ensure_market_extractor_tables
        p2 = db + ".2.db"
        con = _sql.connect(p2)
        ensure_market_extractor_tables(con)
        con.close()
        res = nl.import_records(p2, exported)
        assert res["added"] >= 2
        got = nl.search_names(p2, "欧阳菲")["items"][0]
        assert got["gender"] == "female"
        lmb = nl.search_names(p2, "李慕白")["items"][0]
        assert lmb["example_sentence"] == "李慕白说道。" and lmb["source_category"] == "玄幻"

    def test_import_plain_text_lines(self, db) -> None:
        res = nl.import_records(db, [{"full_name": "韩立"}, {"full_name": "x"}])
        assert res["added"] == 1 and res["skipped"] == 1   # 'x' 非法被跳过


class TestFragmentDedup:
    def test_fragment_not_added_when_fullname_exists(self, db) -> None:
        nl.add_name(db, "王一涵", source="ltp_ner", count_df=True)
        # 去姓的名 / 截断前缀都不再各占一条
        assert nl.add_name(db, "一涵", source="ltp_ner") is None
        assert nl.add_name(db, "王一", source="ltp_ner") is None
        assert [i["full_name"] for i in nl.search_names(db)["items"]] == ["王一涵"]

    def test_fullname_absorbs_existing_auto_fragments(self, db) -> None:
        nl.add_name(db, "一涵", source="ltp_ner", count_df=True)
        nl.add_name(db, "王一", source="ltp_ner", count_df=True)
        nl.add_name(db, "王一涵", source="ltp_ner", count_df=True)
        assert [i["full_name"] for i in nl.search_names(db)["items"]] == ["王一涵"]

    def test_manual_add_bypasses_dedup(self, db) -> None:
        nl.add_name(db, "王一涵", source="ltp_ner")
        # 用户手动/导入：完全尊重输入，不去碎片
        assert nl.add_name(db, "一涵", source="user", dedupe_fragments=False)
        names = {i["full_name"] for i in nl.search_names(db)["items"]}
        assert names == {"王一涵", "一涵"}

    def test_independent_name_not_harmed(self, db) -> None:
        nl.add_name(db, "王一涵", source="ltp_ner")
        nl.add_name(db, "李芳", source="ltp_ner")    # 不同姓的独立名
        assert nl.search_names(db, "李芳")["total"] == 1

    def test_user_fragment_preserved_when_fullname_arrives(self, db) -> None:
        nl.add_name(db, "一涵", source="user")        # 用户加的碎片
        nl.add_name(db, "王一涵", source="ltp_ner")   # 长名到来：只清自动碎片，不动用户的
        names = {i["full_name"] for i in nl.search_names(db)["items"]}
        assert "一涵" in names and "王一涵" in names


class TestNameGenerator:
    def test_recombines_with_surname_constraint(self, db) -> None:
        from ui.backend.app.services.market_extractor import name_generator as ng
        nl.seed_if_empty(db)
        out = ng.generate_names(db, kind="chinese", surname="慕容", count=6)
        assert out["count"] >= 1
        assert all(n["full_name"].startswith("慕容") for n in out["names"])
        assert all(n["name_kind"] == "chinese" for n in out["names"])

    def test_gender_constraint_and_uniqueness(self, db) -> None:
        from ui.backend.app.services.market_extractor import name_generator as ng
        nl.seed_if_empty(db)
        out = ng.generate_names(db, kind="chinese", gender="female", count=10)
        names = [n["full_name"] for n in out["names"]]
        assert len(names) == len(set(names))            # 不重复
        # 女性约束下不应出现明显男性字（启发式，允许中性）。
        assert not any("伟" in n or "刚" in n for n in names)


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
