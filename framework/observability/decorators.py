"""@traced decorator — auto-log entry / exit / duration of a function.

Useful for long-running pipeline steps where you want a record of
"this step started, took N ms, returned X" without having to write
the boilerplate by hand. Honors the trace_id contextvar so the entry
and exit lines correlate.

Both sync and async functions are supported.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import time
from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def traced(
    *,
    logger: logging.Logger | str | None = None,
    level: int = logging.INFO,
    include_args: bool = False,
    name: str | None = None,
) -> Callable[[F], F]:
    """Decorate a function with entry / exit / duration logging.

    Parameters
    ----------
    logger : the logger to use; if a string, ``logging.getLogger(...)``.
             Default: the decorated function's module logger.
    level  : log level for the entry / exit lines (default INFO).
    include_args : if True, repr() the positional args at DEBUG level.
                   Off by default to avoid leaking secrets / huge payloads.
    name : override the operation name (default: ``module.qualname``).
    """
    def decorator(fn: F) -> F:
        log = (
            logger if isinstance(logger, logging.Logger)
            else logging.getLogger(logger or fn.__module__)
        )
        op = name or f"{fn.__module__}.{fn.__qualname__}"

        if asyncio.iscoroutinefunction(fn):
            @functools.wraps(fn)
            async def awrapper(*args, **kwargs):
                log.log(level, "start %s", op)
                if include_args:
                    log.debug("  args=%r kwargs=%r", args, kwargs)
                t0 = time.monotonic()
                try:
                    result = await fn(*args, **kwargs)
                    dt = (time.monotonic() - t0) * 1000
                    log.log(level, "done  %s (%dms)", op, int(dt))
                    return result
                except Exception:
                    dt = (time.monotonic() - t0) * 1000
                    log.exception("fail  %s (%dms)", op, int(dt))
                    raise
            return awrapper  # type: ignore[return-value]

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            log.log(level, "start %s", op)
            if include_args:
                log.debug("  args=%r kwargs=%r", args, kwargs)
            t0 = time.monotonic()
            try:
                result = fn(*args, **kwargs)
                dt = (time.monotonic() - t0) * 1000
                log.log(level, "done  %s (%dms)", op, int(dt))
                return result
            except Exception:
                dt = (time.monotonic() - t0) * 1000
                log.exception("fail  %s (%dms)", op, int(dt))
                raise
        return wrapper  # type: ignore[return-value]

    return decorator
