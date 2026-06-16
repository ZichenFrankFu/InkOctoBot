"""人名实体识别后端 —— 只用 LTP（语言科技平台）。

spec 语言学文本特征 §4 的降级路径（**只在 LTP 内部分级，不退回 jieba**）：

    有可用 GPU            → LTP on CUDA
    否则 CPU 算力足够      → LTP on CPU
    LTP / torch 装不上     → 后端记为 ``seed``：**不从正文抽名**（返回空），
                            仅保留打包的静态种子人名库供「高频词剔名」兜底。

明确不用 jieba 做人名识别：jieba 的 ``nr`` 名实体标注对网文人名误报率极高，按用户
要求彻底移除。LTP 抽不出来时宁可返回 0，也不拿 jieba 的结果冒充人名。

LTP 装不上 / 模型加载失败时，**把真实报错暴露出来**（不再 ``except: pass`` 吞掉），
经 ``backend_status`` / ``/api/analysis/ner-test`` 回传前端，便于精确定位、修复 LTP。

本模块只负责「从文本抽 PER 人名实体」与「（给句法分析复用的）LTP 句柄」；人名库
的去重、入库、DF 统计在 name_library.py。
"""
from __future__ import annotations

import logging
import re
import traceback
from dataclasses import dataclass

logger = logging.getLogger("inkoctobot.market_extractor.ner_backend")

# LTP NER 里人名实体的标签（跨版本兼容：4.x 用 'Nh'）。
_LTP_PER_TAGS = {"Nh", "PER", "PERSON", "person", "name"}
_CJK_ONLY = re.compile(r"^[一-鿿]{2,8}$")

# 切句：LTP NER 是 transformer（~512 token 上限），整章直接送会被截断 → 召回极低。
# 必须先切成短句再做 NER。
_SENT_SPLIT_NER = re.compile(r"[。！？!?…\n\r；;]+")
_ner_diag_logged = False

# LTP 不可用时捕获的真实报错（暴露给前端诊断，便于定位为何 LTP 加载不了）。
_ltp_import_error = ""     # import ltp / torch 失败的原因
_ltp_load_error = ""       # LTP() 模型构造/下载失败的原因（含各构造方式的报错）
_ltp_ner_error = ""        # NER 推理期的真实报错（如 batch_encode_plus / .encodings）
_ner_err_logged = False


def _split_sentences(texts: list[str], max_len: int = 180) -> list[str]:
    out: list[str] = []
    for t in texts:
        for s in _SENT_SPLIT_NER.split(t or ""):
            s = s.strip()
            if len(s) < 2:
                continue
            if len(s) <= max_len:
                out.append(s)
            else:                       # 超长句再按长度硬切，避免越过模型上限
                for i in range(0, len(s), max_len):
                    chunk = s[i:i + max_len].strip()
                    if len(chunk) >= 2:
                        out.append(chunk)
    return out


def _context_window(sents: list[str], gi: int, target: int = 130) -> str:
    """围绕第 gi 句拼一段更长的例句（前后各扩若干句，约 target 字），让人名库的例句
    更有上下文。人名所在句一定包含在内（前端按全名高亮仍命中）。"""
    if not (0 <= gi < len(sents)):
        return ""
    parts = [sents[gi]]
    total = len(sents[gi])
    left, right = gi - 1, gi + 1
    while total < target:
        grew = False
        if right < len(sents) and total + len(sents[right]) <= target + 40:
            parts.append(sents[right]); total += len(sents[right]); right += 1; grew = True
        if total < target and left >= 0 and total + len(sents[left]) <= target + 40:
            parts.insert(0, sents[left]); total += len(sents[left]); left -= 1; grew = True
        if not grew:
            break
    return "。".join(parts) + "。"


# 跑 LTP 的最低 CPU 门槛（低于此判为「算力不足」→ 跳过 LTP）。
_MIN_CPU_CORES = 4
_MIN_RAM_MB = 4096
_MIN_VRAM_MB = 1000     # LTP 模型很小，低显存 GPU 也能跑


