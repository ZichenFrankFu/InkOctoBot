"""Tests for core.skill_registry."""
import pytest
from pathlib import Path

from core.skill_registry import SkillRegistry
from agents.base_skill import BaseSkill, SkillMeta


class DummySkill(BaseSkill):
    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="dummy",
            display_name="Dummy Skill",
            description="A test skill",
            tags=["test", "dummy"],
        )

    async def build_prompt(self, inputs: dict) -> str:
        return f"test prompt: {inputs}"

    async def parse_output(self, raw: str) -> dict:
        return {"raw": raw}


class AnotherSkill(BaseSkill):
    def meta(self) -> SkillMeta:
        return SkillMeta(
            name="another",
            display_name="Another Skill",
            description="Another test skill",
            tags=["test", "another"],
        )

    async def build_prompt(self, inputs: dict) -> str:
        return ""

    async def parse_output(self, raw: str) -> dict:
        return {}


class TestSkillRegistry:
    def test_register_and_get(self):
        registry = SkillRegistry()
        skill = DummySkill()
        registry.register(skill)
        assert registry.has("dummy")
        assert registry.get("dummy") is skill
        assert registry.count == 1

    def test_get_missing_raises(self):
        registry = SkillRegistry()
        with pytest.raises(KeyError, match="not found"):
            registry.get("nonexistent")

    def test_find_by_tags(self):
        registry = SkillRegistry()
        registry.register(DummySkill())
        registry.register(AnotherSkill())

        found = registry.find_by_tags(["dummy"])
        assert len(found) == 1
        assert found[0].meta().name == "dummy"

        found_test = registry.find_by_tags(["test"])
        assert len(found_test) == 2

    def test_list_all(self):
        registry = SkillRegistry()
        registry.register(DummySkill())
        registry.register(AnotherSkill())
        metas = registry.list_all()
        assert len(metas) == 2
        names = {m.name for m in metas}
        assert names == {"dummy", "another"}

    def test_unregister(self):
        registry = SkillRegistry()
        registry.register(DummySkill())
        assert registry.has("dummy")
        registry.unregister("dummy")
        assert not registry.has("dummy")

    def test_scan_directory(self, tmp_path):
        """Test scanning a directory with a skill structure."""
        skill_dir = tmp_path / "test_skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: test\n---\n")
        (skill_dir / "skill.py").write_text(
            '''
from agents.base_skill import BaseSkill, SkillMeta

class Skill(BaseSkill):
    def meta(self):
        return SkillMeta(name="test_scan", display_name="Test", description="test")
    async def build_prompt(self, inputs):
        return ""
    async def parse_output(self, raw):
        return {}
'''
        )
        registry = SkillRegistry()
        count = registry.scan_directory(tmp_path)
        assert count == 1
        assert registry.has("test_scan")
