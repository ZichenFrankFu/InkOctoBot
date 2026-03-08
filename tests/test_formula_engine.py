"""
Tests for the formula engine: presets, aggregator, constraint_converter.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from analysis.formula_engine.presets import get_preset, list_presets, all_presets, GenrePreset
from analysis.formula_engine.aggregator import aggregate_features
from analysis.formula_engine.constraint_converter import convert_to_constraints


class TestPresets(unittest.TestCase):

    def test_list_presets(self):
        names = list_presets()
        self.assertIsInstance(names, list)
        self.assertGreater(len(names), 0)
        self.assertIn("玄幻", names)
        self.assertIn("都市", names)

    def test_get_preset(self):
        p = get_preset("玄幻")
        self.assertIsNotNone(p)
        self.assertEqual(p.genre, "玄幻")
        self.assertIsInstance(p.feature_weights, dict)
        self.assertGreater(len(p.constraint_templates), 0)
        self.assertGreater(len(p.shuangdian_types), 0)

    def test_get_nonexistent(self):
        self.assertIsNone(get_preset("不存在的题材"))

    def test_to_dict(self):
        p = get_preset("科幻")
        self.assertIsNotNone(p)
        d = p.to_dict()
        self.assertEqual(d["genre"], "科幻")
        self.assertIn("feature_weights", d)

    def test_all_presets(self):
        presets = all_presets()
        self.assertGreater(len(presets), 3)
        for name, preset in presets.items():
            self.assertIsInstance(preset, GenrePreset)
            self.assertEqual(preset.genre, name)


class TestAggregator(unittest.TestCase):

    def _sample_style(self):
        return {
            "avg_sentence_length": 18.5,
            "dialogue_ratio": 0.35,
            "description_density": 0.12,
            "rhetoric_frequency": 2.5,
            "vocab_complexity": 0.45,
            "pacing_profile": {"fast": 0.35, "medium": 0.40, "slow": 0.25},
        }

    def test_basic_aggregation(self):
        result = aggregate_features(
            style=self._sample_style(),
            narrative={},
            rhythm={"pacing_segments": [{"pacing": "fast"}]},
        )
        self.assertIn("aggregated_score", result)
        self.assertIn("dimension_scores", result)
        self.assertIsInstance(result["aggregated_score"], float)

    def test_with_preset(self):
        preset = get_preset("玄幻")
        result = aggregate_features(
            style=self._sample_style(),
            narrative={},
            rhythm={},
            preset=preset,
        )
        self.assertIn("aggregated_score", result)

    def test_with_rhetoric(self):
        result = aggregate_features(
            style={"avg_sentence_length": 20},
            narrative={},
            rhythm={},
            rhetoric={"density_per_1k": 3.5},
        )
        self.assertIn("rhetoric_frequency", result["dimension_scores"])

    def test_with_shuangdian(self):
        result = aggregate_features(
            style={},
            narrative={},
            rhythm={},
            shuangdian={"per_chapter": 1.5, "chapter_coverage": 0.6},
        )
        self.assertIn("shuangdian", result["dimension_scores"])

    def test_empty_inputs(self):
        result = aggregate_features(style={}, narrative={}, rhythm={})
        self.assertEqual(result["dimension_scores"], {})

    def test_recommendations(self):
        result = aggregate_features(
            style={"dialogue_ratio": 0.05},
            narrative={},
            rhythm={},
        )
        # Low dialogue should trigger a recommendation
        self.assertIsInstance(result["recommendations"], list)


class TestConstraintConverter(unittest.TestCase):

    def test_basic_conversion(self):
        aggregated = {
            "aggregated_score": 45.0,
            "dimension_scores": {"dialogue_ratio": 20.0, "shuangdian": 15.0},
            "recommendations": [],
        }
        rules = convert_to_constraints(aggregated)
        self.assertIsInstance(rules, list)
        self.assertGreater(len(rules), 0)
        # Should have constraints for low dialogue and low shuangdian
        categories = [r["category"] for r in rules]
        self.assertTrue(any("style" in c for c in categories))

    def test_with_preset(self):
        preset = get_preset("玄幻")
        aggregated = {
            "aggregated_score": 50.0,
            "dimension_scores": {},
            "recommendations": [],
        }
        rules = convert_to_constraints(aggregated, preset=preset)
        # Should include preset's constraint templates
        self.assertGreater(len(rules), 0)
        texts = [r["text"] for r in rules]
        self.assertTrue(any("修为" in t or "等级" in t for t in texts))

    def test_no_issues(self):
        aggregated = {
            "aggregated_score": 85.0,
            "dimension_scores": {
                "dialogue_ratio": 55.0,
                "pacing": 60.0,
                "shuangdian": 50.0,
                "rhetoric_frequency": 40.0,
            },
            "recommendations": [],
        }
        rules = convert_to_constraints(aggregated)
        # No preset → no templates; scores are fine → few/no dynamic rules
        self.assertIsInstance(rules, list)

    def test_constraint_structure(self):
        aggregated = {
            "aggregated_score": 30.0,
            "dimension_scores": {"dialogue_ratio": 10.0},
            "recommendations": ["测试建议"],
        }
        rules = convert_to_constraints(aggregated)
        for r in rules:
            self.assertIn("level", r)
            self.assertIn("category", r)
            self.assertIn("text", r)
            self.assertIsInstance(r["level"], int)


if __name__ == "__main__":
    unittest.main()
