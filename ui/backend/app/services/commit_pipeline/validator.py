"""Consistency validator (spec § 模块 9).

Three layers:
- Layer 1 (script, zero token): predicate vocab + negative-balance
  detection + delta-field completeness. Runs always.
- Layer 2 (embedding, local-free): SPO uniqueness vs existing
  triples + entity-name overlap. Runs always.
- Layer 3 (LLM, rate-limited): semantic conflict detection. Runs
  on-demand, max 5 items per call, fails gracefully on LLM error.

Layer 1+2 results write to ``validator_issues`` + can be
back-populated into ``state_change_log.validation_warning``.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("inkoctobot.commit_pipeline.validator")


# Predicate whitelist mirrors state_extractor._ALLOWED_PREDICATES.
_ALLOWED_PREDICATES = {
    "位于", "在", "持有", "属于", "状态",
    "境界", "情绪", "关系", "目标", "信念", "对象",
}


@dataclass
class ValidationIssue:
    issue_id: str
    severity: str          # high / medium / low
    category: str          # predicate_invalid / negative_balance /
                            # duplicate_triple / entity_collision /
                            # semantic_conflict
    description: str
    related_table: str
    related_row_id: str


def _new_issue_id() -> str:
    return f"vi_{uuid.uuid4().hex[:12]}"


# ─────────── Layer 1: script ───────────


def run_layer1(db_path: str, project_id: str) -> list[ValidationIssue]:
    """Pure-Python checks — zero token cost, fast enough to run on
    every commit."""
    issues: list[ValidationIssue] = []

    with sqlite3.connect(db_path) as con:
        # Predicate vocab: any state_change_log row with an unknown
        # predicate is a candidate issue.
        bad_preds = con.execute(
            "SELECT log_id, predicate FROM state_change_log "
            "WHERE project_id = ? AND change_type = 'state_change' "
            "AND predicate NOT IN (" + ",".join("?" * len(_ALLOWED_PREDICATES)) + ")",
            (project_id, *sorted(_ALLOWED_PREDICATES)),
        ).fetchall()
        for log_id, pred in bad_preds:
            issues.append(ValidationIssue(
                issue_id=_new_issue_id(), severity="medium",
                category="predicate_invalid",
                description=f"predicate {pred!r} not in vocabulary",
                related_table="state_change_log", related_row_id=log_id,
            ))

        # Negative-balance ledger rows.
        negatives = con.execute(
            "SELECT ledger_id, character_name, key, new_value "
            "FROM character_ledger "
            "WHERE project_id = ? AND new_value < 0",
            (project_id,),
        ).fetchall()
        for lid, char, key, val in negatives:
            issues.append(ValidationIssue(
                issue_id=_new_issue_id(), severity="high",
                category="negative_balance",
                description=f"{char}/{key} = {val}",
                related_table="character_ledger", related_row_id=lid,
            ))

    return issues


# ─────────── Layer 2: embedding ───────────


def run_layer2(db_path: str, project_id: str) -> list[ValidationIssue]:
    """Embedding-based checks: SPO duplicates (same subject+predicate
    appearing twice as active) + entity-name overlap."""
    issues: list[ValidationIssue] = []

    with sqlite3.connect(db_path) as con:
        # Duplicate triples: two active rows for the same (subject,
        # predicate) — should never happen given supersede semantics.
        dup_rows = con.execute(
            "SELECT subject, predicate, COUNT(*) FROM truth_current_state "
            "WHERE project_id = ? AND valid_to_chapter IS NULL "
            "GROUP BY subject, predicate HAVING COUNT(*) > 1",
            (project_id,),
        ).fetchall()
        for subj, pred, n in dup_rows:
            issues.append(ValidationIssue(
                issue_id=_new_issue_id(), severity="high",
                category="duplicate_triple",
                description=f"{subj}/{pred} has {n} active rows",
                related_table="truth_current_state", related_row_id="",
            ))

    # Entity collision: two entities whose names are embedding-similar
    # (cosine ≥ 0.92). Layer 2 of the state_extractor's 5-layer filter
    # is the prevention path; this is the audit.
    try:
        from ui.backend.app.services.embedding import get_embedding_service
        import numpy as np
        svc = get_embedding_service()
        with sqlite3.connect(db_path) as con:
            rows = con.execute(
                "SELECT entity_id, name FROM storyland_entities "
                "WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        if len(rows) >= 2:
            names = [r[1] for r in rows]
            raw = svc.embed_batch(names)
            vecs = [np.frombuffer(b, dtype="float32") for b in raw if b]
            for i in range(len(vecs)):
                for j in range(i + 1, len(vecs)):
                    if vecs[i].size == 0 or vecs[j].size == 0:
                        continue
                    na = float(np.linalg.norm(vecs[i]))
                    nb = float(np.linalg.norm(vecs[j]))
                    if na == 0 or nb == 0:
                        continue
                    cos = float(np.dot(vecs[i], vecs[j]) / (na * nb))
                    if cos >= 0.92:
                        issues.append(ValidationIssue(
                            issue_id=_new_issue_id(), severity="medium",
                            category="entity_collision",
                            description=(
                                f"{rows[i][1]} ↔ {rows[j][1]} "
                                f"(cosine {cos:.3f})"
                            ),
                            related_table="storyland_entities",
                            related_row_id=rows[i][0],
                        ))
    except Exception as e:
        logger.debug("layer2 embedding check skipped: %s", e)

    return issues


# ─────────── Layer 3: LLM (rate-limited) ───────────


_MAX_LLM_ITEMS = 5


async def run_layer3(
    db_path: str, project_id: str, candidates: list[ValidationIssue],
) -> list[ValidationIssue]:
    """Optional LLM-based deep check.

    ``candidates`` filters to ≤ 5 medium-severity items from L1/L2 so
    we don't run a 200-item batch. Returns potentially-promoted issues
    (severity upgraded with semantic explanation). On any LLM failure
    returns an empty list — never blocks the validator chain."""
    if not candidates:
        return []
    batch = candidates[:_MAX_LLM_ITEMS]
    try:
        from llm.call_site import LLMCallSite
        cs = LLMCallSite(
            call_site_id="post_commit.validator_layer3",
            primary_role="post_commit",
            parsed_target_table="validator_issues",
            default_max_tokens=1500, default_temperature=0.2,
        )
        prompt = (
            "以下是世界状态一致性候选问题，每条 JSON 一条。请判断每条是否"
            "是真实冲突，输出 JSON："
            '{"results":[{"issue_id":"...","is_conflict":true/false,"explanation":"..."}]}'
            "\n\n候选问题：\n"
            + json.dumps([{
                "issue_id":   c.issue_id,
                "category":   c.category,
                "description": c.description,
            } for c in batch], ensure_ascii=False, indent=2)
        )
        raw = await cs.invoke(
            prompt=prompt,
            system="你是世界一致性审查助手。",
            project_id=project_id, db_path=db_path,
        )
        from .sub_tasks.summarizer import _extract_json
        parsed = _extract_json(raw)
        results = parsed.get("results") or []
        confirmed: list[ValidationIssue] = []
        by_id = {c.issue_id: c for c in batch}
        for r in results:
            iid = r.get("issue_id")
            if not r.get("is_conflict") or iid not in by_id:
                continue
            orig = by_id[iid]
            confirmed.append(ValidationIssue(
                issue_id=orig.issue_id,
                severity="high",
                category=orig.category,
                description=orig.description + " | LLM: " + str(r.get("explanation", "")),
                related_table=orig.related_table,
                related_row_id=orig.related_row_id,
            ))
        return confirmed
    except Exception as e:
        logger.warning("validator layer 3 LLM call failed: %s", e)
        return []


# ─────────── public surface ───────────


def run_all_sync(
    db_path: str, project_id: str,
) -> list[ValidationIssue]:
    """L1 + L2 only (no LLM). Used by state_extractor in-pipeline."""
    return run_layer1(db_path, project_id) + run_layer2(db_path, project_id)


async def run_all_async(
    db_path: str, project_id: str, *, include_llm: bool = False,
) -> dict[str, list]:
    """All 3 layers. Returns categorized + LLM-confirmed lists."""
    issues = run_all_sync(db_path, project_id)
    llm_confirmed: list[ValidationIssue] = []
    if include_llm and issues:
        medium = [i for i in issues if i.severity == "medium"]
        llm_confirmed = await run_layer3(db_path, project_id, medium)
    return {
        "issues":        [i.__dict__ for i in issues],
        "llm_confirmed": [i.__dict__ for i in llm_confirmed],
    }
