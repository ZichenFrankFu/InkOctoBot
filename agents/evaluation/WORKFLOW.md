# Workflow: Quality Evaluation

> Multi-criteria chapter evaluation with a targeted-rewrite loop. The
> chapter must clear every dimension OR the loop kicks in to fix the
> specific issues until it passes (max 3 attempts).

## 1. Purpose

After EditorWriter produces a chapter, we need to confirm it actually
meets the project's standards before adding it to memory and moving on.
The Evaluator runs five orthogonal checks plus an LLM holistic pass:

| Dimension | Detector | What it catches |
|---|---|---|
| Constraint satisfaction | the Evaluator's LLM call | violations of guardrails (world rules, knowledge isolation, plot constraints) |
| Repetition | `repetition_detector.py` | sentence-head repeats, phrase repeats, structure repeats |
| AI slop | `slop_detector.py` | the AI-flavored phrases listed in `config/slop_patterns.json` |
| Style drift | `style_drift_detector.py` | deviations from the project's style fingerprint |
| Cross-chapter consistency | `cross_chapter_checker.py` | overdue foreshadowing, character contradictions |
| Holistic quality | the Evaluator's LLM call | dimension scores, overall pass/fail, free-text issues |

If any detector fires OR the LLM score is below threshold,
`targeted_rewrite` runs on the affected passages (NOT a full re-gen).

## 2. Who triggers it

- **Pipeline runner** (`pipeline/steps/evaluate`, not yet extracted —
  currently `generation_api._run_pipeline_inner`): after EditorWriter
  finishes, before the chapter is persisted.
- **UI** (`POST /api/generation/evaluate`): manual one-off evaluation on
  arbitrary text — used by the Editor page's "evaluate" button.

## 3. Inputs / Outputs

| Method | In | Out |
|---|---|---|
| `Evaluator.evaluate_chapter(...)` | chapter_text, chapter_num, scene_plan, character_cards, memory_context, constraints | `{passed, score, issues[], dimension_scores{}, summary_text, process[]}` |
| Individual detectors | chapter_text | list of issue dicts |

The full evaluation JSON is logged at INFO level (counts) and DEBUG
level (full payload) per the observability audit GAP 3 fix — never a
silent rejection.

## 4. Sequence

```mermaid
sequenceDiagram
  participant Pipe as pipeline.runner
  participant Eval as Evaluator
  participant Det as Detectors (parallel)
  participant LLM as ModelRouter
  participant Bus as EventBus
  participant Edit as EditorWriter

  Pipe->>Eval: evaluate_chapter(text, ch_num, ...)
  par parallel detectors
    Eval->>Det: RepetitionDetector.detect(text)
    Det-->>Eval: rep_issues
  and
    Eval->>Det: SlopDetector.detect(text)
    Det-->>Eval: slop_issues
  and
    Eval->>Det: StyleDriftDetector.detect(text)
    Det-->>Eval: drift_issues
  and
    Eval->>Det: CrossChapterChecker.audit_foreshadowing(text)
    Det-->>Eval: foreshadow_issues
  end
  Eval->>LLM: invoke (holistic eval prompt + all issues)
  LLM-->>Eval: { passed, score, issues, dimension_scores }
  Eval->>Bus: emit EVALUATION_COMPLETED (with full result)
  Eval-->>Pipe: parsed
  alt not passed and retries < 3
    Pipe->>Edit: targeted_rewrite(text, diagnosis)
    Edit-->>Pipe: revised_text
    Pipe->>Eval: evaluate_chapter(revised_text, ...)
    Note over Pipe,Eval: loop up to 3 times; on failure mark "needs review"
  end
```

## 5. Decision points

- **Pass threshold**: `score >= 70` AND `passed == True` AND no critical
  issues. Tuned via `config/app_config.yaml: evaluation.score_threshold`.
- **Detector severity**: each issue carries `severity ∈ {low, medium, high}`.
  Only `high` items are sent to `targeted_rewrite`; medium ones get
  flagged in `process[]` for user awareness.
- **Rewrite vs full regen**: rewrite ONLY when issues are localized
  (passages). If issues are structural (e.g. wrong POV throughout),
  surface to user — don't loop.
- **Retry cap**: hardcoded 3 in `_run_pipeline_inner`. After 3 the
  chapter is saved with status `needs_review` so the user can intervene.

## 6. Error handling

- Each detector wrapped in try/except (a broken detector never blocks
  the whole evaluation — its issues just don't surface).
- LLM call errors: `invoke()` logs with `exc_info` (per GAP 2 fix);
  caller treats LLM-eval-failure as `passed=True, score=70` so the
  pipeline still finishes. The operator sees the failure in the log.
- Cross-chapter checker requires the L4 episodic timeline — degrades
  silently if the project has no prior chapters yet.

## 7. Related code + tests

- Source: `agents/evaluation/{evaluator,repetition_detector,slop_detector,
  style_drift_detector,quality_scorer,cross_chapter_checker,edit_analyzer}.py`
- Skills: `agents/evaluation/skills/{repetition_detect,slop_detect,
  style_drift_detect,quality_score,consistency_check}/`
- Tests: `tests/agents/evaluation/test_detectors.py`,
  `test_consistency_check.py`, `test_repetition_detect.py`
- Configuration: `config/app_config.yaml: evaluation.*`,
  `config/slop_patterns.json`
- See also `WORKFLOW.md` in `framework/observability/` for how the
  full evaluation JSON flows through to the log buffer + debug endpoints.
