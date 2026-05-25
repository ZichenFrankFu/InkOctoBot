"""
Shared pytest fixtures for InkOctoBot test suite.

Provides mock LLM router, skill registry, sample data, and other
common test utilities.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ── Mock LLM Router ─────────────────────────────────────────────

@pytest.fixture
def mock_model_router():
    """Mock ModelRouter that returns predictable responses."""
    router = MagicMock()
    router.generate = AsyncMock(
        return_value=MagicMock(
            content='{"result": "mocked"}',
            model="mock-model",
            input_tokens=100,
            output_tokens=50,
            finish_reason="stop",
            raw={},
        )
    )
    router.invoke = AsyncMock(return_value='{"result": "mocked"}')
    router.generate_stream = AsyncMock()
    router.estimate_cost = MagicMock(return_value=0.0)
    return router


@pytest.fixture
def mock_llm_response():
    """Factory for creating mock LLM responses with specific content."""
    def _make(content: str = '{"result": "ok"}', **kwargs):
        return MagicMock(
            content=content,
            model=kwargs.get("model", "mock-model"),
            input_tokens=kwargs.get("input_tokens", 100),
            output_tokens=kwargs.get("output_tokens", 50),
            finish_reason="stop",
            raw={"parsed": json.loads(content)} if content.startswith("{") else {},
        )
    return _make


# ── Mock Event Bus ───────────────────────────────────────────────

@pytest.fixture
def mock_event_bus():
    """Mock EventBus for testing event emission."""
    bus = MagicMock()
    bus.publish = MagicMock()
    bus.subscribe = MagicMock()
    return bus


# ── Skill Registry ───────────────────────────────────────────────

@pytest.fixture
def skill_registry(tmp_path):
    """A SkillRegistry scanning a temporary directory."""
    from framework.skill_registry import SkillRegistry
    registry = SkillRegistry()
    return registry


@pytest.fixture
def populated_skill_registry():
    """A SkillRegistry pre-loaded with all built-in skills."""
    from framework.skill_registry import SkillRegistry
    registry = SkillRegistry()
    agents_dir = PROJECT_ROOT / "agents"
    registry.scan_all(agents_dir)
    return registry


# ── Sample Data ──────────────────────────────────────────────────

@pytest.fixture
def sample_chapter_text():
    """Sample Chinese novel chapter text for testing."""
    return (
        "张三站在山崖边，俯瞰着下方的村庄。"
        "微风拂过他的衣袍，带来了一丝清凉。"
        "\u201c师父说过，这个世界上没有魔法。\u201d他低声自语。"
        "但他知道，总有一天，他会找到改变命运的方法。"
        "远处的天际线上，一道金光闪过。"
        "张三瞪大了眼睛，那是\u2014\u2014"
        "\u201c不可能！\u201d他惊呼出声。"
    )


@pytest.fixture
def sample_scene_plan():
    """Sample scene plan for testing production skills."""
    return {
        "chapter_num": 1,
        "scenes": [{
            "scene_index": 0,
            "location": "山崖",
            "time": "黄昏",
            "characters": ["张三", "李四"],
            "summary": "张三在山崖边遇到李四",
            "beats": ["相遇", "对话", "冲突"],
            "character_instructions": {
                "张三": {
                    "emotional_state": "困惑",
                    "secret_goal": "寻找师父的下落",
                    "must": ["表现出犹豫"],
                    "must_not": ["直接攻击"],
                },
            },
            "narrator_instructions": "描写黄昏山崖的壮丽景色",
        }],
        "chapter_arc": "从迷茫到觉醒",
    }


@pytest.fixture
def sample_character_cards():
    """Sample character cards for testing."""
    return {
        "张三": "性格沉稳，武功高强，但内心充满迷茫。师从无名老人。",
        "李四": "性格豪爽，直来直去，张三的师兄。",
    }


@pytest.fixture
def sample_worldbook():
    """Sample world book for testing."""
    return "此世界没有魔法，修炼体系以内功心法为主。分为先天、后天、宗师三个境界。"


# ── Database ─────────────────────────────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary SQLite database for testing."""
    import sqlite3
    db_path = str(tmp_path / "test.db")
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS constraint_rules (
                rule_id TEXT PRIMARY KEY,
                project_id TEXT,
                rule_type TEXT,
                priority INTEGER DEFAULT 4,
                description TEXT,
                positive_restatement TEXT DEFAULT '',
                good_example TEXT DEFAULT '',
                bad_example TEXT DEFAULT '',
                source TEXT DEFAULT 'user',
                is_active INTEGER DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS user_style_preferences (
                pref_id TEXT PRIMARY KEY,
                project_id TEXT,
                preference_type TEXT,
                description TEXT,
                confidence REAL DEFAULT 0.15,
                observation_count INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)
    return db_path


# ── Fixtures directory ───────────────────────────────────────────

@pytest.fixture
def fixtures_dir():
    """Path to test fixtures directory."""
    return Path(__file__).parent / "fixtures"
