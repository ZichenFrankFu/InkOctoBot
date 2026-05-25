"""Tests for core.skill_learner."""
import pytest
from framework.skill_learner import SkillLearner


class TestSkillLearnerValidation:
    def test_rejects_forbidden_imports(self):
        learner = SkillLearner()
        code = '''
import os
from agents.base_skill import BaseSkill, SkillMeta
class Skill(BaseSkill):
    def meta(self): return SkillMeta(name="bad", display_name="Bad", description="bad")
    async def build_prompt(self, inputs): return ""
    async def parse_output(self, raw): return {}
'''
        ok, reason = learner._validate_skill_code(code)
        assert not ok
        assert "Forbidden import" in reason

    def test_rejects_subprocess(self):
        learner = SkillLearner()
        code = '''
import subprocess
from agents.base_skill import BaseSkill, SkillMeta
class Skill(BaseSkill):
    def meta(self): return SkillMeta(name="bad", display_name="Bad", description="bad")
    async def build_prompt(self, inputs): return ""
    async def parse_output(self, raw): return {}
'''
        ok, reason = learner._validate_skill_code(code)
        assert not ok

    def test_rejects_no_base_skill(self):
        learner = SkillLearner()
        code = '''
class Skill:
    pass
'''
        ok, reason = learner._validate_skill_code(code)
        assert not ok
        assert "BaseSkill" in reason

    def test_accepts_valid_skill(self):
        learner = SkillLearner()
        code = '''
from agents.base_skill import BaseSkill, SkillMeta
class Skill(BaseSkill):
    def meta(self):
        return SkillMeta(name="good", display_name="Good", description="good")
    async def build_prompt(self, inputs):
        return ""
    async def parse_output(self, raw):
        return {}
'''
        ok, reason = learner._validate_skill_code(code)
        assert ok

    def test_install_skill(self, tmp_path):
        learner = SkillLearner()
        # Override the default path
        import framework.skill_learner as sl
        original = sl._LEARNED_SKILLS_DIR
        sl._LEARNED_SKILLS_DIR = tmp_path

        try:
            path = learner.install_skill(
                "test_skill",
                "---\nname: test_skill\n---",
                "class Skill: pass",
            )
            assert (path / "SKILL.md").exists()
            assert (path / "skill.py").exists()
        finally:
            sl._LEARNED_SKILLS_DIR = original
