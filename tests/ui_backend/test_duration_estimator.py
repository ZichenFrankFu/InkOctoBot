"""性能预期 (spec 5.7.2) — ETA from same-hardware history with defaults."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ui.backend.app.services import duration_estimator as de


@pytest.fixture
def db(tmp_path: Path) -> str:
    return str(tmp_path / "perf.db")


@pytest.fixture
def cpu(monkeypatch):
    monkeypatch.setattr(de, "hardware_key", lambda: "cpu")


class TestEstimate:
    def test_cold_start_uses_default(self, db, cpu) -> None:
        out = de.estimate(db, "embedding_batch_reindex", 100)
        assert out["basis"] == "default"
        assert out["samples"] == 0
        assert out["estimated_seconds"] == pytest.approx(
            de.DEFAULT_PER_ITEM_SECONDS["embedding_batch_reindex"] * 100,
        )
        assert out["display"].startswith("约")

    def test_history_median_wins(self, db, cpu) -> None:
        # 3 runs: 0.5s/item, 1.0s/item, 10s/item (outlier) → median 1.0
        de.record_duration(db, "reference_index_l3", 10, 5.0)
        de.record_duration(db, "reference_index_l3", 10, 10.0)
        de.record_duration(db, "reference_index_l3", 10, 100.0)
        out = de.estimate(db, "reference_index_l3", 20)
        assert out["basis"] == "history"
        assert out["samples"] == 3
        assert out["per_item_seconds"] == pytest.approx(1.0)
        assert out["estimated_seconds"] == pytest.approx(20.0)

    def test_hardware_buckets_isolated(self, db, monkeypatch) -> None:
        """机制3: GPU history must not leak into CPU estimates."""
        de.record_duration(db, "t", 10, 1.0, hw_key="cuda:rtx4090")
        monkeypatch.setattr(de, "hardware_key", lambda: "cpu")
        out = de.estimate(db, "t", 10, default_per_item=2.0)
        assert out["basis"] == "default"
        assert out["estimated_seconds"] == pytest.approx(20.0)
        out2 = de.estimate(db, "t", 10, hw_key="cuda:rtx4090")
        assert out2["basis"] == "history"

    def test_record_never_raises(self, cpu) -> None:
        de.record_duration("/nonexistent/dir/x.db", "t", 5, 1.0)
        de.record_duration("ignored.db", "t", 0, 1.0)   # invalid counts no-op


class TestRemaining:
    def test_remaining_scales_with_progress(self) -> None:
        out = de.remaining(100.0, done=25, total=100)
        assert out["remaining_seconds"] == pytest.approx(75.0)
        assert out["progress"] == pytest.approx(0.25)
        done = de.remaining(100.0, done=100, total=100)
        assert done["remaining_seconds"] == 0


class TestFormat:
    def test_human_display(self) -> None:
        assert de.format_duration(45) == "约 45 秒"
        assert de.format_duration(150) == "约 2 分 30 秒"
        assert de.format_duration(3660) == "约 1 小时 1 分"


class TestAPI:
    @pytest.fixture
    def client(self, db, monkeypatch) -> TestClient:
        monkeypatch.setattr(
            "ui.backend.app.routers.perf_api._db", lambda: db,
        )
        monkeypatch.setattr(de, "hardware_key", lambda: "cpu")
        from ui.backend.app.main import app
        return TestClient(app)

    def test_estimate_endpoint(self, client) -> None:
        r = client.get("/api/perf/estimate", params={
            "task_type": "reference_feature_extract", "item_count": 4,
        })
        assert r.status_code == 200
        body = r.json()
        assert body["estimated_seconds"] == pytest.approx(180.0)
        assert body["display"] == "约 3 分"

    def test_remaining_endpoint(self, client) -> None:
        r = client.get("/api/perf/remaining", params={
            "estimated_seconds": 60, "done": 30, "total": 60,
        })
        assert r.json()["remaining_seconds"] == pytest.approx(30.0)
