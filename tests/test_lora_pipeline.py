"""
Tests for the LoRA pipeline: data_constructor, quality_filter.

Note: trainer.py is NOT tested here because it requires GPU + large
model downloads.  Only smoke-test the config dataclass.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from preprocessing.lora.data_constructor import (
    construct_sft_data,
    save_dataset,
)
from preprocessing.lora.trainer import LoRATrainConfig


class TestDataConstructor(unittest.TestCase):

    def _sample_chapters(self) -> list[dict]:
        return [
            {
                "index": 0,
                "title": "第一章 初入江湖",
                "content": (
                    "张远缓缓走出山门，目光扫过远方连绵的群山。"
                    "天色微亮，晨雾弥漫在山谷之间。他深吸一口气，感受着灵气在体内流转。"
                    "今天是他下山历练的第一天。师父的话犹在耳畔：切记，江湖险恶，万事小心。"
                    "他握紧腰间的长剑，迈步走向远方。路上遇到了几位行脚商人。"
                    "他们热情地与张远打招呼。张远微微点头回礼。"
                ),
            },
            {
                "index": 1,
                "title": "第二章 客栈风波",
                "content": (
                    "客栈里人声鼎沸。张远找了个角落坐下，点了一壶茶。"
                    "突然，一声怒吼响起，一个彪形大汉摔碎了手中的酒杯。"
                    "\u201c谁敢动我兄弟？\u201d大汉怒目圆睁。"
                    "张远看了一眼，并未理会。他继续喝茶，心中思考着接下来的路线。"
                ),
            },
        ]

    def test_construct_style_transfer(self):
        chapters = self._sample_chapters()
        samples = construct_sft_data(chapters, task_type="style_transfer")
        self.assertGreater(len(samples), 0)
        for s in samples:
            self.assertIn("instruction", s)
            self.assertIn("input", s)
            self.assertIn("output", s)
            self.assertIn("text", s)
            self.assertIn("metadata", s)
            self.assertEqual(s["metadata"]["task_type"], "style_transfer")

    def test_construct_continuation(self):
        chapters = self._sample_chapters()
        samples = construct_sft_data(chapters, task_type="continuation")
        self.assertGreater(len(samples), 0)

    def test_construct_editing(self):
        chapters = self._sample_chapters()
        samples = construct_sft_data(chapters, task_type="editing")
        self.assertGreater(len(samples), 0)

    def test_empty_chapters(self):
        samples = construct_sft_data([])
        self.assertEqual(len(samples), 0)

    def test_with_style_fingerprint(self):
        chapters = self._sample_chapters()
        fp = {"avg_sentence_length": 18.0, "dialogue_ratio": 0.3}
        samples = construct_sft_data(chapters, style_fingerprint=fp)
        self.assertGreater(len(samples), 0)
        self.assertEqual(samples[0]["metadata"]["style_fingerprint"], fp)

    def test_save_jsonl(self):
        chapters = self._sample_chapters()
        samples = construct_sft_data(chapters)
        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
            path = f.name
        try:
            save_dataset(samples, path, format="jsonl")
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
            self.assertEqual(len(lines), len(samples))
            # Each line should be valid JSON
            for line in lines:
                json.loads(line)
        finally:
            os.unlink(path)

    def test_save_json(self):
        chapters = self._sample_chapters()
        samples = construct_sft_data(chapters)
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            path = f.name
        try:
            save_dataset(samples, path, format="json")
            with open(path, encoding="utf-8") as f:
                loaded = json.load(f)
            self.assertEqual(len(loaded), len(samples))
        finally:
            os.unlink(path)


class TestQualityFilter(unittest.TestCase):

    def test_length_filter(self):
        from preprocessing.lora.quality_filter import filter_samples
        samples = [
            {"text": "太短"},
            {"text": "这是一段足够长的小说文本，包含了一些角色对话和场景描写。张远走在路上，心中思绪万千。"},
        ]
        result = filter_samples(samples, min_length=10)
        self.assertEqual(len(result.passed), 1)
        self.assertEqual(len(result.rejected), 1)
        self.assertEqual(result.stats["reject_reasons"].get("too_short", 0), 1)

    def test_all_pass(self):
        from preprocessing.lora.quality_filter import filter_samples
        samples = [
            {"text": "张远缓缓走出山门，目光扫过远方连绵的群山。天色微亮，晨雾弥漫在山谷之间。"},
            {"text": "客栈里人声鼎沸。张远找了个角落坐下，点了一壶茶。突然一声怒吼响起。"},
        ]
        result = filter_samples(samples, min_length=5)
        self.assertEqual(len(result.passed), 2)
        self.assertEqual(result.stats["pass_rate"], 1.0)

    def test_empty_input(self):
        from preprocessing.lora.quality_filter import filter_samples
        result = filter_samples([])
        self.assertEqual(len(result.passed), 0)
        self.assertEqual(result.stats["total"], 0)


class TestLoRATrainConfig(unittest.TestCase):

    def test_defaults(self):
        cfg = LoRATrainConfig()
        self.assertEqual(cfg.rank, 16)
        self.assertEqual(cfg.alpha, 32)
        self.assertTrue(cfg.use_4bit)

    def test_custom(self):
        cfg = LoRATrainConfig(base_model="test/model", rank=8, epochs=5)
        self.assertEqual(cfg.base_model, "test/model")
        self.assertEqual(cfg.rank, 8)
        self.assertEqual(cfg.epochs, 5)

    def test_to_dict(self):
        cfg = LoRATrainConfig()
        d = cfg.to_dict()
        self.assertIn("base_model", d)
        self.assertIn("rank", d)
        self.assertIn("learning_rate", d)


if __name__ == "__main__":
    unittest.main()
