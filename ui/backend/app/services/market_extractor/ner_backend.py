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
from functools import lru_cache

logger = logging.getLogger("inkoctobot.market_extractor.ner_backend")

# LTP NER 里人名实体的标签（跨版本兼容：4.x 用 'Nh'）。
_LTP_PER_TAGS = {"Nh", "PER", "PERSON", "person", "name"}
_CJK_ONLY = re.compile(r"^[一-鿿]{2,8}$")

# 切句：LTP NER 是 transformer（~512 token 上限），整章直接送会被截断 → 召回极低。
# 必须先切成短句再做 NER。
_SENT_SPLIT_NER = re.compile(r"[。！？!?…\n\r；;]+")
_ner_diag_logged = False


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


# ─────────── jieba「姓氏门控」抽名（LTP 缺位/无果时的可靠兜底，扫真实正文）───────────
# 用 jieba **分词**（不是 jieba 的 nr 名实体标注）拿到词边界，再用「姓氏表 + 名字常用字
# + 词频」做门控：只有「以真实姓氏开头 + 其余是名字常用字（或 jieba 标为 nr）+ 非高频
# 常用词」的词才算人名。姓氏门控滤掉了 jieba nr 的误报（灵能/异能不以姓氏开头），精度高。

_CJK_RUN = re.compile(r"^[一-鿿]+$")
# 末字若是这些地名/机构后缀，几乎不会是人名 → 排除（不含 山/林/江/河 等可入名的字）。
_PLACE_ORG_TAIL = set("寺宫殿庙观城省市县国帮派教会厅局部区街道镇乡")
_jieba_userdict_fed = False


@lru_cache(maxsize=1)
def _name_resources():
    from . import wordlists as wl
    sur = wl.load_surnames()
    single = frozenset(s for s in sur if len(s) == 1)
    compound = tuple(sorted((s for s in sur if len(s) >= 2), key=len, reverse=True))
    return single, compound, wl.load_name_chars(), wl.load_common_words()


@lru_cache(maxsize=1)
def _jieba_freq() -> dict:
    try:
        import jieba
        jieba.initialize()
        return jieba.dt.FREQ
    except Exception:
        return {}


def _feed_jieba_userdict() -> None:
    """把复姓 + 种子全名喂进 jieba 词典并标 nr，让它们整体成词、不被切碎。"""
    global _jieba_userdict_fed
    if _jieba_userdict_fed:
        return
    try:
        import jieba
        jieba.initialize()
        _, compound, _, _ = _name_resources()
        for cs in compound:
            jieba.add_word(cs, tag="nr")
        try:
            from . import name_library
            for fn in name_library._read_seed_names():
                if len(fn) >= 2:
                    jieba.add_word(fn, tag="nr")
        except Exception:
            pass
        _jieba_userdict_fed = True
    except Exception:
        pass


def _surname_prefix(w: str, single: frozenset, compound: tuple) -> str:
    for cs in compound:                      # 复姓优先（上官/欧阳/诸葛…）
        if w.startswith(cs) and len(w) > len(cs):
            return cs
    return w[0] if (w and w[0] in single) else ""


def _jieba_surname_extract_context(texts: list[str]) -> list[tuple[str, str]]:
    """jieba 分词 + 姓氏门控抽人名（含所在句）。无 LTP 时的可靠兜底。

    精度优先：要求 **jieba 标 nr（人名）且以真实姓氏开头**，并排除常用词/地名后缀。
    姓氏门控滤掉 nr 误报（灵能/异能非姓氏开头），nr 要求滤掉 龙王/江山 这类姓+常用字。
    """
    try:
        import jieba.posseg as pseg
    except Exception:
        return []
    _feed_jieba_userdict()
    single, compound, name_chars, common = _name_resources()
    freq = _jieba_freq()
    pairs: list[tuple[str, str]] = []
    for sent in _split_sentences(texts, max_len=200):
        seen: set[str] = set()
        for tok in pseg.cut(sent):
            w, flag = tok.word, tok.flag
            if not (2 <= len(w) <= 4) or not _CJK_RUN.match(w):
                continue
            if w in common or w in seen or freq.get(w, 0) >= 300:
                continue
            if not flag.startswith("nr"):           # 必须是 jieba 判定的人名
                continue
            sur = _surname_prefix(w, single, compound)
            if not sur or not w[len(sur):]:          # 必须以真实姓氏开头、姓后有名
                continue
            if w[-1] in _PLACE_ORG_TAIL:             # 末字是地名/机构后缀 → 多半不是人名
                continue
            pairs.append((w, sent))
            seen.add(w)
    return pairs

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


def _degrade_to_jieba(reason: str) -> None:
    """LTP 运行时加载失败时，后端缓存改成 jieba（姓氏门控分词抽名，扫真实正文）。"""
    global _cached_info
    _cached_info = NerBackendInfo("jieba", "none", True, reason)
    logger.warning("NER degraded to jieba surname-gated mode: %s", reason)


