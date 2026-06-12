"""市场数据库 UPDATE (spec 市场数据库·机制4/机制5) — user edits + crawler lock."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storage.market_schema import create_all


@pytest.fixture
def db(tmp_path: Path) -> str:
    path = tmp_path / "crawler.db"
    con = sqlite3.connect(str(path))
    create_all(con)
    con.execute(
        "INSERT INTO novels (novel_uid, platform, platform_novel_id, author, "
        "author_norm, intro, main_category, status, total_words, url, "
        "created_date, last_seen_date) "
        "VALUES (1, 'qidian', 'q1', '老作者', '老作者', '旧简介', '玄幻', "
        "'ongoing', 100, 'http://x', date('now'), date('now'))",
    )
    con.execute(
        "INSERT INTO novel_titles (novel_uid, title, title_norm, is_primary, "
        "first_seen_date, last_seen_date) "
        "VALUES (1, '旧书名', '旧书名', 1, date('now'), date('now'))",
    )
    con.execute(
        "INSERT INTO first_n_chapters (chapter_id, novel_uid, chapter_num, "
        "chapter_title, chapter_content, word_count, publish_date) "
        "VALUES (10, 1, 1, '第一章', '旧正文', 3, date('now'))",
    )
    con.commit()
    con.close()
    return str(path)


@pytest.fixture
def client(db: str, monkeypatch) -> TestClient:
    monkeypatch.setattr(
        "ui.backend.app.utils.resolve_crawler_db_path", lambda: db,
    )
    from ui.backend.app.main import app
    return TestClient(app)


class TestNovelUpdate:
    def test_update_basic_info_title_tags(self, client, db) -> None:
        r = client.put("/api/db/novel/1", json={
            "title": "新书名", "author": "新作者", "intro": "新简介",
            "status": "completed", "total_words": 5000,
            "tags": ["系统流", "热血"],
        })
        assert r.status_code == 200
        body = r.json()
        assert body["novel"]["author"] == "新作者"
        assert "title" in body["patched"] and "tags" in body["patched"]
        with sqlite3.connect(db) as con:
            con.row_factory = sqlite3.Row
            n = con.execute("SELECT * FROM novels WHERE novel_uid=1").fetchone()
            assert n["author"] == "新作者"
            assert n["status"] == "completed"
            assert n["total_words"] == 5000
            t = con.execute(
                "SELECT title FROM novel_titles "
                "WHERE novel_uid=1 AND is_primary=1").fetchone()
            assert t["title"] == "新书名"
            tags = {r2[0] for r2 in con.execute(
                "SELECT t.tag_name FROM tags t "
                "JOIN novel_tag_map m ON m.tag_id=t.tag_id "
                "WHERE m.novel_uid=1")}
            assert tags == {"系统流", "热血"}

    def test_identity_fields_ignored(self, client, db) -> None:
        """Crawler-derived identity columns can't be reshaped — unknown
        keys are ignored; with nothing editable left the call is 400."""
        r = client.put("/api/db/novel/1", json={"platform": "fanqie"})
        assert r.status_code == 400
        r2 = client.put("/api/db/novel/1",
                        json={"platform": "fanqie", "author": "新作者"})
        assert r2.status_code == 200
        assert r2.json()["patched"] == ["author"]
        with sqlite3.connect(db) as con:
            plat, = con.execute(
                "SELECT platform FROM novels WHERE novel_uid=1").fetchone()
        assert plat == "qidian"

    def test_missing_novel_404(self, client) -> None:
        assert client.put(
            "/api/db/novel/999", json={"author": "x"}).status_code == 404


class TestChapterUpdate:
    def test_update_content_recounts_words(self, client, db) -> None:
        r = client.put("/api/db/chapter/10", json={
            "chapter_title": "第一章·改", "chapter_content": "修正后的正文内容",
        })
        assert r.status_code == 200
        with sqlite3.connect(db) as con:
            con.row_factory = sqlite3.Row
            c = con.execute(
                "SELECT * FROM first_n_chapters WHERE chapter_id=10").fetchone()
            assert c["chapter_title"] == "第一章·改"
            assert c["chapter_content"] == "修正后的正文内容"
            assert c["word_count"] == len("修正后的正文内容")

    def test_empty_body_rejected(self, client) -> None:
        assert client.put("/api/db/chapter/10", json={}).status_code == 400


class TestCrawlerLock:
    def test_user_write_blocked_while_crawler_holds_lock(
        self, db, monkeypatch,
    ) -> None:
        """机制4: crawler holds the write lock → user write waits, then
        surfaces 423 instead of corrupting / failing opaquely."""
        # Shrink the queue wait so the test is fast.
        import ui.backend.app.routers.market_db_api as api
        monkeypatch.setattr(api, "_WRITE_BUSY_TIMEOUT_MS", 300)
        monkeypatch.setattr(
            "ui.backend.app.utils.resolve_crawler_db_path", lambda: db,
        )
        from ui.backend.app.main import app
        client = TestClient(app)

        crawler = sqlite3.connect(db)
        crawler.execute("BEGIN IMMEDIATE")     # crawler mid-write
        try:
            r = client.put("/api/db/novel/1", json={"author": "并发作者"})
            assert r.status_code == 423
            assert "爬虫" in r.json()["detail"]
        finally:
            crawler.rollback()
            crawler.close()
        # Lock released → same write now queues through fine.
        r2 = client.put("/api/db/novel/1", json={"author": "并发作者"})
        assert r2.status_code == 200
