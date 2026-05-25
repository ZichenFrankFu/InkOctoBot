# InkOctoBot Architecture

## Overview

InkOctoBot is an AI-powered novel creation system organized around an **Agent + Skill** architecture.

```
┌─────────────────────────────────────────────┐
│                  CLI / UI                     │
│         (cli.py / launcher.py)               │
├─────────────────────────────────────────────┤
│              Agent Layer                      │
│   planner / production / evaluation / analysis│
│         ┌──────────────┐                     │
│         │  BaseAgent   │ ← orchestrates      │
│         │  ┌────────┐  │                     │
│         │  │ Skills  │  │ ← atomic LLM units │
│         │  └────────┘  │                     │
│         └──────────────┘                     │
├─────────────────────────────────────────────┤
│              Core Framework                   │
│  SkillRegistry │ EventBus │ Config │ Triggers│
├─────────────────────────────────────────────┤
│           Models (LLM Providers)             │
│  Router │ OpenAI │ Anthropic │ Ollama │ ...  │
├─────────────────────────────────────────────┤
│          RAG / Memory / Constraints          │
│  VectorStore │ 4-Layer Memory │ Constraints  │
├─────────────────────────────────────────────┤
│              Database / Storage              │
│     App DB │ Crawler DB │ Reference DB       │
└─────────────────────────────────────────────┘
```

## Directory Structure

```
InkOctoBot/
├── cli.py                  # Typer CLI entry point
├── launcher.py             # PyWebView GUI launcher
├── core/                   # Core framework (non-LLM)
│   ├── config.py           # Unified configuration loader
│   ├── log_setup.py        # Structured logging
│   ├── event_bus.py        # Event pub/sub
│   ├── event_types.py      # Event type definitions
│   ├── triggers.py         # Trigger registry
│   ├── skill_registry.py   # Skill discovery + registration + hot-reload
│   └── skill_learner.py    # Self-learning skill generation (sandboxed)
│
├── models/                 # LLM provider layer
│   ├── base.py             # Abstract provider interface
│   ├── router.py           # Model routing by agent role
│   ├── cost_estimator.py   # API cost estimation
│   ├── ab_compare.py       # A/B comparison engine
│   └── *_provider.py       # Provider implementations
│
├── agents/                 # Agent layer (Skill orchestration)
│   ├── base_agent.py       # Agent base class
│   ├── base_skill.py       # Skill base class
│   ├── planner/            # Planning agents + skills
│   ├── production/         # Content production agents + skills
│   ├── evaluation/         # Evaluation agents + skills
│   ├── analysis/           # Analysis agents + skills
│   └── learned_skills/     # Self-learning generated skills (sandbox)
│
├── knowledge/              # Retrieval & Memory
├── constraints/            # Constraint system (non-LLM)
├── analysis/               # Market data analysis (non-LLM)
├── preprocessing/          # Data preprocessing
├── database/               # Database layer
├── security/               # Security utilities
├── config/                 # Configuration files
├── ui/                     # Web UI (FastAPI + React)
├── tests/                  # Test suite
└── docs/                   # Documentation
```

## Key Concepts

### Skill
An **atomic LLM interaction unit**. Each Skill:
- Has a `SKILL.md` declaration and a `skill.py` implementation
- Defines `input_schema → build_prompt → LLM call → parse_output → output_schema`
- Is independently testable without UI
- See [SKILL_AUTHORING.md](SKILL_AUTHORING.md)

### Agent
A **Skill orchestrator** with state management. Each Agent:
- Inherits from `BaseAgent`
- Uses `SkillRegistry` to discover and call Skills
- Manages multi-step workflows and decision logic
- Emits events via `EventBus`

### SkillRegistry
Central registry for all Skills:
- Scans `agents/*/skills/` at startup
- Watches `agents/learned_skills/` for hot-reload
- Queryable by name or tags

### Three Database Architecture
| Database | Contents | Access Pattern |
|----------|----------|----------------|
| InkOctoBot_Crawler.db | Rankings, novels, tags | Read-only, imported from crawler |
| InkOctoBot_Reference.db | Reference works, style fingerprints | Read-mostly |
| InkOctoBot_App.db | Projects, chapters, memory, preferences | Read-write, runtime core |
