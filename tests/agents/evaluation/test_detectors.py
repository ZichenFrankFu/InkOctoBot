"""
Tests for the evaluation system.

Tests: RepetitionDetector, SlopDetector, StyleDriftDetector, QualityScorer.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.evaluation.repetition_detector import RepetitionDetector
from agents.evaluation.slop_detector import SlopDetector
from agents.evaluation.style_drift_detector import StyleDriftDetector
from agents.evaluation.quality_scorer import QualityScorer


class TestRepetitionDetector(unittest.TestCase):

    def setUp(self):
        self.detector = RepetitionDetector()

    def test_no_repetition(self):
        text = "张远走进大殿。殿内灯火通明。众弟子正在修炼。长老坐在上方。空气中弥漫着灵气。"
        result = self.detector.detect(text)
        self.assertGreater(result["score"], 70)

    def test_phrase_repetition(self):
        text = "他不禁叹了口气。他不禁叹了口气。他不禁叹了口气。这里有一些普通文本。"
        result = self.detector.detect(text)
        self.assertLess(result["score"], 80)
        self.assertTrue(any("不禁叹了口气" in str(i) for i in result["issues"]))

    def test_structural_repetition(self):
        text = ("他抬起头看向远方。她抬起头看向远方。"
                "他低下头沉思。她低下头沉思。"
                "然后正常的一些文本内容。")
        result = self.detector.detect(text)
        self.assertIsInstance(result["score"], (int, float))

    def test_empty_text(self):
        result = self.detector.detect("")
        self.assertEqual(result["score"], 100)


class TestSlopDetector(unittest.TestCase):

    def setUp(self):
        self.detector = SlopDetector()

    def test_clean_text(self):
        text = "张远拔出剑，挡住了来袭的一击。金铁交鸣声响彻山谷。对手后退三步，面色微变。"
        result = self.detector.detect(text)
        self.assertGreater(result["score"], 50)

    def test_slop_text(self):
        text = ("他不禁深吸一口气，嘴角微微上扬。值得注意的是，这位修炼者的瞳孔猛地一缩。"
                "一股莫名的力量涌上心头。空气仿佛都凝固了。")
        result = self.detector.detect(text)
        self.assertTrue(result["has_issues"])
        self.assertGreater(len(result["matches"]), 0)

    def test_ai_tell_patterns(self):
        text = "综上所述，让我们来看看这个角色。需要注意的是，他的背景非常复杂。"
        result = self.detector.detect(text)
        ai_cats = [m["category"] for m in result["matches"]]
        self.assertTrue(any("ai_tell" in c for c in ai_cats))

    def test_empty_text(self):
        result = self.detector.detect("")
        self.assertEqual(result["score"], 100)
        self.assertEqual(len(result["matches"]), 0)


class TestStyleDriftDetector(unittest.TestCase):

    def setUp(self):
        self.target = {
            "avg_sentence_length": 20.0,
            "dialogue_ratio": 0.3,
            "avg_paragraph_length": 100.0,
        }
        self.detector = StyleDriftDetector(target_profile=self.target)

    def test_no_drift(self):
        # Generate text roughly matching target
        text = "「你来了。」张远转过身。\n\n他看着眼前的人，心中五味杂陈。「是的，我来了。」\n\n沉默片刻后，两人并肩走出大殿。"
        result = self.detector.detect(text)
        self.assertIsInstance(result["score"], (int, float))
        self.assertIn("features", result)

    def test_drift_detected(self):
        # Very long sentences, no dialogue = should drift
        text = ("这是一段非常非常非常非常非常非常非常非常非常非常非常长的句子" * 5 + "。") * 3
        result = self.detector.detect(text)
        self.assertTrue(result["drifted"])

    def test_no_target(self):
        detector = StyleDriftDetector()
        result = detector.detect("随便一段文字。")
        self.assertFalse(result["drifted"])

    def test_set_target(self):
        detector = StyleDriftDetector()
        detector.set_target(self.target)
        result = detector.detect("测试文本。")
        self.assertIn("features", result)


class TestQualityScorer(unittest.TestCase):

    def setUp(self):
        self.scorer = QualityScorer(pass_threshold=60.0)

    def test_high_quality(self):
        scores = {
            "constraint_satisfaction": 90,
            "consistency": 85,
            "knowledge_isolation": 95,
            "repetition": 80,
            "style_quality": 75,
            "pacing": 70,
            "slop_free": 90,
        }
        report = self.scorer.score(scores)
        self.assertTrue(report.passed)
        self.assertGreater(report.overall_score, 70)

    def test_low_quality(self):
        scores = {
            "constraint_satisfaction": 20,
            "consistency": 30,
            "repetition": 10,
        }
        report = self.scorer.score(scores)
        self.assertFalse(report.passed)
        self.assertLess(report.overall_score, 60)

    def test_partial_dimensions(self):
        scores = {"consistency": 80}
        report = self.scorer.score(scores)
        self.assertIsInstance(report.overall_score, float)

    def test_empty_scores(self):
        report = self.scorer.score({})
        self.assertEqual(report.overall_score, 0)

    def test_to_dict(self):
        report = self.scorer.score({"consistency": 85})
        d = report.to_dict()
        self.assertIn("overall_score", d)
        self.assertIn("passed", d)


if __name__ == "__main__":
    unittest.main()
