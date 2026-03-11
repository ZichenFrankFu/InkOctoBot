# Self-Learning Skill System

## Overview

InkOctoBot can automatically generate new Skills when agents detect
missing capabilities. This is inspired by [OpenClaw](https://github.com/openclaw)'s
skill auto-discovery, but with strict sandboxing.

## How It Works

1. **Trigger**: An agent detects a missing capability
   - `EditAnalyzer` detects user repeatedly editing the same type of issue
   - `Evaluator` repeatedly fails on the same evaluation dimension
   - Agent's `discover_skills()` returns empty for needed tags

2. **Generation**: `SkillLearner` uses LLM to generate skill code
   - Produces `SKILL.md` + `skill.py`
   - Follows the same format as built-in skills

3. **Validation**: AST-level safety check
   - No forbidden imports (os, subprocess, socket, requests, etc.)
   - No file write operations
   - Must inherit from `BaseSkill`
   - Must implement required methods

4. **Installation**: Written to `agents/learned_skills/{name}/`
   - `SkillRegistry`'s watchdog detects the new files
   - Skill is hot-loaded and immediately available

## Permission Boundaries

Learned skills are sandboxed:

| Permission | Allowed? | Reason |
|-----------|----------|--------|
| read_worldbook | Yes | Read world settings |
| read_characters | Yes | Read character cards |
| read_memory | Yes | Read memory context |
| read_reference_db | Yes | Read user-uploaded references |
| invoke_llm | Yes | Call LLM via router |
| write_memory | **No** | Prevent pollution |
| write_worldbook | **No** | Prevent unauthorized changes |
| file_system_write | **No** | No arbitrary file access |
| network_access | **No** | No internet access |
| execute_command | **No** | No shell commands |

## Example Workflow

```
EditAnalyzer detects: user prefers poetic prose style
    → SkillLearner proposes: "poetic_prose_rewrite" skill
    → LLM generates skill code based on built-in poetry knowledge
    → AST check passes (no forbidden imports)
    → Installed to agents/learned_skills/poetic_prose_rewrite/
    → SkillRegistry hot-loads it
    → EditorWriter can now use it for future chapters
```

## Configuration

See `config/skill_permissions.yaml` for permission settings.
