"""
/api/eval — Evaluation engine: repetition, slop, style drift, quality scoring.

Uses both direct detector imports and the Skill registry for evaluation.
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
async def analyze_text(req: EvalTextRequest):
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
                "description": f"\u300c{r.get('phrase', '')}\u300d\u91cd\u590d {r.get('count', 0)} \u6b21",
                "suggestion": "\u8003\u8651\u4f7f\u7528\u540c\u4e49\u8bcd\u66ff\u6362",
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
                "description": f"AI\u5473\u8868\u8fbe: {s.get('match', s.get('pattern', ''))}",
                "suggestion": "\u7528\u66f4\u81ea\u7136\u7684\u8868\u8fbe\u66ff\u6362",
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

    # 5. Skill-based evaluation (supplementary — uses SkillRegistry)
    try:
        from core.skill_registry import SkillRegistry
        registry = SkillRegistry()
        registry.scan_all()
        eval_skills = registry.find_by_tags(["evaluation"])
        skills_used = []
        for skill in eval_skills:
            meta = skill.meta()
            try:
                # Rule-based skills can run without a model router
                skill_result = await skill.execute({"text": req.text}, model_router=None)
                results[f"skill_{meta.name}"] = skill_result
                skills_used.append(meta.name)
            except Exception:
                # Skip skills that need LLM (no router provided)
                pass
        results["skills_used"] = skills_used
    except Exception as e:
        logger.debug("Skill-based evaluation skipped: %s", e)

    # Compute summary
    score = max(0, 100 - len(results["issues"]) * 8)
    results["score"] = score
    results["passed"] = score >= 60

    return results


@router.post("/consistency")
async def check_consistency(req: ConsistencyCheckRequest):
    """Check text against world rules and character constraints."""
    try:
        from agents.evaluation.consistency_checker import ConsistencyChecker
        from models.router import ModelRouter
        router_inst = ModelRouter()
        checker = ConsistencyChecker(router_inst, project_id=req.project_id)
        # For now, do a basic rule-based check
        violations = []
        for rule in req.world_rules:
            # Simple keyword check
            if "\u4e0d\u80fd" in rule or "\u7981\u6b62" in rule:
                key_parts = rule.replace("\u4e0d\u80fd", "").replace("\u7981\u6b62", "").strip()
                if key_parts and key_parts in req.text:
                    violations.append({
                        "rule": rule,
                        "description": f"\u6587\u672c\u4e2d\u51fa\u73b0\u4e86\u8fdd\u53cd\u89c4\u5219\u300c{rule}\u300d\u7684\u5185\u5bb9",
                        "severity": "high",
                    })
        return {"status": "ok", "violations": violations, "passed": len(violations) == 0}
    except Exception as e:
        return {"status": "error", "message": str(e)[:200]}
