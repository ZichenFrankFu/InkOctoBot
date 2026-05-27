"""Sub-task registry for the post-commit pipeline.

Each post-commit task is a Python function::

    async def run(ctx: SubTaskContext) -> dict[str, Any]:
        # do work; raise on failure
        return {"summary": "..."}

Plus a few static fields registered alongside (task_type identifier,
manual_action_url for the failure notification, default enabled
flag). Phase 1 ships 6 no-op stubs; Phases 2-5 replace each ``run``
with the real implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType
from typing import Any, Awaitable

from . import (
    chromadb_indexer, event_extractor, skill_emitter,
    snapshot_detector, state_extractor, summarizer,
)


@dataclass
class SubTaskContext:
    """Inputs handed to every sub-task. New fields go here so we don't
    explode the signature of every ``run`` function over time."""

    project_id: str
    chapter_id: str
    db_path: str
    chapter_text: str = ""
    chapter_num: int = 0


@dataclass(frozen=True)
class SubTaskDef:
    """Definition + late-bound runner.

    ``module`` is the sub-task module. The runner re-reads
    ``module.run`` on every call so unittest patches on the module's
    ``run`` symbol actually reach the pipeline — capturing the
    callable at registry-build time would bypass any later patch.
    """

    task_type: str
    module: ModuleType
    manual_action_url_template: str
    default_enabled: bool = True
    notification_title_failed: str = ""
    notification_description_failed: str = ""

    async def run(self, ctx: "SubTaskContext") -> dict[str, Any]:
        return await self.module.run(ctx)


_REGISTRY: tuple[SubTaskDef, ...] = (
    SubTaskDef(
        task_type="chapter_summarizer",
        module=summarizer,
        manual_action_url_template="/chapters/{chapter_id}/edit-summary",
        notification_title_failed="章节摘要生成失败",
        notification_description_failed=(
            "本章摘要任务多次重试后仍失败。摘要是 Reader Memory L1 的数据源，"
            "缺失会影响后续章节生成质量。请点击下方按钮手动生成。"
        ),
    ),
    SubTaskDef(
        task_type="event_extractor",
        module=event_extractor,
        manual_action_url_template="/chapters/{chapter_id}/edit-events",
        notification_title_failed="关键事件提取失败",
        notification_description_failed=(
            "本章关键事件提取多次重试失败。事件用于 Reader Memory L4，"
            "请手动添加 1-5 条本章重要事件。"
        ),
    ),
    SubTaskDef(
        task_type="chromadb_indexer",
        module=chromadb_indexer,
        manual_action_url_template="/settings/embedding",
        notification_title_failed="章节语义索引失败",
        notification_description_failed=(
            "本章 ChromaDB 索引构建失败（影响 Reader Memory L3）。"
            "可能是 embedding 服务问题，请查看 embedding 状态。"
        ),
    ),
    SubTaskDef(
        task_type="state_extractor",
        module=state_extractor,
        manual_action_url_template="/chapters/{chapter_id}/extract-state-manually",
        notification_title_failed="状态变更自动提取失败",
        notification_description_failed=(
            "本章 state/ledger/emotion 自动提取失败。Writer 仍可工作"
            "（使用旧 state），但建议手动补充本章变更以维持一致性。"
        ),
    ),
    SubTaskDef(
        task_type="snapshot_detector",
        module=snapshot_detector,
        manual_action_url_template="/characters/snapshots",
        notification_title_failed="角色快照检测失败",
        notification_description_failed=(
            "本章 snapshot 触发检测失败。请到角色快照页查看待绑定项。"
        ),
    ),
    SubTaskDef(
        task_type="skill_emitter",
        module=skill_emitter,
        manual_action_url_template="/skills/manual-create",
        default_enabled=False,    # opt-in per spec
        notification_title_failed="技能学习失败",
        notification_description_failed=(
            "本章 skill_emitter 失败（不影响主流程）。可手动创建技能。"
        ),
    ),
)


def all_sub_tasks() -> tuple[SubTaskDef, ...]:
    """Every registered sub-task in declaration order."""
    return _REGISTRY


def enabled_sub_tasks() -> tuple[SubTaskDef, ...]:
    """Default-enabled sub-tasks. Spec § 模块 1 says each task can be
    independently toggled — wire to settings.json later; for now this
    honors ``default_enabled``."""
    return tuple(t for t in _REGISTRY if t.default_enabled)


def manual_action_url_for(task_type: str, chapter_id: str) -> str:
    for t in _REGISTRY:
        if t.task_type == task_type:
            return t.manual_action_url_template.format(chapter_id=chapter_id)
    return ""


def get_sub_task(task_type: str) -> SubTaskDef | None:
    for t in _REGISTRY:
        if t.task_type == task_type:
            return t
    return None
