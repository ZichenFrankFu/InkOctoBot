"""
Tests for Constitutional DPO data construction (InkOS B3).

Trainer itself requires torch + trl, which aren't installed in the test
env. We test the data-prep pipeline (construct_dpo_data + DB mining +
schema) which is pure-Python and the part most likely to have bugs.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from reference_ingest.lora.data_constructor import (
    construct_dpo_data,
    collect_dpo_pairs_from_db,
)


def _seed_chapter_with_versions(
    db_path: str, project_id: str, chapter_num: int,
    ai_text: str, user_text: str,
    title: str = "", outline: str = "",
    evaluation_json: str = "{}",
) -> None:
    """Helper: create a chapter with both AI and user_edit versions."""
    chapter_id = f"{project_id}_ch{chapter_num:04d}"
    with sqlite3.connect(db_path) as c:
        c.execute(
            "INSERT OR IGNORE INTO projects (project_id, title) VALUES (?, ?)",
            (project_id, "t"),
        )
        c.execute(
            "INSERT OR REPLACE INTO chapters "
            "(chapter_id, project_id, chapter_num, title, outline, "
            "evaluation_json, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'draft')",
            (chapter_id, project_id, chapter_num, title, outline,
             evaluation_json),
        )
        c.execute(
            "INSERT INTO text_versions "
            "(version_id, chapter_id, version_num, source, content) "
            "VALUES (?, ?, 1, 'ai', ?)",
            (f"v_{chapter_id}_ai", chapter_id, ai_text),
        )
        c.execute(
            "INSERT INTO text_versions "
            "(version_id, chapter_id, version_num, source, content) "
            "VALUES (?, ?, 2, 'user_edit', ?)",
            (f"v_{chapter_id}_user", chapter_id, user_text),
        )
        c.commit()


class TestConstructDpoData(unittest.TestCase):

    def test_valid_triple_passes(self) -> None:
        out = construct_dpo_data([
            {"prompt": "p", "rejected": "r" * 100, "chosen": "c" * 100,
             "rationale": "user 改了节奏"},
        ])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["prompt"], "p")
        self.assertEqual(out[0]["metadata"]["rationale"], "user 改了节奏")

    def test_missing_field_dropped(self) -> None:
        out = construct_dpo_data([
            {"prompt": "", "rejected": "r", "chosen": "c"},
            {"prompt": "p", "rejected": "", "chosen": "c"},
            {"prompt": "p", "rejected": "r", "chosen": ""},
        ])
        self.assertEqual(out, [])

    def test_degenerate_pair_dropped(self) -> None:
        """rejected == chosen → no preference signal."""
        out = construct_dpo_data([
            {"prompt": "p", "rejected": "same", "chosen": "same"},
        ])
        self.assertEqual(out, [])

    def test_max_length_truncation(self) -> None:
        out = construct_dpo_data([
            {"prompt": "a" * 5000, "rejected": "r", "chosen": "c"},
        ], max_length=200)
        self.assertEqual(len(out[0]["prompt"]), 200)

    def test_metadata_carries_through(self) -> None:
        out = construct_dpo_data([
            {"prompt": "p", "rejected": "r", "chosen": "c",
             "violation_categories": ["pov_break", "list_syntax"],
             "source_chapter_id": "ch_test", "source_project_id": "p1"},
        ])
        meta = out[0]["metadata"]
        self.assertEqual(meta["violation_categories"], ["pov_break", "list_syntax"])
        self.assertEqual(meta["source_chapter_id"], "ch_test")
        self.assertEqual(meta["source_project_id"], "p1")


class TestCollectDpoPairsFromDB(unittest.TestCase):

    def setUp(self) -> None:
        self.db = os.path.join(tempfile.mkdtemp(), "dpo.db")
        with sqlite3.connect(self.db) as c:
            ensure_creation_tables(c)

    def test_no_data_returns_empty(self) -> None:
        triples = collect_dpo_pairs_from_db(self.db, project_id="nonexistent")
        self.assertEqual(triples, [])

    def test_user_edit_after_ai_yields_pair(self) -> None:
        # Edit diff must be ≥ min_diff_chars (default 50) to pass the
        # "trivial edits" filter.
        _seed_chapter_with_versions(
            self.db, "p1", 1,
            ai_text="A" * 200, user_text="B" * 100,
            title="Ch1", outline="主角发现奇怪的水晶",
        )
        triples = collect_dpo_pairs_from_db(self.db, project_id="p1")
        self.assertEqual(len(triples), 1)
        t = triples[0]
        self.assertEqual(t["rejected"], "A" * 200)
        self.assertEqual(t["chosen"], "B" * 100)
        self.assertIn("主角发现奇怪的水晶", t["prompt"])
        self.assertIn("Ch1", t["prompt"])

    def test_small_edit_filtered_by_min_diff(self) -> None:
        _seed_chapter_with_versions(
            self.db, "p1", 1,
            ai_text="A" * 100, user_text="A" * 95,  # only 5 char diff
            title="t",
        )
        triples = collect_dpo_pairs_from_db(self.db, "p1", min_diff_chars=50)
        self.assertEqual(triples, [])

    def test_violation_categories_extracted_from_evaluation(self) -> None:
        import json as _j
        eval_json = _j.dumps({
            "issues": [
                {"type": "pov_break", "message": "x"},
                {"type": "list_syntax", "message": "y"},
            ],
        })
        _seed_chapter_with_versions(
            self.db, "p1", 1,
            ai_text="A" * 200, user_text="B" * 100,  # > 50 char diff
            evaluation_json=eval_json,
        )
        triples = collect_dpo_pairs_from_db(self.db, "p1")
        self.assertEqual(
            sorted(triples[0]["violation_categories"]),
            ["list_syntax", "pov_break"],
        )

    def test_only_includes_user_edits_after_ai(self) -> None:
        """An AI version followed by another AI version (no user_edit)
        should NOT produce a triple."""
        with sqlite3.connect(self.db) as c:
            c.execute(
                "INSERT OR IGNORE INTO projects (project_id, title) VALUES ('p1', 't')"
            )
            c.execute(
                "INSERT INTO chapters (chapter_id, project_id, chapter_num) "
                "VALUES ('ch1', 'p1', 1)"
            )
            c.execute(
                "INSERT INTO text_versions (version_id, chapter_id, version_num, source, content) "
                "VALUES ('v1', 'ch1', 1, 'ai', 'first ai')"
            )
            c.execute(
                "INSERT INTO text_versions (version_id, chapter_id, version_num, source, content) "
                "VALUES ('v2', 'ch1', 2, 'ai', 'second ai')"
            )
            c.commit()
        self.assertEqual(collect_dpo_pairs_from_db(self.db, "p1"), [])

    def test_project_filter(self) -> None:
        _seed_chapter_with_versions(
            self.db, "p1", 1, "A" * 200, "B" * 100,
        )
        _seed_chapter_with_versions(
            self.db, "p2", 1, "X" * 200, "Y" * 100,
        )
        only_p1 = collect_dpo_pairs_from_db(self.db, "p1")
        self.assertEqual(len(only_p1), 1)
        self.assertEqual(only_p1[0]["source_project_id"], "p1")

        all_projects = collect_dpo_pairs_from_db(self.db, project_id=None)
        self.assertEqual(len(all_projects), 2)


class TestLoRADPOConfigShape(unittest.TestCase):

    def test_config_to_dict(self) -> None:
        # We test the config without instantiating torch.
        from reference_ingest.lora.trainer import LoRADPOConfig
        c = LoRADPOConfig(
            base_model="Qwen/Qwen2-1.5B",
            sft_adapter_path="data/lora_output/adapter",
            beta=0.2, epochs=2,
        )
        d = c.to_dict()
        self.assertEqual(d["base_model"], "Qwen/Qwen2-1.5B")
        self.assertEqual(d["sft_adapter_path"], "data/lora_output/adapter")
        self.assertEqual(d["beta"], 0.2)
        self.assertEqual(d["epochs"], 2)


if __name__ == "__main__":
    unittest.main()