@dataclass(frozen=True)
class NerBackendInfo:
    backend: str        # 'ltp_gpu' | 'ltp_cpu' | 'seed'
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
            "ltp_import_error": _ltp_import_error,
            "ltp_load_error": _ltp_load_error,
            "ltp_ner_error": _ltp_ner_error,
        }


_cached_info: NerBackendInfo | None = None


# 新版 huggingface_hub 删除了 ``use_auth_token`` 等旧参数，而 LTP 4.x 仍按旧签名
# 调用 → ``hf_hub_download() got an unexpected keyword argument 'use_auth_token'``，
# 导致模型永远加载失败。这里在 import ltp 之前打一层兼容补丁：先按原样调用，仅当抛出
# "未知关键字参数"的 TypeError 时才把 use_auth_token 翻译成 token、或去掉被删的参数后
# 重试 —— 对新旧 huggingface_hub 都安全。
_hf_patched = False
_tok_patched = False
_LTP_MARK = "_ltp_hf_compat"      # 标记"已是本补丁包装"，避免重复包装

# LTP 4.2.x 兼容的 transformers 版本（新版 transformers 5.x 移除了公开的
# batch_encode_plus、改了 fast tokenizer 回退行为 → LTP NER 崩）。给前端做修复指引。
_LTP_FIX_HINT = ("pip install -U 'ltp-extension>=0.1.9' 'tokenizers' "
                 "'transformers==4.30.2'，然后重启后端")


def _ensure_tokenizer_compat() -> None:
    """新版 transformers（5.x）移除了公开的 ``batch_encode_plus``，而 LTP 4.2.x 仍在
    NER 里调用它 → ``... has no attribute batch_encode_plus``。这里给 tokenizer 基类补一个
    兼容方法：委托给 ``__call__`` —— 在 **fast tokenizer** 下同样产出 ``.encodings``
    （LTP 抽 NER 必需），从而无需用户改环境即可修复。幂等、对旧版无副作用。"""
    global _tok_patched
    if _tok_patched:
        return
    try:
        from transformers.tokenization_utils_base import (
            PreTrainedTokenizerBase as _Base,
        )
    except Exception:
        try:
            from transformers import PreTrainedTokenizerBase as _Base  # type: ignore
        except Exception:
            return
    if not hasattr(_Base, "batch_encode_plus"):
        def batch_encode_plus(self, batch_text_or_text_pairs, **kwargs):
            return self(batch_text_or_text_pairs, **kwargs)
        try:
            _Base.batch_encode_plus = batch_encode_plus
            logger.info("patched transformers tokenizer.batch_encode_plus for LTP compat")
        except Exception:
            pass
    if not hasattr(_Base, "encode_plus"):
        def encode_plus(self, text, text_pair=None, **kwargs):
            return self(text, text_pair, **kwargs)
        try:
            _Base.encode_plus = encode_plus
        except Exception:
            pass
    _tok_patched = True


def _ensure_fast_tokenizer(ltp_obj) -> str:
    """LTP NER 需要 **fast tokenizer**（读取 ``tokenized.encodings``）。若 LTP 回退到了
    slow ``BertTokenizer``（多因 transformers 版本过新/缺 fast 后端），尝试重建 fast；
    仍失败则返回一条可操作的诊断（空字符串=正常，可继续）。"""
    tok = getattr(ltp_obj, "tokenizer", None)
    if tok is None or getattr(tok, "is_fast", False):
        return ""
    name = getattr(tok, "name_or_path", "") or ""
    try:
        from transformers import AutoTokenizer
        fast = AutoTokenizer.from_pretrained(name, use_fast=True)
        if getattr(fast, "is_fast", False):
            ltp_obj.tokenizer = fast
            logger.info("rebuilt fast tokenizer for LTP (%s)", name)
            return ""
    except Exception as e:
        logger.warning("fast tokenizer rebuild failed: %s", e)
    return ("LTP 回退到 slow tokenizer（NER 需要 fast tokenizer 的 .encodings）。"
            f"多为 transformers 版本过新或缺 fast 后端 —— 请对齐版本：{_LTP_FIX_HINT}。")


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
    """清掉后端判定缓存 + LTP 句柄（改了补丁/装好 LTP 后想免重启重试时用）。"""
    global _cached_info, _pipeline_singleton, _hf_patched, _tok_patched
    global _ltp_import_error, _ltp_load_error, _ltp_ner_error, _ner_err_logged
    _cached_info = None
    _pipeline_singleton = None
    _hf_patched = False
    _tok_patched = False
    _ltp_import_error = ""
    _ltp_load_error = ""
    _ltp_ner_error = ""
    _ner_err_logged = False


