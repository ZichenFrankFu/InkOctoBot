"""
Tests for advanced feature extraction modules:
rhetoric_classifier, shuangdian_templates, embedding_cluster.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from reference_pipeline.rhetoric_classifier import (
    classify_rhetoric,
    rhetoric_summary,
)
from reference_pipeline.shuangdian_templates import (
    extract_shuangdian,
    shuangdian_density,
)


class TestRhetoricClassifier(unittest.TestCase):

    def test_simile_detection(self):
        text = "他如同一道闪电般冲了出去。"
        results = classify_rhetoric(text)
        types = [r["rhetoric_type"] for r in results]
        self.assertIn("simile", types)

    def test_rhetorical_question_detection(self):
        text = "这里有一些正常的文字。难道这就是传说中的缩地成寸？众人不敢相信。"
        results = classify_rhetoric(text)
        types = [r["rhetoric_type"] for r in results]
        self.assertIn("rhetorical_question", types)

    def test_hyperbole_detection(self):
        text = "那一掌打出，惊天动地，整片大地都在颤抖。"
        results = classify_rhetoric(text)
        types = [r["rhetoric_type"] for r in results]
        self.assertTrue(any(t == "hyperbole" for t in types))

    def test_personification_detection(self):
        text = "风在低语，月在沉默。"
        results = classify_rhetoric(text)
        types = [r["rhetoric_type"] for r in results]
        self.assertIn("personification", types)

    def test_no_rhetoric(self):
        text = "他走进房间。"
        results = classify_rhetoric(text)
        self.assertEqual(len(results), 0)

    def test_empty_text(self):
        results = classify_rhetoric("")
        self.assertEqual(results, [])

    def test_summary(self):
        text = "他如同一道闪电般冲出去。难道这就是传说中的缩地成寸？众人目瞪口呆。"
        summary = rhetoric_summary(text)
        self.assertIn("total_devices", summary)
        self.assertIn("distribution", summary)
        self.assertIn("density_per_1k", summary)
        self.assertGreater(summary["total_devices"], 0)

    def test_summary_empty(self):
        summary = rhetoric_summary("")
        self.assertEqual(summary["total_devices"], 0)
        self.assertIsNone(summary["top_type"])


class TestShuangdianTemplates(unittest.TestCase):

    def _make_chapters(self, *contents: str) -> list[dict]:
        return [{"index": i, "content": c} for i, c in enumerate(contents)]

    def test_face_slap(self):
        chapters = self._make_chapters(
            "众人全都震惊了，目瞪口呆地看着这一切。"
        )
        results = extract_shuangdian(chapters)
        types = [r["type"] for r in results]
        self.assertIn("face_slap", types)

    def test_power_reveal(self):
        chapters = self._make_chapters(
            "他的气息骤然提升，一股恐怖的力量从体内涌出。"
        )
        results = extract_shuangdian(chapters)
        types = [r["type"] for r in results]
        self.assertIn("power_reveal", types)

    def test_mystery_reveal(self):
        chapters = self._make_chapters(
            "原来那个神秘人竟然是失踪多年的掌门。"
        )
        results = extract_shuangdian(chapters)
        types = [r["type"] for r in results]
        self.assertIn("mystery_reveal", types)

    def test_breakthrough(self):
        chapters = self._make_chapters(
            "他终于顿悟了剑道的真谛，一身剑意冲天而起。"
        )
        results = extract_shuangdian(chapters)
        types = [r["type"] for r in results]
        self.assertIn("breakthrough", types)

    def test_context_extraction(self):
        chapters = self._make_chapters(
            "前面的铺垫内容。众人全都震惊了，目瞪口呆。后续的反应。"
        )
        results = extract_shuangdian(chapters)
        self.assertTrue(len(results) > 0)
        r = results[0]
        self.assertIn("context_before", r)
        self.assertIn("climax", r)
        self.assertIn("reaction", r)

    def test_no_shuangdian(self):
        chapters = self._make_chapters("他走进房间坐了下来。")
        results = extract_shuangdian(chapters)
        self.assertEqual(len(results), 0)

    def test_density(self):
        chapters = self._make_chapters(
            "众人全都震惊了。",
            "平淡的一章。",
            "他的气息骤然提升。",
        )
        d = shuangdian_density(chapters)
        self.assertIn("total", d)
        self.assertIn("per_chapter", d)
        self.assertIn("chapter_coverage", d)
        self.assertGreater(d["total"], 0)
        self.assertGreater(d["chapter_coverage"], 0)

    def test_density_empty(self):
        d = shuangdian_density([])
        self.assertEqual(d["total"], 0)


class TestEmbeddingCluster(unittest.TestCase):
    """Test embedding_cluster with TF-IDF backend (no GPU dependency)."""

    def test_cluster_basic(self):
        from reference_pipeline.embedding_cluster import cluster_fragments
        texts = [
            "他拔出剑冲了上去",
            "她挥舞长枪杀向敌人",
            "战斗异常激烈",
            "花园里鸟语花香",
            "春风拂过湖面",
            "景色宜人令人陶醉",
        ]
        result = cluster_fragments(texts, n_clusters=2, backend="tfidf")
        self.assertEqual(result["n_clusters"], 2)
        self.assertEqual(len(result["clusters"]), 2)
        total_members = sum(c["size"] for c in result["clusters"])
        self.assertEqual(total_members, len(texts))

    def test_embed_texts(self):
        from reference_pipeline.embedding_cluster import embed_texts
        texts = ["你好世界", "测试文本"]
        vecs = embed_texts(texts, backend="tfidf")
        self.assertEqual(vecs.shape[0], 2)
        self.assertGreater(vecs.shape[1], 0)

    def test_empty(self):
        from reference_pipeline.embedding_cluster import cluster_fragments
        result = cluster_fragments([], n_clusters=3)
        self.assertEqual(result["n_clusters"], 0)

    def test_single_text(self):
        from reference_pipeline.embedding_cluster import cluster_fragments
        result = cluster_fragments(["只有一段文本"], n_clusters=1, backend="tfidf")
        self.assertEqual(result["n_clusters"], 1)


if __name__ == "__main__":
    unittest.main()
