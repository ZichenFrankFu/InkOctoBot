"""Tests for the consistency_check skill (LLM-based, mocked)."""
import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.skill
@pytest.mark.asyncio
async def test_consistency_check_detects_contradiction(mock_model_router):
    """When text contradicts worldbook, should detect issues."""
    mock_model_router.generate.return_value = MagicMock(
        content='{"consistent": false, "issues": [{"type": "world", "description": "此世界没有魔法，但文中出现了火焰剑", "severity": "high"}]}',
        input_tokens=100,
        output_tokens=50,
        raw={"parsed": {"consistent": False, "issues": [{"type": "world", "description": "此世界没有魔法", "severity": "high"}]}},
    )

    from agents.evaluation.consistency_checker import ConsistencyChecker
    checker = ConsistencyChecker(mock_model_router, project_id="test")
    result = await checker.check(
        text="张三挥舞着火焰剑",
        world_rules="此世界没有魔法",
    )
    assert not result.get("consistent", True) or len(result.get("issues", [])) > 0


@pytest.mark.skill
@pytest.mark.asyncio
async def test_consistency_check_passes_clean_text(mock_model_router):
    """When text is consistent with settings, should pass."""
    mock_model_router.generate.return_value = MagicMock(
        content='{"consistent": true, "issues": []}',
        input_tokens=100,
        output_tokens=50,
        raw={"parsed": {"consistent": True, "issues": []}},
    )

    from agents.evaluation.consistency_checker import ConsistencyChecker
    checker = ConsistencyChecker(mock_model_router, project_id="test")
    result = await checker.check(
        text="张三运起内功心法",
        world_rules="修炼体系以内功心法为主",
    )
    assert result.get("consistent", True)
    assert len(result.get("issues", [])) == 0