def _ltp_importable() -> bool:
    """能否 import ltp + torch。失败时**把真实报错记进 _ltp_import_error**（不再吞掉）。"""
    global _ltp_import_error
    try:
        _ensure_hf_compat()        # 必须在 import ltp 之前打补丁
        import ltp  # noqa: F401
        import torch  # noqa: F401
        _ensure_tokenizer_compat()  # 补 batch_encode_plus（新版 transformers 移除了）
        _ltp_import_error = ""
        return True
    except Exception as e:
        _ltp_import_error = f"{type(e).__name__}: {e}"
        logger.warning("LTP/torch import failed: %s", _ltp_import_error)
        return False


def _degrade_to_seed(reason: str) -> None:
    """LTP 运行时加载失败时，后端缓存改成 seed（不从正文抽名，仅保留种子库剔名）。"""
    global _cached_info
    _cached_info = NerBackendInfo("seed", "none", False, reason)
    logger.warning("NER degraded to seed-only (no name extraction): %s", reason)


def detect_ner_backend(*, refresh: bool = False) -> NerBackendInfo:
    """按硬件挑选 NER 后端并缓存。``refresh=True`` 重测（GPU 驱动变化/装好 LTP/测试）。
    只用 LTP；LTP 装不上时后端记为 ``seed``（不抽名，仅静态种子库供剔名），并把真实
    import 报错带在 reason 里，便于前端诊断、定位为何 LTP 加载不了。"""
    global _cached_info
    if _cached_info is not None and not refresh:
        return _cached_info

    if not _ltp_importable():
        err = _ltp_import_error or "未安装"
        info = NerBackendInfo(
            backend="seed", device="none", ltp_available=False,
            reason=f"LTP/torch 不可用（{err}）—— 暂不抽名，请安装/修复 LTP 后刷新",
        )
        _cached_info = info
        logger.info("NER backend: %s (%s)", info.backend, info.reason)
        return info

    try:
        from ..embedding.hardware_detector import detect_hardware
        caps = detect_hardware(refresh=refresh)
    except Exception as e:
        info = NerBackendInfo("ltp_cpu", "cpu", True,
                              f"硬件检测失败（{e}）—— 先用 CPU 跑 LTP")
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
    else:
        # 即使 CPU 算力偏低也照常跑 LTP（用户明确要求只用 LTP，不退 jieba）。
        info = NerBackendInfo("ltp_cpu", "cpu", True,
                              f"无可用 GPU，用 CPU 跑 LTP（{caps.cpu_count} 核 / {caps.ram_mb}MB 内存）")
    _cached_info = info
    logger.info("NER backend: %s (%s)", info.backend, info.reason)
    return info


# ─────────── LTP pipeline 适配器（单例，懒加载）───────────


