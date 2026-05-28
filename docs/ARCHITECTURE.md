# InkOctoBot — Current Architecture & Feature Reference

> Authoritative snapshot as of 2026-05-26 (branch `claude/repo-architecture-review-8L8E2`).
> Cross-checked against the source tree, not against earlier docs.
> ~48 k LoC Python (excluding tests + frontend), ~7 k LoC tests, 14 React pages.

---

## 1. What InkOctoBot Is

An AI-assisted Chinese web-novel creation workbench. Desktop app
(PyWebView + FastAPI + Uvicorn + React/Vite, runs on `127.0.0.1:8713`).
Single-user, local-first, all project data lives in `data/novels.db`.

Core promise: multi-agent novel writing (planner → scenes → actors →
narrator → editor → evaluator) with persistent memory, knowledge
isolation per character, and a "Truth File" system that keeps long-form
narrative state consistent across hundreds of chapters.

---

## 2. Top-Level Layout

```
agents/              Multi-agent pipeline (planner/production/evaluation/guardrails/reference_extractors)
framework/           Runtime kernel: config, logging, events, skill registry/learner, observability, triggers
knowledge/           Memory (4 layers) + Truth Files (7 canonical) + character_cards/world_book/references/ideas/vector_store
llm/                 Provider abstraction (Anthropic/OpenAI/DeepSeek/Gemini/Ollama/vLLM/Mock) + router + cost
storage/             SQLite schemas (project/truth/reference/idea/market/extraction) + connection pool
market_analysis/     Market data analysis (heat/trend/metrics/formula engine/reports)
reference_pipeline/  Reference-work feature extraction (parser/AI extractor/embedding/rhetoric/...)
reference_ingest/    Reference-work ingestion (novel ingester/style/skill mining)
security/            API-key manager + test-mode isolation
ui/backend/app/      FastAPI app (28 routers, services/, pipeline/)
ui/frontend/         React + Vite SPA (14 pages)
config/              YAML/JSON config (model providers, prompts, presets, slop patterns, truth files schema)
scripts/             One-off utilities (migrations, self-check, project health)
tests/               Per-module tests mirroring source (37 files, ~7 kloc)
docs/                Architecture + design docs (this file, SCHEMA_REDESIGN, USER_GUIDE, TESTING_AND_LOGS, ...)
data/                Runtime data — novels.db, reference.db, idea.db, settings.json, usage.json
outputs/             Generated chapters, eval reports, self-check reports, logs/
```

**Top-level files**: `launcher.py` (entrypoint — starts Uvicorn in a thread + PyWebView), `test_seed.py` (test-mode seeder), `pytest.ini`.

---

## 3. Per-Module Detail

### 3.1 `agents/` — Multi-agent pipeline

Base classes: `base_agent.py:BaseAgent`, `base_skill.py:BaseSkill`.

**`agents/planner/`** — pre-writing setup
| File | Purpose |
|---|---|
| `story_architect.py` | Refines world_book / character cards via interactive Q&A (`refine_world_book`, `refine_character`) |
| `chapter_planner.py` | Plans chapters within a volume |
| `volume_planner.py` | Multi-volume macro outline |
| `marketing_agent.py` | Genre/title/logline advice driven by market data (no LLM) |
| `calibration.py` | Generates a 500-word style sample for user calibration |

**`agents/production/`** — scene-to-text
| File | Purpose |
|---|---|
| `scene_director.py` | `plan_scenes(world_rules=, character_cards=, ...)` — breaks outline into scenes + per-character instructions (emotional state, secret goal, knowledge boundary, must/must_not) |
| `actor_agent.py` | Per-character role-play; emits semi-structured performance record |
| `narrator_agent.py` | Environment / atmosphere / transitions (non-character POV) |
| `editor_writer.py` | `assemble_chapter(performance_records=, narrator_text=, *, chapter_num=, chapter_title=, narrative_instructions=, style_profile=, ...)` — final polish |
| `scene_simulator.py` | Batch scene comparison |

**`agents/evaluation/`** — quality assurance
| File | Purpose |
|---|---|
| `evaluator.py` | `evaluate_chapter(text, *, chapter_num=, constraints=, ...)` — orchestrator |
| `consistency_checker.py` | Character trait + world rule consistency |
| `cross_chapter_checker.py` | Multi-chapter continuity, foreshadow audit |
| `edit_analyzer.py` | Diffs user edits → user_style_preferences |
| `quality_scorer.py` | Multi-dimensional score |
| `repetition_detector.py` | Phrase/pattern repetition |
| `slop_detector.py` | AI-writing artifacts (lists, placeholders) |
| `style_drift_detector.py` | Style deviation vs user preferences |

