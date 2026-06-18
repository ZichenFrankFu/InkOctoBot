"""Minimal, self-contained hardware detection (copy of InkOctoBot's, trimmed).

ner_backend only needs ``detect_hardware()`` + the capability fields; the
model-registry helpers are dropped. Failure-tolerant: missing torch/psutil
degrades to has_cuda=False rather than raising.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass

logger = logging.getLogger("name_trainer.hardware")


@dataclass(frozen=True)
class HardwareCapabilities:
    has_cuda: bool
    cuda_vram_mb: int
    cuda_device_name: str
    has_mps: bool
    ram_mb: int
    cpu_count: int
    physical_gpu: bool = False
    gpu_name: str = ""
    gpu_vram_mb: int = 0
    torch_cuda_build: bool = False
    torch_version: str = ""
    torch_cuda_version: str = ""


_cached: HardwareCapabilities | None = None


def _probe_cuda() -> tuple[bool, int, str]:
    try:
        import torch
    except Exception:
        return False, 0, ""
    try:
        if not torch.cuda.is_available():
            return False, 0, ""
        props = torch.cuda.get_device_properties(0)
        return True, int(props.total_memory // (1024 * 1024)), props.name
    except Exception:
        return False, 0, ""


def _torch_cuda_build() -> bool:
    try:
        import torch
        return bool(getattr(getattr(torch, "version", None), "cuda", None))
    except Exception:
        return False


def _torch_info() -> tuple[str, str]:
    try:
        import torch
        ver = str(getattr(torch, "__version__", "") or "")
        cu = getattr(getattr(torch, "version", None), "cuda", None)
        return ver, str(cu or "")
    except Exception:
        return "", ""


def _probe_nvidia_smi() -> tuple[bool, int, str]:
    import shutil
    import subprocess
    exe = shutil.which("nvidia-smi")
    if not exe:
        return False, 0, ""
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=6,
        )
        lines = [l for l in (out.stdout or "").strip().splitlines() if l.strip()]
        if not lines:
            return False, 0, ""
        parts = lines[0].split(",")
        name = parts[0].strip()
        vram = int(float(parts[1].strip())) if len(parts) > 1 and parts[1].strip() else 0
        return True, vram, name
    except Exception:
        return False, 0, ""


def _probe_mps() -> bool:
    try:
        import torch
        return bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available())
    except Exception:
        return False


def _probe_ram_mb() -> int:
    try:
        import psutil
        return int(psutil.virtual_memory().total // (1024 * 1024))
    except Exception:
        return 4096


def detect_hardware(*, refresh: bool = False) -> HardwareCapabilities:
    global _cached
    if _cached is not None and not refresh:
        return _cached
    has_cuda, vram_mb, name = _probe_cuda()
    if has_cuda:
        phys_gpu, phys_vram, phys_name = True, vram_mb, name
    else:
        phys_gpu, phys_vram, phys_name = _probe_nvidia_smi()
    caps = HardwareCapabilities(
        has_cuda=has_cuda, cuda_vram_mb=vram_mb, cuda_device_name=name,
        has_mps=_probe_mps(), ram_mb=_probe_ram_mb(), cpu_count=os.cpu_count() or 1,
        physical_gpu=phys_gpu, gpu_name=name or phys_name,
        gpu_vram_mb=vram_mb or phys_vram, torch_cuda_build=_torch_cuda_build(),
        torch_version=_torch_info()[0], torch_cuda_version=_torch_info()[1],
    )
    _cached = caps
    logger.info("hardware: cuda=%s vram=%dmb ram=%dmb cpus=%d phys_gpu=%s(%s)",
                caps.has_cuda, caps.cuda_vram_mb, caps.ram_mb, caps.cpu_count,
                caps.physical_gpu, caps.gpu_name)
    return caps


def reset_cache() -> None:
    global _cached
    _cached = None
