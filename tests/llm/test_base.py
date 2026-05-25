"""
Tests for model provider infrastructure.

Tests: ProviderConfig, LLMMessage, LLMResponse, ModelRouter config loading.
Does NOT require a running model — tests provider initialization and config only.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from llm.base import (
    ProviderConfig, LLMMessage, LLMResponse,
)


class TestProviderConfig(unittest.TestCase):

    def test_default_config(self):
        config = ProviderConfig(provider_type="ollama", model_name="test")
        self.assertEqual(config.provider_type, "ollama")
        self.assertEqual(config.max_tokens, 4096)
        self.assertEqual(config.temperature, 0.7)

    def test_custom_config(self):
        config = ProviderConfig(
            provider_type="openai",
            api_key="sk-test",
            model_name="gpt-4",
            max_tokens=8192,
            temperature=0.3,
        )
        self.assertEqual(config.api_key, "sk-test")
        self.assertEqual(config.max_tokens, 8192)


class TestLLMMessage(unittest.TestCase):

    def test_message(self):
        msg = LLMMessage(role="system", content="You are helpful.")
        self.assertEqual(msg.role, "system")


class TestLLMResponse(unittest.TestCase):

    def test_response(self):
        resp = LLMResponse(
            content="Hello", model="test",
            input_tokens=10, output_tokens=5,
        )
        self.assertEqual(resp.total_tokens, 15)
        self.assertEqual(resp.content, "Hello")

    def test_empty_response(self):
        resp = LLMResponse(content="")
        self.assertEqual(resp.total_tokens, 0)


class TestModelRouterConfig(unittest.TestCase):

    def test_load_config(self):
        from llm.router import ModelRouter
        router = ModelRouter()
        # Should have loaded config/models.yaml
        self.assertIsNotNone(router._config)

    def test_estimate_cost_local(self):
        from llm.router import ModelRouter
        router = ModelRouter()
        # Local models should have zero cost
        cost = router.estimate_cost("actor_agent", 1000, 500)
        self.assertIsInstance(cost, float)


class TestCostEstimator(unittest.TestCase):

    def test_estimate_step(self):
        from llm.router import ModelRouter
        from llm.cost_estimator import CostEstimator, SessionCostTracker
        router = ModelRouter()
        estimator = CostEstimator(router)
        est = estimator.estimate_step("scene_director")
        self.assertGreater(est.estimated_input_tokens, 0)

    def test_session_tracker(self):
        from llm.cost_estimator import SessionCostTracker
        tracker = SessionCostTracker(budget_usd=2.0)
        tracker.record_actual("test", 100, 50, 0.01)
        self.assertAlmostEqual(tracker.total_actual, 0.01)
        self.assertAlmostEqual(tracker.remaining_budget, 1.99)
        summary = tracker.summary()
        self.assertEqual(summary["calls"], 1)


if __name__ == "__main__":
    unittest.main()
