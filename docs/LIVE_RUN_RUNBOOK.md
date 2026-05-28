# Live-Run Runbook — InkOctoBot

A full-feature walkthrough you can run on your own machine to validate
the entire authoring workflow end-to-end with real models, real
embeddings, and your real market / reference data.

This complements the sandbox `tests/integration/test_realdata_e2e.py`
suite, which proves the pipes connect but **cannot** assess real model
quality or hit external services. Use this runbook to fill that gap.

## Prerequisites

| Tool | Why |
|---|---|
| Python 3.11+ with project requirements installed | running the backend |
| LLM provider API key (Anthropic / OpenAI / 通义 / DeepSeek / …) | real generation |
| `sentence-transformers` | real embeddings (zh: bge-base-zh, en: bge-base-en) |
| `chromadb` | real vector index for chapter chunks |
| **Real market DB** (`market_data.db` from your Crawler) | Marketing Agent and trend analysis |
| **Real reference DB** (`references.db`) | reference-work integration |

## ⚠️ Critical: match the backend's DB path

`launcher.py` uses **two** DB files:

- **production mode** (`python launcher.py`) → `data/novels.db`
- **test mode** (`python launcher.py --test`) → `data_test/novels.db`

`live_run_check.py` mirrors this with a `--test` flag. **If your
backend was launched with `--test`, you must also pass `--test` to
every `live_run_check.py` invocation** — otherwise this script writes
to / reads from a different DB file than the UI and you'll see "the
project doesn't show up" with no obvious cause.

The `--seed` report now prints which DB path it wrote to in big
letters — eyeball it against your backend's startup logs to confirm.

## Three-step pre-flight

```bash
# 1. Environment self-check  (add --test if backend uses --test)
python scripts/live_run_check.py --env

# 2. Seed the "轨道挽歌" demo project (does NOT touch market/reference DBs).
#    Add --test if your backend was started with --test:
python scripts/live_run_check.py --seed
# or
python scripts/live_run_check.py --test --seed

# 3. Restart the backend (or just hit refresh in the UI), then walk
#    through the sections below.

# 4. Post-walkthrough DB inspection (match the --test flag from step 2)
python scripts/live_run_check.py --verify --project rt_proj
```

The `--env` report flags missing dependencies; the `--seed` report
prints which `chapter_ids` were inserted; the `--verify` report tells
you which tables got rows and (importantly) which were *expected* to
get rows but didn't, with a diagnostic hint for each empty table.

## The 34-step walkthrough

Each step lists what to do in the UI and what to check (DB table, log
line, file).

### A. Preparation — the seed already created angles & chapters

The seed script inserts the **"轨道挽歌"** sci-fi project: 5 chapters
with progressively aerospace-focused special_requirements (the signal
that should trigger the Part B domain detector by chapter 5).

1. Open the editor — the project list should show **轨道挽歌**.
   → `projects` table has one row with `project_id='rt_proj'`.
2. Characters tab → **林越** and **邓星** with Layer A (qualitative)
   and Layer B (0-100 sliders) populated.
   → `characters` table has two rows.
3. Worldbook tab → **启明号空间站** and **曲率残响**.
   → `worldbook_entries` has two rows in 技术 category.
4. Storyline tab → **结构危机** main thread (best-effort: may be
   absent depending on schema version).
5. Editor sidebar → tree of 5 chapters; each has a synopsis + a
   distinct `special_requirements` line.

### B. Market + reference (your real data)

The seed deliberately leaves these alone. Point the app at your own
files.

6. Settings → set the market DB path to your real `market_data.db`.
   Save returns `took_effect: true`.
7. Market page → browse rankings / charts / Marketing Agent gives a
   recommendation. If it can't, check that the role
   `marketing_advice` resolves a provider in `models.yaml`.
8. References page → link 1–2 real reference works to **轨道挽歌**
   and select the dimensions you want (style / characters / plot /
   pacing).

### C. Generate chapter 1 (multi-agent pipeline)

9. Editor → select **ch1** → AI panel → "集群式生成".
10. Watch the 4-step event stream
    (SceneDirector → Actor → Narrator → Writer → Evaluator). At each
    confirm gate, accept the result.
11. Finalize. The post-commit pipeline fires.
    → `text_versions` gets a `source='ai'` row.
    → `commit_tasks` should have 6 rows (summarizer / event /
       state / snapshot completed; chromadb completed-or-failed
       depending on dependency availability; skill_emitter skipped).
    → `chapter_summaries` has a row; `truth_current_state` has the
       initial SPOs; the notification centre may show an audit hint.
12. ChromaDB — open the embedding inspector. The collection for
    this project should have chunks (real embeddings, not stubs).

### D. Review + edit (triggers the Part A capture)

13. Evaluator pane shows 5-dimension scores + issues.
14. In the editor body, change a few sentences to nudge toward the
    target style (e.g., trim adverbs, add a technical detail). Save.
15. → `edit_observations` gets a row whose `special_requirement`
    matches **ch1**'s line and whose `consumed=0`.
16. Version history → diff vs the AI version → roll back once to
    verify the rollback path works.

