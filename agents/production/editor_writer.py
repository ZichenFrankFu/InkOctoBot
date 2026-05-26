"""Backward-compat shim — class moved to ``agents.production.writer``.

The ``EditorWriter`` name still works (re-exported below), but new
code should use ``Writer`` from the new module path.
"""
from __future__ import annotations

from agents.production.writer import EditorWriter, Writer  # noqa: F401

__all__ = ["EditorWriter", "Writer"]
