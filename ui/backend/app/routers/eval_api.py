"""
/api/eval — Evaluation engine: repetition, slop, style drift, quality scoring.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/eval", tags=["eval"])
logger = logging.getLogger("inkoctobot.ui.backend.eval_api")


class EvalTextRequest(BaseModel):
    text: str
    reference_text: str = ""
    chapter_num: int = 1


class ConsistencyCheckRequest(BaseModel):
    project_id: str
    text: str
    world_rules: list[str] = []
    character_names: list[str] = []


@router.get("/health")
def health():
    return {"status": "ok", "router": "eval"}


@router.post("/analyze")
def analyze_text(req: EvalTextRequest):
    """Run all local evaluation checks on text."""
    results: dict[str, Any] = {"text_length": len(req.text), "issues": []}

    # 1. Repetition detection
    try:
        from agents.evaluation.repetition_detector import RepetitionDetector
        det = RepetitionDetector()
        rep = det.detect(req.text)
        results["repetition"] = rep or []
        for r in (rep or [])[:5]:
            results["issues"].append({
                "type": "repetition",
                "severity": "medium" if r.get("count", 0) > 3 else "low",
                "description": f"「{r.get('phrase', '')}」重复 {r.get('count', 0)} 次",
                "suggestion": "考虑使用同义词替换",
            })
    except Exception as e:
        results["repetition_error"] = str(e)

    # 2. Slop detection
    try:
        from agents.evaluation.slop_detector import SlopDetector
        slop = SlopDetector()
        slop_issues = slop.detect(req.text)
        results["slop"] = slop_issues or []
        for s in (slop_issues or [])[:5]:
            results["issues"].append({
                "type": "ai_flavor",
                "severity": "medium",
                "description": f"AI味表达: {s.get('match', s.get('pattern', ''))}",
                "suggestion": "用更自然的表达替换",
            })
    except Exception as e:
        results["slop_error"] = str(e)

    # 3. Style drift (if reference provided)
    if req.reference_text:
        try:
            from agents.evaluation.style_drift_detector import StyleDriftDetector
            drift = StyleDriftDetector()
            drift_result = drift.compare(req.reference_text, req.text)
            results["style_drift"] = drift_result
        except Exception as e:
            results["style_drift_error"] = str(e)

    # 4. Quality score
    try:
        from agents.evaluation.quality_scorer import QualityScorer
        scorer = QualityScorer()
        score_result = scorer.score(req.text)
        results["quality"] = score_result
    except Exception as e:
        results["quality_error"] = str(e)

    # Compute summary
    score = max(0, 100 - len(results["issues"]) * 8)
    results["score"] = score
    results["passed"] = score >= 60

    return results


@router.post("/consistency")
def check_consistency(req: ConsistencyCheckRequest):
    """Check text against world rules and character constraints."""
    try:
        from agents.evaluation.consistency_checker import ConsistencyChecker
        from agents.model_router import ModelRouter
        router_inst = ModelRouter()
        checker = ConsistencyChecker(router_inst, project_id=req.project_id)
        # For now, do a basic rule-based check
        violations = []
        text_lower = req.text.lower()
        for rule in req.world_rules:
            # Simple keyword check
            if "不能" in rule or "禁止" in rule:
                key_parts = rule.replace("不能", "").replace("禁止", "").strip()
                if key_parts and key_parts in req.text:
                    violations.append({
                        "rule": rule,
                        "description": f"文本中出现了违反规则「{rule}」的内容",
                        "severity": "high",
                    })
        return {"status": "ok", "violations": violations, "passed": len(violations) == 0}
    except Exception as e:
        return {"status": "error", "message": str(e)[:200]}
