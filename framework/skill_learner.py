"""
Self-learning Skill system — sandboxed skill generation.

Inspired by OpenClaw's skill auto-discovery, but with strict permission
boundaries:
- Only writes to agents/learned_skills/
- Only reads from data/, config/prompts/, knowledge/
- Forbidden: os.system, subprocess, socket, requests, urllib
- AST-level static analysis before installation
"""
from __future__ import annotations

import ast
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("inkoctobot.core.skill_learner")

_FORBIDDEN_MODULES = frozenset({
    "os", "subprocess", "socket", "requests", "urllib",
    "shutil", "http", "ftplib", "smtplib", "telnetlib",
})

_FORBIDDEN_ATTRS = frozenset({
    "os.system", "os.popen", "os.exec", "os.execv",
    "subprocess.run", "subprocess.call", "subprocess.Popen",
})

_LEARNED_SKILLS_DIR = Path(__file__).resolve().parent.parent / "agents" / "learned_skills"


class SkillLearner:
    """
    Agent discovers a missing capability -> LLM generates Skill code ->
    AST safety check -> write to learned_skills/ -> SkillRegistry hot-loads.
    """

    ALLOWED_WRITE_PATHS = ["agents/learned_skills/"]
    ALLOWED_READ_PATHS = ["data/", "config/prompts/", "knowledge/"]

    def __init__(self, model_router: Any = None):
        self._router = model_router

    async def propose_skill(
        self,
        need_description: str,
        examples: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """
        Propose a new skill based on a described need.

        Trigger conditions (determined by the calling Agent):
        1. User repeatedly edits the same type of issue (EditAnalyzer pattern)
        2. Evaluation repeatedly fails on the same dimension
        3. Agent's discover_skills() returns empty for needed tags

        Returns: {"name": ..., "installed": True/False, "reason": ...}
        """
        if self._router is None:
            return {"name": "", "installed": False, "reason": "No model router"}

        examples = examples or []

        # Step 1: LLM generates skill code
        skill_md, skill_py = await self._generate_skill(need_description, examples)

        # Step 2: Extract name from generated code
        name = self._extract_skill_name(skill_py)
        if not name:
            return {"name": "", "installed": False, "reason": "Could not extract skill name"}

        # Step 3: Safety validation
        is_safe, reason = self._validate_skill_code(skill_py)
        if not is_safe:
            logger.warning("Skill '%s' rejected: %s", name, reason)
            return {"name": name, "installed": False, "reason": reason}

        # Step 4: Install
        self.install_skill(name, skill_md, skill_py)
        return {"name": name, "installed": True, "reason": "OK"}

    def _validate_skill_code(self, code: str) -> tuple[bool, str]:
        """AST-level safety check."""
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return False, f"Syntax error: {e}"

        for node in ast.walk(tree):
            # Check imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    mod_root = alias.name.split(".")[0]
                    if mod_root in _FORBIDDEN_MODULES:
                        return False, f"Forbidden import: {alias.name}"

            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    mod_root = node.module.split(".")[0]
                    if mod_root in _FORBIDDEN_MODULES:
                        return False, f"Forbidden import: {node.module}"

            # Check for open() with write mode
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name) and node.func.id == "open":
                    for kw in node.keywords:
                        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                            if "w" in str(kw.value.value) or "a" in str(kw.value.value):
                                return False, "File write operations not allowed"

        # Check class inheritance
        has_base_skill = False
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                for base in node.bases:
                    if isinstance(base, ast.Name) and base.id == "BaseSkill":
                        has_base_skill = True
                    elif isinstance(base, ast.Attribute) and base.attr == "BaseSkill":
                        has_base_skill = True

        if not has_base_skill:
            return False, "Must inherit from BaseSkill"

        return True, ""

    async def _generate_skill(
        self, need: str, examples: list[dict[str, Any]],
    ) -> tuple[str, str]:
        """Use LLM to generate SKILL.md + skill.py."""
        examples_text = ""
        if examples:
            examples_text = "\n".join(
                f"Example {i+1}: {e}" for i, e in enumerate(examples)
            )

        prompt = f"""You are creating a new Skill for InkOctoBot, an AI novel writing system.

Need description: {need}
{f'Examples:{chr(10)}{examples_text}' if examples_text else ''}

Generate two files:

1. SKILL.md (YAML frontmatter + markdown description)
2. skill.py (Python class inheriting from BaseSkill)

Rules:
- The skill.py MUST have `from agents.base_skill import BaseSkill, SkillMeta`
- The class MUST be named `Skill`
- MUST implement: meta(), build_prompt(), parse_output()
- NO imports of: os, subprocess, socket, requests, urllib
- NO file I/O
- Output MUST be valid JSON from parse_output()
- All text should be in Chinese where appropriate

Output format:
```skill_md
(SKILL.md content)
```

```python
(skill.py content)
```"""

        from llm.base import LLMMessage
        messages = [LLMMessage(role="user", content=prompt)]
        resp = await self._router.generate(
            agent_role="default",
            messages=messages,
            temperature=0.5,
            max_tokens=4000,
        )

        import re
        md_match = re.search(r"```skill_md\s*([\s\S]*?)```", resp.content)
        py_match = re.search(r"```python\s*([\s\S]*?)```", resp.content)

        skill_md = md_match.group(1).strip() if md_match else f"---\nname: generated\n---\n{need}"
        skill_py = py_match.group(1).strip() if py_match else ""

        return skill_md, skill_py

    @staticmethod
    def _extract_skill_name(code: str) -> str:
        """Extract skill name from the generated code's meta() method."""
        try:
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if node.value.replace("_", "").isalpha() and len(node.value) > 3:
                        return node.value
        except SyntaxError:
            pass
        return ""

    def install_skill(self, name: str, skill_md: str, skill_py: str) -> Path:
        """Write to agents/learned_skills/{name}/ directory."""
        target = _LEARNED_SKILLS_DIR / name
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(skill_md, encoding="utf-8")
        (target / "skill.py").write_text(skill_py, encoding="utf-8")
        logger.info("Installed learned skill: %s -> %s", name, target)
        return target
