"""
Constraint Assembler — assembles constraints by priority into system prompts.

Migrated from agents/constraints/assembler.py (non-LLM, database-driven).
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any

logger = logging.getLogger("inkoctobot.constraints.assembler")

PRIORITY_ORDER = {
    "world_rule": 1,
    "knowledge_isolation": 2,
    "plot_constraint": 3,
    "style_constraint": 4,
}

PRIORITY_LABELS = {
    1: "世界观硬规则（违反即逻辑错误）",
    2: "知识隔离指令（违反即角色穿帮）",
    3: "情节约束（违反会偏离剧情走向）",
    4: "风格约束（违反影响质感但不致命）",
}


class ConstraintAssembler:
    """Assembles constraints into prioritized prompt sections."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def get_constraints(
        self, project_id: str, rule_type: str | None = None,
    ) -> list[dict]:
        """Get active constraints for a project."""
        with self._conn() as c:
            if rule_type:
                rows = c.execute(
                    """SELECT * FROM constraint_rules
                       WHERE project_id=? AND rule_type=? AND is_active=1
                       ORDER BY priority""",
                    (project_id, rule_type),
                ).fetchall()
            else:
                rows = c.execute(
                    """SELECT * FROM constraint_rules
                       WHERE project_id=? AND is_active=1
                       ORDER BY priority""",
                    (project_id,),
                ).fetchall()
        return [dict(r) for r in rows]

    def assemble(
        self,
        project_id: str,
        *,
        include_types: list[str] | None = None,
        scene_constraints: list[dict[str, Any]] | None = None,
    ) -> str:
        """Build the constraint section for a system prompt."""
        constraints = self.get_constraints(project_id)

        if include_types:
            constraints = [
                c for c in constraints if c["rule_type"] in include_types
            ]

        by_priority: dict[int, list[dict]] = {}
        for c in constraints:
            p = c.get("priority", 4)
            by_priority.setdefault(p, []).append(c)

        if scene_constraints:
            by_priority.setdefault(3, []).extend([
                {
                    "description": sc.get("description", ""),
                    "positive_restatement": sc.get("positive_restatement", ""),
                    "good_example": sc.get("good_example", ""),
                    "bad_example": sc.get("bad_example", ""),
                    "rule_type": "plot_constraint",
                    "priority": 3,
                }
                for sc in scene_constraints
            ])

        parts = ["## 约束规则（按优先级排列）\n"]
        for priority in sorted(by_priority.keys()):
            label = PRIORITY_LABELS.get(priority, f"优先级{priority}")
            rules = by_priority[priority]
            parts.append(f"\n### {label}")
            for r in rules:
                desc = r.get("positive_restatement") or r.get("description", "")
                parts.append(f"- {desc}")
                if r.get("good_example"):
                    parts.append(f"  ✓ 正确示例: {r['good_example']}")
                if r.get("bad_example"):
                    parts.append(f"  ✗ 错误示例: {r['bad_example']}")

        return "\n".join(parts)

    def add_constraint(
        self,
        project_id: str,
        rule_type: str,
        description: str,
        *,
        positive_restatement: str = "",
        good_example: str = "",
        bad_example: str = "",
        priority: int | None = None,
        source: str = "user",
    ) -> str:
        """Add a new constraint rule."""
        import uuid
        rule_id = f"rule_{uuid.uuid4().hex[:12]}"
        if priority is None:
            priority = PRIORITY_ORDER.get(rule_type, 4)
        with self._conn() as c:
            c.execute(
                """INSERT INTO constraint_rules
                   (rule_id, project_id, rule_type, priority, description,
                    positive_restatement, good_example, bad_example, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    rule_id, project_id, rule_type, priority, description,
                    positive_restatement, good_example, bad_example, source,
                ),
            )
            c.commit()
        return rule_id
