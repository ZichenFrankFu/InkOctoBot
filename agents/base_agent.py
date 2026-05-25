"""
Base agent abstraction.

Every agent in the pipeline (Scene Director, Actor, Editor-Writer, etc.)
inherits from BaseAgent which provides:
  - Prompt template loading from config/prompts/{agent_name}.yaml
  - Unified LLM invocation via ModelRouter
  - Structured output parsing (JSON / YAML blocks)
  - Token budget tracking
  - Event bus integration
  - Skill registry integration (call_skill / discover_skills)
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, TYPE_CHECKING

import yaml

from llm.base import LLMMessage, LLMResponse

if TYPE_CHECKING:
    from framework.skill_registry import SkillRegistry

logger = logging.getLogger("inkoctobot.agents.base_agent")

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "config" / "prompts"

# Maps an agent_name to the registry key whose (possibly user-overridden)
# template should take precedence over the static config/prompts/*.yaml
# `system` block. Lets the Settings → LLM Prompt tab edit these prompts.
_AGENT_PROMPT_KEYS: dict[str, str] = {
    "scene_director": "pipeline.scene_direct",
    "actor_agent": "pipeline.actor",
    "narrator_agent": "pipeline.narrator",
    "editor_writer": "pipeline.editor",
}


class BaseAgent:
    """Base class for all creative pipeline agents."""

    agent_name: str = "base"

    def __init__(
        self,
        router: Any,                    # ModelRouter instance
        *,
        project_id: str = "",
        event_bus: Any | None = None,    # EventBus instance
        extra_system: str = "",
        skill_registry: SkillRegistry | None = None,
        memory_manager: Any | None = None,
    ):
        self.router = router
        self.project_id = project_id
        self.event_bus = event_bus
        self.skills = skill_registry
        self.memory = memory_manager
        self._extra_system = extra_system
        self._prompt_template = self._load_prompt()
        self._logger = logging.getLogger(f"inkoctobot.agents.{self.agent_name}")
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._call_count = 0

    def _load_prompt(self) -> dict[str, str]:
        path = _PROMPT_DIR / f"{self.agent_name}.yaml"
        if not path.exists():
            return {"system": ""}
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {"system": ""}

    @property
    def system_prompt(self) -> str:
        base = self._prompt_template.get("system", "")
        # Prefer a user-editable prompt from the registry (Settings → LLM
        # Prompt). Falls back to the config/prompts/*.yaml `system` block.
        reg_key = _AGENT_PROMPT_KEYS.get(self.agent_name)
        if reg_key:
            try:
                from reference_pipeline.prompts import get_template
                base = get_template(reg_key) or base
            except Exception:
                pass
        if self._extra_system:
            base += "\n\n" + self._extra_system
        return base.strip()

    def build_messages(
        self,
        user_content: str,
        *,
        context: str = "",
        constraints: str = "",
        history: list[LLMMessage] | None = None,
    ) -> list[LLMMessage]:
        """Assemble the full message list for an LLM call."""
        msgs: list[LLMMessage] = []
        system_parts = [self.system_prompt]
        if constraints:
            system_parts.append(f"\n## 约束规则\n{constraints}")
        if context:
            system_parts.append(f"\n## 上下文信息\n{context}")
        msgs.append(LLMMessage(role="system", content="\n".join(system_parts)))
        if history:
            msgs.extend(history)
        msgs.append(LLMMessage(role="user", content=user_content))
        return msgs

    async def invoke(
        self,
        user_content: str,
        *,
        context: str = "",
        constraints: str = "",
        history: list[LLMMessage] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        parse_json: bool = False,
    ) -> LLMResponse:
        """Call the LLM through the router.

        Logs the prompt envelope at INFO (message count + total chars) so
        a developer can see "what got sent" without dumping the full
        prompt; DEBUG-level logs the full message bodies for forensic
        analysis. LLM errors propagate with a logged exc_info so failures
        are never silent (was GAP 2 in the architecture review).
        """
        msgs = self.build_messages(
            user_content, context=context, constraints=constraints, history=history,
        )
        total_chars = sum(len(m.content) for m in msgs)
        self._logger.info(
            "invoke prep msgs=%d chars=%d temp=%s max_tokens=%s",
            len(msgs), total_chars, temperature, max_tokens,
        )
        if self._logger.isEnabledFor(logging.DEBUG):
            self._logger.debug(
                "invoke messages=%s",
                json.dumps(
                    [{"role": m.role, "content": m.content} for m in msgs],
                    ensure_ascii=False,
                )[:8000],
            )
        t0 = time.monotonic()
        try:
            resp = await self.router.generate(
                agent_role=self.agent_name, messages=msgs,
                temperature=temperature, max_tokens=max_tokens,
            )
        except Exception:
            elapsed = time.monotonic() - t0
            self._logger.exception(
                "invoke FAILED after %.1fs (msgs=%d chars=%d)",
                elapsed, len(msgs), total_chars,
            )
            raise
        elapsed = time.monotonic() - t0
        self._total_input_tokens += resp.input_tokens
        self._total_output_tokens += resp.output_tokens
        self._call_count += 1
        self._logger.info(
            "invoke done #%d in=%d out=%d %.1fs",
            self._call_count, resp.input_tokens, resp.output_tokens, elapsed,
        )
        if self._logger.isEnabledFor(logging.DEBUG):
            self._logger.debug(
                "invoke response=%s",
                (resp.content or "")[:4000],
            )
        if parse_json:
            resp.raw["parsed"] = self.extract_json(resp.content)
        return resp

    async def invoke_stream(
        self,
        user_content: str,
        *,
        context: str = "",
        constraints: str = "",
        history: list[LLMMessage] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ):
        """Stream tokens from the LLM."""
        msgs = self.build_messages(
            user_content, context=context, constraints=constraints, history=history,
        )
        async for token in self.router.generate_stream(
            agent_role=self.agent_name, messages=msgs,
            temperature=temperature, max_tokens=max_tokens,
        ):
            yield token

    # ── Skill integration ────────────────────────────────────────

    def get_skill(self, name: str) -> Any:
        """Get a registered skill by name."""
        if self.skills is None:
            raise RuntimeError("No SkillRegistry configured for this agent")
        return self.skills.get(name)

    def discover_skills(self, tags: list[str]) -> list[Any]:
        """Discover skills matching any of the given tags."""
        if self.skills is None:
            return []
        return self.skills.find_by_tags(tags)

    async def call_skill(self, skill_name: str, inputs: dict[str, Any]) -> dict[str, Any]:
        """Call a skill and emit a SKILL_EXECUTED event."""
        skill = self.get_skill(skill_name)
        result = await skill.execute_with_messages(inputs, self.router)
        self.emit_event("SKILL_EXECUTED", {
            "agent": self.agent_name,
            "skill": skill_name,
            "success": True,
        })
        return result

    # ── Event integration ────────────────────────────────────────

    def emit_event(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        """Publish an event if an event bus is configured."""
        if self.event_bus is None:
            return
        from framework.event_types import Event
        self.event_bus.publish(Event(
            event_type=event_type,
            source=self.agent_name,
            project_id=self.project_id,
            data=data or {},
        ))

    @property
    def usage_stats(self) -> dict[str, int]:
        return {
            "calls": self._call_count,
            "input_tokens": self._total_input_tokens,
            "output_tokens": self._total_output_tokens,
        }

    # ── Output parsing helpers ──

    @staticmethod
    def extract_json(text: str) -> Any:
        """Extract the first JSON object or array from LLM output."""
        for pattern in [r"```json\s*([\s\S]*?)```", r"```\s*([\s\S]*?)```"]:
            m = re.search(pattern, text)
            if m:
                try:
                    return json.loads(m.group(1))
                except json.JSONDecodeError:
                    continue
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def extract_yaml(text: str) -> Any:
        """Extract the first YAML block from LLM output."""
        m = re.search(r"```ya?ml\s*([\s\S]*?)```", text)
        if m:
            try:
                return yaml.safe_load(m.group(1))
            except yaml.YAMLError:
                pass
        return None

    @staticmethod
    def extract_sections(text: str) -> dict[str, str]:
        """Split markdown-style ## sections into a dict."""
        sections: dict[str, str] = {}
        current_key = ""
        lines: list[str] = []
        for line in text.split("\n"):
            if line.startswith("## "):
                if current_key:
                    sections[current_key] = "\n".join(lines).strip()
                current_key = line[3:].strip()
                lines = []
            else:
                lines.append(line)
        if current_key:
            sections[current_key] = "\n".join(lines).strip()
        return sections
