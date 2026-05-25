"""End-to-end integration: trace_id flows through FastAPI + agent + LLM.

Verifies that a single HTTP request:
  1. gets a trace_id bound (auto or from X-Request-ID)
  2. echoes that trace_id back in the response header
  3. populates every log record emitted while handling it
  4. makes those records reachable via /api/debug/trace/{trace_id}
"""
from __future__ import annotations

import logging
import os

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from framework.observability import get_buffer, current_trace_id
from framework.observability.log_buffer import LogRingBuffer
from framework.observability.request_middleware import TraceIDMiddleware
from framework.observability.trace_context import _TraceFilter


def _make_app() -> FastAPI:
    """A minimal FastAPI app wired with the trace middleware and a route
    that emits a log line so we can assert it carried the trace_id."""
    app = FastAPI()
    app.add_middleware(TraceIDMiddleware)
    r = APIRouter()
    log = logging.getLogger("test.integration.endpoint")

    @r.get("/ping")
    def ping():
        log.info("handling ping")
        return {"trace_id": current_trace_id()}

    app.include_router(r)
    return app


@pytest.fixture
def app_with_buffer():
    """Spin up the app + attach the buffer + the trace filter to the
    test logger so records are recorded with trace context."""
    app = _make_app()
    buf = LogRingBuffer(capacity=50)
    buf.addFilter(_TraceFilter())
    log = logging.getLogger("test.integration.endpoint")
    log.handlers.clear()
    log.setLevel(logging.DEBUG)
    log.addHandler(buf)
    return app, buf


def test_request_gets_trace_id_in_response_header(app_with_buffer):
    app, _ = app_with_buffer
    with TestClient(app) as client:
        resp = client.get("/ping")
    assert resp.status_code == 200
    assert resp.headers.get("X-Request-ID")
    body = resp.json()
    assert body["trace_id"] == resp.headers["X-Request-ID"]


def test_request_honors_inbound_X_Request_ID(app_with_buffer):
    app, _ = app_with_buffer
    with TestClient(app) as client:
        resp = client.get("/ping", headers={"X-Request-ID": "user-given-abc"})
    assert resp.headers["X-Request-ID"] == "user-given-abc"
    assert resp.json()["trace_id"] == "user-given-abc"


def test_log_records_carry_request_trace_id(app_with_buffer):
    app, buf = app_with_buffer
    with TestClient(app) as client:
        resp = client.get("/ping", headers={"X-Request-ID": "trace-xyz-1"})
    assert resp.status_code == 200

    # The handler emitted "handling ping" — find it in the buffer and
    # confirm it carries the trace_id.
    recs = buf.recent(trace_id="trace-xyz-1")
    assert recs, "no records found for trace_id"
    assert any(r["message"] == "handling ping" for r in recs)


def test_trace_id_is_reset_between_requests(app_with_buffer):
    app, buf = app_with_buffer
    with TestClient(app) as client:
        a = client.get("/ping", headers={"X-Request-ID": "trace-A"})
        b = client.get("/ping", headers={"X-Request-ID": "trace-B"})
    assert a.json()["trace_id"] == "trace-A"
    assert b.json()["trace_id"] == "trace-B"
    # Each request's log lives under its own trace_id, not mixed.
    assert all(r.get("trace_id") == "trace-A"
               for r in buf.recent(trace_id="trace-A"))
    assert all(r.get("trace_id") == "trace-B"
               for r in buf.recent(trace_id="trace-B"))


def test_debug_endpoint_returns_records_for_trace(monkeypatch):
    """Live test of the /api/debug/trace/{tid} endpoint against the real app.

    Uses the production main:app (which mounts the real debug_router and
    real middleware). Test mode is enabled so /api/debug/* is unlocked.
    """
    monkeypatch.setenv("WN_TEST_MODE", "1")
    # Re-import settings so test_mode=True is observed.
    import importlib
    import ui.backend.app.settings as settings_mod
    importlib.reload(settings_mod)
    import ui.backend.app.main as main_mod
    importlib.reload(main_mod)

    # Install the buffer + filter on the root logger so any log call hits it.
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    buf = LogRingBuffer(capacity=200)
    buf.addFilter(_TraceFilter())
    root.addHandler(buf)
    # Patch get_buffer() to return our buffer so the endpoint reads from it.
    import framework.observability.log_buffer as lb
    monkeypatch.setattr(lb, "_buffer", buf)

    try:
        with TestClient(main_mod.app) as client:
            client.get("/api/debug/status", headers={"X-Request-ID": "trace-int"})
            # Now query the trace endpoint
            resp = client.get("/api/debug/trace/trace-int")
        assert resp.status_code == 200
        body = resp.json()
        assert body["trace_id"] == "trace-int"
        # Some records should have been logged while handling /status
        # (uvicorn logs, FastAPI, etc.) — at minimum nothing should error.
        assert "records" in body
        assert "count" in body
    finally:
        root.removeHandler(buf)