### E. Repeat for ch2 → ch5 (build continuity + accumulate captures)

17. Generate each chapter in order. Edit each one before moving on,
    keeping the aerospace technical edits going (a couple of changes
    per chapter is plenty).
18. While generating **ch3**, peek at the prompt the Writer receives.
    The `memory_context` block should carry summaries of **ch1+ch2**;
    the `truth_bundle` should have SPOs from earlier chapters.
19. By **ch4** and **ch5**, `truth_current_state` should have
    several rows showing 林越's location / status changing across
    chapters (not just the latest one).

### F. Part A trigger (after ch5's user-edit save)

20. ★ With the default threshold of **5**, saving the user-edited
    version of **ch5** should fire batch extraction.
    → `user_style_preferences` gains rows. Rows whose source was a
      `special_requirement` will have `confidence >= 0.4`;
      edit-derived rows start at 0.15.
    → All five `edit_observations` rows flip to `consumed=1`.
21. Regenerate **ch5** (or kick off **ch6** if you've added one).
    The Writer's prompt should now include a `[用户写作偏好]` block
    listing the high-confidence learned preferences.
22. ★ Notifications: a "建议补充【航天动力学】知识" entry should
    show up. → `domain_suggestions` has one row with `status='proposed'`
    and `domain` mentioning 航天 / orbital / aerospace.

### G. Part B — domain knowledge compile (two gates)

23. **Gate 1**: click "研究" on the domain suggestion. Pick mode.
    - **API mode**: web-search LLM compile. Confirm cost dialog.
    - **Manual mode**: free; system returns a research-prompt to
      paste into a browser LLM (Claude.ai / 通义网页版 /
      DeepSeek 网页版) — bring the answer back.
24. (Manual only) Run the prompt in a browser LLM. The compiled
    answer should be structured (核心概念 / 常见误区 / 关键术语 /
    写作可用细节 / 参考来源) and hedged with "(存疑)" where the
    model isn't sure.
25. **Gate 2 (soft door)**: preview the compiled content. **You do
    NOT have to fact-check it** — that's not the bar. You may edit
    minor things, then save. Or discard.
26. → `agents/knowledge_skills/<slug>/SKILL.md` exists on disk with
    the "AI 编译 非权威" disclaimer in both metadata and prompt body.
    → `skill_index` has a new row; once an embedding is computed it
    will be searchable.
27. Generate a new chapter that mentions orbital mechanics or EVA.
    Inspect the prompt — the `skills` block should include this
    knowledge skill, prefixed with the "AI 编译，背景参考" banner.

### H. Cross-cutting checks

28. **LLM audit** — `/api/llm-audit` or its UI surface lists every
    call site fired during the run, with token counts and parse
    status.
29. **Pipeline history** — `pipeline_sessions` rows persist across
    backend restarts.
30. **Embedding switch** — toggle the language mode or model key in
    settings. The `SwitchResult` notes `need_reindex`; run the
    reindex action and watch it complete.
31. **Skills management** — disable one skill. The next chapter
    generation should NOT show it in the skills block.
32. **Manual mode end-to-end** — flip the global `manual_mode`
    toggle, generate an entire chapter by pasting each LLM call's
    output. This proves the system works without an API key.

### I. Wrap-up

33. Run `python scripts/live_run_check.py --verify --project rt_proj`.
    The report walks every relevant table; the warnings call out
    anything that should have rows but doesn't.
34. Open the Markdown report. Cross-reference with the table below:

| Table empty? | Most likely cause | Where to look |
|---|---|---|
| `text_versions` | commit never wrote — pipeline crashed before finalize | event stream, INKOCTO_DEBUG logs |
| `commit_tasks` | `fire_and_forget_from_handler` not called | save-version handler |
| `chapter_summaries` | summarizer sub-task failed | `commit_pipeline/summarizer` |
| `truth_current_state` | settlement failed or parsed empty | `storyland_state` settlement |
| `edit_observations` | edits went in with `source != 'user_edit'` | `editor_api.save_version`, the source field |
| `user_style_preferences` | batch extraction never fired or failed | check threshold vs edit count; LLM audit for `edit_learning.batch_extract` |
| `domain_suggestions` | extractor returned `domain_gap.needs_domain_knowledge=false` | check the canned-vs-real LLM response shape |
| `skill_index` | first prompt build hasn't synced from registry | trigger one more generation to force `sync_from_registry` |
| ChromaDB collection 0 chunks | chromadb / embedding model not installed | re-run `--env`, install missing deps |

## What this proves vs. doesn't

**Proves** (when all walkthrough steps succeed):

- End-to-end authoring loop with real models works for this project
- Cross-chapter memory accumulates from real summaries / truth state
- Edit-learning captures real edits, batch-extracts at threshold,
  and gets back into prompts
- Domain knowledge can be compiled, reviewed, accepted, and recalled
- Market / reference integration works against your real DBs
- Manual paste mode is a viable no-API-key escape hatch

**Does NOT prove** (out of scope):

- Specific generation quality — that's subjective, evaluate by hand
- Catastrophic-failure modes on networks / providers you didn't use
- Behaviour under datasets much larger than your current one