**`agents/guardrails/`** — pre-flight checks
| File | Purpose |
|---|---|
| `assembler.py` | Assembles system prompts |
| `disambiguator.py` | Story-Architect clarification questions |
| `violation_detector.py` | Constraint pre-check |

**`agents/reference_extractors/`** — currently `__init__.py` + `skills/` only; extractor skills live as self-contained packages under `skills/`.

### 3.2 `framework/` — runtime kernel

| File | Purpose |
|---|---|
| `config.py` | YAML loader, cached |
| `log_setup.py` | Per-module loggers, optional JSON formatter (`INKOCTO_LOG_JSON=1`) |
| `event_bus.py` + `event_types.py` | Pub/sub with history buffer |
| `triggers.py` | Conditional workflow triggers (skill async/sync execution) |
| `skill_registry.py` | Skill discovery, watchdog hot-reload |
| `skill_learner.py` | Mine new skills from edit patterns; AST validation |
| `observability/trace_context.py` | `contextvars` for `trace_id` / `session_id` |
| `observability/decorators.py` | `@traced` to auto-log args/return/duration |
| `observability/log_buffer.py` | 500-entry in-memory ring buffer; exposes filter-by-trace API |
| `observability/json_formatter.py` | Structured JSON log lines |
| `observability/request_middleware.py` | FastAPI middleware: per-request `X-Request-ID` |

### 3.3 `knowledge/` — memory + truth + entities

**Memory (4 layers)** — `knowledge/memory/`:
| Layer | File | Purpose |
|---|---|---|
| L1 Immediate | `immediate.py` | In-process scene/chapter dict, cleared per-scene |
| L2 Chapter Buffer | `chapter_buffer.py` | Last 10 chapters' summaries from DB (`chapter_summaries` table) |
| L3 Semantic | `semantic_store.py` | ChromaDB vector index for free-text recall |
| L4 Episodic | `episodic_timeline.py` | Event graph (`episodic_events` table) |
| coordinator | `manager.py` | Unified `MemoryManager` — agents access only their permitted layers |
| consolidator | `consolidator.py` | When L2 overflows, emits `TruthDeltas` (StatePatch + HookDelta + EmotionArcEntry) to Truth Files **and** mirrors free text to L3 |
| isolation | `knowledge_isolation.py` | `KnowledgeIsolationEngine` + `FilteredWorldView` — what each character "knows at this chapter" |

**Truth Files (7 canonical) — state authority** — `knowledge/truth/`:
| File | Purpose |
|---|---|
| `store.py` | `TruthFileStore` — `apply_deltas()` is atomic with idempotency log |
| `schemas.py` | Pydantic: `TruthDeltas`, `StatePatch`, `HookDelta`, `ChapterSummaryDelta`, `EmotionArcEntry`, `RelationUpdate`, `SubplotUpdate`, `NumericalReconciliation` |
| `sql.py` | Insert/select SQL strings |
| `validators.py` | Cross-reference checks |
| `markdown_renderer.py` | Renders each truth file as markdown for prompt injection |
| `migrate.py` | One-shot v1→v2 migrator (foreshadowing JSON, episodic_events.foreshadow_status, etc.) |

**Supporting** — `knowledge/`:
| File | Purpose |
|---|---|
| `character_cards.py` | Loader + validator |
| `world_book.py` | World-entry catalog |
| `constraint_store.py` | Rule persistence |
| `decision_engine.py` | Question resolution helper for Story Architect |
| `vector_store.py` | ChromaDB wrapper |
| `reference_db.py` | Reference works/entries CRUD |
| `idea_db.py` | Inspiration entries (`data/idea.db`) |
| `work_index.py` | Reference-work indexing progress |
| `chunk_stream.py` | Streaming chunk processor |

### 3.4 `llm/` — provider abstraction

| File | Purpose |
|---|---|
| `base.py` | `BaseLLMProvider`, `LLMMessage`, `LLMResponse` |
| `router.py` | `ModelRouter` — dispatches to provider by agent_role |
| `anthropic_provider.py` / `openai_provider.py` / `deepseek_provider.py` / `gemini_provider.py` / `ollama_provider.py` / `vllm_provider.py` | 6 cloud + local providers |
| `mock_provider.py` | Deterministic mock for tests |
| `embedding_provider.py` | Embedding abstraction |
| `cost_estimator.py` | Token accounting |
| `ab_compare.py` | A/B-compare two routing configurations |
| `web_search_capabilities.py` | Web-search tool plumbing |

