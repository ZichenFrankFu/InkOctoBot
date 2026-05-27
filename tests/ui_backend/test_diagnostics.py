"""Tests for build_generation_context() diagnostics (MVP).

Locks the public contract of the diagnostics dict — shape, status
classification, alert classification, per-section roll-up, and total
consistency. Builds against an empty project so most loaders are
inactive; injects synthetic plans where we need to exercise the
non-inactive code paths.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from storage.project_schema import ensure_creation_tables
from ui.backend.app.services.prompt_context import builder
from ui.backend.app.services.prompt_context import token_counter
from ui.backend.app.services.prompt_context.loader_protocol import LoaderPlan


def _fresh_db() -> str:
    db = os.path.join(tempfile.mkdtemp(), "diag.db")
    con = sqlite3.connect(db)
    ensure_creation_tables(con)
    con.execute("INSERT INTO projects (project_id, title) VALUES ('p1', 'T')")
    con.commit()
    con.close()
    return db


def _build(db: str) -> dict:
    """Run build_generation_context against an empty project so every
    loader is inactive — gives us a clean baseline to assert structure."""
    with mock.patch(
        "ui.backend.app.services.project_paths.get_db_path",
        return_value=db,
    ):
        return builder.build_generation_context(
            project_id="p1", chapter_num=1, db_path=db,
        )


class TestDiagnosticsStructure(unittest.TestCase):

    def setUp(self) -> None:
        token_counter.reset_for_tests()
        self.db = _fresh_db()

    def tearDown(self) -> None:
        token_counter.reset_for_tests()

    def test_top_level_keys(self) -> None:
        ctx = _build(self.db)
        diag = ctx["diagnostics"]
        for k in ("total", "sections", "loaders", "alerts"):
            self.assertIn(k, diag, f"diagnostics missing {k}")

    def test_total_shape(self) -> None:
        diag = _build(self.db)["diagnostics"]
        for k in ("chars", "tokens", "tokenizer_method"):
            self.assertIn(k, diag["total"])
        self.assertIn(diag["total"]["tokenizer_method"], ("tiktoken", "heuristic"))

    def test_sections_three_buckets(self) -> None:
        diag = _build(self.db)["diagnostics"]
        for bucket in ("system", "context", "user"):
            self.assertIn(bucket, diag["sections"])
            self.assertIn("chars", diag["sections"][bucket])
            self.assertIn("tokens", diag["sections"][bucket])

    def test_alerts_three_categories(self) -> None:
        diag = _build(self.db)["diagnostics"]
        for cat in ("empty", "pressure", "dominant"):
            self.assertIn(cat, diag["alerts"])
            self.assertIsInstance(diag["alerts"][cat], list)

    def test_all_14_loaders_present(self) -> None:
        diag = _build(self.db)["diagnostics"]
        expected = {
            "platform_directive", "user_preferences", "user_special_requirements",
            "chapter_outline", "character_cards", "worldbook", "reference",
            "foreshadowing", "inspiration", "current_chapter_draft",
            "subplots", "skills", "storyland_state", "reader_memory",
        }
        self.assertEqual(set(diag["loaders"]), expected)

    def test_per_loader_field_shape(self) -> None:
        diag = _build(self.db)["diagnostics"]
        info = diag["loaders"]["platform_directive"]
        for field in ("status", "chars", "tokens", "natural_length",
                       "allocated_budget", "utilization",
                       "share_of_total_tokens", "tier",
                       "plan_ms", "render_ms"):
            self.assertIn(field, info)


class TestStatusClassification(unittest.TestCase):

    def setUp(self) -> None:
        token_counter.reset_for_tests()
        self.db = _fresh_db()

    def tearDown(self) -> None:
        token_counter.reset_for_tests()

    def test_inactive_when_plan_returns_none(self) -> None:
        """An empty project means every loader returns None from plan()."""
        diag = _build(self.db)["diagnostics"]
        # platform_market is a permanent TODO → always inactive.
        self.assertEqual(diag["loaders"]["platform_directive"]["status"], "inactive")
        self.assertEqual(diag["loaders"]["platform_directive"]["chars"], 0)
        self.assertEqual(diag["loaders"]["platform_directive"]["tokens"], 0)
        self.assertEqual(diag["loaders"]["platform_directive"]["natural_length"], 0)

    def test_rendered_when_natural_fits_budget(self) -> None:
        """Inject a plan whose natural length is below its allocation."""
        def fake_plan(*a, **kw):
            body = "短小内容"
            return LoaderPlan(
                block_id="worldbook",
                natural_length=len(body) + 20,   # tiny, below any budget
                minimum=300, target=1600, maximum=2500, priority_tier=3,
                render=lambda budget: f"\n\n## 世界书\n{body}",
            )

        with mock.patch.object(builder, "worldbook", autospec=False) as m:
            m.plan = fake_plan
            diag = _build(self.db)["diagnostics"]
        self.assertEqual(diag["loaders"]["worldbook"]["status"], "rendered")

    def test_clipped_when_natural_exceeds_budget(self) -> None:
        """Natural length > allocated budget → status = 'clipped'."""
        long_body = "甲" * 5000

        def fake_plan(*a, **kw):
            return LoaderPlan(
                block_id="worldbook",
                natural_length=10000,            # way over max=2500
                minimum=300, target=1600, maximum=2500, priority_tier=3,
                render=lambda budget: f"\n\n## 世界书\n{long_body[:max(0, budget - 10)]}",
            )

        with mock.patch.object(builder, "worldbook", autospec=False) as m:
            m.plan = fake_plan
            diag = _build(self.db)["diagnostics"]
        self.assertEqual(diag["loaders"]["worldbook"]["status"], "clipped")
        self.assertGreater(diag["loaders"]["worldbook"]["natural_length"],
                            diag["loaders"]["worldbook"]["allocated_budget"])


class TestAlerts(unittest.TestCase):

    def setUp(self) -> None:
        token_counter.reset_for_tests()
        self.db = _fresh_db()

    def tearDown(self) -> None:
        token_counter.reset_for_tests()

    def test_empty_loaders_listed_in_alerts(self) -> None:
        """Every inactive loader (and every loader with utilization <
        EMPTY_THRESHOLD) lands in alerts.empty. Verify the property,
        not a magic count: an empty project still triggers a few
        loaders that have default content (skills returns canonical
        defaults, for instance)."""
        diag = _build(self.db)["diagnostics"]
        for block_id, info in diag["loaders"].items():
            if info["status"] == "inactive" or info["utilization"] < builder.EMPTY_THRESHOLD:
                self.assertIn(block_id, diag["alerts"]["empty"],
                                f"{block_id} should be flagged empty")
        # Sanity: at least the platform_market (perma-TODO) is empty.
        self.assertIn("platform_directive", diag["alerts"]["empty"])

    def test_pressure_alert_on_clipped_loader(self) -> None:
        def fake_plan(*a, **kw):
            return LoaderPlan(
                block_id="worldbook",
                natural_length=10000,
                minimum=300, target=1600, maximum=2500, priority_tier=3,
                render=lambda b: "## 世界书\n" + ("x" * max(0, b - 10)),
            )

        with mock.patch.object(builder, "worldbook", autospec=False) as m:
            m.plan = fake_plan
            diag = _build(self.db)["diagnostics"]
        # Either via status='clipped' OR utilization > 0.9 — both route
        # to pressure.
        self.assertIn("worldbook", diag["alerts"]["pressure"])

    def test_dominant_alert_when_one_loader_takes_majority(self) -> None:
        """If a single loader produces > 30% of total tokens, it's
        dominant. Stub a fat loader against the otherwise-empty project."""
        fat = "甲" * 2000

        def fake_plan(*a, **kw):
            return LoaderPlan(
                block_id="worldbook",
                natural_length=len(fat) + 20,
                minimum=300, target=1600, maximum=2500, priority_tier=3,
                render=lambda b: f"\n\n## 世界书\n{fat[:max(0, b - 10)]}",
            )

        with mock.patch.object(builder, "worldbook", autospec=False) as m:
            m.plan = fake_plan
            diag = _build(self.db)["diagnostics"]
        # Worldbook is the only non-empty loader, so its share = ~1.0.
        self.assertIn("worldbook", diag["alerts"]["dominant"])
        self.assertGreater(diag["loaders"]["worldbook"]["share_of_total_tokens"],
                            builder.DOMINANT_THRESHOLD)


class TestTotalConsistency(unittest.TestCase):

    def setUp(self) -> None:
        token_counter.reset_for_tests()
        self.db = _fresh_db()

    def tearDown(self) -> None:
        token_counter.reset_for_tests()

    def test_loader_tokens_sum_to_total(self) -> None:
        """diag.total.tokens == sum of per-loader tokens (every loader
        contributes its block to the total)."""
        diag = _build(self.db)["diagnostics"]
        loader_sum = sum(info["tokens"] for info in diag["loaders"].values())
        self.assertEqual(loader_sum, diag["total"]["tokens"])

    def test_section_tokens_match_member_loaders(self) -> None:
        """sections.<group>.tokens == sum(loader.tokens for loader in group)."""
        diag = _build(self.db)["diagnostics"]
        groups = builder._SECTION_GROUPS
        for group_name in ("system", "context", "user"):
            expected = sum(
                info["tokens"]
                for bid, info in diag["loaders"].items()
                if groups.get(bid) == group_name
            )
            self.assertEqual(diag["sections"][group_name]["tokens"], expected,
                              f"{group_name} mismatch")


if __name__ == "__main__":
    unittest.main()
