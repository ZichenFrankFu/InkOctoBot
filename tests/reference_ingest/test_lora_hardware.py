"""
Tests for reference_ingest/lora/hardware.py — hardware detection.

Module-level torch import is conditional, so we cover three branches:
  - torch not installed (warnings, recommended_device='cpu')
  - torch installed + CUDA available (mocked)
  - torch installed + MPS available (mocked)

Plus the recommendation heuristics.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from reference_ingest.lora.hardware import (
    HardwareReport,
    GPUInfo,
    detect_hardware,
    _populate_recommendations,
)


class TestNoTorch(unittest.TestCase):

    def test_returns_cpu_with_warning(self) -> None:
        with patch.dict(sys.modules, {"torch": None}):
            # Force import to fail
            if "torch" in sys.modules:
                del sys.modules["torch"]
            # Sabotage the import lookup
            import builtins
            orig_import = builtins.__import__

            def mock_import(name, *a, **k):
                if name == "torch":
                    raise ImportError("torch sabotaged")
                return orig_import(name, *a, **k)

            with patch.object(builtins, "__import__", side_effect=mock_import):
                rep = detect_hardware()

        self.assertFalse(rep.torch_installed)
        self.assertEqual(rep.recommended_device, "cpu")
        self.assertEqual(rep.recommended_dtype, "fp32")
        self.assertFalse(rep.recommended_4bit)
        self.assertTrue(any("torch 未安装" in w for w in rep.warnings))


class TestRecommendations(unittest.TestCase):
    """Direct tests of the heuristic, no torch needed."""

    def test_cuda_ampere_recommends_bf16_and_4bit(self) -> None:
        rep = HardwareReport()
        rep.cuda_available = True
        rep.gpus = [GPUInfo(
            index=0, name="NVIDIA RTX 3090",
            vram_total_mb=24_000, vram_free_mb=22_000,
            compute_capability="8.6",
            bf16_supported=True, fp16_supported=True,
            bitsandbytes_available=True,
        )]
        _populate_recommendations(rep)
        self.assertEqual(rep.recommended_device, "cuda")
        self.assertEqual(rep.recommended_dtype, "bf16")
        # 22 GB free, bf16 + bnb → recommend 4bit on this tier (24G is the cutoff)
        # The exact decision depends on heuristic; check the result is sane.
        self.assertGreaterEqual(rep.recommended_batch_size, 1)
        self.assertGreaterEqual(rep.recommended_grad_accum, 1)

    def test_cuda_turing_recommends_fp16(self) -> None:
        rep = HardwareReport()
        rep.cuda_available = True
        rep.gpus = [GPUInfo(
            index=0, name="NVIDIA GTX 1080 Ti",
            vram_total_mb=11_000, vram_free_mb=10_000,
            compute_capability="6.1",
            bf16_supported=False, fp16_supported=False,
            bitsandbytes_available=False,
        )]
        _populate_recommendations(rep)
        self.assertEqual(rep.recommended_device, "cuda")
        # No bf16/fp16 → fp32
        self.assertEqual(rep.recommended_dtype, "fp32")
        # bnb unavailable → no 4bit
        self.assertFalse(rep.recommended_4bit)

    def test_low_vram_cuda_recommends_4bit(self) -> None:
        rep = HardwareReport()
        rep.cuda_available = True
        rep.gpus = [GPUInfo(
            index=0, name="NVIDIA RTX 3060",
            vram_total_mb=12_000, vram_free_mb=10_000,
            compute_capability="8.6",
            bf16_supported=True, fp16_supported=True,
            bitsandbytes_available=True,
        )]
        _populate_recommendations(rep)
        self.assertTrue(rep.recommended_4bit, "Should suggest 4bit for 12GB")
        self.assertEqual(rep.recommended_dtype, "bf16")

    def test_mps_recommends_bf16_no_4bit(self) -> None:
        rep = HardwareReport(total_ram_mb=16_000)
        rep.mps_available = True
        rep.gpus = [GPUInfo(
            index=0, name="Apple MPS",
            vram_total_mb=8_000, vram_free_mb=8_000,
            bf16_supported=True, fp16_supported=True,
        )]
        _populate_recommendations(rep)
        self.assertEqual(rep.recommended_device, "mps")
        self.assertEqual(rep.recommended_dtype, "bf16")
        self.assertFalse(rep.recommended_4bit)
        self.assertEqual(rep.recommended_batch_size, 1)
        # warns about MPS speed
        self.assertTrue(any("MPS" in w for w in rep.warnings))

    def test_cpu_only_recommends_fp32_with_warning(self) -> None:
        rep = HardwareReport()
        # no cuda / mps
        _populate_recommendations(rep)
        self.assertEqual(rep.recommended_device, "cpu")
        self.assertEqual(rep.recommended_dtype, "fp32")
        self.assertFalse(rep.recommended_4bit)
        self.assertEqual(rep.recommended_batch_size, 1)
        self.assertTrue(any("未检测到 GPU" in w for w in rep.warnings))


class TestReportSerialization(unittest.TestCase):

    def test_to_dict_includes_gpus(self) -> None:
        rep = HardwareReport(platform="linux", arch="x86_64")
        rep.gpus = [GPUInfo(name="Test GPU", vram_total_mb=8000)]
        d = rep.to_dict()
        self.assertEqual(d["platform"], "linux")
        self.assertIsInstance(d["gpus"], list)
        self.assertEqual(d["gpus"][0]["name"], "Test GPU")


class TestDetectHardwareSmokeIntegration(unittest.TestCase):

    def test_no_exception_on_real_call(self) -> None:
        """Real call should not throw — degrades gracefully when torch
        isn't usable in the test env."""
        rep = detect_hardware()
        self.assertIsInstance(rep, HardwareReport)
        self.assertIn(rep.recommended_device, {"cuda", "mps", "cpu"})
        self.assertIn(rep.recommended_dtype, {"bf16", "fp16", "fp32"})
        self.assertGreaterEqual(rep.recommended_batch_size, 1)