### 3.5 `storage/` — SQLite schemas + connection

| File | Purpose |
|---|---|
| `connection.py` | `get_conn()` ctx manager with WAL/FK/synchronous PRAGMAs + retry logging |
| `project_schema.py` | Main project tables — see §4 |
| `truth_schema.py` | 8 Truth File tables (7 canonical + `truth_apply_log`) |
| `reference_schema.py` | reference_works + reference_entries + project_reference_links + work_index_progress |
| `idea_schema.py` | inspirations (single table, `data/idea.db`) |
| `market_schema.py` | novels, novel_titles, tags, rank_lists, rank_entries, ... (crawler import) |
| `extraction_schema.py` | Novel skill extraction pipeline tables |
| `market_db.py` | High-level wrapper around market schema |
| `DATABASE.md` | Schema docs |

### 3.6 `market_analysis/` — market data

| File | Purpose |
|---|---|
| `data_access.py` | Crawler DB query helpers |
| `heat.py` / `metrics.py` / `trend_analyzer.py` | Heat/trend/metric calculations |
| `visualization.py` / `report.py` | Output formatters (Markdown report) |
| `run_analysis.py` | CLI entry |
| `formula_engine/` | Aggregator + presets + constraint converter (user-defined heat formulas) |

### 3.7 `reference_pipeline/` — reference-work analysis

`pipeline.py` orchestrates the 5-stage extraction. Stage modules:
`chapter_parser.py`, `ai_extractor.py`, `narrative_extractor.py`,
`embedding_cluster.py`, `nlp_stats.py`, `rhetoric_classifier.py`,
`shuangdian_templates.py`, `volume_detector.py`, `platform_profiles.py`,
`prompts.py`, `preprocess_jobs.py`.

### 3.8 `reference_ingest/` — reference-work ingestion

| File | Purpose |
|---|---|
| `novel_ingester.py` | Format detection + chapter splitting + metadata |
| `style_extractor.py` | Author style fingerprint |
| `chapter_splitter.py` | Standalone chapter detection |
| `skill_extraction/` | Mines novel patterns into Skill candidates |

### 3.9 `security/`

`api_key_manager.py` (Fernet-encryptable secrets), `test_mode_isolation.py` (per-test data dir isolation).

### 3.10 `ui/backend/app/` — FastAPI

**28 routers** in `routers/`:

Project-data CRUD: `project_api`, `characters_api`, `worldbook_api`, `json_storage_api` (the omnibus that exposes most `/api/data/*` collections — most now DB-backed), `editor_api`, `version_api`, `evaluation_api`.

Generation/pipeline: `generation_api` (~2.5 k LoC), `planner_api`, `prompt_api`, `events_api` (WebSocket), `formula_api`.

Reference + market: `reference_api` + `reference/` package, `extraction_api`, `analysis_api`, `marketing_api`, `market_db_api`, `reports_api`.

Model + settings: `model_api`, `settings_api`, `security_api`, `skill_api`.

Dev: `dev_actions_api`, `debug_api` (gated by `INKOCTO_DEBUG=1` or `--test` mode).

Internal: `_rag_context.py` (shim, redirects to `services/prompt_context/`).

**Services** under `services/`:

| File | Purpose |
|---|---|
| `project_paths.py` | `get_db_path()` — single resolver (honors test mode) |
| `project_store.py` | DB-backed CRUD adapters (projects, characters, worldbook, project_memory, storyline, writing_knowledge, chat_messages, editor doc, versions, foreshadowing legacy, generic blobs) |
| `model_router_factory.py` | Build LLM router per request |
| `usage_tracker.py` | Live token usage snapshot |
| `style_preferences.py` | Read user style preferences |

**Prompt context** under `services/prompt_context/`:

`builder.py` orchestrates 11 loaders in `loaders/`: `adjacent_context`, `character_cards`, `foreshadowing`, `platform_market`, `project_memory`, `reference_blocks`, `style_calibration`, `user_preferences`, `worldbook`, `writing_knowledge`, + supporting `references.py`, `skills_block.py`, `chapter_fields.py`, `budgets.py`, `utils.py`. All loaders now read from SQLite via `project_store`.

### 3.11 `ui/frontend/src/` — React SPA

14 pages — see §5 below.

---

