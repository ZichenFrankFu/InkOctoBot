"""
Tests for Character Cards (Layer A) and World Book Manager.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from rag.character_cards import CharacterCardManager
from rag.world_book import WorldBookManager


class TestCharacterCardManager(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.manager = CharacterCardManager(self.tmpdir)

    def test_create_character(self):
        card = self.manager.create_character(
            "张远", personality="沉稳内敛", role="protagonist",
            speech_style="简洁有力",
        )
        self.assertEqual(card["name"], "张远")
        self.assertEqual(card["role"], "protagonist")

    def test_get_character(self):
        self.manager.create_character("A", personality="test")
        card = self.manager.get_character("A")
        self.assertIsNotNone(card)
        self.assertEqual(card["name"], "A")

    def test_list_characters(self):
        self.manager.create_character("A", personality="x")
        self.manager.create_character("B", personality="y")
        names = self.manager.list_characters()
        self.assertEqual(len(names), 2)

    def test_update_character(self):
        self.manager.create_character("A", personality="original")
        result = self.manager.update_character("A", personality="updated")
        self.assertTrue(result)
        card = self.manager.get_character("A")
        self.assertIn("updated", str(card))

    def test_delete_character(self):
        self.manager.create_character("A", personality="test")
        self.assertTrue(self.manager.delete_character("A"))
        self.assertIsNone(self.manager.get_character("A"))

    def test_get_character_prompt(self):
        self.manager.create_character(
            "张远", personality="沉稳内敛", speech_style="简洁",
        )
        prompt = self.manager.get_character_prompt("张远")
        self.assertIn("沉稳内敛", prompt)

    def test_nonexistent_character(self):
        self.assertIsNone(self.manager.get_character("不存在"))
        self.assertEqual(self.manager.get_character_prompt("不存在"), "")


class TestWorldBookManager(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.wb = WorldBookManager(self.tmpdir)

    def test_add_entry(self):
        entry = self.wb.add_entry("修炼体系", "灵根", "修炼者必须觉醒灵根")
        self.assertEqual(entry["title"], "灵根")

    def test_get_category(self):
        self.wb.add_entry("修炼体系", "灵根", "内容A")
        self.wb.add_entry("修炼体系", "筑基", "内容B")
        entries = self.wb.get_category("修炼体系")
        self.assertEqual(len(entries), 2)

    def test_list_categories(self):
        self.wb.add_entry("修炼体系", "A", "x")
        self.wb.add_entry("地理", "B", "y")
        cats = self.wb.list_categories()
        self.assertIn("修炼体系", cats)
        self.assertIn("地理", cats)

    def test_add_rule(self):
        self.wb.add_rule("筑基期无法飞行", rule_type="hard")
        rules = self.wb.get_rules()
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["type"], "hard")

    def test_get_summary(self):
        self.wb.add_entry("体系", "灵根", "觉醒后可修炼")
        self.wb.add_rule("不可逆天", rule_type="hard")
        summary = self.wb.get_summary()
        self.assertIn("灵根", summary)
        self.assertIn("不可逆天", summary)

    def test_update_entry(self):
        self.wb.add_entry("体系", "A", "original")
        result = self.wb.update_entry("体系", "A", content="updated")
        self.assertTrue(result)

    def test_delete_entry(self):
        self.wb.add_entry("体系", "A", "content")
        self.assertTrue(self.wb.delete_entry("体系", "A"))
        self.assertEqual(len(self.wb.get_category("体系")), 0)

    def test_max_length_truncation(self):
        self.wb.add_entry("体系", "X", "长" * 5000)
        summary = self.wb.get_summary(max_length=100)
        self.assertLessEqual(len(summary), 120)


if __name__ == "__main__":
    unittest.main()