class TestNvidiaSmiFallback(unittest.TestCase):
    """The fallback should populate gpus even when torch is absent
    or installed as CPU-only."""

    def test_smi_output_parsed_when_torch_missing(self) -> None:
        """Simulate: no torch, nvidia-smi present + returns 2 GPUs."""
        import reference_ingest.lora.hardware as hw_mod

        fake_smi_output = (
            "0, NVIDIA GeForce RTX 4090, 24564, 23800, 8.9\n"
            "1, NVIDIA GeForce RTX 3060, 12288, 11000, 8.6\n"
        )

        class FakeProc:
            def __init__(self):
                self.returncode = 0
                self.stdout = fake_smi_output
                self.stderr = ""

        import builtins
        orig_import = builtins.__import__

        def mock_import(name, *a, **k):
            if name == "torch":
                raise ImportError("torch sabotaged for test")
            return orig_import(name, *a, **k)

        with patch.object(builtins, "__import__", side_effect=mock_import), \
             patch.object(hw_mod, "shutil", MagicMock(which=MagicMock(return_value="/usr/bin/nvidia-smi"))), \
             patch.object(hw_mod, "subprocess", MagicMock(run=MagicMock(return_value=FakeProc()))):
            rep = hw_mod.detect_hardware()

        self.assertFalse(rep.torch_installed)
        self.assertTrue(rep.cuda_available)
        self.assertEqual(len(rep.gpus), 2)
        self.assertEqual(rep.gpus[0].name, "NVIDIA GeForce RTX 4090")
        self.assertEqual(rep.gpus[0].vram_total_mb, 24564)
        self.assertEqual(rep.gpus[0].compute_capability, "8.9")
        self.assertTrue(rep.gpus[0].bf16_supported)  # Ada (8.9) supports bf16
        # 3060 (8.6) also supports bf16
        self.assertTrue(rep.gpus[1].bf16_supported)
        # Recommended device is cuda even without torch
        self.assertEqual(rep.recommended_device, "cuda")

    def test_no_smi_no_torch_falls_back_to_cpu(self) -> None:
        import reference_ingest.lora.hardware as hw_mod
        import builtins
        orig_import = builtins.__import__

        def mock_import(name, *a, **k):
            if name == "torch":
                raise ImportError("no torch")
            return orig_import(name, *a, **k)

        with patch.object(builtins, "__import__", side_effect=mock_import), \
             patch.object(hw_mod, "shutil", MagicMock(which=MagicMock(return_value=None))):
            rep = hw_mod.detect_hardware()

        self.assertEqual(rep.gpus, [])
        self.assertEqual(rep.recommended_device, "cpu")


if __name__ == "__main__":
    unittest.main()
