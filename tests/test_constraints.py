"""
Tests for the constraint system.

Tests: ConstraintAssembler, ViolationDetector, ConstraintStore.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from database.creation_schema import ensure_creation_tables
from agents.constraints.assembler import ConstraintAssembler


def _setup_db() -> str:
    db_path = os.path.join(tempfile.mkdtemp(), "test.db")
    conn = sqlite3.connect(db_path)
    ensure_creation_tables(conn)
    conn.execute("INSERT INTO projects (project_id, title) VALUES ('p1', 'Test')")
    conn.commit()
    conn.close()
    return db_path


class TestConstraintAssembler(unittest.TestCase):

    def setUp(self):
        self.db_path = _setup_db()
        self.assembler = ConstraintAssembler(self.db_path)

    def test_add_and_get_constraint(self):
        rule_id = self.assembler.add_constraint(
            "p1", "world_rule",
            "本世界没有枪械",
            positive_restatement="所有战斗只能使用冷兵器和法术",
            good_example="他抽出长剑迎敌",
            bad_example="他掏出手枪",
        )
        self.assertTrue(rule_id.startswith("rule_"))
        rules = self.assembler.get_constraints("p1", rule_type="world_rule")
        self.assertEqual(len(rules), 1)
        self.assertIn("没有枪械", rules[0]["description"])

    def test_assemble_priority_order(self):
        self.assembler.add_constraint("p1", "world_rule", "世界规则1", priority=1)
        self.assembler.add_constraint("p1", "style_constraint", "风格规则1", priority=4)
        self.assembler.add_constraint("p1", "plot_constraint", "情节规则1", priority=3)
        text = self.assembler.assemble("p1")
        # World rules should appear before style constraints
        world_pos = text.find("世界规则1")
        style_pos = text.find("风格规则1")
        self.assertLess(world_pos, style_pos)

    def test_assemble_with_scene_constraints(self):
        scene = [{"description": "场景约束: 不得提及宝物"}]
        text = self.assembler.assemble("p1", scene_constraints=scene)
        self.assertIn("不得提及宝物", text)

    def test_assemble_empty(self):
        text = self.assembler.assemble("p1")
        self.assertIn("约束规则", text)

    def test_include_types_filter(self):
        self.assembler.add_constraint("p1", "world_rule", "世界规则")
        self.assembler.add_constraint("p1", "style_constraint", "风格规则")
        text = self.assembler.assemble("p1", include_types=["world_rule"])
        self.assertIn("世界规则", text)
        self.assertNotIn("风格规则", text)


class TestConstraintAssemblerGoodBad(unittest.TestCase):

    def setUp(self):
        self.db_path = _setup_db()
        self.assembler = ConstraintAssembler(self.db_path)

    def test_good_bad_examples_in_output(self):
        self.assembler.add_constraint(
            "p1", "world_rule", "筑基期无法飞行",
            positive_restatement="筑基期及以下只能步行或骑乘",
            good_example="他骑着灵鹤赶路",
            bad_example="筑基期的他飞身而起",
        )
        text = self.assembler.assemble("p1")
        self.assertIn("正确示例", text)
        self.assertIn("错误示例", text)
        self.assertIn("骑着灵鹤", text)
        self.assertIn("飞身而起", text)


if __name__ == "__main__":
    unittest.main()