def _load_ltp():
    """尝试多种 LTP 构造方式加载模型，返回 (ltp_obj_or_None, error_str)。

    不同 LTP 版本/网络环境下可用的构造入口不同；逐个尝试并**把每种方式的真实报错
    收集起来**，全失败时拼成一条详细错误回传前端（这是定位"LTP 加载不了"的关键）。
    """
    from ltp import LTP
    attempts: list = [
        ("LTP()", lambda: LTP()),
        ("LTP.from_pretrained('LTP/small')",
         lambda: LTP.from_pretrained("LTP/small")),
        ("LTP('LTP/small')", lambda: LTP("LTP/small")),
        ("LTP('LTP/legacy')", lambda: LTP("LTP/legacy")),  # 轻量感知机模型，CPU 友好
    ]
    errors: list[str] = []
    for desc, factory in attempts:
        try:
            obj = factory()
            logger.info("LTP loaded via %s", desc)
            return obj, ""
        except Exception as e:
            msg = f"{desc} → {type(e).__name__}: {e}"
            errors.append(msg)
            logger.warning("LTP ctor failed: %s", msg)
    return None, " || ".join(errors)


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
        global _ltp_load_error
        if self._ltp is not None:
            return True
        if self._failed:              # 上次已失败 → 直接返回，不重试、不再打日志
            return False
        try:
            _ensure_hf_compat()       # 兼容新版 huggingface_hub（use_auth_token 等）
            _ensure_tokenizer_compat()  # 兼容新版 transformers（batch_encode_plus）
        except Exception as e:
            logger.warning("compat patch failed: %s", e)
        ltp_obj, err = _load_ltp()
        if ltp_obj is None:
            self._failed = True
            _ltp_load_error = err
            # 完整 traceback 进日志，详细 error 进诊断接口 —— 让用户能看清为何加载不了。
            logger.error("LTP model load failed (all ctors):\n%s", err)
            return False
        # NER 需要 fast tokenizer（.encodings）；slow 兜底会在抽名时崩 → 提前检测+给指引。
        slow_msg = _ensure_fast_tokenizer(ltp_obj)
        if slow_msg:
            self._failed = True
            _ltp_load_error = slow_msg
            logger.error(slow_msg)
            return False
        self._ltp = ltp_obj
        _ltp_load_error = ""
        try:
            import torch
            if self.device == "cuda" and torch.cuda.is_available():
                self._ltp.to("cuda")
        except Exception as e:
            logger.warning("LTP .to(cuda) failed, staying on cpu: %s", e)
        logger.info("LTP ready on %s", self.device)
        return True

    def _run_ner(self, batch: list[str]):
        """跑一批句子的 NER，返回 (ner_per_sent, seg_per_sent)。兼容 LTP 4.2+ 的
        ``pipeline`` 与 4.1.x 的 ``seg``/``ner`` 两套 API。两套都失败时把真实报错记进
        ``_ltp_ner_error``（如 batch_encode_plus / .encodings），让"抽不到名"不再无声。"""
        global _ltp_ner_error, _ner_err_logged
        errs: list[str] = []
        # —— LTP 4.2+：pipeline(sentences, tasks=[...]) → output.ner / output.cws
        try:
            res = self._ltp.pipeline(batch, tasks=["cws", "ner"])
            ner = getattr(res, "ner", None)
            if ner is None and isinstance(res, dict):
                ner = res.get("ner")
            seg = getattr(res, "cws", None)
            if seg is None and isinstance(res, dict):
                seg = res.get("cws")
            if ner is not None:
                _ltp_ner_error = ""
                return ner, seg
        except Exception as e:  # pragma: no cover
            errs.append(f"pipeline: {type(e).__name__}: {e}")
            logger.debug("LTP pipeline API failed (%s); trying seg/ner", e)
        # —— LTP 4.1.x：seg(sentences) → (seg, hidden)；ner(seg, hidden)
        try:
            seg, hidden = self._ltp.seg(batch)
            ner = self._ltp.ner(seg, hidden)
            _ltp_ner_error = ""
            return ner, seg
        except Exception as e:  # pragma: no cover
            errs.append(f"seg/ner: {type(e).__name__}: {e}")
            logger.debug("LTP seg/ner API failed: %s", e)
        if errs:
            _ltp_ner_error = " || ".join(errs)
            if "batch_encode_plus" in _ltp_ner_error or "encodings" in _ltp_ner_error:
                _ltp_ner_error += f"（多为 transformers 版本不兼容 —— 修复：{_LTP_FIX_HINT}）"
            if not _ner_err_logged:
                _ner_err_logged = True
                logger.error("LTP NER runtime failed: %s", _ltp_ner_error)
        return None, None

    def extract_per_context(self, texts: list[str]) -> list[tuple[str, str]]:
        """返回 (人名, 所在句) 列表。**先切句**再送 LTP NER —— 避开 transformer 的长度
        上限，否则整章送进去只识别开头一小段、召回极低（= 抽不到名）。"""
        global _ner_diag_logged
        if not self._ensure():
            return []
        sents = _split_sentences(texts)
        if not sents:
            return []
        pairs: list[tuple[str, str]] = []
        for i in range(0, len(sents), 64):
            batch = sents[i:i + 64]
            ner_lists, seg_lists = self._run_ner(batch)
            if not _ner_diag_logged:            # 一次性诊断：打印真实 NER/CWS 输出格式
                _ner_diag_logged = True
                logger.info("LTP NER raw sample: ner=%r seg=%r",
                            (ner_lists[:1] if ner_lists else ner_lists),
                            (seg_lists[:1] if seg_lists else None))
            if not ner_lists:
                continue
            for k, sent in enumerate(batch):
                ents = ner_lists[k] if k < len(ner_lists) else []
                seg = seg_lists[k] if (seg_lists and k < len(seg_lists)) else None
                for ent in (ents or []):
                    tag = _entity_tag(ent)
                    word = _entity_word(ent, seg)
                    if tag in _LTP_PER_TAGS and word and _CJK_ONLY.match(word):
                        pairs.append((word, _context_window(sents, i + k)))   # 更长例句
        return pairs

    def debug_ner(self, text: str) -> dict:
        """诊断：返回一句话的原始 NER/CWS 输出 + 解析出的人名，供排查格式。"""
        if not self._ensure():
            return {"loaded": False, "load_error": _ltp_load_error}
        sents = _split_sentences([text]) or [text]
        ner_lists, seg_lists = self._run_ner(sents[:8])
        parsed = []
        for k, sent in enumerate(sents[:8]):
            ents = ner_lists[k] if (ner_lists and k < len(ner_lists)) else []
            seg = seg_lists[k] if (seg_lists and k < len(seg_lists)) else None
            for ent in (ents or []):
                parsed.append({"tag": _entity_tag(ent), "word": _entity_word(ent, seg)})
        return {"loaded": True, "ner_raw": repr(ner_lists)[:1500],
                "seg_raw": repr(seg_lists)[:800], "parsed": parsed}

    def extract_per(self, texts: list[str]) -> list[str]:
        return [n for n, _ in self.extract_per_context(texts)]

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


