"""词表资源管理 (人名/常用词 overlay CRUD) + 高频词归类 + 趋势平滑.

Covers Round-9 additions:
- writable overlay (frozen-safe) add/remove/list with bundled tombstones,
- the /api/analysis/wordlist* endpoints,
- 高频词 carry per-work examples (点击下钻),
- the smoothed window-percentage trend (全部数据 假高增长 修复).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def wl(tmp_path, monkeypatch):
    monkeypatch.setenv("INKOCTOBOT_WORDLIST_DIR", str(tmp_path / "wl"))
    from ui.backend.app.services.market_extractor import wordlists as _wl
    _wl._invalidate()
    yield _wl
    _wl._invalidate()


class TestWordlistOverlay:
    def test_add_persists_and_is_idempotent(self, wl):
        assert wl.add_word("surnames", "测姓") is True
        assert "测姓" in wl.load_surnames()
        assert wl.add_word("surnames", "测姓") is False   # already effective

    def test_remove_user_added(self, wl):
        wl.add_word("common_words", "测词")
        assert "测词" in wl.load_common_words()
        assert wl.remove_word("common_words", "测词") is True
        assert "测词" not in wl.load_common_words()

    def test_tombstone_bundled_then_readd(self, wl):
        assert "怎么" in wl.load_common_words()           # bundled baseline
        assert wl.remove_word("common_words", "怎么") is True
        assert "怎么" not in wl.load_common_words()        # tombstoned
        assert wl.add_word("common_words", "怎么") is True
        assert "怎么" in wl.load_common_words()             # restored

    def test_list_words_search_and_user_flag(self, wl):
        wl.add_word("surnames", "独孤")
        data = wl.list_words("surnames", "独")
        hit = [it for it in data["items"] if it["word"] == "独孤"]
        assert hit and hit[0]["user"] is True
        # a bundled surname is flagged not-user
        allw = wl.list_words("surnames")
        bundled = [it for it in allw["items"] if it["word"] == "韩"]
        assert bundled and bundled[0]["user"] is False

    def test_unknown_list_raises(self, wl):
        with pytest.raises(ValueError):
            wl.add_word("nope", "x")


class TestWordlistGroupingIO:
    def test_surnames_grouped_by_country(self, wl):
        g = wl.list_grouped("surnames")
        groups = {x["group"]: x["items"] for x in g["groups"]}
        assert "中国" in groups and "日本" in groups
        assert any(it["word"] == "李" for it in groups["中国"])
        assert any(it["word"] == "上杉" for it in groups["日本"])

    def test_user_added_lands_in_own_group(self, wl):
        wl.add_word("surnames", "霍格沃茨姓")
        groups = {x["group"]: x["items"] for x in wl.list_grouped("surnames")["groups"]}
        assert "用户添加" in groups
        assert any(it["word"] == "霍格沃茨姓" and it["user"] for it in groups["用户添加"])

    def test_export_import_roundtrip(self, wl):
        wl.add_word("common_words", "唯一导出词")
        text = wl.export_text("common_words")
        assert "唯一导出词" in text
        # import into surnames a small txt with comments
        added = wl.import_text("surnames", "甲姓 乙姓\n# 注释行\n丙姓")
        assert added == 3
        assert "甲姓" in wl.load_surnames() and "丙姓" in wl.load_surnames()


class TestWordlistGroupedAPI:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        monkeypatch.setenv("INKOCTOBOT_WORDLIST_DIR", str(tmp_path / "wl"))
        from ui.backend.app.services.market_extractor import wordlists as _wl
        _wl._invalidate()
        from ui.backend.app.main import app
        return TestClient(app)

    def test_grouped_export_import_endpoints(self, client):
        g = client.get("/api/analysis/wordlist/grouped", params={"list": "surnames"})
        assert g.status_code == 200
        assert any(grp["group"] == "日本" for grp in g.json()["groups"])
        ex = client.get("/api/analysis/wordlist/export", params={"list": "surnames"})
        assert ex.status_code == 200 and "李" in ex.text
        imp = client.post("/api/analysis/wordlist/import",
                          json={"list": "common_words", "content": "导入词甲 导入词乙"})
        assert imp.json()["added"] == 2


class TestWordlistAPI:
    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        monkeypatch.setenv("INKOCTOBOT_WORDLIST_DIR", str(tmp_path / "wl"))
        from ui.backend.app.services.market_extractor import wordlists as _wl
        from ui.backend.app.services.market_extractor import opening_stats as _os
        _wl._invalidate()
        _os.reload_wordlists()
        from ui.backend.app.main import app
        return TestClient(app)

    def test_list_add_remove_roundtrip(self, client):
        r = client.get("/api/analysis/wordlist", params={"list": "surnames"})
        assert r.status_code == 200 and r.json()["label"] == "人名"
        assert client.post("/api/analysis/wordlist/add",
                           json={"list": "surnames", "word": "霍格"}).json()["added"]
        r2 = client.get("/api/analysis/wordlist",
                        params={"list": "surnames", "q": "霍格"})
        assert any(it["word"] == "霍格" for it in r2.json()["items"])
        assert client.post("/api/analysis/wordlist/remove",
                           json={"list": "surnames", "word": "霍格"}).json()["removed"]

    def test_bad_inputs(self, client):
        assert client.get("/api/analysis/wordlist", params={"list": "x"}).status_code == 400
        assert client.post("/api/analysis/wordlist/add",
                           json={"list": "x", "word": "a"}).status_code == 400
        assert client.post("/api/analysis/wordlist/add",
                           json={"list": "surnames", "word": ""}).status_code == 400


class TestOpeningStatsExamples:
    def test_top_words_carry_work_examples(self):
        from ui.backend.app.services.market_extractor import opening_stats as os_
        rows = [
            {"chapter_num": 1, "text": "青云宗的弟子在灵脉旁修炼觉醒。" * 20, "title": "甲书"},
            {"chapter_num": 1, "text": "灵脉之力涌动，青云宗弟子顿悟。" * 20, "title": "乙书"},
        ]
        st = os_.compute_opening_stats(rows)
        assert st["available"]
        tw = st["top_words"]
        assert tw and all("examples" in w for w in tw)
        for w in tw:
            for ex in w["examples"]:
                assert {"title", "count", "sentence"} <= set(ex)
                assert ex["title"] in {"甲书", "乙书"}
                assert ex["count"] >= 1


class TestWindowPct:
    def test_single_sparse_early_snapshot_not_inflated(self):
        from ui.backend.app.routers.analysis_api import _window_pct
        series = [10.0] + [1000.0] * 20      # one sparse early point
        naive = (series[-1] - series[0]) / series[0]   # 99.0 (=9900%)
        smoothed = _window_pct(series)
        assert smoothed is not None
        assert smoothed < 1.0 < naive        # massively damped

    def test_short_window_is_first_vs_last(self):
        from ui.backend.app.routers.analysis_api import _window_pct
        assert _window_pct([100.0, 150.0]) == pytest.approx(0.5)

    def test_none_without_baseline(self):
        from ui.backend.app.routers.analysis_api import _window_pct
        assert _window_pct([0, 0, 0]) is None
        assert _window_pct([5.0]) is None
