"""
PromptComposer — InkOS B1 prompt assembly abstraction.

Before this module, ``Writer._build_single_writer_prompt`` was a private
method that hardcoded the order, format, and budget of every prompt
block. Adding a new RAG block (a new Truth File kind, a new reference
material type, a new style profile section, etc.) meant editing
Writer's private method directly.

This module replaces that with a small composer pattern:

  - ``BlockProvider`` — protocol any block conforms to. ``render(ctx)``
    returns a string (or "" to omit).
  - ``PromptComposer`` — orchestrator. Builds the final prompt body
    from an ordered list of providers, with per-block budgets and
    optional section headers.

Writer's modes (``write_chapter`` single-agent, ``assemble_chapter``
multi-agent) each get a composer with the right provider set wired
in. New blocks become "add a provider"; the Writer stays untouched.

Example usage:

    composer = PromptComposer([
        OutlineBlock(),
        ChapterMetadataBlock(),
        CharacterCardsBlock(),
        WorldRulesBlock(),
        TruthBundleBlock(),
        NarrativeInstructionsBlock(),
    ])
    user_content = composer.build_user_content(ctx)
    context_parts = composer.build_context(ctx)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Protocol


# ─────────────── Context bundle ────────────────────────────────────


@dataclass
class PromptContext:
    """The shared state every BlockProvider reads from.

    A simple typed bag — adding fields is free, no provider has to
    change its signature. Providers pull just the fields they need.
    """
    # Identity + routing
    project_id: str = ""
    chapter_num: int = 1
    chapter_title: str = ""
    chapter_id: str = ""

    # Outline / synopsis / scene metadata
    outline: str = ""
    synopsis: str = ""
    time_label: str = ""
    location: str = ""
    characters: list[str] = field(default_factory=list)
    pov_character: str = ""

    # RAG blocks (raw strings; providers select / wrap them)
    character_cards: str = ""
    world_rules: str = ""
    style_profile: str = ""
    user_preferences: str = ""
    memory_context: str = ""
    adjacent_context: str = ""
    reference_blocks: str = ""
    writing_knowledge: str = ""

    # InkOS Truth Files
    truth_bundle: str = ""
    pressured_hooks_text: str = ""
    ledger_anchors_text: str = ""

    # Behavior
    narrative_instructions: str = ""
    constraints: str = ""
    target_word_count: int = 2000
    # Mode tag — providers can branch on it (e.g. single_writer vs
    # assembly_pipeline) without subclassing.
    mode: str = "single_writer"

    # Multi-agent assembly inputs
    performance_records: list[str] = field(default_factory=list)
    narrator_text: str = ""

    # Free-form extras providers might consume
    extras: dict[str, Any] = field(default_factory=dict)


# ─────────────── Provider protocol + base ──────────────────────────


class BlockProvider(Protocol):
    """A composable prompt block.

    Each provider has:
      name      — short string identifier (for logging, UI)
      role      — "user_content" (goes into the user message) or
                  "context" (goes into BaseAgent.invoke(context=...))
      render()  — returns the block text, or "" to omit cleanly

    Providers are stateless; all data flows through PromptContext.
    """
    name: str
    role: str  # "user_content" | "context"

    def render(self, ctx: PromptContext) -> str: ...


@dataclass
class FnBlock:
    """Anonymous BlockProvider built from a lambda. Useful for tests
    and for quick custom additions without writing a full class."""
    name: str
    role: str
    fn: Callable[[PromptContext], str]

    def render(self, ctx: PromptContext) -> str:
        try:
            return self.fn(ctx) or ""
        except Exception:
            return ""


# ─────────────── Composer ──────────────────────────────────────────


class PromptComposer:
    """Orders BlockProviders into a final prompt body.

    Two-output design — one string is the **user_content** (the actual
    user message), the other is the **context** string (the ``context=``
    arg of BaseAgent.invoke). Both are produced by walking the same
    provider list and dispatching on each provider's ``role``.
    """

    def __init__(self, providers: Iterable[BlockProvider]):
        self.providers: list[BlockProvider] = list(providers)

    def build_user_content(self, ctx: PromptContext) -> str:
        parts = [
            p.render(ctx) for p in self.providers if p.role == "user_content"
        ]
        return "\n\n".join(s for s in parts if s.strip())

    def build_context(self, ctx: PromptContext) -> str:
        parts = [
            p.render(ctx) for p in self.providers if p.role == "context"
        ]
        return "\n\n".join(s for s in parts if s.strip())

    def render_manifest(self, ctx: PromptContext) -> list[dict[str, Any]]:
        """Return a per-block size breakdown — useful for the UI /
        debug panel (so you can see which blocks ballooned)."""
        out: list[dict[str, Any]] = []
        for p in self.providers:
            try:
                body = p.render(ctx)
            except Exception as e:
                out.append({"name": p.name, "role": p.role,
                            "chars": 0, "error": str(e)})
                continue
            out.append({
                "name": p.name, "role": p.role,
                "chars": len(body),
                "present": bool(body.strip()),
            })
        return out


# ─────────────── Concrete providers ─────────────────────────────────


class HeaderBlock:
    """Opening line: "请直接创作第N章「标题」的完整章节正文。"""
    name = "header"
    role = "user_content"

    def render(self, ctx: PromptContext) -> str:
        opener = f"请直接创作第{ctx.chapter_num}章"
        if ctx.chapter_title:
            opener += f"「{ctx.chapter_title}」"
        opener += "的完整章节正文。"
        return opener


