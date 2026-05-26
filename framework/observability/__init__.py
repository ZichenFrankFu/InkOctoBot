"""Observability primitives — trace IDs, structured logging, in-memory log buffer.

Lightweight by design: no Prometheus, no OpenTelemetry, no Jaeger. This
is a single-user desktop app — what we need is:

- a trace_id / session_id that follows a request through every module
- structured (JSON) logs as an opt-in mode for machine parsing
- an in-memory ring buffer of recent log records so a /debug endpoint
  can answer "show me what just happened" without parsing log files
- a ``@traced`` decorator to auto-log entry / exit / duration of long ops

Wire everything together via ``framework.observability.install(...)``
once at app startup.
"""
from .trace_context import (
    bind_session,
    bind_trace,
    current_session_id,
    current_trace_id,
    new_trace_id,
    trace_scope,
)
from .log_buffer import LogRingBuffer, get_buffer
from .decorators import traced

__all__ = [
    "bind_session",
    "bind_trace",
    "current_session_id",
    "current_trace_id",
    "new_trace_id",
    "trace_scope",
    "LogRingBuffer",
    "get_buffer",
    "traced",
]
