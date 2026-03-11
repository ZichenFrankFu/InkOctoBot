"""
Constraint store — unified access to constraint data.

Re-exports from rag/constraint_store.py if available,
otherwise provides a minimal interface.
"""
from __future__ import annotations

try:
    from rag.constraint_store import *  # noqa: F401, F403
except ImportError:
    pass
