"""人名实体识别后端 —— 硬件分级 LTP，缺位时 jieba ``nr`` 兜底。

spec 语言学文本特征 §4 的完整降级路径：

    有可用 GPU            → LTP on CUDA
    否则 CPU 算力足够      → LTP on CPU
    CPU 不足 / 测不到算力   → 跳过 LTP，仅用 jieba 'nr' + 打包种子人名库兜底

复用 app 既有的硬件检测模块（``embedding.hardware_detector``）做判定，不另起一套。
LTP / torch 未安装时一律走 jieba 分支，保证 day-1 在纯 CPU、无重型依赖的机器上也能跑。

本模块只负责「从文本抽 PER 人名实体」与「（给句法分析复用的）LTP 句柄」；人名库
的去重、入库、DF 统计在 name_library.py。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

logger = logging.getLogger("inkoctobot.market_extractor.ner_backend")

# LTP NER 里人名实体的标签（跨版本兼容：4.x 用 'Nh'）。
_LTP_PER_TAGS = {"Nh", "PER", "PERSON", "person", "name"}
_CJK_ONLY = re.compile(r"^[一-鿿]{2,8}$")

# 跑 LTP 的最低 CPU 门槛（低于此判为「算力不足」→ 跳过 LTP）。
_MIN_CPU_CORES = 4
_MIN_RAM_MB = 4096
_MIN_VRAM_MB = 1000     # LTP 模型很小，低显存 GPU 也能跑


@dataclass(frozen=True)
class NerBackendInfo:
    backend: str        # 'ltp_gpu' | 'ltp_cpu' | 'jieba'
    device: str         # 'cuda' | 'cpu' | 'none'
    ltp_available: bool
    reason: str
    cuda_device_name: str = ""

    @property
    def uses_ltp(self) -> bool:
        return self.backend in ("ltp_gpu", "ltp_cpu")

    def to_dict(self) -> dict:
        return {
            "backend": self.backend, "device": self.device,
            "ltp_available": self.ltp_available, "reason": self.reason,
            "cuda_device_name": self.cuda_device_name, "uses_ltp": self.uses_ltp,
        }


_cached_info: NerBackendInfo | None = None


# 新版 huggingface_hub 删除了 ``use_auth_token`` 等旧参数，而 LTP 4.x 仍按旧签名
# 调用 → ``hf_hub_download() got an unexpected keyword argument 'use_auth_token'``，
# 导致模型永远加载失败、退回 jieba。这里在 import ltp 之前打一层兼容补丁：先按原样
# 调用，仅当抛出"未知关键字参数"的 TypeError 时才把 use_auth_token 翻译成 token、或
# 去掉被删的参数后重试 —— 对新旧 huggingface_hub 都安全。
_hf_patched = False
_LTP_MARK = "_ltp_hf_compat"      # 标记"已是本补丁包装"，避免重复包装


def _ensure_hf_compat() -> None:
    global _hf_patched
    if _hf_patched:
        return
    try:
        import re as _re
        import sys
        import huggingface_hub as _hf
    except Exception:
        return

    def _wrap(fn):
        if getattr(fn, _LTP_MARK, False):
            return fn

        def inner(*args, **kwargs):
            for _ in range(8):     # 至多几轮逐个修正被删的关键字参数
                try:
                    return fn(*args, **kwargs)
                except TypeError as e:
                    m = _re.search(r"unexpected keyword argument '(\w+)'", str(e))
                    if not m:
                        raise
                    bad = m.group(1)
                    if bad == "use_auth_token" and "use_auth_token" in kwargs:
                        kwargs["token"] = kwargs.pop("use_auth_token")   # 改用新参数名
                    elif bad in kwargs:
                        kwargs.pop(bad)                                  # 去掉被删的参数
                    else:
                        raise
            return fn(*args, **kwargs)
        setattr(inner, _LTP_MARK, True)
        inner.__wrapped__ = fn
        return inner

    patched_any = False
    for fname in ("hf_hub_download", "snapshot_download", "cached_download"):
        orig = getattr(_hf, fname, None)
        if orig is None or getattr(orig, _LTP_MARK, False):
            continue
        wrapped = _wrap(orig)
        try:
            setattr(_hf, fname, wrapped)
        except Exception:
            continue
        patched_any = True
        # 任何已 ``from huggingface_hub import hf_hub_download`` 绑定旧引用的模块
        # （含 huggingface_hub.file_download、ltp 的下载模块），一并替换。
        for mod in list(sys.modules.values()):
            try:
                if getattr(mod, fname, None) is orig:
                    setattr(mod, fname, wrapped)
            except Exception:
                pass
    _hf_patched = True
    if patched_any:
        logger.info("patched huggingface_hub download fns for LTP compat "
                    "(use_auth_token→token)")


def reset_backend_cache() -> None:
    """清掉后端判定缓存 + LTP 句柄（改了补丁后想免重启重试时用）。"""
    global _cached_info, _pipeline_singleton, _hf_patched
    _cached_info = None
    _pipeline_singleton = None
    _hf_patched = False


def _ltp_importable() -> bool:
    try:
        _ensure_hf_compat()        # 必须在 import ltp 之前打补丁
        import ltp  # noqa: F401
        import torch  # noqa: F401
        return True
    except Exception:
        return False


def _degrade_to_seed(reason: str) -> None:
    """LTP 运行时加载失败时，后端缓存改成 seed（只靠静态种子人名库，不抽新名）。"""
    global _cached_info
    _cached_info = NerBackendInfo("seed", "none", True, reason)
    logger.warning("NER degraded to seed name DB: %s", reason)


def detect_ner_backend(*, refresh: bool = False) -> NerBackendInfo:
    """按硬件挑选 NER 后端并缓存。``refresh=True`` 重测（GPU 驱动变化/测试）。
    人名识别只用 LTP；LTP 不可用时退到静态种子人名库（不再用 jieba 抽人名——
    jieba 对人名的错误率太高）。"""
    global _cached_info
    if _cached_info is not None and not refresh:
        return _cached_info

    if not _ltp_importable():
        info = NerBackendInfo(
            backend="seed", device="none", ltp_available=False,
            reason="LTP/torch 未安装 —— 仅用静态种子人名库（装 ltp+torch 后用 LTP 抽名）",
        )
        _cached_info = info
        logger.info("NER backend: %s (%s)", info.backend, info.reason)
        return info

    try:
        from ..embedding.hardware_detector import detect_hardware
        caps = detect_hardware(refresh=refresh)
    except Exception as e:
        info = NerBackendInfo("seed", "none", True,
                              f"硬件检测失败（{e}）—— 跳过 LTP，仅用静态种子人名库")
        _cached_info = info
        return info

    if caps.has_cuda and caps.cuda_vram_mb >= _MIN_VRAM_MB:
        info = NerBackendInfo("ltp_gpu", "cuda", True,
                              f"检测到 GPU（{caps.cuda_device_name}，{caps.cuda_vram_mb}MB 显存）",
                              cuda_device_name=caps.cuda_device_name)
    elif caps.physical_gpu and not caps.has_cuda:
        # 有显卡但 torch 用不了 GPU（多为装了 CPU-only torch）→ 先 CPU，给出明确指引。
        why = "未启用 CUDA" if caps.torch_cuda_build else "是 CPU 版"
        info = NerBackendInfo(
            "ltp_cpu", "cpu", True,
            f"检测到 GPU（{caps.gpu_name}）但当前 torch {why}，无法用 GPU；先用 CPU 跑 LTP。"
            f"装 CUDA 版 torch（pip install torch --index-url "
            f"https://download.pytorch.org/whl/cu121）后即可用 GPU。",
            cuda_device_name=caps.gpu_name)
    elif caps.cpu_count >= _MIN_CPU_CORES and caps.ram_mb >= _MIN_RAM_MB:
        info = NerBackendInfo("ltp_cpu", "cpu", True,
                              f"无可用 GPU，用 CPU 跑 LTP（{caps.cpu_count} 核 / {caps.ram_mb}MB 内存）")
    else:
        info = NerBackendInfo(
            "seed", "none", True,
            f"CPU 算力不足（{caps.cpu_count} 核 / {caps.ram_mb}MB）—— 跳过 LTP，仅用静态种子人名库")
    _cached_info = info
    logger.info("NER backend: %s (%s)", info.backend, info.reason)
    return info


# ─────────── LTP pipeline 适配器（单例，懒加载）───────────


class LtpPipeline:
    """对 LTP 4.x 的薄封装：PER 抽取 + （供 MDD 用的）依存距离。对 LTP API 的版本
    差异做防御，任一步失败都抛回上层降级，不让异常逃逸到分析主流程。"""

    def __init__(self, device: str) -> None:
        self.device = device
        self._ltp = None
        self._failed = False          # 加载失败后置位 → 不再每本书重试刷屏

    def ensure_ready(self) -> bool:
        return self._ensure()

    def _ensure(self) -> bool:
        if self._ltp is not None:
            return True
        if self._failed:              # 上次已失败 → 直接返回，不重试、不再打日志
            return False
        try:
            _ensure_hf_compat()       # 兼容新版 huggingface_hub（use_auth_token 等）
            from ltp import LTP
            self._ltp = LTP()
            try:
                import torch
                if self.device == "cuda" and torch.cuda.is_available():
                    self._ltp.to("cuda")
            except Exception:
                pass
            logger.info("LTP loaded on %s", self.device)
            return True
        except Exception as e:  # pragma: no cover - LTP 未安装/模型不可用
            self._failed = True       # 只记一次，之后静默降级
            logger.warning("LTP load failed (%s); falling back to jieba nr "
                           "for the rest of this session", e)
            return False

    def extract_per(self, texts: list[str]) -> list[str]:
        if not self._ensure():
            return []
        names: list[str] = []
        try:
            out = self._ltp.pipeline(texts, tasks=["cws", "ner"])
            ner_lists = getattr(out, "ner", out.get("ner") if isinstance(out, dict) else None)
            for sent_ents in (ner_lists or []):
                for ent in sent_ents:
                    # 兼容 (tag, word, start, end) 或 (word, tag) 等形态。
                    tag, word = _unpack_entity(ent)
                    if tag in _LTP_PER_TAGS and word and _CJK_ONLY.match(word):
                        names.append(word)
        except Exception as e:  # pragma: no cover
            logger.debug("LTP ner failed: %s", e)
        return names

    def mean_dependency_distance(self, sentences: list[str]) -> float | None:
        if not self._ensure():
            return None
        try:
            out = self._ltp.pipeline(sentences, tasks=["cws", "dep"])
            deps = getattr(out, "dep", out.get("dep") if isinstance(out, dict) else None)
            total_dist = 0
            total_arcs = 0
            for sent_dep in (deps or []):
                heads = _dep_heads(sent_dep)
                for i, head in enumerate(heads):
                    if head is None or head == 0:
                        continue   # 跳过根
                    total_dist += abs((i + 1) - head)
                    total_arcs += 1
            if total_arcs == 0:
                return None
            return total_dist / total_arcs
        except Exception as e:  # pragma: no cover
            logger.debug("LTP dep failed: %s", e)
            return None


def _unpack_entity(ent) -> tuple[str, str]:
    """从 LTP 的实体元组/字典里取 (tag, word)，兼容多种形态。"""
    if isinstance(ent, dict):
        return str(ent.get("type") or ent.get("tag") or ""), str(ent.get("text") or ent.get("word") or "")
    if isinstance(ent, (list, tuple)):
        if len(ent) >= 2:
            a, b = ent[0], ent[1]
            # (tag, word) 还是 (word, tag)？标签通常是短大写码。
            if isinstance(a, str) and a in _LTP_PER_TAGS:
                return a, str(b)
            if isinstance(b, str) and b in _LTP_PER_TAGS:
                return b, str(a)
            return str(a), str(b)
    return "", ""


def _dep_heads(sent_dep) -> list[int | None]:
    """从 LTP dep 结果取每 token 的 head（1-indexed，0=root）。"""
    if isinstance(sent_dep, dict):
        head = sent_dep.get("head") or sent_dep.get("arcs")
        if head:
            return [int(h) if h is not None else None for h in head]
    if isinstance(sent_dep, (list, tuple)):
        heads: list[int | None] = []
        for arc in sent_dep:
            if isinstance(arc, (list, tuple)) and arc:
                heads.append(int(arc[0]))
            elif isinstance(arc, dict):
                heads.append(int(arc.get("head", 0)))
            else:
                heads.append(None)
        return heads
    return []


_pipeline_singleton: LtpPipeline | None = None


def get_ltp_pipeline() -> LtpPipeline | None:
    """返回当前生效的 LTP 句柄（供 MDD 等复用）；非 LTP 后端时返回 None。"""
    global _pipeline_singleton
    info = detect_ner_backend()
    if not info.uses_ltp:
        return None
    if _pipeline_singleton is None:
        _pipeline_singleton = LtpPipeline(info.device)
    return _pipeline_singleton


# ─────────── public ───────────


def extract_per_names(texts: list[str]) -> list[str]:
    """从一批文本抽 PER 人名实体（去重前的原始列表，含重复以便上层数 DF）。

    **只用 LTP 抽名**。LTP 不可用时返回空 —— 不再用 jieba 'nr'（对人名错误率极高）；
    人名库的静态种子库已作为 fallback 负责高频词剔名。
    """
    info = detect_ner_backend()
    if info.uses_ltp:
        pipe = get_ltp_pipeline()
        if pipe is not None and pipe.ensure_ready():
            return pipe.extract_per(texts)
        # LTP 运行时加载失败 → 本会话降级为「仅静态种子库」（避免每本书重试刷屏）。
        _degrade_to_seed("LTP 运行时加载失败（依赖不兼容/模型缺失）—— 仅用静态种子人名库")
    return []


def backend_status() -> dict:
    """供 UI / API 展示当前 NER 后端、原因与 GPU 诊断。"""
    d = detect_ner_backend().to_dict()
    try:
        from ..embedding.hardware_detector import detect_hardware
        caps = detect_hardware()
        d["gpu"] = {
            "physical_gpu": caps.physical_gpu,
            "gpu_name": caps.gpu_name,
            "gpu_vram_mb": caps.gpu_vram_mb,
            "torch_cuda_build": caps.torch_cuda_build,
            "torch_cuda_available": caps.has_cuda,
        }
    except Exception:
        d["gpu"] = {}
    return d
