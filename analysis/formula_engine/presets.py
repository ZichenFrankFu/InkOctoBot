"""
Genre-specific presets.

Defines ``GenrePreset`` and ships built-in presets for common Chinese
web-novel genres.  Extends the YAML constraint presets in
``config/constraint_presets/``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GenrePreset:
    """Feature-weight + constraint template bundle for a genre."""

    genre: str
    description: str = ""

    # Weights applied when aggregating multi-dimensional features (0–1)
    feature_weights: dict[str, float] = field(default_factory=dict)

    # Constraint templates (same schema as ConstraintAssembler rules)
    constraint_templates: list[dict[str, Any]] = field(default_factory=list)

    # Preferred shuangdian types for this genre
    shuangdian_types: list[str] = field(default_factory=list)

    # Expected pacing profile {fast/medium/slow → proportion}
    pacing_profile: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "genre": self.genre,
            "description": self.description,
            "feature_weights": self.feature_weights,
            "constraint_templates": self.constraint_templates,
            "shuangdian_types": self.shuangdian_types,
            "pacing_profile": self.pacing_profile,
        }


# ── Built-in presets ──

_PRESETS: dict[str, GenrePreset] = {}


def _register(p: GenrePreset) -> None:
    _PRESETS[p.genre] = p


_register(GenrePreset(
    genre="玄幻",
    description="玄幻/仙侠修真题材",
    feature_weights={
        "avg_sentence_length": 0.10,
        "dialogue_ratio": 0.15,
        "description_density": 0.10,
        "rhetoric_frequency": 0.15,
        "vocab_complexity": 0.10,
        "pacing": 0.20,
        "shuangdian": 0.20,
    },
    constraint_templates=[
        {
            "rule_type": "world_rule",
            "priority": 1,
            "description": "修炼体系必须遵循等级递进，不可跨阶突破",
            "positive_restatement": "角色修为必须按等级顺序递进",
        },
        {
            "rule_type": "style_constraint",
            "priority": 4,
            "description": "使用半古风语言，避免现代网络用语",
            "positive_restatement": "对话和叙述保持古风或半文言风格",
        },
    ],
    shuangdian_types=["face_slap", "power_reveal", "breakthrough", "treasure_gain"],
    pacing_profile={"fast": 0.40, "medium": 0.35, "slow": 0.25},
))

_register(GenrePreset(
    genre="都市",
    description="都市/现实题材",
    feature_weights={
        "avg_sentence_length": 0.10,
        "dialogue_ratio": 0.25,
        "description_density": 0.10,
        "rhetoric_frequency": 0.05,
        "vocab_complexity": 0.15,
        "pacing": 0.20,
        "shuangdian": 0.15,
    },
    constraint_templates=[
        {
            "rule_type": "world_rule",
            "priority": 1,
            "description": "遵循现实世界物理规律，无超自然力量",
            "positive_restatement": "所有事件必须符合现实世界常识",
        },
        {
            "rule_type": "style_constraint",
            "priority": 4,
            "description": "对话自然口语化，符合角色身份特征",
            "positive_restatement": "对话使用自然口语，匹配角色年龄和社会阶层",
        },
    ],
    shuangdian_types=["face_slap", "recognition", "revenge", "underdog_win"],
    pacing_profile={"fast": 0.30, "medium": 0.45, "slow": 0.25},
))

_register(GenrePreset(
    genre="科幻",
    description="科幻/未来题材",
    feature_weights={
        "avg_sentence_length": 0.10,
        "dialogue_ratio": 0.15,
        "description_density": 0.20,
        "rhetoric_frequency": 0.10,
        "vocab_complexity": 0.20,
        "pacing": 0.15,
        "shuangdian": 0.10,
    },
    constraint_templates=[
        {
            "rule_type": "world_rule",
            "priority": 1,
            "description": "科技设定必须在故事内部自洽",
            "positive_restatement": "所有科技描写需遵循故事已建立的科学体系",
        },
    ],
    shuangdian_types=["mystery_reveal", "breakthrough", "power_reveal"],
    pacing_profile={"fast": 0.25, "medium": 0.40, "slow": 0.35},
))

_register(GenrePreset(
    genre="悬疑",
    description="悬疑/推理题材",
    feature_weights={
        "avg_sentence_length": 0.10,
        "dialogue_ratio": 0.20,
        "description_density": 0.15,
        "rhetoric_frequency": 0.05,
        "vocab_complexity": 0.15,
        "pacing": 0.15,
        "shuangdian": 0.20,
    },
    constraint_templates=[
        {
            "rule_type": "plot_constraint",
            "priority": 3,
            "description": "线索必须在揭示前合理铺垫，不可凭空出现",
            "positive_restatement": "每条关键线索揭示前至少有一次铺垫或暗示",
        },
    ],
    shuangdian_types=["mystery_reveal", "recognition", "revenge"],
    pacing_profile={"fast": 0.20, "medium": 0.45, "slow": 0.35},
))

_register(GenrePreset(
    genre="言情",
    description="言情/romance 题材",
    feature_weights={
        "avg_sentence_length": 0.10,
        "dialogue_ratio": 0.25,
        "description_density": 0.15,
        "rhetoric_frequency": 0.15,
        "vocab_complexity": 0.10,
        "pacing": 0.10,
        "shuangdian": 0.15,
    },
    constraint_templates=[
        {
            "rule_type": "style_constraint",
            "priority": 4,
            "description": "注重心理描写和情感细腻表达",
            "positive_restatement": "关键情感场景需要细腻的心理活动描写",
        },
    ],
    shuangdian_types=["recognition", "face_slap", "mystery_reveal"],
    pacing_profile={"fast": 0.20, "medium": 0.50, "slow": 0.30},
))


# ── Public API ──

def get_preset(genre: str) -> GenrePreset | None:
    """Return preset for *genre* or ``None``."""
    return _PRESETS.get(genre)


def list_presets() -> list[str]:
    """Return names of all registered presets."""
    return sorted(_PRESETS.keys())


def all_presets() -> dict[str, GenrePreset]:
    """Return a copy of the full preset registry."""
    return dict(_PRESETS)
