"""
Constraint Store — manages persistent constraint rules and their embeddings.

Bridges the SQLite constraint_rules table with the ChromaDB
ViolationDetector for semantic checking.
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from typing import Any

from agents.guardrails.violation_detector import ViolationDetector

logger = logging.getLogger("inkoctobot.rag.constraint_store")


class ConstraintStore:
    """Manages constraint rules with both SQL and vector storage."""

    def __init__(self, db_path: str, chromadb_dir: str | None = None):
        self.db_path = db_path
        self._detector = ViolationDetector(persist_dir=chromadb_dir)

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def add_rule(
        self,
        project_id: str,
        rule_type: str,
        description: str,
        *,
        positive_restatement: str = "",
        good_example: str = "",
        bad_example: str = "",
        priority: int = 3,
        source: str = "user",
    ) -> str:
        """Add a constraint rule to both SQL and vector stores."""
        rule_id = f"rule_{uuid.uuid4().hex[:12]}"
        with self._conn() as c:
            c.execute(
                """INSERT INTO constraint_rules
                   (rule_id, project_id, rule_type, priority, description,
                    positive_restatement, good_example, bad_example, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (rule_id, project_id, rule_type, priority, description,
                 positive_restatement, good_example, bad_example, source),
            )
            c.commit()

        # Index in vector store for semantic detection
        self._detector.index_constraint(
            rule_id, description, bad_example,
            metadata={"project_id": project_id, "rule_type": rule_type, "priority": priority},
        )
        return rule_id

    def get_rules(self, project_id: str, rule_type: str | None = None) -> list[dict]:
        with self._conn() as c:
            if rule_type:
                rows = c.execute(
                    "SELECT * FROM constraint_rules WHERE project_id=? AND rule_type=? AND is_active=1 ORDER BY priority",
                    (project_id, rule_type),
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM constraint_rules WHERE project_id=? AND is_active=1 ORDER BY priority",
                    (project_id,),
                ).fetchall()
        return [dict(r) for r in rows]

    def check_violations(self, text: str, project_id: str = "") -> list[dict]:
        """Check text against all indexed constraints."""
        return self._detector.check_text(text, project_id=project_id)

    def deactivate_rule(self, rule_id: str) -> None:
        with self._conn() as c:
            c.execute("UPDATE constraint_rules SET is_active=0 WHERE rule_id=?", (rule_id,))
            c.commit()
