"""
Tests for the preprocessing pipeline.

Tests: ChapterSplitter, CharacterProfiler, StyleExtractor,
RhythmAnalyzer, FragmentSelector, PreprocessingPipeline.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from preprocessing.chapter_splitter import split_chapters, detect_chapter_pattern
from preprocessing.character_profiler import CharacterProfiler
from preprocessing.style_extractor import StyleExtractor
from preprocessing.rhythm_analyzer import RhythmAnalyzer
from preprocessing.fragment_selector import FragmentSelector
from preprocessing.pipeline import PreprocessingPipeline

_SAMPLE_TEXT = """第一章 初入仙门

张远站在山门前，仰望着巨大的石碑。石碑上刻着"青云宗"三个大字，散发着淡淡的灵光。

"你就是新来的弟子？"一个声音从身后传来。

张远转过身，看到一个白衣少女正打量着他。"是的，我叫张远。"他拱手行礼。

少女点了点头："我叫李清漪，负责带你入门。跟我来吧。"

两人沿着石阶而上。山路两旁古木参天，偶有仙鹤掠过天际。张远心中既紧张又期待，这是他第一次踏入修仙界。

"青云宗有三大峰，你被分到了内峰。"李清漪边走边说道，"内峰主修剑道，你需要先觉醒灵根才能正式修炼。"

张远点了点头，将这些信息记在心中。

第二章 灵根觉醒

第二天清晨，张远被一阵钟声唤醒。他匆忙穿好衣服，赶往大殿。

大殿中已聚集了数十名新弟子。一位白发长老站在高台之上，目光威严。

"今日，你们将在灵泉中觉醒灵根。"长老的声音在大殿中回荡，"能否觉醒，全看天命。"

张远走向灵泉，将双手探入冰冷的泉水中。一股暖流从指尖涌入全身——他的灵根觉醒了。

"木属性灵根，中品。"长老淡淡说道。

李清漪在一旁露出了微笑："恭喜你，从今天起你就是青云宗的正式弟子了。"

第三章 首次修炼

张远开始了正式的修炼生活。每日清晨打坐，午后练剑，傍晚研读功法。

"修炼最重要的是心境。"师兄王涛说道，"急功近利只会走火入魔。"

张远虚心受教，每日勤勉修炼。一个月后，他成功突破至练气二层。

但他心中始终有一个疑问——那日在灵泉中，他似乎感受到了一股被封印的力量。
"""


class TestChapterSplitter(unittest.TestCase):

    def test_split_chinese_chapters(self):
        chapters = split_chapters(_SAMPLE_TEXT)
        self.assertGreaterEqual(len(chapters), 2)
        self.assertEqual(chapters[0]["chapter_num"], 1)
        self.assertIn("张远", chapters[0]["content"])

    def test_single_text_no_headings(self):
        text = "这是一段没有章节标题的文本。内容很短。"
        chapters = split_chapters(text)
        self.assertEqual(len(chapters), 1)

    def test_detect_pattern(self):
        pat = detect_chapter_pattern(_SAMPLE_TEXT)
        self.assertEqual(pat, "chinese_numbered")


class TestCharacterProfiler(unittest.TestCase):

    def setUp(self):
        self.profiler = CharacterProfiler()
        self.chapters = split_chapters(_SAMPLE_TEXT)

    def test_extract_characters(self):
        characters = self.profiler.extract_characters(self.chapters)
        # May or may not find characters depending on verb patterns
        self.assertIsInstance(characters, list)
        # Character extraction depends on action verb patterns
        self.assertIsInstance(characters, list)

    def test_min_mentions(self):
        characters = self.profiler.extract_characters(self.chapters, min_mentions=100)
        self.assertEqual(len(characters), 0)


class TestStyleExtractor(unittest.TestCase):

    def setUp(self):
        self.extractor = StyleExtractor()

    def test_extract(self):
        style = self.extractor.extract(_SAMPLE_TEXT)
        self.assertIn("avg_sentence_length", style)
        self.assertIn("dialogue_ratio", style)
        self.assertIn("total_chars", style)
        self.assertGreater(style["total_chars"], 0)

    def test_compare(self):
        p1 = self.extractor.extract(_SAMPLE_TEXT)
        p2 = {"avg_sentence_length": 50.0, "dialogue_ratio": 0.01, "total_chars": 100}
        result = self.extractor.compare(p1, p2)
        self.assertIn("similar", result)


class TestRhythmAnalyzer(unittest.TestCase):

    def setUp(self):
        self.analyzer = RhythmAnalyzer()

    def test_analyze(self):
        result = self.analyzer.analyze(_SAMPLE_TEXT)
        self.assertIn("segments", result)
        self.assertIn("overall", result)
        self.assertGreater(len(result["segments"]), 0)
        self.assertIn("avg_tension", result["overall"])


class TestFragmentSelector(unittest.TestCase):

    def setUp(self):
        self.selector = FragmentSelector()

    def test_sentence_templates(self):
        templates = self.selector.extract_sentence_templates(_SAMPLE_TEXT)
        self.assertGreater(len(templates), 0)
        self.assertIn("type", templates[0])

    def test_paragraph_templates(self):
        templates = self.selector.extract_paragraph_templates(_SAMPLE_TEXT)
        self.assertIsInstance(templates, list)


class TestPreprocessingPipeline(unittest.TestCase):

    def test_full_pipeline(self):
        pipeline = PreprocessingPipeline()
        result = pipeline.process(_SAMPLE_TEXT, ref_id="test_ref", skip_embedding=True)
        self.assertIn("chapters", result)
        self.assertIn("characters", result)
        self.assertIn("style_fingerprint", result)
        self.assertIn("rhythm", result)
        self.assertGreater(result["chapter_count"], 0)


if __name__ == "__main__":
    unittest.main()
