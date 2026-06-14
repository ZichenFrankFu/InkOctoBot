"""单章生成成本预估 + 超限降级 (spec LLM调用·机制1).

Before generation the editor shows the chapter's expected LLM call
count and token / USD cost — director mode scales with the scene
count. The user can set a per-chapter cost cap
(``chapter_cost_cap_usd`` in settings.json, 0 = uncapped); when the
estimate exceeds it the degradation ladder applies, in spec order:

1. 导演模式 → 单 Agent 模式 (drops Scene Director + per-scene Actor)
2. 跳过 LLM 评估层 (deterministic detectors still run — they're free)

If even the floor configuration exceeds the cap the estimate is
returned with ``over_cap=True`` so the UI asks the user to either
raise the cap or proceed knowingly.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("inkoctobot.services.generation_cost")


# (input_tokens, output_tokens) heuristics per call. Director-mode
# Actor is ONE ensemble call per scene (Actor·机制3).
_CALL_PROFILES: dict[str, tuple[int, int]] = {
    "scene_director":  (3000, 2000),
    "actor_ensemble":  (3500, 3000),
    "narrator":        (2000, 1000),
    "writer":          (6000, 4000),
    "state_extract":   (4000, 1500),
    "evaluator_llm":   (4000, 1500),
}

_ROLE_FOR_PROFILE = {
    "scene_director": "scene_director",
    "actor_ensemble": "actor_agent",
    "narrator":       "narrator_agent",
    "writer":         "editor_writer",
    "state_extract":  "post_commit",
    "evaluator_llm":  "evaluator",
}


def chapter_cost_cap_usd() -> float:
    """Per-chapter cost cap from settings.json; 0 disables the cap."""
    try:
        from ui.backend.app.settings import settings as _s
        path = _s.get_data_path("settings.json")
        if path.exists():
            blob = json.loads(path.read_text("utf-8"))
            return max(0.0, float(blob.get("chapter_cost_cap_usd") or 0))
    except Exception:
        pass
    return 0.0


def _calls_for(mode: str, num_scenes: int, *, skip_llm_eval: bool) -> list[str]:
    """Per spec LLM调用汇总: 单Agent = Writer + 状态抽取 + 评估 (3 次);
    导演 = Scene Director + Actor(每场景一次) + Narrator(每场景) +
    Writer + 状态抽取 + 评估."""
    calls: list[str] = []
    if mode == "director":
        calls.append("scene_director")
        calls.extend(["actor_ensemble"] * max(1, num_scenes))
        calls.extend(["narrator"] * max(1, num_scenes))
    calls.append("writer")
    calls.append("state_extract")
    if not skip_llm_eval:
        calls.append("evaluator_llm")
    return calls


def estimate_chapter(
    mode: str, num_scenes: int = 3, context_tokens: int = 0,
    *, skip_llm_eval: bool = False, router: Any = None,
) -> dict[str, Any]:
    """Estimate one chapter's LLM calls / tokens / USD for ``mode``
    ('single' | 'director'). ``context_tokens`` is the assembled RAG
    prompt size (added to every call's input)."""
    if router is None:
        try:
            from llm.router import ModelRouter
            router = ModelRouter()
        except Exception:
            router = None

    calls = _calls_for(mode, num_scenes, skip_llm_eval=skip_llm_eval)
    breakdown: list[dict[str, Any]] = []
    total_in = total_out = 0
    total_usd = 0.0
    for profile in calls:
        base_in, base_out = _CALL_PROFILES[profile]
        in_tokens = base_in + context_tokens
        usd = 0.0
        if router is not None:
            try:
                usd = float(router.estimate_cost(
                    _ROLE_FOR_PROFILE[profile], in_tokens, base_out,
                ))
            except Exception:
                usd = 0.0
        breakdown.append({
            "call": profile,
            "input_tokens": in_tokens,
            "output_tokens": base_out,
            "usd": round(usd, 4),
        })
        total_in += in_tokens
        total_out += base_out
        total_usd += usd
    return {
        "mode": mode,
        "num_scenes": num_scenes if mode == "director" else 0,
        "llm_calls": len(calls),
        "input_tokens": total_in,
        "output_tokens": total_out,
        "estimated_usd": round(total_usd, 4),
        "skip_llm_eval": skip_llm_eval,
        "breakdown": breakdown,
    }


def estimate_with_degradation(
    mode: str, num_scenes: int = 3, context_tokens: int = 0,
    *, cap_usd: float | None = None, router: Any = None,
) -> dict[str, Any]:
    """Estimate + spec degradation ladder. Returns the requested-mode
    estimate plus ``effective``: the configuration that fits the cap
    (or the floor configuration with ``over_cap=True``)."""
    if cap_usd is None:
        cap_usd = chapter_cost_cap_usd()
    requested = estimate_chapter(
        mode, num_scenes, context_tokens, router=router,
    )
    out: dict[str, Any] = {
        "requested": requested,
        "cap_usd": cap_usd,
        "effective": {**requested, "degraded": False, "over_cap": False,
                      "degradation_steps": []},
    }
    if cap_usd <= 0 or requested["estimated_usd"] <= cap_usd:
        return out

    steps: list[str] = []
    current_mode, skip_eval = mode, False
    # Step 1 (机制1): 导演转单 Agent.
    if current_mode == "director":
        current_mode = "single"
        steps.append("director_to_single")
        est = estimate_chapter(
            current_mode, num_scenes, context_tokens,
            skip_llm_eval=skip_eval, router=router,
        )
        if est["estimated_usd"] <= cap_usd:
            out["effective"] = {**est, "degraded": True, "over_cap": False,
                                "degradation_steps": steps}
            return out
    # Step 2: 跳过 LLM 评估层.
    skip_eval = True
    steps.append("skip_llm_eval")
    est = estimate_chapter(
        current_mode, num_scenes, context_tokens,
        skip_llm_eval=True, router=router,
    )
    out["effective"] = {
        **est, "degraded": True,
        "over_cap": est["estimated_usd"] > cap_usd,
        "degradation_steps": steps,
    }
    return out
