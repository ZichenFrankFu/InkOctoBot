"""
Hardware detection for LoRA training.

Detects available compute (CUDA / ROCm / MPS / CPU) and reports per-
device characteristics (name, VRAM, bf16/fp16 support, compute
capability). The trainer uses this to pick safe defaults:

  - device map: 'auto' on CUDA/MPS, 'cpu' otherwise
  - dtype:      bf16 if Ampere+ or MPS, fp16 if Turing, fp32 otherwise
  - 4-bit:      only on CUDA with bitsandbytes (auto-detect)
  - batch size: scaled to fit VRAM budget

Module degrades gracefully — if ``torch`` isn't installed, returns a
report that says "no GPU support detected, install torch to train".
"""
from __future__ import annotations

import logging
import platform
import shutil
import subprocess
from dataclasses import dataclass, field, asdict
from typing import Any

logger = logging.getLogger("inkoctobot.reference_ingest.lora.hardware")


@dataclass
class GPUInfo:
    index: int = 0
    name: str = ""
    vram_total_mb: int = 0
    vram_free_mb: int = 0
    compute_capability: str = ""        # CUDA only, e.g. "8.6"
    bf16_supported: bool = False
    fp16_supported: bool = False
    bitsandbytes_available: bool = False
    notes: str = ""


@dataclass
class HardwareReport:
    """What the trainer needs to know about the host."""
    platform: str = ""                  # "linux" / "darwin" / "windows"
    arch: str = ""                       # "x86_64" / "arm64" / ...
    cpu_cores: int = 0
    total_ram_mb: int = 0

    torch_installed: bool = False
    torch_version: str = ""

    cuda_available: bool = False
    rocm_available: bool = False
    mps_available: bool = False         # Apple Silicon

    gpus: list[GPUInfo] = field(default_factory=list)
    recommended_device: str = "cpu"     # one of: cuda / mps / cpu
    recommended_dtype: str = "fp32"     # bf16 / fp16 / fp32
    recommended_4bit: bool = False
    recommended_batch_size: int = 1
    recommended_grad_accum: int = 8

    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["gpus"] = [asdict(g) for g in self.gpus]
        return d


def detect_hardware() -> HardwareReport:
    """Probe the host. Single call; safe to memoize at the caller."""
    rep = HardwareReport(
        platform=platform.system().lower(),
        arch=platform.machine(),
        cpu_cores=_cpu_cores(),
        total_ram_mb=_ram_total_mb(),
    )

    try:
        import torch  # type: ignore
    except ImportError:
        rep.warnings.append(
            "torch 未安装 — LoRA 训练需要 `pip install torch transformers peft datasets`"
        )
        return rep

    rep.torch_installed = True
    rep.torch_version = getattr(torch, "__version__", "")

    # ─── CUDA ────────────────────────────────────────────────
    try:
        if torch.cuda.is_available():
            rep.cuda_available = True
            for i in range(torch.cuda.device_count()):
                gpu = _probe_cuda_gpu(torch, i)
                rep.gpus.append(gpu)
    except Exception as e:
        logger.debug("CUDA probe failed: %s", e)

    # ─── ROCm (AMD on Linux) ─────────────────────────────────
    # PyTorch's CUDA API also exposes ROCm devices via torch.cuda.*,
    # so the rocm_available flag is informational.
    if rep.cuda_available and rep.gpus:
        first = rep.gpus[0].name.lower()
        if "amd" in first or "radeon" in first or "instinct" in first:
            rep.rocm_available = True

    # ─── MPS (Apple Silicon) ─────────────────────────────────
    try:
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            rep.mps_available = True
            # MPS doesn't expose VRAM the same way; report a conservative slot
            rep.gpus.append(GPUInfo(
                index=0,
                name="Apple MPS (unified memory)",
                vram_total_mb=rep.total_ram_mb // 2,  # rough estimate
                vram_free_mb=rep.total_ram_mb // 2,
                bf16_supported=True,
                fp16_supported=True,
                bitsandbytes_available=False,
                notes="unified memory; bitsandbytes 4-bit not supported on MPS",
            ))
    except Exception as e:
        logger.debug("MPS probe failed: %s", e)

    # ─── Recommend device + dtype + batch size ───────────────
    _populate_recommendations(rep)

    return rep


