"""Tests for the embedding model registry (EMBEDDING_SPEC § 2)."""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from ui.backend.app.services.embedding.registry import (
    default_for_language, get_model, list_models, models_for_language,
)


class TestRegistry(unittest.TestCase):

    def test_seven_models_registered(self) -> None:
        # 6 spec models + 1 legacy text2vec-zh kept for v3.1 carry-over.
        self.assertEqual(len(list_models()), 7)

    def test_exactly_one_zh_default(self) -> None:
        zh = [m for m in list_models() if m.is_default_zh]
        self.assertEqual(len(zh), 1)
        self.assertEqual(zh[0].model_key, "bge-base-zh")

    def test_exactly_one_en_default(self) -> None:
        en = [m for m in list_models() if m.is_default_en]
        self.assertEqual(len(en), 1)
        self.assertEqual(en[0].model_key, "bge-m3")

    def test_all_required_fields_present(self) -> None:
        for m in list_models():
            self.assertTrue(m.model_key)
            self.assertTrue(m.display_name)
            self.assertTrue(m.hf_repo)
            self.assertIn(m.language, ("zh", "en", "multilingual"))
            self.assertGreater(m.dimension, 0)
            self.assertGreater(m.min_vram_mb, 0)
            self.assertGreater(m.min_ram_mb, 0)
            self.assertGreater(m.model_size_mb, 0)

    def test_get_model_by_key(self) -> None:
        m = get_model("bge-large-zh")
        self.assertEqual(m.dimension, 1024)
        self.assertEqual(m.language, "zh")

    def test_get_unknown_model_raises(self) -> None:
        with self.assertRaises(KeyError):
            get_model("nonexistent")

    def test_models_for_language_filters(self) -> None:
        zh = {m.model_key for m in models_for_language("zh")}
        en = {m.model_key for m in models_for_language("en")}
        # zh mode sees Chinese + multilingual (bge-m3, text2vec-multi)
        self.assertIn("bge-base-zh", zh)
        self.assertIn("bge-m3", zh)
        # en mode does NOT see zh-only models
        self.assertNotIn("bge-base-zh", en)
        self.assertNotIn("conan-embedding-v2", en)
        # but DOES see multilingual
        self.assertIn("bge-m3", en)

    def test_default_for_language(self) -> None:
        self.assertEqual(default_for_language("zh").model_key, "bge-base-zh")
        self.assertEqual(default_for_language("en").model_key, "bge-m3")
        # multilingual reuses en default (bge-m3 is multilingual)
        self.assertEqual(default_for_language("multilingual").model_key, "bge-m3")


if __name__ == "__main__":
    unittest.main()
