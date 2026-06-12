"""compute_cache — instant-response single-flight cache (恶性 bug 修复:
多次切换页面后全站无限加载 = 重型同步端点耗尽 anyio 线程池).

Covers the protocol (empty / computing / ready / stale / error), the
single-flight guarantee, and the two wired endpoints:
``/api/analysis/run`` and ``/api/db/opening_nlp_analysis``.
"""
from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ui.backend.app.services import compute_cache


def _wait_ready(db: str, key: str, version: str, fn, timeout: float = 5.0):
    """Poll get_or_compute until the background run lands."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        out = compute_cache.get_or_compute(db, key, version, fn)
        if out["state"] == "ready" and not out["computing"]:
            return out
        if out["state"] == "error":
            return out
        time.sleep(0.02)
    raise AssertionError(f"compute for {key} never finished")


@pytest.fixture(autouse=True)
def _reset():
    compute_cache.reset_for_tests()
    yield
    compute_cache.reset_for_tests()


@pytest.fixture
def db(tmp_path: Path) -> str:
    return str(tmp_path / "cache.db")


class TestProtocol:
    def test_first_call_starts_background_and_returns_computing(self, db):
        started = threading.Event()

        def fn():
            started.set()
            return {"n": 1}

        out = compute_cache.get_or_compute(db, "k", "v1", fn)
        assert out["state"] == "computing"
        assert started.wait(2.0)
        ready = _wait_ready(db, "k", "v1", fn)
        assert ready["payload"] == {"n": 1}
        assert ready["stale"] is False

    def test_cached_only_never_starts_work(self, db):
        ran = threading.Event()
        out = compute_cache.get_or_compute(
            db, "k", "v1", lambda: ran.set() or {}, cached_only=True,
        )
        assert out["state"] == "empty"
        time.sleep(0.1)
        assert not ran.is_set()

    def test_ready_payload_survives_new_module_state(self, db):
        _wait_ready(db, "k", "v1", lambda: {"n": 1})
        # Simulate process restart: in-memory flags gone, DB row stays.
        compute_cache.reset_for_tests()
        out = compute_cache.get_or_compute(
            db, "k", "v1", lambda: {"n": 2}, cached_only=True,
        )
        assert out["state"] == "ready"
        assert out["payload"] == {"n": 1}

    def test_stale_flag_on_version_change_without_autorun(self, db):
        _wait_ready(db, "k", "v1", lambda: {"n": 1})
        ran = threading.Event()
        out = compute_cache.get_or_compute(
            db, "k", "v2", lambda: ran.set() or {"n": 2},
        )
        # Old payload served instantly with the 数据已更新 flag; no
        # auto-recompute — the user decides when to 重新分析.
        assert out["state"] == "ready"
        assert out["payload"] == {"n": 1}
        assert out["stale"] is True
        time.sleep(0.1)
        assert not ran.is_set()

    def test_refresh_recomputes_in_background(self, db):
        _wait_ready(db, "k", "v1", lambda: {"n": 1})
        out = compute_cache.get_or_compute(
            db, "k", "v2", lambda: {"n": 2}, refresh=True,
        )
        assert out["state"] == "ready"          # old payload, instantly
        assert out["payload"] == {"n": 1}
        assert out["computing"] is True
        ready = _wait_ready(db, "k", "v2", lambda: {"n": 2})
        assert ready["payload"] == {"n": 2}
        assert ready["stale"] is False

    def test_single_flight(self, db):
        runs: list[int] = []
        gate = threading.Event()

        def slow():
            runs.append(1)
            gate.wait(2.0)
            return {"ok": True}

        for _ in range(8):
            out = compute_cache.get_or_compute(db, "k", "v1", slow)
            assert out["state"] == "computing"
        gate.set()
        _wait_ready(db, "k", "v1", slow)
        assert len(runs) == 1

    def test_error_state_then_refresh_retries(self, db):
        def boom():
            raise RuntimeError("pipeline exploded")

        compute_cache.get_or_compute(db, "k", "v1", boom)
        deadline = time.time() + 5
        out = None
        while time.time() < deadline:
            out = compute_cache.get_or_compute(db, "k", "v1", boom, cached_only=True)
            if out["state"] == "error":
                break
            time.sleep(0.02)
        assert out and out["state"] == "error"
        assert "exploded" in out["error"]
        # Plain polls must NOT hot-loop a crashing job — only an
        # explicit refresh clears the error and retries.
        kicked = compute_cache.get_or_compute(
            db, "k", "v1", lambda: {"n": 3}, refresh=True,
        )
        assert kicked["state"] == "computing"
        ready = _wait_ready(db, "k", "v1", lambda: {"n": 3}, timeout=5)
        assert ready["payload"] == {"n": 3}


class TestWiredEndpoints:
    @pytest.fixture
    def client(self, tmp_path: Path, monkeypatch):
        from storage.market_schema import create_all

        crawler = tmp_path / "crawler.db"
        con = sqlite3.connect(str(crawler))
        create_all(con)
        con.execute(
            "INSERT INTO novels (novel_uid, platform, platform_novel_id, "
            "author, author_norm, intro, main_category, status, total_words, "
            "url, created_date, last_seen_date) "
            "VALUES (1, 'qidian', 'q1', 'a', 'a', 'i', '玄幻', 'ongoing', "
            "100000, 'http://x', date('now'), date('now'))",
        )
        con.execute(
            "INSERT INTO first_n_chapters (novel_uid, chapter_num, "
            "chapter_title, chapter_content, word_count, publish_date) "
            "VALUES (1, 1, '第一章', ?, 400, date('now'))",
            ("他举起了手中的剑，山门外忽然传来钟声！这是为什么？" * 20,),
        )
        con.commit()
        con.close()

        proj = tmp_path / "project.db"
        from storage.project_schema import ensure_creation_tables
        pcon = sqlite3.connect(str(proj))
        ensure_creation_tables(pcon)
        pcon.commit()
        pcon.close()

        monkeypatch.setattr(
            "ui.backend.app.utils.resolve_crawler_db_path",
            lambda: str(crawler),
        )
        monkeypatch.setattr(
            "ui.backend.app.services.project_paths.get_db_path",
            lambda: str(proj),
        )
        from ui.backend.app.main import app
        return TestClient(app)

    def test_opening_nlp_lazy_mount_then_refresh(self, client):
        # 懒加载: page mount asks cached_only → empty, instantly, no job.
        r = client.get("/api/db/opening_nlp_analysis",
                       params={"cached_only": "true"})
        assert r.status_code == 200
        assert r.json()["state"] == "empty"

        # User clicks 运行分析 → computing → poll to ready.
        r = client.get("/api/db/opening_nlp_analysis",
                       params={"refresh": "true"})
        assert r.json()["state"] in ("computing", "ready")
        deadline = time.time() + 10
        body = None
        while time.time() < deadline:
            body = client.get("/api/db/opening_nlp_analysis").json()
            if body["state"] == "ready" and not body["computing"]:
                break
            time.sleep(0.05)
        assert body and body["state"] == "ready"
        payload = body["payload"]
        assert payload["available"] is True
        assert payload["spec_stats"]["available"] is True
        assert body["stale"] is False

    def test_analysis_run_returns_envelope_instantly(self, client):
        t0 = time.time()
        r = client.get("/api/analysis/run", params={"cached_only": "true"})
        assert r.status_code == 200
        assert time.time() - t0 < 2.0
        assert r.json()["state"] in ("empty", "ready")
