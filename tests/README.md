# Tests

Per-module test layout mirroring `<repo>/`. Each top-level Python
package has a corresponding directory under `tests/`; each source file
gets a `test_<source>.py` next to its peers.

## Layout

```
tests/
  conftest.py                pytest config, mock fixtures, sys.path setup
  pytest.ini                 markers + asyncio mode

  agents/                    mirrors agents/
    guardrails/
      test_assembler.py
    evaluation/
      test_detectors.py        repetition / slop / style_drift / quality_score
      test_consistency_check.py
      test_repetition_detect.py

  framework/                 mirrors framework/
    test_config.py
    test_event_bus.py
    test_event_system.py
    test_observability.py             unit tests for trace_context, log_buffer, @traced
    test_observability_integration.py end-to-end trace_id flow through FastAPI
    test_skill_learner.py             AST-validation sandbox tests
    test_skill_registry.py            discovery + hot-reload

  knowledge/                 mirrors knowledge/
    test_character_worldbook.py
    test_decision_engine.py
    memory/
      test_memory_system.py           L1/L2/L3/L4 + consolidator
    truth/                            ★ the reference architecture
      test_schemas.py
      test_store_apply.py
      test_validators.py
      test_render_and_query.py
      test_truth_schema_ddl.py
      test_migrate.py
      integration/
        test_full_lifecycle.py

  llm/                       mirrors llm/
    test_base.py             BaseLLMProvider, ProviderConfig, ModelRouter, CostEstimator

  market_analysis/           mirrors market_analysis/
    test_formula_engine.py

  reference_pipeline/        mirrors reference_pipeline/
    test_advanced.py         rhetoric / shuangdian features

  storage/                   mirrors storage/
    test_project_schema.py

  integration/               cross-module end-to-end
    test_agents_pipeline.py  full pipeline run with Mock provider
```

## Running

```bash
# Everything
pytest tests/

# Just one module
pytest tests/knowledge/truth/

# Quiet + short tracebacks
pytest tests/ -q --tb=short

# With coverage on a specific module
pytest tests/llm/ --cov=llm --cov-report=term-missing
```

## Conventions

- Test files mirror their source by path and name. Adding `agents/new_thing.py`
  means adding `tests/agents/test_new_thing.py`.
- **Public API only**: tests must import via the package's public surface.
  No reaching into `_private` modules from a test.
- **No `__init__.py`** under `tests/` — pytest uses rootdir-based collection
  to avoid name collision with the top-level packages they mirror
  (e.g. `tests/framework/__init__.py` would collide with `framework/`).
- Shared fixtures go in `tests/conftest.py`. Pull common mocks (LLM router,
  sample projects) from there rather than re-implementing per-test.
- Tests that depend on Ollama / network / heavy ML libs use
  `@unittest.skipUnless(...)` so the suite stays runnable without them.
- One assert-per-behavior. Name tests after the property being asserted
  (e.g. `test_trace_scope_is_reentrant`, not `test_trace_scope_4`).

## Test markers (see pytest.ini)

- `unit`        — no external services
- `integration` — needs Ollama or other live services (auto-skipped if unavailable)
- `skill`       — exercises a specific SKILL.md
- `agent`       — exercises an agent's orchestration

Filter by marker: `pytest -m unit`.
