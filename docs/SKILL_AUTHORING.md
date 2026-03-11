# Skill Authoring Guide

## Quick Start

Create a new skill:
```bash
python cli.py skill create my_new_skill evaluation
```

This creates:
```
agents/evaluation/skills/my_new_skill/
├── SKILL.md    # Declaration file
└── skill.py    # Implementation
```

## SKILL.md Format

```yaml
---
name: my_skill_name           # unique snake_case identifier
display_name: 我的技能          # display name (Chinese)
description: 技能功能描述       # functional description
version: 1.0.0
model_role: default            # maps to models.yaml role
tags: [evaluation, quality]    # for Agent tag-based discovery
permissions: [read_worldbook]  # required permissions
---

## Description
Detailed description of what this skill does.

## Input
- `text`: The text to analyze
- `context`: Optional context information

## Output
- `result`: Analysis result
- `score`: Numeric score (0-100)

## Usage
- Evaluator Agent calls this after chapter generation
- Can be tested independently via CLI
```

## skill.py Format

```python
from agents.base_skill import BaseSkill, SkillMeta


class Skill(BaseSkill):
    \"\"\"Skill: my_skill_name\"\"\"

    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="my_skill_name",
            display_name="我的技能",
            description="技能功能描述",
            model_role="default",
            tags=["evaluation", "quality"],
            permissions=["read_worldbook"],
        )

    async def build_prompt(self, inputs: dict) -> str:
        text = inputs.get("text", "")
        return f"请分析以下文本:\\n{text}"

    async def parse_output(self, raw: str) -> dict:
        parsed = self.extract_json(raw)
        if parsed:
            return parsed
        return {"raw": raw}
```

## Rule-Based Skills (No LLM)

For skills that don't need LLM, override `execute()`:

```python
class Skill(BaseSkill):
    async def execute(self, inputs: dict, model_router=None) -> dict:
        text = inputs.get("text", "")
        # Pure Python computation
        return {"score": compute_score(text)}

    async def build_prompt(self, inputs): return ""
    async def parse_output(self, raw): return {}
```

## Testing

```bash
# List all skills
python cli.py skill list

# Test with JSON input
python cli.py skill test my_skill_name --input test_input.json

# Run pytest
pytest tests/unit/test_skills/test_my_skill_name.py -v
```

## Naming Conventions

- Skill name: `snake_case` (e.g., `consistency_check`)
- Skill directory: matches skill name
- Class name: always `Skill`
- Tags: lowercase, categorize by agent type
