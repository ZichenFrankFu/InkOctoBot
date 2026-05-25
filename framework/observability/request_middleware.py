"""FastAPI middleware that binds a trace_id per request.

Reads an inbound ``X-Request-ID`` header if present (so a caller can
correlate their own ID), otherwise generates one. Echoes the trace_id
back in the response header and binds it to the contextvars so every
log line emitted while handling the request carries it.
"""
from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from .trace_context import bind_trace, current_trace_id, new_trace_id

logger = logging.getLogger("inkoctobot.framework.request_middleware")

_HEADER = "X-Request-ID"


class TraceIDMiddleware(BaseHTTPMiddleware):
    """Bind/propagate a trace_id for the duration of each HTTP request."""

    async def dispatch(self, request: Request, call_next):
        inbound = request.headers.get(_HEADER, "").strip()
        trace_id = inbound or new_trace_id()
        token = bind_trace(trace_id)
        try:
            response = await call_next(request)
            response.headers[_HEADER] = current_trace_id() or trace_id
            return response
        finally:
            # reset to whatever was there before (usually empty)
            from .trace_context import _trace_id_var
            _trace_id_var.reset(token)
