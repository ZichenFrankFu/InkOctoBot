"""Manual-mode bridge: prompt out → web-LLM → response paste back.

When global ``llm_manual_mode`` is on, every ``LLMCallSite.invoke()``
does:

1. ``register_pending_paste(call_site_id, prompt, system)`` — returns
   a paste token, registers an asyncio.Future, pushes a
   ``llm_paste_pending`` event onto the EventBus so the frontend
   bell icon lights up.
2. Awaits ``await_paste(token, timeout=...)``.
3. The user opens the paste-inbox UI, copies the prompt into web
   ChatGPT/Claude, copies the response back, ``POST
   /api/llm-paste/{token}`` calls ``submit_paste(token, raw_response)``
   which resolves the future.
4. The call site treats the pasted text as if it were the LLM
   response (same parser, same persistence path).

Failure modes:
- Timeout (default 30 min): paste token expires, ``LLMPasteTimeout``
  raised, call site falls into its normal retry/notify path.
- User cancels: ``cancel_paste(token)`` → ``LLMPasteCancelled``.
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


logger = logging.getLogger("inkoctobot.llm.manual_paste")


# 30 min default — user might be away from the keyboard.
_DEFAULT_TIMEOUT_SECONDS = 30 * 60


class LLMPasteTimeout(RuntimeError):
    """Paste token expired before the user submitted a response."""


class LLMPasteCancelled(RuntimeError):
    """User explicitly cancelled the pending paste."""


@dataclass
class PendingPaste:
    paste_token: str
    call_site_id: str
    prompt: str
    system: str
    created_at: float = field(default_factory=time.time)
    project_id: str = ""
    chapter_id: str = ""
    # Resolved when the user submits.
    future: asyncio.Future[str] | None = None


_pending: dict[str, PendingPaste] = {}
_lock = threading.Lock()


def _new_token() -> str:
    return f"pst_{uuid.uuid4().hex[:12]}"


def register_pending_paste(
    call_site_id: str, prompt: str, system: str = "",
    *, project_id: str = "", chapter_id: str = "",
    loop: asyncio.AbstractEventLoop | None = None,
) -> str:
    """Register a paste-token; pushes EventBus notification; returns
    the token. ``loop`` is the event loop the awaiting coroutine lives
    on (defaults to the current running loop)."""
    if loop is None:
        loop = asyncio.get_event_loop()
    token = _new_token()
    pp = PendingPaste(
        paste_token=token, call_site_id=call_site_id,
        prompt=prompt, system=system,
        project_id=project_id, chapter_id=chapter_id,
        future=loop.create_future(),
    )
    with _lock:
        _pending[token] = pp

    # Push to EventBus so the frontend can surface "1 paste pending"
    # immediately. Best-effort — test environments may not have the bus.
    try:
        from framework.event_types import AgentSuggestion
        from framework.global_event_bus import get_event_bus
        get_event_bus().push_suggestion(AgentSuggestion(
            agent_name="llm_manual_paste",
            title=f"待粘贴 LLM 响应：{call_site_id}",
            content=(
                f"LLM 调用 {call_site_id} 处于 manual 模式，请粘贴回复。\n"
                f"prompt 长度 {len(prompt)} 字符。"
            ),
            severity="suggestion",
            event_type="llm_paste_pending",
            project_id=project_id,
            data={
                "paste_token":   token,
                "call_site_id":  call_site_id,
                "project_id":    project_id,
                "chapter_id":    chapter_id,
                "prompt_chars":  len(prompt),
            },
        ))
    except Exception as e:
        logger.debug("EventBus push for paste %s skipped: %s", token, e)

    return token


async def await_paste(
    token: str, *, timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> str:
    """Block until the paste arrives. Raises ``LLMPasteTimeout`` if no
    submission within ``timeout`` seconds."""
    with _lock:
        pp = _pending.get(token)
    if pp is None or pp.future is None:
        raise RuntimeError(f"unknown paste token {token!r}")
    try:
        raw = await asyncio.wait_for(pp.future, timeout=timeout)
    except asyncio.TimeoutError:
        with _lock:
            _pending.pop(token, None)
        raise LLMPasteTimeout(f"paste token {token!r} timed out")
    # Clean up after successful paste.
    with _lock:
        _pending.pop(token, None)
    return raw


def submit_paste(token: str, raw_response: str) -> bool:
    """Resolve the pending future with the user-pasted response.
    Returns True if the token was pending; False if already resolved /
    cancelled / unknown."""
    with _lock:
        pp = _pending.get(token)
    if pp is None or pp.future is None:
        return False
    if pp.future.done():
        return False
    try:
        pp.future.get_loop().call_soon_threadsafe(
            pp.future.set_result, raw_response,
        )
    except Exception as e:
        logger.warning("failed to resolve paste %s: %s", token, e)
        return False
    return True


def cancel_paste(token: str) -> bool:
    with _lock:
        pp = _pending.get(token)
    if pp is None or pp.future is None or pp.future.done():
        return False
    try:
        pp.future.get_loop().call_soon_threadsafe(
            pp.future.set_exception,
            LLMPasteCancelled(f"paste {token!r} cancelled by user"),
        )
    except Exception:
        return False
    with _lock:
        _pending.pop(token, None)
    return True


def list_pending() -> list[dict[str, Any]]:
    with _lock:
        return [{
            "paste_token":   pp.paste_token,
            "call_site_id":  pp.call_site_id,
            "project_id":    pp.project_id,
            "chapter_id":    pp.chapter_id,
            "prompt_chars":  len(pp.prompt),
            "created_at":    pp.created_at,
            "age_seconds":   time.time() - pp.created_at,
        } for pp in _pending.values()]


def get_pending(token: str) -> PendingPaste | None:
    with _lock:
        return _pending.get(token)


def reset_for_tests() -> None:
    with _lock:
        for pp in list(_pending.values()):
            if pp.future is not None and not pp.future.done():
                try:
                    pp.future.get_loop().call_soon_threadsafe(
                        pp.future.set_exception,
                        LLMPasteCancelled("test teardown"),
                    )
                except Exception:
                    pass
        _pending.clear()
