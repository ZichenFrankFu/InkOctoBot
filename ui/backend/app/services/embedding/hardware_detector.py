"""Hardware capability detection for the embedding service.

Probes the local machine once per process and caches the result.
Decisions consumed:
- Should this model run on GPU vs CPU?
- Is there enough VRAM for fp16, or does it have to fall back to CPU?
- How much RAM is available for CPU inference?

Failure-tolerant — torch / psutil being unavailable degrades to
``has_cuda=False`` + a conservative ram estimate rather than raising.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal

from .registry import ModelInfo

logger = logging.getLogger("inkoctobot.services.embedding.hardware")


Device = Literal["cuda", "cpu", "mps"]


@dataclass(frozen=True)
class HardwareCapabilities:
    """One-shot snapshot of the local machine's compute resources."""

    has_cuda: bool
    cuda_vram_mb: int        # total VRAM on the chosen CUDA device; 0 if none
    cuda_device_name: str    # "" when has_cuda is False
    has_mps: bool            # Apple Silicon GPU
    ram_mb: int              # total system RAM
    cpu_count: int           # logical cores
    # 物理 GPU 探测（即便 torch 是 CPU 版也能查到）—— 用于「有显卡但 torch 用不了」
    # 的清晰提示。
    physical_gpu: bool = False
    gpu_name: str = ""           # 物理 GPU 名（nvidia-smi）
    gpu_vram_mb: int = 0         # 物理 GPU 显存
    torch_cuda_build: bool = False   # 安装的 torch 是否为 CUDA 构建


_cached: HardwareCapabilities | None = None


def _probe_cuda() -> tuple[bool, int, str]:
    try:
        import torch  # noqa: WPS433 — lazy import
    except Exception as e:
        logger.debug("torch not importable: %s", e)
        return False, 0, ""
    try:
        if not torch.cuda.is_available():
            return False, 0, ""
        props = torch.cuda.get_device_properties(0)
        vram_mb = int(props.total_memory // (1024 * 1024))
        return True, vram_mb, props.name
    except Exception as e:
        logger.debug("cuda probe failed: %s", e)
        return False, 0, ""


def _torch_cuda_build() -> bool:
    """torch 是否带 CUDA（CPU-only 版 ``torch.version.cuda`` 为 None）。"""
    try:
        import torch  # noqa: WPS433
        return bool(getattr(getattr(torch, "version", None), "cuda", None))
    except Exception:
        return False


def _probe_nvidia_smi() -> tuple[bool, int, str]:
    """用 nvidia-smi 查物理 GPU —— 不依赖 torch，CPU-only torch 也能查到显卡。"""
    import shutil
    import subprocess
    exe = shutil.which("nvidia-smi")
    if not exe:
        return False, 0, ""
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=6,
        )
        lines = [l for l in (out.stdout or "").strip().splitlines() if l.strip()]
        if not lines:
            return False, 0, ""
        parts = lines[0].split(",")
        name = parts[0].strip()
        vram = int(float(parts[1].strip())) if len(parts) > 1 and parts[1].strip() else 0
        return True, vram, name
    except Exception as e:
        logger.debug("nvidia-smi probe failed: %s", e)
        return False, 0, ""


def _probe_mps() -> bool:
    try:
        import torch  # noqa: WPS433
        return bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
    except Exception:
        return False


def _probe_ram_mb() -> int:
    try:
        import psutil  # noqa: WPS433
        return int(psutil.virtual_memory().total // (1024 * 1024))
    except Exception as e:
        logger.debug("psutil not available: %s", e)
        # Conservative default: assume 4 GB. Real numbers come back the
        # moment psutil is installed; nothing else uses this value as a
        # hard gate.
        return 4096


def detect_hardware(*, refresh: bool = False) -> HardwareCapabilities:
    """Return the cached snapshot of the machine's compute resources.

    ``refresh=True`` re-probes — used by tests that swap in mocked
    torch backends and by ``EmbeddingService.switch_model`` if the
    user has changed GPU drivers mid-session.
    """
    global _cached
    if _cached is not None and not refresh:
        return _cached
    has_cuda, vram_mb, name = _probe_cuda()
    # 物理 GPU：torch 能用就用 torch 的数；否则退到 nvidia-smi（CPU-only torch 也能查）。
    if has_cuda:
        phys_gpu, phys_vram, phys_name = True, vram_mb, name
    else:
        phys_gpu, phys_vram, phys_name = _probe_nvidia_smi()
    caps = HardwareCapabilities(
        has_cuda=has_cuda,
        cuda_vram_mb=vram_mb,
        cuda_device_name=name,
        has_mps=_probe_mps(),
        ram_mb=_probe_ram_mb(),
        cpu_count=os.cpu_count() or 1,
        physical_gpu=phys_gpu,
        gpu_name=name or phys_name,
        gpu_vram_mb=vram_mb or phys_vram,
        torch_cuda_build=_torch_cuda_build(),
    )
    _cached = caps
    logger.info(
        "hardware detected: cuda=%s vram=%dmb mps=%s ram=%dmb cpus=%d "
        "physical_gpu=%s(%s) torch_cuda_build=%s",
        caps.has_cuda, caps.cuda_vram_mb, caps.has_mps,
        caps.ram_mb, caps.cpu_count, caps.physical_gpu, caps.gpu_name,
        caps.torch_cuda_build,
    )
    return caps


def reset_cache() -> None:
    """Test-helper: clear the in-process detection cache."""
    global _cached
    _cached = None


def can_run_on_gpu(model: ModelInfo, caps: HardwareCapabilities | None = None) -> bool:
    """True iff the requested model fits in the available VRAM."""
    caps = caps or detect_hardware()
    if not caps.has_cuda:
        return False
    return caps.cuda_vram_mb >= model.min_vram_mb


def decide_device(
    model: ModelInfo, caps: HardwareCapabilities | None = None,
) -> tuple[Device, list[str]]:
    """Pick the device for a model + return user-readable warnings.

    Decision order:
      1. CUDA if VRAM is sufficient.
      2. CPU if RAM is sufficient.
      3. CPU anyway with a "may swap" warning.
    """
    caps = caps or detect_hardware()
    warnings: list[str] = []

    if caps.has_cuda and can_run_on_gpu(model, caps):
        return "cuda", warnings

    if caps.has_cuda and not can_run_on_gpu(model, caps):
        warnings.append(
            f"{model.display_name} 需要 {model.min_vram_mb}MB 显存，"
            f"当前 GPU 仅 {caps.cuda_vram_mb}MB；将降级到 CPU 运行"
            f"（速度通常慢 5–10 倍）。"
        )

    if caps.ram_mb < model.min_ram_mb:
        warnings.append(
            f"系统内存 {caps.ram_mb}MB 低于 {model.display_name} 推荐的 "
            f"{model.min_ram_mb}MB；CPU 推理可能触发 swap 严重拖慢速度。"
        )
    return "cpu", warnings