def _entity_tag(ent) -> str:
    """从 LTP 实体取标签。"""
    if isinstance(ent, dict):
        return str(ent.get("type") or ent.get("tag") or ent.get("label") or "")
    if isinstance(ent, (list, tuple)):
        for x in ent:
            if isinstance(x, str) and x in _LTP_PER_TAGS:
                return x
        # 没匹配到已知标签 → 第一个字符串当标签
        for x in ent:
            if isinstance(x, str):
                return x
    return ""


def _entity_word(ent, seg=None) -> str:
    """从 LTP 实体取人名文本。兼容：
      - (tag, word, start, end) / (word, tag) → 直接取词串；
      - (tag, start, end) 偏移式 → 用 cws 分词结果 seg 把 [start:end] 拼回词；
      - dict 形态。"""
    if isinstance(ent, dict):
        w = ent.get("text") or ent.get("word") or ent.get("entity")
        if w:
            return str(w)
        s, e = ent.get("start"), ent.get("end")
        if seg is not None and isinstance(s, int) and isinstance(e, int):
            try:
                return "".join(seg[s:e + 1])
            except Exception:
                return ""
        return ""
    if isinstance(ent, (list, tuple)):
        # 优先：非标签的字符串就是词
        strs = [x for x in ent if isinstance(x, str)]
        non_tag = [x for x in strs if x not in _LTP_PER_TAGS]
        if non_tag:
            return non_tag[0]
        # 否则是 (tag, start, end) 偏移式 → 用 seg 拼回
        ints = [x for x in ent if isinstance(x, int)]
        if seg is not None and len(ints) >= 2:
            try:
                return "".join(seg[ints[0]:ints[1] + 1])
            except Exception:
                return ""
    return ""


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


_last_method = "seed"


def last_method() -> str:
    """上一次 extract 实际用的方法标签：ltp_ner / seed（seed = 无 LTP，未抽名）。"""
    return _last_method


def last_ltp_error() -> str:
    """暴露最近一次 LTP 不可用的真实报错（加载失败 > NER 运行期失败 > import 失败）。"""
    return _ltp_load_error or _ltp_ner_error or _ltp_import_error


