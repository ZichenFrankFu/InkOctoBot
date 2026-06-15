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
# jieba 人名词性。
_JIEBA_NAME_FLAGS = {"nr", "nrt", "nrfg", "nrj"}
_CJK_ONLY = re.compile(r"^[一-鿿]{2,8}$")

# 跑 LTP 的最低 CPU 门槛（低于此判为「算力不足」→ 跳过 LTP）。
_MIN_CPU_CORES = 4
_MIN_RAM_MB = 4096
_MIN_VRAM_MB = 1500


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


def _ltp_importable() -> bool:
    try:
        import ltp  # noqa: F401
        import torch  # noqa: F401
        return True
    except Exception:
        return False


def detect_ner_backend(*, refresh: bool = False) -> NerBackendInfo:
    """按硬件挑选 NER 后端并缓存。``refresh=True`` 重测（GPU 驱动变化/测试）。"""
    global _cached_info
    if _cached_info is not None and not refresh:
        return _cached_info

    if not _ltp_importable():
        info = NerBackendInfo(
            backend="jieba", device="none", ltp_available=False,
            reason="LTP/torch 未安装 —— 用 jieba nr + 种子人名库兜底",
        )
        _cached_info = info
        logger.info("NER backend: %s (%s)", info.backend, info.reason)
        return info

    try:
        from ...embedding.hardware_detector import detect_hardware
        caps = detect_hardware(refresh=refresh)
    except Exception as e:
        info = NerBackendInfo("jieba", "none", True,
                              f"硬件检测失败（{e}）—— 跳过 LTP，jieba 兜底")
        _cached_info = info
        return info

    if caps.has_cuda and caps.cuda_vram_mb >= _MIN_VRAM_MB:
        info = NerBackendInfo("ltp_gpu", "cuda", True,
                              f"检测到 GPU（{caps.cuda_device_name}，{caps.cuda_vram_mb}MB 显存）",
                              cuda_device_name=caps.cuda_device_name)
    elif caps.cpu_count >= _MIN_CPU_CORES and caps.ram_mb >= _MIN_RAM_MB:
        info = NerBackendInfo("ltp_cpu", "cpu", True,
                              f"无可用 GPU，用 CPU 跑 LTP（{caps.cpu_count} 核 / {caps.ram_mb}MB 内存）")
    else:
        info = NerBackendInfo(
            "jieba", "none", True,
            f"CPU 算力不足（{caps.cpu_count} 核 / {caps.ram_mb}MB）—— 跳过 LTP，jieba 兜底")
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

    def _ensure(self) -> bool:
        if self._ltp is not None:
            return True
        try:
            from ltp import LTP
            self._ltp = LTP()
            try:
                import torch
                if self.device == "cuda" and torch.cuda.is_available():
                    self._ltp.to("cuda")
            except Exception:
                pass
            return True
        except Exception as e:  # pragma: no cover - LTP 未安装
            logger.warning("LTP load failed (%s); caller should fall back", e)
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


# ─────────── jieba 'nr' 兜底 ───────────


def _jieba_per(texts: list[str]) -> list[str]:
    try:
        import jieba.posseg as pseg
    except Exception:
        return []
    names: list[str] = []
    for t in texts:
        for tok in pseg.cut(t):
            if tok.flag in _JIEBA_NAME_FLAGS and _CJK_ONLY.match(tok.word):
                names.append(tok.word)
    return names


# ─────────── public ───────────


def extract_per_names(texts: list[str]) -> list[str]:
    """从一批文本抽 PER 人名实体（去重前的原始列表，含重复以便上层数 DF）。

    选定后端：LTP（GPU/CPU）→ 失败则就地降级 jieba。jieba 后端：直接 jieba 'nr'。
    """
    info = detect_ner_backend()
    if info.uses_ltp:
        pipe = get_ltp_pipeline()
        if pipe is not None:
            names = pipe.extract_per(texts)
            if names or pipe._ltp is not None:
                return names
        # LTP 句柄构建/推理失败 → 运行时降级。
        logger.info("LTP runtime unavailable; degrading to jieba nr for this run")
    return _jieba_per(texts)


def backend_status() -> dict:
    """供 UI / API 展示当前 NER 后端与原因。"""
    return detect_ner_backend().to_dict()
