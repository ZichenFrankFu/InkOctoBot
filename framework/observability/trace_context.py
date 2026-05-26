"""Trace and session ID propagation via contextvars.

Bind a trace_id at the top of a request / pipeline run; every logger
call inside automatically picks it up via the LoggingFilter. Async
tasks inherit the binding because contextvars are copy-on-spawn.

Typical usage:

    from framework.observability import trace_scope, new_trace_id

    async def run_chapter(session_id: str, ...):
        with trace_scope(trace_id=new_trace_id(), session_id=session_id):
            # every log record emitted from here (including from
            # spawned tasks) carries trace_id and session_id fields
            ...
"""
from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

_trace_id_var: ContextVar[str] = ContextVar("inkoctobot_trace_id", default="")
_session_id_var: ContextVar[str] = ContextVar("inkoctobot_session_id", default="")


def new_trace_id() -> str:
    """Generate a fresh 12-char trace ID."""
    return uuid.uuid4().hex[:12]


def current_trace_id() -> str:
    return _trace_id_var.get()


def current_session_id() -> str:
    return _session_id_var.get()


def bind_trace(trace_id: str) -> object:
    """Bind a trace_id to the current context. Returns the reset token."""
    return _trace_id_var.set(trace_id)


def bind_session(session_id: str) -> object:
    """Bind a session_id to the current context. Returns the reset token."""
    return _session_id_var.set(session_id)


@contextmanager
def trace_scope(
    *,
    trace_id: str | None = None,
    session_id: str | None = None,
) -> Iterator[str]:
    """Context manager that binds trace+session for the enclosed block.

    Yields the effective trace_id (a new one is auto-generated if not given).
    On exit the original bindings are restored.
    """
    tid = trace_id or new_trace_id()
    tt = _trace_id_var.set(tid)
    st = None
    if session_id is not None:
        st = _session_id_var.set(session_id)
    try:
        yield tid
    finally:
        _trace_id_var.reset(tt)
        if st is not None:
            _session_id_var.reset(st)


class _TraceFilter(logging.Filter):
    """Logging filter that injects trace_id / session_id onto every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = current_trace_id()
        record.session_id = current_session_id()
        return True


def install_trace_filter(logger: logging.Logger | None = None) -> None:
    """Attach the trace-context filter to a logger (root by default).

    Idempotent — calling repeatedly is safe.
    """
    target = logger if logger is not None else logging.getLogger()
    for h in target.handlers:
        if not any(isinstance(f, _TraceFilter) for f in h.filters):
            h.addFilter(_TraceFilter())