def extract_per_names_with_context(texts: list[str]) -> list[tuple[str, str]]:
    """抽 PER 人名 + 所在句 (name, sentence)，**只用 LTP 从真实章节正文**抽取。
    LTP 不可用 / 加载失败时返回**空列表**（不退 jieba），并把后端降级为 seed、记录
    真实报错供前端诊断 —— 宁可 0，也不拿 jieba 的误报冒充人名。"""
    global _last_method
    info = detect_ner_backend()
    if info.uses_ltp:
        pipe = get_ltp_pipeline()
        if pipe is not None and pipe.ensure_ready():
            _last_method = "ltp_ner"
            return pipe.extract_per_context(texts)
        _degrade_to_seed(
            f"LTP 运行时加载失败（{_ltp_load_error or '未知错误'}）—— 暂不抽名")
    _last_method = "seed"
    return []


def extract_per_names(texts: list[str]) -> list[str]:
    """从一批文本抽 PER 人名实体（去重前的原始列表，含重复以便上层数 DF）。"""
    return [n for n, _ in extract_per_names_with_context(texts)]


def debug_ner_text(text: str) -> dict:
    """诊断 LTP NER：返回 LTP 版本、torch 版本、后端、**真实加载报错**、原始输出与
    解析出的人名。用于排查「LTP 加载不了 / 处理了很多书却抽不到名」的具体原因。"""
    out: dict = {}
    reset_backend_cache()          # 清旧缓存/句柄 → 真正重试一次 LTP 加载（装好后点诊断即生效）
    try:
        import ltp
        out["ltp_version"] = getattr(ltp, "__version__", "?")
    except Exception as e:
        out["ltp_version"] = f"not importable: {type(e).__name__}: {e}"
    try:
        import torch
        out["torch_version"] = getattr(torch, "__version__", "?")
        out["torch_cuda_available"] = bool(torch.cuda.is_available())
        out["torch_cuda_version"] = getattr(getattr(torch, "version", None), "cuda", None)
    except Exception as e:
        out["torch_version"] = f"not importable: {type(e).__name__}: {e}"
    # 强制重新探测 + 尝试真正加载 LTP，把 import/load 报错刷新进来。
    out["backend"] = detect_ner_backend(refresh=True).to_dict()
    try:
        pairs = extract_per_names_with_context([text])
        out["names"] = [n for n, _ in pairs]
        out["count"] = len(pairs)
    except Exception as e:
        out["extract_error"] = f"{type(e).__name__}: {e}"
        out["extract_traceback"] = traceback.format_exc()[-1500:]
    # 重新读后端（抽取过程可能触发降级 → 把最终状态与报错带回）。
    out["backend_after"] = detect_ner_backend().to_dict()
    out["ltp_import_error"] = _ltp_import_error
    out["ltp_load_error"] = _ltp_load_error
    out["ltp_ner_error"] = _ltp_ner_error
    pipe = get_ltp_pipeline()
    if pipe is not None:
        try:
            out["raw"] = pipe.debug_ner(text)
        except Exception as e:
            out["raw_error"] = f"{type(e).__name__}: {e}"
    return out


def backend_status() -> dict:
    """供 UI / API 展示当前 NER 后端、原因、真实 LTP 报错与 GPU 诊断。"""
    d = detect_ner_backend().to_dict()
    d["ltp_import_error"] = _ltp_import_error
    d["ltp_load_error"] = _ltp_load_error
    d["ltp_ner_error"] = _ltp_ner_error
    try:
        from ..embedding.hardware_detector import detect_hardware
        caps = detect_hardware()
        d["gpu"] = {
            "physical_gpu": caps.physical_gpu,
            "gpu_name": caps.gpu_name,
            "gpu_vram_mb": caps.gpu_vram_mb,
            "torch_cuda_build": caps.torch_cuda_build,
            "torch_cuda_available": caps.has_cuda,
            "torch_version": caps.torch_version,
            "torch_cuda_version": caps.torch_cuda_version,
        }
    except Exception:
        d["gpu"] = {}
    return d
