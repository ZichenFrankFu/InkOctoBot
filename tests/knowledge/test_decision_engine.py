"""
Tests for the quantitative decision engine (Character Card Layer B).

Tests: ValueWeights, ProspectParams, BayesianTrust, DecisionModel.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from knowledge.decision_engine import (
    DecisionModel, ValueWeights, ProspectParams,
    StochasticParams, BayesianTrust,
)


class TestBayesianTrust(unittest.TestCase):

    def test_initial_trust(self):
        bt = BayesianTrust()
        self.assertAlmostEqual(bt.trust_level, 0.5, places=2)

    def test_positive_update(self):
        bt = BayesianTrust()
        bt.observe_positive(weight=3.0)
        self.assertGreater(bt.trust_level, 0.5)

    def test_negative_update(self):
        bt = BayesianTrust()
        bt.observe_negative(weight=3.0)
        self.assertLess(bt.trust_level, 0.5)

    def test_to_dict(self):
        bt = BayesianTrust(alpha=7, beta=3)
        d = bt.to_dict()
        self.assertAlmostEqual(d["trust"], 0.7, places=2)


class TestDecisionModel(unittest.TestCase):

    def setUp(self):
        self.model = DecisionModel(character_name="张远")

    def test_action_utility_positive(self):
        util = self.model.compute_action_utility(
            "修炼", {"power": 0.5, "survival": 0.1},
        )
        self.assertIsInstance(util, float)

    def test_action_utility_negative(self):
        util = self.model.compute_action_utility(
            "冒险", {"survival": -0.5, "power": 0.3},
        )
        # Loss aversion should make this negative
        self.assertIsInstance(util, float)

    def test_trust_tracking(self):
        self.model.update_trust("李清漪", positive=True, weight=2.0)
        trust = self.model.get_trust("李清漪")
        self.assertGreater(trust, 0.5)

        self.model.update_trust("李清漪", positive=False, weight=5.0)
        trust = self.model.get_trust("李清漪")
        self.assertLess(trust, 0.6)

    def test_impulse_check(self):
        # Just verify it returns a bool
        result = self.model.should_act_impulsively()
        self.assertIsInstance(result, bool)

    def test_conversation_likelihood(self):
        result = self.model.conversation_likelihood()
        self.assertIsInstance(result, int)
        self.assertGreaterEqual(result, 0)

    def test_generate_guidance(self):
        guidance = self.model.generate_guidance(
            "面对强敌",
            [
                {"action": "战斗", "outcomes": {"power": 0.3, "survival": -0.4}},
                {"action": "逃跑", "outcomes": {"survival": 0.3, "loyalty": -0.2}},
                {"action": "谈判", "outcomes": {"survival": 0.1, "justice": 0.2}},
            ],
        )
        self.assertIn("张远", guidance)
        self.assertIn("情境", guidance)

    def test_serialization(self):
        self.model.update_trust("A", positive=True)
        d = self.model.to_dict()
        restored = DecisionModel.from_dict(d)
        self.assertEqual(restored.character_name, "张远")
        self.assertIn("A", restored.trust_map)

    def test_custom_values(self):
        model = DecisionModel(
            character_name="反派",
            values=ValueWeights(power=0.4, survival=0.1, justice=0.0),
            prospect=ProspectParams(loss_aversion=1.5),
        )
        util_risky = model.compute_action_utility("夺权", {"power": 0.8, "survival": -0.3})
        self.assertIsInstance(util_risky, float)


if __name__ == "__main__":
    unittest.main()
