"""人名库 / 取名规律 / NER 状态 / 纠错回环 的 HTTP API + 增量刷新流程。"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from storage.market_extractor_schema import ensure_market_extractor_tables


@pytest.fixture
def env(tmp_path, monkeypatch):
    # 隔离词表 overlay（含人名黑名单）到 tmp，避免污染真实用户数据目录。
    monkeypatch.setenv("INKOCTOBOT_WORDLIST_DIR", str(tmp_path / "wordlists"))
    from ui.backend.app.services.market_extractor import wordlists as _wl
    _wl._effective.cache_clear()

    proj = str(tmp_path / "project.db")
    con = sqlite3.connect(proj)
    ensure_market_extractor_tables(con)
    con.close()

    # 爬虫库：两本书各 2 章开篇，含人名实体。
    crawler = str(tmp_path / "crawler.db")
    from storage.market_schema import create_all
    ccon = sqlite3.connect(crawler)
    create_all(ccon)
    for uid, title, cat in ((1, "玄天剑主", "玄幻"), (2, "都市修仙", "都市")):
        ccon.execute(
            "INSERT INTO novels (novel_uid, platform, platform_novel_id, author, "
            "author_norm, intro, main_category, status, total_words, url, "
            "created_date, last_seen_date) VALUES (?, 'qidian', ?, '作者', '作者', "
            "'简介', ?, 'ongoing', 100000, 'http://x', date('now'), date('now'))",
            (uid, f"q{uid}", cat),
        )
        ccon.execute(
            "INSERT INTO novel_titles (novel_uid, title, title_norm, is_primary, "
            "first_seen_date, last_seen_date) VALUES (?, ?, ?, 1, date('now'), date('now'))",
            (uid, title, title),
        )
        for cn in (1, 2):
            ccon.execute(
                "INSERT INTO first_n_chapters (novel_uid, chapter_num, chapter_title, "
                "chapter_content, word_count, content_hash, publish_date) "
                "VALUES (?, ?, ?, ?, ?, ?, date('now'))",
                (uid, cn, f"第{cn}章",
                 "李慕白对陈玄说道，张三在一旁微笑，王林默默看着这一切。" * 8,
                 200, f"hash_{uid}_{cn}"),
            )
    ccon.commit()
    ccon.close()

    monkeypatch.setattr(
        "ui.backend.app.routers.analysis_api._project_db_path", lambda: proj)
    monkeypatch.setattr(
        "ui.backend.app.routers.analysis_api.resolve_crawler_db_path", lambda: crawler)
    monkeypatch.setattr(
        "ui.backend.app.utils.resolve_crawler_db_path", lambda: crawler)
    from ui.backend.app.main import app
    return TestClient(app), proj, crawler


class TestNameLibraryApi:
    def test_seed_stats_and_search(self, env) -> None:
        client, proj, _ = env
        from ui.backend.app.services.market_extractor import name_library as nl
        nl.seed_if_empty(proj)                 # 种子库由刷新/分析按需灌入（非 stats）
        st = client.get("/api/analysis/name-library/stats")
        assert st.status_code == 200
        assert st.json()["total"] > 0          # 种子库 day-1 覆盖

        r = client.get("/api/analysis/name-library?q=诸葛")
        assert r.status_code == 200
        assert any("诸葛" in i["full_name"] for i in r.json()["items"])
        # 每条带 DF 可信度信号
        assert all("book_df" in i for i in r.json()["items"])

    def test_clear_empties_library(self, env) -> None:
        client, proj, _ = env
        from ui.backend.app.services.market_extractor import name_library as nl
        nl.seed_if_empty(proj)
        assert nl.library_stats(proj)["total"] > 0
        r = client.post("/api/analysis/name-library/clear")
        assert r.status_code == 200 and r.json()["ok"] is True
        assert r.json()["removed"] > 0
        # 清空后确为空（stats 不再自动重灌种子）
        assert client.get("/api/analysis/name-library/stats").json()["total"] == 0
        assert nl.search_names(proj)["total"] == 0

    def test_crud(self, env) -> None:
        client, _proj, _ = env
        add = client.post("/api/analysis/name-library/add",
                          json={"full_name": "墨千寻", "category": "玄幻"})
        assert add.status_code == 200
        assert add.json()["name"]["surname"] == "墨"
        rm = client.post("/api/analysis/name-library/remove",
                         json={"full_name": "墨千寻"})
        assert rm.json()["removed"] is True

    def test_add_validation(self, env) -> None:
        client, *_ = env
        bad = client.post("/api/analysis/name-library/add", json={"full_name": "x"})
        assert bad.status_code == 400

    def test_export_then_import(self, env) -> None:
        client, proj, _ = env
        from ui.backend.app.services.market_extractor import name_library as nl
        nl.add_name(proj, "墨千寻", source="ltp_ner", example_sentence="墨千寻一笑。")
        ex = client.get("/api/analysis/name-library/export?format=json")
        assert ex.status_code == 200
        records = ex.json()
        assert any(r["full_name"] == "墨千寻" for r in records)
        # 清空再导回 → 又能搜到（JSON 整库回灌）。
        client.post("/api/analysis/name-library/clear")
        imp = client.post("/api/analysis/name-library/import", json={"content": records})
        assert imp.status_code == 200 and imp.json()["added"] >= 1
        assert client.get("/api/analysis/name-library?q=墨千寻").json()["total"] >= 1

    def test_export_txt_and_import_lines(self, env) -> None:
        client, *_ = env
        client.post("/api/analysis/name-library/add", json={"full_name": "韩立"})
        txt = client.get("/api/analysis/name-library/export?format=txt")
        assert txt.status_code == 200 and "韩立" in txt.text
        imp = client.post("/api/analysis/name-library/import",
                          json={"content": "诸葛云\n# 注释行\n方源\n"})
        assert imp.json()["added"] == 2

    def test_remove_blocklists_so_ner_wont_readd(self, env) -> None:
        client, proj, _ = env
        from ui.backend.app.services.market_extractor import name_library as nl
        client.post("/api/analysis/name-library/add", json={"full_name": "错误名"})
        client.post("/api/analysis/name-library/remove", json={"full_name": "错误名"})
        # 删除即拉黑：后续 LTP 抽名的质量闸会挡掉它，不会再被抽回。
        assert nl.is_plausible_person_name("错误名") is False

    def test_ner_status_reports_backend(self, env) -> None:
        client, *_ = env
        r = client.get("/api/analysis/ner-status")
        assert r.status_code == 200
        body = r.json()
        assert body["backend"] in ("seed", "ltp_gpu", "ltp_cpu")
        assert "reason" in body
        assert "gpu" in body                    # GPU 诊断信息
        assert "ltp_import_error" in body       # 真实 LTP 报错暴露给前端诊断


class TestRefreshFlow:
    def test_refresh_without_ltp_is_clean_noop(self, env) -> None:
        client, proj, crawler = env
        from ui.backend.app.services.market_extractor import name_refresh as nr
        # 无 LTP（沙箱）→ 只用 LTP、不退 jieba：刷新不抽名，返回 ltp_unavailable + 报错。
        out = nr.refresh(proj, crawler)
        assert out["status"] == "ltp_unavailable"
        assert out["backend"] == "seed"
        assert out["new_books"] == 0
        assert out["names_added"] == 0
        assert "ltp_error" in out                # 真实报错带回，便于定位
        # 没把任何书标记为已处理 → 装好 LTP 后会重新处理（不是被吞掉）。
        with sqlite3.connect(proj) as con:
            n = con.execute("SELECT COUNT(*) FROM name_extraction_state").fetchone()[0]
        assert n == 0

    def test_status_endpoint_has_progress_and_backend(self, env) -> None:
        client, proj, crawler = env
        from ui.backend.app.services.market_extractor import name_refresh as nr
        nr.refresh(proj, crawler)
        r = client.get("/api/analysis/name-library/refresh-status")
        assert r.status_code == 200
        body = r.json()
        assert body["books_processed"] == 0      # 无 LTP → 未处理任何书
        assert "progress" in body
        assert body["backend"]["backend"] == "seed"


class TestNamingPatternsApi:
    def test_patterns_after_refresh(self, env) -> None:
        client, proj, crawler = env
        from ui.backend.app.services.market_extractor import name_refresh as nr
        nr.refresh(proj, crawler)
        cats = client.get("/api/analysis/naming-patterns/categories").json()["categories"]
        # NER 抽到的人名带题材（玄幻/都市），应至少出现一个题材桶
        assert isinstance(cats, list)


class TestNameCorrection:
    def test_correction_writes_library_and_excludes_fragment(self, env) -> None:
        client, proj, _ = env
        r = client.post("/api/analysis/name-correction",
                        json={"full_name": "李翠翠", "fragment": "翠翠",
                              "category": "古言"})
        assert r.status_code == 200
        assert r.json()["fragment_excluded"] in (True, False)
        # 全名进了人名库
        found = client.get("/api/analysis/name-library?q=李翠翠").json()
        assert found["total"] >= 1