class ChapterMetadataBlock:
    """Synopsis / time / location / POV / character list."""
    name = "chapter_metadata"
    role = "user_content"

    def render(self, ctx: PromptContext) -> str:
        meta_lines: list[str] = []
        if ctx.synopsis:
            meta_lines.append(f"梗概：{ctx.synopsis}")
        if ctx.time_label:
            meta_lines.append(f"时间：{ctx.time_label}")
        if ctx.location:
            meta_lines.append(f"地点：{ctx.location}")
        if ctx.pov_character:
            meta_lines.append(f"POV 视角：{ctx.pov_character}")
        if ctx.characters:
            meta_lines.append(f"出场角色：{', '.join(ctx.characters)}")
        if not meta_lines:
            return ""
        return "## 章节元数据\n" + "\n".join(meta_lines)


class OutlineBlock:
    """The user's chapter outline (or synopsis as fallback)."""
    name = "outline"
    role = "user_content"

    def render(self, ctx: PromptContext) -> str:
        body = ctx.outline or ctx.synopsis
        if not body:
            return ""
        return f"## 章节大纲\n{body}"


class NarrativeInstructionsBlock:
    name = "narrative_instructions"
    role = "user_content"

    def render(self, ctx: PromptContext) -> str:
        # Fold pressured-hooks reminder + raw narrative_instructions
        parts: list[str] = []
        if ctx.narrative_instructions.strip():
            parts.append(ctx.narrative_instructions.strip())
        if ctx.pressured_hooks_text.strip():
            parts.append(ctx.pressured_hooks_text.strip())
        if not parts:
            return ""
        return "## 叙事指令\n" + "\n\n".join(parts)


class OutputRequirementsBlock:
    """Closing 输出要求 — varies slightly between single-writer and
    assembly mode."""
    name = "output_requirements"
    role = "user_content"

    def render(self, ctx: PromptContext) -> str:
        if ctx.mode == "single_writer":
            return (
                "## 输出要求\n"
                "- 这是**单 Writer 模式**：你需要独立完成场景规划、角色表演、环境描写、剧情推进\n"
                "- 严格遵循上面的章节大纲，不要漏掉任何关键节点\n"
                "- 严格遵循 POV 视角，知道与不知道的信息要分清楚\n"
                "- 严格遵循世界规则和角色设定（见上下文）\n"
                f"- 目标字数约 {ctx.target_word_count} 中文字，内容要充实完整\n"
                "- 直接输出章节正文，从第一个字就是小说正文\n"
                "- 禁止输出\"好的\"\"我明白了\"\"以下是\"等确认语、标题或导航链接\n"
                "- 禁止使用列表/编号语法（- 1. 2.）；用连贯的散文段落"
            )
        # assembly mode (multi-agent pipeline final stage)
        return (
            "## 输出要求\n"
            "- 将表演记录中的对话、动作、内心独白转化为文学化的叙事文本\n"
            "- 融入旁白的环境描写和氛围渲染\n"
            "- 保持情绪弧线的连贯性\n"
            "- 不要保留表演记录的格式标记\n"
            f"- 目标字数约 {ctx.target_word_count} 中文字，内容要充实完整\n"
            "- 直接输出章节正文，从第一个字就是小说正文\n"
            "- 禁止输出\"好的\"\"我明白了\"\"以下是\"等确认语、标题或导航链接"
        )


