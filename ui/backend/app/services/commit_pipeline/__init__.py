"""Post-commit pipeline package — orchestrates background work
triggered by chapter saves.

Public surface:

- :func:`fire_and_forget_from_handler` — call from save-version
  handlers; never raises.
- :func:`run_pipeline_async` — schedule all enabled sub-tasks on the
  caller's event loop. Returns the list of created task_ids.
- :func:`run_pipeline_sync` — test-mode helper that awaits every
  sub-task inline.
- :mod:`.task_registry` — DB + in-memory state lookup
  (``get_live`` / ``get`` / ``list_for_chapter`` / ``list_failed_for_project``).
- :mod:`.sub_tasks` — registry of the 6 task types + their
  manual_action_url templates.
"""
from . import retry, sub_tasks, task_registry
from .pipeline import (
    fire_and_forget_from_handler,
    run_pipeline_async,
    run_pipeline_sync,
)

__all__ = [
    "fire_and_forget_from_handler",
    "run_pipeline_async",
    "run_pipeline_sync",
    "retry",
    "sub_tasks",
    "task_registry",
]
