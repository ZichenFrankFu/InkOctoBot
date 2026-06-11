"""Per-agent loader profiles (Builder·机制1) + spec budget table checks."""
from __future__ import annotations

from ui.backend.app.services.prompt_context.builder import (
    AGENT_LOADER_PROFILES, agent_default_budget, build_generation_context,
)
from ui.backend.app.services.prompt_context.budget_allocator import (
    LOADER_BUDGETS,
)


class TestProfiles:
    def test_writer_profile_total_target_is_spec_24k(self) -> None:
        """spec § 3.3.5 [机制]: 总Token Budget在24000左右 — the writer
        profile's target sum must land exactly on the spec table's 24000."""
        assert agent_default_budget("writer") == 24000

    def test_writer_profile_excludes_market_overview(self) -> None:
        assert "market_overview" not in AGENT_LOADER_PROFILES["writer"]

    def test_scene_director_profile_matches_spec(self) -> None:
        """spec § 3.4.3: Scene Director uses exactly 10 loaders."""
        expected = {
            "inspiration", "character_cards", "worldbook", "chapter_outline",
            "reader_memory", "storyland_state", "foreshadowing", "subplots",
            "user_special_requirements", "skills",
        }
        assert set(AGENT_LOADER_PROFILES["scene_director"]) == expected

    def test_book_opening_profile(self) -> None:
        assert set(AGENT_LOADER_PROFILES["book_opening"]) == {
            "market_overview", "platform_directive", "reference",
        }

    def test_every_profile_block_has_budget_row(self) -> None:
        for profile in AGENT_LOADER_PROFILES.values():
            for block in profile:
                assert block in LOADER_BUDGETS, block

    def test_builder_filters_blocks_by_agent(self) -> None:
        """Loaders outside the agent's profile must not appear in blocks
        nor in diagnostics (they don't take part in allocation)."""
        ctx = build_generation_context(
            "no-such-project", chapter_num=1, agent="scene_director",
        )
        blocks = set(ctx["blocks"].keys())
        assert blocks == set(AGENT_LOADER_PROFILES["scene_director"])
        assert "current_chapter_draft" not in blocks
        assert set(ctx["diagnostics"]["loaders"].keys()) == blocks
        assert ctx["diagnostics"]["agent"] == "scene_director"

    def test_builder_default_agent_is_writer(self) -> None:
        ctx = build_generation_context("no-such-project", chapter_num=1)
        assert ctx["diagnostics"]["agent"] == "writer"
        assert set(ctx["blocks"].keys()) == set(AGENT_LOADER_PROFILES["writer"])


class TestSpecBudgetTable:
    """LOADER_BUDGETS must match the spec's 预算画像表 (§ 3.3.6)."""

    SPEC = {
        "chapter_outline":           (1,  600, 1200, 1800),
        "user_special_requirements": (1,  300,  600, 1000),
        "character_cards":           (1,  800, 1800, 2800),
        "storyland_state":           (1,  800, 2000, 3000),
        "current_chapter_draft":     (1, 1500, 4000, 6000),
        "reader_memory":             (2, 1500, 4500, 6000),
        "worldbook":                 (2,  600, 1600, 2400),
        "subplots":                  (2,  500, 1200, 1800),
        "foreshadowing":             (2,  300,  800, 1200),
        "skills":                    (2,  800, 2400, 3600),
        "reference":                 (3,  800, 2400, 3600),
        "inspiration":               (3,  200,  600,  900),
        "platform_directive":        (3,  150,  400,  600),
        "user_preferences":          (3,  200,  500,  800),
        "market_overview":           (4,  600, 1500, 2000),
    }

    def test_table_matches_spec(self) -> None:
        for block, (tier, mn, target, mx) in self.SPEC.items():
            cfg = LOADER_BUDGETS[block]
            assert cfg["tier"] == tier, block
            assert cfg["min"] == mn, block
            assert cfg["target"] == target, block
            assert cfg["max"] == mx, block

    def test_no_extra_loaders(self) -> None:
        assert set(LOADER_BUDGETS.keys()) == set(self.SPEC.keys())