## 4. Data Layer

### 4.1 Three SQLite files

| File | Schemas | Source of truth for |
|---|---|---|
| `data/novels.db` | project + truth + extraction | Everything project-related |
| `data/reference.db` | reference | Reference works + entries |
| `data/idea.db` | idea | Inspirations |
| `InkOctoBot_Crawler.db` (user-imported, **read-only**) | market | Novel market data (heat/trend) |

Settings/usage stay as JSON for backup simplicity:
`data/settings.json`, `data/usage.json`.

### 4.2 `novels.db` table inventory (v2)

**Project core** — created by `project_schema.py`:

| Table | CRUD endpoint | Purpose |
|---|---|---|
| `projects` | `/api/data/projects` | Project metadata |
| `chapters` | (via editor doc PUT) | Chapter outline + final_text + synopsis + time/location/characters/POV |
| `text_versions` | `/api/data/versions` | Per-chapter version history |
| `characters` | `/api/data/characters` | Character cards (Layer A/B JSON) |
| `worldbook_entries` | `/api/data/worldbook` | World building |
| `project_memories` | `/api/data/project_memory` | User-confirmed shared facts |
| `storyline_nodes` + `_edges` | `/api/data/storyline` | DAG outline |
| `writing_knowledge` | `/api/data/writing_knowledge` | Cross-project craft notes |
| `chat_messages` | `/api/data/chat_history` | AI conversation history (per scope) |
| `project_blobs` | `/api/data/{editor,calibration,reference_injection,knowledge_injection}` | Single-row-per-project KV |
| `chapter_summaries` | (internal — consolidator + Truth File #4) | L2 buffer (also re-used as Truth File 4) |
| `episodic_events` | (internal) | L4 timeline — **no foreshadow columns** in v2 |
| `information_events` | (internal) | Per-character knowledge state |
| `user_style_preferences` | (internal — EditAnalyzer writes) | Learned style/content/pacing patterns |
| `constraint_rules` | (internal) | Persistent world/plot/style/knowledge rules |

**Truth Files (state authority)** — created by `truth_schema.py`:

| Table | Purpose |
|---|---|
| `truth_current_state` | SPO triples with chapter validity windows (replaces v1 `permanent_facts`) |
| `character_ledger` | Resource/item accounting (`character_name+category+key`, operation add/subtract/set) |
| `pending_hooks` | Foreshadow state machine (`open/progressing/pressured/near_payoff/resolved/abandoned`) |
| `hook_events` | Audit log of every hook transition |
| `subplot_threads` | Parallel narrative threads |
| `emotion_arcs` | Per-character emotion trajectory |
| `character_relations` | Pairwise A→B (sentiment_score / trust_level) |
| `truth_apply_log` | Idempotency + audit |

**Reference (read by reference_api):**

`reference_works`, `reference_entries`, `project_reference_links`, `work_index_progress`, `reference_chapters`.

### 4.3 What's still JSON (intentionally or pending)

| File | Status |
|---|---|
| `data/settings.json` + `data/usage.json` | Stays JSON (app-global, easy backup) |
| `data/preferences/<pid>.json` | Pending — 600 LoC LLM-analyze logic; dedicated commit planned |
| `data/skill_learning_log/*.json` | Internal orchestrator log; low priority |
| `data/projects/*.json`, `data/characters/*.json`, `data/worldbook/*.json`, ... | **No longer used by app** (DB-backed). Migration script copies them on first run. Files left in place so user can verify migration before deleting. |
| `data/foreshadowing/<pid>.json` | **Deleted in v2** — endpoint now reads `pending_hooks` |

---

## 5. Feature List (user-facing, 14 pages)

| Page | What the user does |
|---|---|
| `ProjectListPage` | Create / list / select projects. Card grid with word count, chapter count |
| `ProjectSetupPage` | First-time project config: title/genre/logline/style profile/model preset |
| `DashboardPage` | Project overview, recent chapters, quick stats |
| `WorldBookPage` | Build world entries; LLM consistency check |
| `CharacterManagerPage` | Character cards (Layer A public / Layer B secret); AI profile generation; relationship graph |
| `StorylinePage` | Visual DAG outline editor (plot beats + edges) |
| `EditorPage` | Main writing surface — chapter list, CodeMirror, version history diff, targeted rewrite, RAG context preview |
| `AnalysisDashboardPage` | Post-chapter metrics (quality score breakdown, issues, trends) |
| `ReferenceLibraryPage` | Reference work CRUD, ingestion pipeline (chapter detection, preprocessing) |
| `ReferenceOverviewPage` | Catalog browse, link reference to project, filter by learning dimension |
| `ReferenceSearchPage` | Semantic search across references + inspirations |
| `RankingsPage` | Market data visualization (genre heat, rank lists, author trends) |
| `SkillsPage` | Registered + learned skill list, skill proposals |
| `SettingsPage` | Model provider config, API keys, cost snapshot, prompt presets |

---

## 6. Generation Pipeline (one chapter)

```
User: outline + key beats + POV + reference links
    ↓
SceneDirector.plan_scenes
    → scene_plan: [{characters, beats, location, time}, ...]
    → per-character instructions: {emotional_state, secret_goal, knowledge_boundary, must/must_not}
    ↓
ActorAgent (×N, per character per scene)
    Inputs: scene plan (filtered to this character),
            character card,
            KnowledgeIsolationEngine.build_world_view(char, chapter),
            memory layers 2-3-4 (filtered),
            adjacent chapters,
            Truth File markdown views,
            style profile, constraints
    Output: performance_record (节拍 + action + dialogue + 内心 + 氛围)
    ↓
NarratorAgent
    Output: narrator_text (environment / atmosphere / non-character POV)
    ↓
EditorWriter.assemble_chapter
    Inputs: performance_records, narrator_text, narrative_instructions
            (POV, pacing, emotional arc), style_profile, user_style_preferences
    Output: chapter_text (polished prose)
    ↓
Evaluator.evaluate_chapter
    Runs: consistency_checker, cross_chapter_checker, repetition_detector,
          slop_detector, style_drift_detector, quality_scorer
    Output: {passed, score, issues, dimension_scores}
    ↓
User reviews → edits → saves user_edit version
    ↓
EditAnalyzer
    Diffs AI text vs user edit → user_style_preferences signals
    ↓
MemoryConsolidator (cascade when L2 overflows)
    Emits TruthDeltas (StatePatch + HookDelta + EmotionArcEntry) →
    TruthFileStore.apply_deltas (atomic, idempotent)
    Mirrors free text to ChromaDB (L3)
```

All steps emit `GENERATION_STEP_COMPLETED` events to the EventBus →
WebSocket fanout to frontend.

---

## 7. Observability

### `/api/debug/*` endpoints (gated by `INKOCTO_DEBUG=1` or `--test`)

| Endpoint | Returns |
|---|---|
| `GET /api/debug/status` | Liveness probe (debug_enabled, test_mode) |
| `GET /api/debug/recent-logs` | Last N records from in-memory buffer (filter by level/logger/trace_id/session_id) |
| `GET /api/debug/trace/{trace_id}` | All logs for one HTTP request |
| `GET /api/debug/session/{session_id}` | All logs for one generation session |
| `GET /api/debug/event-bus?type=...` | Recent EventBus history |
| `GET /api/debug/diagnostics` | DB sizes, log-buffer stats, active sessions, provider reachability |
| `GET /api/debug/usage` | Live token usage snapshot |
| `GET /api/debug/rag-preview?project_id=&chapter_num=&characters=` | Assembled RAG context per block (sizes, token estimate, assembled text) |
| `GET /api/debug/truth-files?project_id=` | Dump all 7 Truth Files (raw rows + markdown views + apply log) |
| `GET /api/debug/memory?project_id=&layer={L1\|L2\|L3\|L4\|all}` | Memory state per layer + knowledge isolation + project memory |
| `GET /api/debug/workflows` | List of `WORKFLOW.md` files |

### Structured logging

JSON output (toggle with `INKOCTO_LOG_JSON=1`), `trace_id` and
`session_id` propagated via `contextvars`. Per-module loggers
`inkoctobot.agents.*`, `inkoctobot.knowledge.*`, `inkoctobot.llm.*`,
`inkoctobot.ui.*`, `inkoctobot.storage.*`.

### WORKFLOW.md files

`framework/observability/`, `agents/production/`, `agents/evaluation/`,
`knowledge/memory/`, `reference_pipeline/`, `reference_ingest/`.

---

## 8. Testing

37 test files mirroring source tree (under `tests/`):

```
tests/
├── agents/            (planner, production, evaluation, guardrails)
├── framework/         (event_bus, skill_*, observability)
├── knowledge/         (memory layers, truth, vector_store)
├── llm/               (router, providers, cost)
├── storage/           (project_schema, truth_schema, connection, v2_schema, v2_migrate)
├── truth/             (store, validators, migrate)
├── market_analysis/, reference_ingest/, reference_pipeline/
├── ui_backend/        (project_store, json_storage_db_endpoints, reference_api_globals)
├── integration/       (cross-module E2E)
└── unit/              (legacy flat tests being migrated)
```

**Run**: `pytest tests/ -q` — **currently 396 pass, 9 skip**.

**End-to-end self-check**: `python scripts/run_full_check.py` — produces
`outputs/checks/check_<timestamp>.md` (last run: 77 OK / 0 FAIL).

---

## 9. v2 Schema Redesign — Net Effect

Documented in `docs/SCHEMA_REDESIGN.md`. The 7 commits between
`da92860` and `bbeeb91` accomplished:

1. **Removed redundancy**: dropped `permanent_facts` table (→ `truth_current_state`); dropped `episodic_events.foreshadow_status` + `foreshadow_target_chapter` columns (→ `pending_hooks` + `hook_events`).
2. **Moved JSON to DB**: characters, worldbook, project_memory, storylines, writing_knowledge, chat_history, editor doc (incl. chapter text & outline), calibration, reference/knowledge injection, projects, versions — all now first-class SQLite tables in `novels.db`.
3. **Consolidator rewrite**: `MemoryConsolidator` emits `TruthDeltas` via `TruthFileStore.apply_deltas` instead of writing to `permanent_facts` + tagging `episodic_events`.
4. **Frontend wire format unchanged**: every refactor preserved the existing HTTP shape, so the React app needed zero changes.

---

## 10. Outstanding Work / Known Gaps

**Still on JSON (low-priority cleanup)**:
- `data/preferences/<pid>.json` — 600 LoC LLM-based edit analyzer; needs dedicated commit
- `data/skill_learning_log/*.json` — internal orchestrator log
- `data/settings.json` + `data/usage.json` — intentionally JSON (app-global)

**Marked deprecated**:
- `data/projects/*.json`, `data/characters/*.json`, `data/worldbook/*.json`, `data/storylines/<pid>.json`, `data/editor/<pid>.json`, etc. — no longer read by app, kept on disk for user verification; can be deleted manually
- `data/foreshadowing/<pid>.json` — endpoint returns 200 with `{deprecated: true}` on PUT, GET reads from `pending_hooks`

**Half-finished / future**:
- Marketing agent LLM-driven recommendations (currently data-only)
- Batch chapter generation UI integration
- Skill-learner refinement loop (proposes skills; needs user feedback closure)
- `agents/reference_extractors/` — package exists but only contains `skills/` (no top-level extractor classes yet)
- Performance: memory consolidation load-tested only to ~30 chapters
- Knowledge isolation: `information_events` rows currently populated manually/semi-auto; needs automatic extraction

**Architecture clean-up still pending**:
- `generation_api.py` is still 2.5 k LoC — Phase 3 split into a `routers/generation/` package is not done
- `_rag_context.py` shim exists but the original 1140-line file was already split into `services/prompt_context/`
- Frontend god-files: `EditorPage.tsx` (3208 LoC), `PreprocessPanel.tsx` (3228 LoC), `AnalysisEditors.tsx` (3053 LoC), `CharacterManagerPage.tsx` (1432), `ProjectListPage.tsx` (1217), `SettingsPage.tsx` (1094), `SkillsPage.tsx` (991) — frontend Phase 3c not started

---

## 11. Quick Reference

| Q | A |
|---|---|
| Where is project data? | `data/novels.db` (single source of truth for project state) |
| Where are reference works? | `data/reference.db` |
| Where are inspirations? | `data/idea.db` |
| Where is market data? | `InkOctoBot_Crawler.db` (user-imported, read-only) |
| Where are logs? | `outputs/logs/launcher_<timestamp>.log` (source mode), OS user state dir (PyInstaller) |
| How do I run self-check? | `python scripts/run_full_check.py` → `outputs/checks/<timestamp>.md` |
| How do I seed test data? | `python test_seed.py data_test` (or use `launcher.py --test` which auto-seeds) |
| How do I migrate v1 JSON to v2 DB? | `python scripts/migrate_to_v2_schema.py` (auto-run by `test_seed.py`) |
| Where do I see RAG output? | `GET /api/debug/rag-preview?project_id=...&chapter_num=...` |
| Where do I see Truth Files? | `GET /api/debug/truth-files?project_id=...` |
| How many tests? | 396 pass, 9 skip (across 37 files) |