class PerformanceRecordsBlock:
    """Multi-agent pipeline: actor agents' performance logs."""
    name = "performance_records"
    role = "user_content"

    def render(self, ctx: PromptContext) -> str:
        if ctx.mode != "assembly":
            return ""
        real = [p for p in ctx.performance_records
                if p and p.strip() and p.strip() not in ("（无表演记录）",)]
        if not real:
            return "## 素材\n（表演记录为空，请根据叙事指令和旁白素材，自行创作章节正文。）"
        parts = ["## 表演记录"]
        for i, perf in enumerate(real):
            parts.append(f"\n--- 场景 {i+1} ---\n{perf}")
        return "\n".join(parts)


class NarratorTextBlock:
    """Multi-agent pipeline: narrator agent's atmosphere text."""
    name = "narrator_text"
    role = "user_content"

    def render(self, ctx: PromptContext) -> str:
        if ctx.mode != "assembly":
            return ""
        if not ctx.narrator_text.strip():
            return ""
        return f"## 旁白素材\n{ctx.narrator_text}"


# ─────────────── Context-role providers (BaseAgent context=) ───────


class TruthBundleContextBlock:
    name = "truth_bundle"
    role = "context"

    def render(self, ctx: PromptContext) -> str:
        if not ctx.truth_bundle.strip():
            return ""
        return f"[Truth Files 状态权威]\n{ctx.truth_bundle.strip()}"


class LedgerAnchorsContextBlock:
    name = "ledger_anchors"
    role = "context"

    def render(self, ctx: PromptContext) -> str:
        return ctx.ledger_anchors_text.strip()


class MemoryContextBlock:
    name = "memory_context"
    role = "context"

    def render(self, ctx: PromptContext) -> str:
        return ctx.memory_context.strip()


class AdjacentContextBlock:
    name = "adjacent_context"
    role = "context"

    def render(self, ctx: PromptContext) -> str:
        return ctx.adjacent_context.strip()


class CharacterCardsContextBlock:
    name = "character_cards"
    role = "context"

    def render(self, ctx: PromptContext) -> str:
        if not ctx.character_cards.strip():
            return ""
        return f"[角色档案]\n{ctx.character_cards.strip()}"


class WorldRulesContextBlock:
    name = "world_rules"
    role = "context"

    def render(self, ctx: PromptContext) -> str:
        if not ctx.world_rules.strip():
            return ""
        return f"[世界规则]\n{ctx.world_rules.strip()}"


class StyleProfileContextBlock:
    name = "style_profile"
    role = "context"

    def render(self, ctx: PromptContext) -> str:
        if not ctx.style_profile.strip():
            return ""
        return f"[风格档案]\n{ctx.style_profile.strip()}"


class UserPreferencesContextBlock:
    name = "user_preferences"
    role = "context"

    def render(self, ctx: PromptContext) -> str:
        if not ctx.user_preferences.strip():
            return ""
        return f"[用户偏好]\n{ctx.user_preferences.strip()}"


class ReferenceBlocksContextBlock:
    name = "reference_blocks"
    role = "context"

    def render(self, ctx: PromptContext) -> str:
        return ctx.reference_blocks.strip()


class WritingKnowledgeContextBlock:
    name = "writing_knowledge"
    role = "context"

    def render(self, ctx: PromptContext) -> str:
        return ctx.writing_knowledge.strip()


# ─────────────── Pre-built composers ────────────────────────────────


def single_writer_composer() -> PromptComposer:
    """Default provider set for Writer.write_chapter (mode='single_writer')."""
    return PromptComposer([
        HeaderBlock(),
        ChapterMetadataBlock(),
        OutlineBlock(),
        NarrativeInstructionsBlock(),
        OutputRequirementsBlock(),
        # context-role providers
        TruthBundleContextBlock(),
        LedgerAnchorsContextBlock(),
        MemoryContextBlock(),
        AdjacentContextBlock(),
        CharacterCardsContextBlock(),
        WorldRulesContextBlock(),
        StyleProfileContextBlock(),
        UserPreferencesContextBlock(),
        ReferenceBlocksContextBlock(),
        WritingKnowledgeContextBlock(),
    ])


def assembly_composer() -> PromptComposer:
    """Provider set for Writer.assemble_chapter (mode='assembly')."""
    return PromptComposer([
        HeaderBlock(),
        NarrativeInstructionsBlock(),
        PerformanceRecordsBlock(),
        NarratorTextBlock(),
        OutputRequirementsBlock(),
        # context-role providers
        TruthBundleContextBlock(),
        MemoryContextBlock(),
        StyleProfileContextBlock(),
        UserPreferencesContextBlock(),
    ])
