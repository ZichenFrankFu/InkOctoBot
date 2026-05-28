"""Tests for hardware detection + device decision logic."""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from ui.backend.app.services.embedding import hardware_detector as hw
from ui.backend.app.services.embedding.registry import get_model


class TestHardwareDetection(unittest.TestCase):

    def setUp(self) -> None:
        hw.reset_cache()

    def tearDown(self) -> None:
        hw.reset_cache()

    def test_no_cuda_returns_cpu_capabilities(self) -> None:
        with mock.patch.object(hw, "_probe_cuda", return_value=(False, 0, "")), \
             mock.patch.object(hw, "_probe_mps", return_value=False), \
             mock.patch.object(hw, "_probe_ram_mb", return_value=8192):
            caps = hw.detect_hardware()
        self.assertFalse(caps.has_cuda)
        self.assertEqual(caps.cuda_vram_mb, 0)
        self.assertEqual(caps.ram_mb, 8192)

    def test_cuda_present_returns_vram(self) -> None:
        with mock.patch.object(hw, "_probe_cuda", return_value=(True, 24000, "NVIDIA RTX 4090")), \
             mock.patch.object(hw, "_probe_mps", return_value=False), \
             mock.patch.object(hw, "_probe_ram_mb", return_value=32768):
            caps = hw.detect_hardware()
        self.assertTrue(caps.has_cuda)
        self.assertEqual(caps.cuda_vram_mb, 24000)
        self.assertEqual(caps.cuda_device_name, "NVIDIA RTX 4090")

    def test_caching_avoids_reprobe(self) -> None:
        with mock.patch.object(hw, "_probe_cuda", return_value=(True, 24000, "x")) as probe, \
             mock.patch.object(hw, "_probe_mps", return_value=False), \
             mock.patch.object(hw, "_probe_ram_mb", return_value=8192):
            hw.detect_hardware()
            hw.detect_hardware()  # cached
        self.assertEqual(probe.call_count, 1)


class TestCanRunOnGpu(unittest.TestCase):

    def setUp(self) -> None:
        hw.reset_cache()

    def test_no_cuda_can_not_run(self) -> None:
        with mock.patch.object(hw, "_probe_cuda", return_value=(False, 0, "")), \
             mock.patch.object(hw, "_probe_mps", return_value=False), \
             mock.patch.object(hw, "_probe_ram_mb", return_value=8192):
            self.assertFalse(hw.can_run_on_gpu(get_model("bge-base-zh")))

    def test_sufficient_vram_can_run(self) -> None:
        with mock.patch.object(hw, "_probe_cuda", return_value=(True, 24000, "x")), \
             mock.patch.object(hw, "_probe_mps", return_value=False), \
             mock.patch.object(hw, "_probe_ram_mb", return_value=8192):
            # bge-base-zh wants 1500 MB; 24 GB is fine.
            self.assertTrue(hw.can_run_on_gpu(get_model("bge-base-zh")))

    def test_insufficient_vram_can_not_run(self) -> None:
        with mock.patch.object(hw, "_probe_cuda", return_value=(True, 4000, "x")), \
             mock.patch.object(hw, "_probe_mps", return_value=False), \
             mock.patch.object(hw, "_probe_ram_mb", return_value=8192):
            # Qwen3-Embedding-8B wants 16 GB; only 4 GB available.
            self.assertFalse(hw.can_run_on_gpu(get_model("qwen3-embedding-8b")))


class TestDecideDevice(unittest.TestCase):

    def setUp(self) -> None:
        hw.reset_cache()

    def test_gpu_chosen_when_sufficient(self) -> None:
        with mock.patch.object(hw, "_probe_cuda", return_value=(True, 24000, "x")), \
             mock.patch.object(hw, "_probe_mps", return_value=False), \
             mock.patch.object(hw, "_probe_ram_mb", return_value=32768):
            device, warnings = hw.decide_device(get_model("bge-base-zh"))
        self.assertEqual(device, "cuda")
        self.assertEqual(warnings, [])

    def test_cpu_chosen_when_vram_low_and_warning_emitted(self) -> None:
        with mock.patch.object(hw, "_probe_cuda", return_value=(True, 4000, "x")), \
             mock.patch.object(hw, "_probe_mps", return_value=False), \
             mock.patch.object(hw, "_probe_ram_mb", return_value=32768):
            device, warnings = hw.decide_device(get_model("qwen3-embedding-8b"))
        self.assertEqual(device, "cpu")
        self.assertTrue(any("显存" in w for w in warnings))

    def test_cpu_with_low_ram_warns_about_swap(self) -> None:
        with mock.patch.object(hw, "_probe_cuda", return_value=(False, 0, "")), \
             mock.patch.object(hw, "_probe_mps", return_value=False), \
             mock.patch.object(hw, "_probe_ram_mb", return_value=2000):
            device, warnings = hw.decide_device(get_model("bge-large-zh"))
        self.assertEqual(device, "cpu")
        self.assertTrue(any("swap" in w.lower() or "内存" in w for w in warnings))


if __name__ == "__main__":
    unittest.main()
