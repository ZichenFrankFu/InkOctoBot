"""暂停 / 恢复 / 中断 (spec LLM交互·机制6) — session pause endpoints."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client() -> TestClient:
    from ui.backend.app.main import app
    return TestClient(app)


@pytest.fixture
def session(client):
    """A fake in-memory running session with a live pause_event."""
    from ui.backend.app.routers import generation_api as g

    async def _mk():
        return g._new_running_pause_event()
    ev = asyncio.get_event_loop_policy().new_event_loop().run_until_complete(_mk())
    sid = "sess_test_pause"
    g._active_sessions[sid] = {
        "status": "running", "events": [], "text": "已生成的部分内容",
        "current_step": "actor_agents", "waiting_confirm": False,
        "confirm_event": None, "confirm_data": None, "task": None,
        "pause_event": ev, "paused": False, "result": None,
    }
    yield sid
    g._active_sessions.pop(sid, None)


class TestPauseResume:
    def test_pause_then_status_then_resume(self, client, session) -> None:
        from ui.backend.app.routers import generation_api as g
        r = client.post(f"/api/generation/pause/{session}")
        assert r.status_code == 200 and r.json()["paused"] is True
        s = g._active_sessions[session]
        assert s["paused"] is True
        assert not s["pause_event"].is_set()
        st = client.get(f"/api/generation/status/{session}").json()
        assert st["paused"] is True

        r2 = client.post(f"/api/generation/resume/{session}")
        assert r2.json()["paused"] is False
        assert s["pause_event"].is_set()
        assert client.get(
            f"/api/generation/status/{session}").json()["paused"] is False

    def test_missing_session_404(self, client) -> None:
        assert client.post("/api/generation/pause/nope").status_code == 404
        assert client.post("/api/generation/resume/nope").status_code == 404

    def test_stop_releases_pause(self, client, session) -> None:
        """中断 a PAUSED session must release the checkpoint so the
        background task can observe the abort (no dead hang)."""
        from ui.backend.app.routers import generation_api as g
        client.post(f"/api/generation/pause/{session}")
        r = client.post(f"/api/generation/stop/{session}")
        assert r.status_code == 200
        s = g._active_sessions[session]
        assert s["pause_event"].is_set()
        assert s["paused"] is False
        assert s["status"] == "complete"
        # 失败措施·机制3: partial text retained after interrupt.
        assert s["text"] == "已生成的部分内容"


class TestCheckpoint:
    @pytest.mark.asyncio
    async def test_checkpoint_blocks_until_resume(self) -> None:
        from ui.backend.app.routers import generation_api as g
        sid = "sess_ckpt"
        ev = g._new_running_pause_event()
        g._active_sessions[sid] = {
            "status": "running", "events": [], "current_step": "writer",
            "pause_event": ev, "paused": False, "text": "",
        }
        try:
            # Running → checkpoint returns immediately.
            await asyncio.wait_for(g._pause_checkpoint(sid), timeout=1)
            # Paused → checkpoint blocks until resume.
            ev.clear()
            g._active_sessions[sid]["paused"] = True
            task = asyncio.create_task(g._pause_checkpoint(sid))
            await asyncio.sleep(0.05)
            assert not task.done()
            types = [e.get("type") for e in g._active_sessions[sid]["events"]]
            assert "paused" in types
            ev.set()
            await asyncio.wait_for(task, timeout=1)
            types = [e.get("type") for e in g._active_sessions[sid]["events"]]
            assert "resumed" in types
        finally:
            g._active_sessions.pop(sid, None)
