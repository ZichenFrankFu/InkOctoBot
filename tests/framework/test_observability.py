"""Tests for framework.observability.

Covers trace_context propagation, log buffer recording + filtering,
and the @traced decorator on both sync and async functions.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from framework.observability import (
    LogRingBuffer,
    bind_session,
    bind_trace,
    current_session_id,
    current_trace_id,
    new_trace_id,
    trace_scope,
    traced,
)
from framework.observability.trace_context import _TraceFilter


# ── trace_context ─────────────────────────────────────────────────


def test_new_trace_id_is_12_hex_chars():
    tid = new_trace_id()
    assert len(tid) == 12
    int(tid, 16)  # parses as hex


def test_trace_scope_binds_and_restores():
    assert current_trace_id() == ""
    with trace_scope(trace_id="abc123", session_id="sess_x") as tid:
        assert tid == "abc123"
        assert current_trace_id() == "abc123"
        assert current_session_id() == "sess_x"
    assert current_trace_id() == ""
    assert current_session_id() == ""


def test_trace_scope_auto_generates_id_when_omitted():
    with trace_scope() as tid:
        assert len(tid) == 12
        assert current_trace_id() == tid


def test_trace_scope_is_reentrant():
    with trace_scope(trace_id="outer"):
        assert current_trace_id() == "outer"
        with trace_scope(trace_id="inner"):
            assert current_trace_id() == "inner"
        assert current_trace_id() == "outer"


def test_trace_propagates_to_async_tasks():
    async def child() -> str:
        return current_trace_id()

    async def main() -> str:
        with trace_scope(trace_id="t-async"):
            return await asyncio.create_task(child())

    assert asyncio.run(main()) == "t-async"


def test_trace_filter_injects_trace_id_on_records():
    log = logging.getLogger("test.filter.trace")
    log.handlers.clear()
    log.setLevel(logging.DEBUG)
    captured: list[logging.LogRecord] = []

    class CaptureHandler(logging.Handler):
        def emit(self, record):
            captured.append(record)

    h = CaptureHandler()
    h.addFilter(_TraceFilter())
    log.addHandler(h)

    with trace_scope(trace_id="t-inject", session_id="s-inject"):
        log.info("hi")
    log.info("bye")

    assert len(captured) == 2
    assert captured[0].trace_id == "t-inject"
    assert captured[0].session_id == "s-inject"
    assert captured[1].trace_id == ""
    assert captured[1].session_id == ""


# ── log_buffer ────────────────────────────────────────────────────


def test_buffer_records_and_returns_recent():
    buf = LogRingBuffer(capacity=10)
    log = logging.getLogger("test.buf.basic")
    log.handlers.clear()
    log.setLevel(logging.DEBUG)
    log.addHandler(buf)

    log.info("first")
    log.warning("second")
    recs = buf.recent(limit=10)
    assert [r["message"] for r in recs] == ["first", "second"]
    assert recs[1]["level"] == "WARNING"


def test_buffer_respects_capacity():
    buf = LogRingBuffer(capacity=3)
    log = logging.getLogger("test.buf.cap")
    log.handlers.clear()
    log.setLevel(logging.DEBUG)
    log.addHandler(buf)

    for i in range(10):
        log.info("msg %d", i)
    recs = buf.recent(limit=10)
    assert len(recs) == 3
    assert [r["message"] for r in recs] == ["msg 7", "msg 8", "msg 9"]


def test_buffer_filters_by_level_and_logger():
    buf = LogRingBuffer(capacity=100)
    log_a = logging.getLogger("test.buf.filter.a")
    log_b = logging.getLogger("test.buf.filter.b")
    for lg in (log_a, log_b):
        lg.handlers.clear()
        lg.setLevel(logging.DEBUG)
        lg.addHandler(buf)
    log_a.info("a info")
    log_a.warning("a warn")
    log_b.info("b info")

    by_warn = buf.recent(level="WARNING")
    assert [r["message"] for r in by_warn] == ["a warn"]

    by_b = buf.recent(logger_prefix="test.buf.filter.b")
    assert [r["message"] for r in by_b] == ["b info"]


def test_buffer_filters_by_trace_id():
    buf = LogRingBuffer(capacity=100)
    log = logging.getLogger("test.buf.trace")
    log.handlers.clear()
    log.setLevel(logging.DEBUG)
    buf.addFilter(_TraceFilter())
    log.addHandler(buf)

    with trace_scope(trace_id="t-A"):
        log.info("inside A")
    with trace_scope(trace_id="t-B"):
        log.info("inside B")
    log.info("outside")

    a_only = buf.recent(trace_id="t-A")
    assert [r["message"] for r in a_only] == ["inside A"]


def test_buffer_clear_empties_records():
    buf = LogRingBuffer(capacity=10)
    log = logging.getLogger("test.buf.clear")
    log.handlers.clear()
    log.setLevel(logging.DEBUG)
    log.addHandler(buf)
    log.info("x")
    log.info("y")
    assert len(buf.recent()) == 2
    buf.clear()
    assert buf.recent() == []


# ── @traced decorator ────────────────────────────────────────────


def test_traced_sync_logs_start_and_done(caplog):
    @traced(logger="test.traced.sync", name="my_op")
    def add(a, b):
        return a + b

    with caplog.at_level(logging.INFO, logger="test.traced.sync"):
        result = add(2, 3)
    assert result == 5
    msgs = [r.message for r in caplog.records]
    assert any(m.startswith("start my_op") for m in msgs)
    assert any(m.startswith("done  my_op") for m in msgs)


def test_traced_async_logs_start_and_done(caplog):
    @traced(logger="test.traced.async", name="async_op")
    async def add(a, b):
        await asyncio.sleep(0)
        return a + b

    with caplog.at_level(logging.INFO, logger="test.traced.async"):
        result = asyncio.run(add(4, 5))
    assert result == 9
    msgs = [r.message for r in caplog.records]
    assert any(m.startswith("start async_op") for m in msgs)
    assert any(m.startswith("done  async_op") for m in msgs)


def test_traced_logs_exception_and_reraises(caplog):
    @traced(logger="test.traced.fail", name="boom")
    def kaboom():
        raise ValueError("nope")

    with caplog.at_level(logging.INFO, logger="test.traced.fail"):
        with pytest.raises(ValueError, match="nope"):
            kaboom()
    msgs = [r.message for r in caplog.records]
    assert any(m.startswith("fail  boom") for m in msgs)