def _probe_cuda_gpu(torch_mod, index: int) -> GPUInfo:
    props = torch_mod.cuda.get_device_properties(index)
    name = props.name
    vram_total = int(props.total_memory // (1024 * 1024))

    # Free memory — best-effort
    vram_free = vram_total
    try:
        free, _total = torch_mod.cuda.mem_get_info(index)
        vram_free = int(free // (1024 * 1024))
    except Exception:
        pass

    cc = f"{props.major}.{props.minor}"
    # bf16 supported on Ampere (8.0)+ and Hopper
    bf16 = props.major >= 8
    # fp16 supported on Volta (7.0)+
    fp16 = props.major >= 7

    bnb_ok = _bitsandbytes_works()

    notes = ""
    if props.major < 7:
        notes = f"compute capability {cc} 偏旧；fp16 训练可能不稳定"

    return GPUInfo(
        index=index, name=name,
        vram_total_mb=vram_total, vram_free_mb=vram_free,
        compute_capability=cc,
        bf16_supported=bf16, fp16_supported=fp16,
        bitsandbytes_available=bnb_ok,
        notes=notes,
    )


def _bitsandbytes_works() -> bool:
    """Detect whether bitsandbytes is importable AND functional.

    Many installs of bitsandbytes import but fail at first kernel call
    (mismatched CUDA versions). We only do the cheap import check here;
    the trainer can do a fuller smoke test.
    """
    try:
        import bitsandbytes  # type: ignore  # noqa: F401
        return True
    except Exception:
        return False


def _populate_recommendations(rep: HardwareReport) -> None:
    """Pick safe defaults for the trainer based on the report so far."""
    if rep.cuda_available and rep.gpus:
        rep.recommended_device = "cuda"
        # Best GPU — pick the one with most free VRAM
        best = max(rep.gpus, key=lambda g: g.vram_free_mb)
        if best.bf16_supported:
            rep.recommended_dtype = "bf16"
        elif best.fp16_supported:
            rep.recommended_dtype = "fp16"
        else:
            rep.recommended_dtype = "fp32"

        # 4-bit quantization recommended when:
        #  - bitsandbytes works
        #  - VRAM is tight (< 24 GB suggests it; we use 16 GB as the bar)
        if best.bitsandbytes_available and best.vram_total_mb < 24_000:
            rep.recommended_4bit = True
        else:
            rep.recommended_4bit = best.bitsandbytes_available and best.vram_free_mb < 12_000

        # Batch size heuristic: 1.5 GB per sample at 2048 tokens, rough.
        # Use free VRAM minus a 4 GB overhead reserve.
        usable = max(0, best.vram_free_mb - 4_000)
        # Quantized base model ~ 3 GB at 1.5B params (rule of thumb).
        if rep.recommended_4bit:
            rep.recommended_batch_size = max(1, min(8, usable // 1500))
        else:
            rep.recommended_batch_size = max(1, min(4, usable // 4500))

        # grad_accum * batch_size targets effective batch ≥ 8
        target_eff = 8
        rep.recommended_grad_accum = max(
            1, target_eff // max(rep.recommended_batch_size, 1)
        )
    elif rep.mps_available:
        rep.recommended_device = "mps"
        rep.recommended_dtype = "bf16"   # MPS bf16 works on M2+
        rep.recommended_4bit = False     # bitsandbytes not on MPS
        rep.recommended_batch_size = 1
        rep.recommended_grad_accum = 16
        rep.warnings.append(
            "MPS 训练较慢且不稳定，建议 epochs 不超过 1 用作小规模实验"
        )
    else:
        rep.recommended_device = "cpu"
        rep.recommended_dtype = "fp32"
        rep.recommended_4bit = False
        rep.recommended_batch_size = 1
        rep.recommended_grad_accum = 16
        rep.warnings.append(
            "未检测到 GPU — CPU 训练速度极慢（千 token 级数据可能耗时数小时），"
            "建议在有 NVIDIA / Apple Silicon 设备上运行"
        )


def _cpu_cores() -> int:
    try:
        import os
        return os.cpu_count() or 1
    except Exception:
        return 1


def _ram_total_mb() -> int:
    """Best-effort total RAM. Uses /proc/meminfo on Linux, ``sysctl`` on
    macOS, ``wmic`` on Windows. Returns 0 if all probes fail."""
    sys = platform.system().lower()
    try:
        if sys == "linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        return kb // 1024
        elif sys == "darwin":
            out = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=3,
            )
            return int(out.stdout.strip()) // (1024 * 1024)
        elif sys == "windows":
            # wmic is being deprecated; tolerate failure
            if shutil.which("wmic"):
                out = subprocess.run(
                    ["wmic", "ComputerSystem", "get", "TotalPhysicalMemory"],
                    capture_output=True, text=True, timeout=3,
                )
                for line in out.stdout.splitlines():
                    line = line.strip()
                    if line.isdigit():
                        return int(line) // (1024 * 1024)
    except Exception as e:
        logger.debug("RAM probe failed: %s", e)
    return 0