def detect_ner_backend(*, refresh: bool = False) -> NerBackendInfo:
    """按硬件挑选 NER 后端并缓存。``refresh=True`` 重测（GPU 驱动变化/测试）。
    优先用 LTP（质量最高）；LTP 不可用/无果时退到「jieba 姓氏门控」从章节正文抽名
    （用 jieba 分词 + 姓氏表门控，非 jieba 的 nr 名实体标注，精度高）。"""
    global _cached_info
    if _cached_info is not None and not refresh:
        return _cached_info

    if not _ltp_importable():
        info = NerBackendInfo(
            backend="jieba", device="none", ltp_available=False,
            reason="LTP/torch 未安装 —— 用 jieba 姓氏门控从章节正文抽名（装 ltp+torch 可升级到 LTP）",
        )
        _cached_info = info
        logger.info("NER backend: %s (%s)", info.backend, info.reason)
        return info

    try:
        from ..embedding.hardware_detector import detect_hardware
        caps = detect_hardware(refresh=refresh)
    except Exception as e:
        info = NerBackendInfo("jieba", "none", True,
                              f"硬件检测失败（{e}）—— 用 jieba 姓氏门控从章节正文抽名")
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
            "jieba", "none", True,
            f"CPU 算力不足（{caps.cpu_count} 核 / {caps.ram_mb}MB）—— 用 jieba 姓氏门控从章节正文抽名")
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

    def _run_ner(self, batch: list[str]):
        """跑一批句子的 NER，返回 (ner_per_sent, seg_per_sent)。兼容 LTP 4.2+ 的
        ``pipeline`` 与 4.1.x 的 ``seg``/``ner`` 两套 API。"""
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
                return ner, seg
        except Exception as e:  # pragma: no cover
            logger.debug("LTP pipeline API failed (%s); trying seg/ner", e)
        # —— LTP 4.1.x：seg(sentences) → (seg, hidden)；ner(seg, hidden)
        try:
            seg, hidden = self._ltp.seg(batch)
            ner = self._ltp.ner(seg, hidden)
            return ner, seg
        except Exception as e:  # pragma: no cover
            logger.debug("LTP seg/ner API failed: %s", e)
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
                        pairs.append((word, sent))
        return pairs

    def debug_ner(self, text: str) -> dict:
        """诊断：返回一句话的原始 NER/CWS 输出 + 解析出的人名，供排查格式。"""
        if not self._ensure():
            return {"loaded": False}
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


_last_method = "jieba"


def last_method() -> str:
    """上一次 extract 实际用的方法标签：ltp_ner / jieba。"""
    return _last_method


def extract_per_names_with_context(texts: list[str]) -> list[tuple[str, str]]:
    """抽 PER 人名 + 所在句 (name, sentence)，**从真实章节正文**抽取。
    优先 LTP；LTP 不可用或没抽到（版本/格式问题）则用 jieba 姓氏门控兜底 —— 保证
    总能从正文里抽到人名，而不是 0。"""
    global _last_method
    info = detect_ner_backend()
    if info.uses_ltp:
        pipe = get_ltp_pipeline()
        if pipe is not None and pipe.ensure_ready():
            ltp_pairs = pipe.extract_per_context(texts)
            if ltp_pairs:
                _last_method = "ltp_ner"
                return ltp_pairs
            # LTP 加载成功却没抽到（多为版本/输出格式不匹配）→ jieba 姓氏门控兜底。
        else:
            _degrade_to_jieba("LTP 运行时加载失败 —— 改用 jieba 姓氏门控从正文抽名")
    _last_method = "jieba"
    return _jieba_surname_extract_context(texts)


def extract_per_names(texts: list[str]) -> list[str]:
    """从一批文本抽 PER 人名实体（去重前的原始列表，含重复以便上层数 DF）。"""
    return [n for n, _ in extract_per_names_with_context(texts)]


def debug_ner_text(text: str) -> dict:
    """诊断 LTP NER：返回 LTP 版本、后端、原始输出与解析出的人名。用于排查
    「LTP 处理了很多书却抽不到名」的具体原因（版本 API / 输出格式不匹配）。"""
    out: dict = {"backend": detect_ner_backend(refresh=True).to_dict()}
    try:
        import ltp
        out["ltp_version"] = getattr(ltp, "__version__", "?")
    except Exception as e:
        out["ltp_version"] = f"not importable: {e}"
    try:
        pairs = extract_per_names_with_context([text])
        out["names"] = [n for n, _ in pairs]
        out["count"] = len(pairs)
    except Exception as e:
        out["extract_error"] = str(e)
    pipe = get_ltp_pipeline()
    if pipe is not None:
        try:
            out["raw"] = pipe.debug_ner(text)
        except Exception as e:
            out["raw_error"] = str(e)
    return out


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
            "torch_version": caps.torch_version,
            "torch_cuda_version": caps.torch_cuda_version,
        }
    except Exception:
        d["gpu"] = {}
    return d
