"""Repo maintenance + migration scripts.

This file exists so `from scripts.<name> import ...` works as a package
import (e.g. ``from scripts.migrate_split_dbs import migrate`` from
``launcher.py``). Without it, Python treats ``scripts/`` as a plain
directory, not a package, and the import fails.
"""
