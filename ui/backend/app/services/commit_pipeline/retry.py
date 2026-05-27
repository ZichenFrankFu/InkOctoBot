"""Retry schedule for failed commit_tasks.

Spec § 模块 1: 1 → 5 → 30 minutes between attempts. 4th failure flips
the task to ``failed_needs_manual`` and the orchestrator creates a
high-priority user_notifications row.

Tests can substitute a zero-delay schedule via :func:`set_schedule_for_tests`.
"""
from __future__ import annotations

# (attempt_index_after_failure) → seconds-until-retry
# Attempt 1 failed → wait 60s, retry as attempt 2
# Attempt 2 failed → wait 300s, retry as attempt 3
# Attempt 3 failed → wait 1800s, retry as attempt 4
# Attempt 4 failed → escalate (no more retries)
_DEFAULT_SCHEDULE: tuple[int, ...] = (60, 300, 1800)


_schedule: tuple[int, ...] = _DEFAULT_SCHEDULE


def get_schedule() -> tuple[int, ...]:
    return _schedule


def max_retries() -> int:
    """Number of retries (= len(schedule)); total attempts = retries + 1."""
    return len(_schedule)


def wait_seconds_for_attempt(failed_attempt_number: int) -> int | None:
    """Return the wait between the n-th failed attempt and the next try.

    Returns ``None`` once we've exhausted the schedule — caller should
    flip state to ``failed_needs_manual`` and notify.
    """
    idx = failed_attempt_number - 1
    if idx < 0 or idx >= len(_schedule):
        return None
    return _schedule[idx]


def set_schedule_for_tests(schedule: tuple[int, ...]) -> None:
    """Override the retry schedule. Tests use ``(0, 0, 0)`` to fire
    retries instantly. Pass ``_DEFAULT_SCHEDULE`` to restore."""
    global _schedule
    _schedule = schedule


def reset_for_tests() -> None:
    set_schedule_for_tests(_DEFAULT_SCHEDULE)
