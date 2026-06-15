"""情感分析 —— 大连理工 DUTIR 词典法标注 + 预留深度模型接口。

设计目标（spec 语言学文本特征 §3）：
- 用 DUTIR《情感词汇本体》对开篇语料做词典法情感标注，统计七大类情感占比，
  在 UI 中可视化。
- **预留**后续接入本地 BERT / RoBERTa / ERNIE 深度模型的接口：所有后端实现
  统一的 ``SentimentBackend`` 协议，``get_sentiment_backend()`` 工厂按可用性
  选择；深度模型缺失（torch 未装 / 无模型 / 算力不足）时自动降级到词典法。

DUTIR 七大类：乐 好 怒 哀 惧 恶 惊。完整本体的 21 小类（PA/PE/PD…）在加载时
归并到这 7 类。词典文件优先级：同目录 ``dutir.txt``（用户放入的完整本体）> 内置
``dutir_seed.txt``（种子兜底）。两种文件格式都支持（见 ``_parse_line``）。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable

logger = logging.getLogger("inkoctobot.market_extractor.sentiment")

_RES = Path(__file__).resolve().parent / "resources" / "sentiment"

# DUTIR 七大类（展示顺序固定）。
EMOTION_CLASSES: tuple[str, ...] = ("乐", "好", "怒", "哀", "惧", "恶", "惊")

# UI 友好标签（供前端兜底；前端可自带文案）。
EMOTION_LABELS: dict[str, str] = {
    "乐": "欢乐", "好": "喜爱", "怒": "愤怒", "哀": "悲伤",
    "惧": "恐惧", "恶": "厌恶", "惊": "惊讶",
}

# 完整 DUTIR 本体的 21 小类 → 7 大类。种子文件已直接用大类，这张表只在加载
# 完整本体（情感分类列是 PA/NB… 这种小类码）时生效。
_SUBCAT_TO_TOP: dict[str, str] = {
    "PA": "乐", "PE": "乐",
    "PD": "好", "PH": "好", "PG": "好", "PB": "好", "PK": "好",
    "NA": "怒",
    "NB": "哀", "NJ": "哀", "NH": "哀", "PF": "哀",
    "NI": "惧", "NC": "惧", "NG": "惧",
    "NE": "恶", "ND": "恶", "NN": "恶", "NK": "恶", "NL": "恶",
    "PC": "惊",
}

# 极性码：1 褒 / 2 贬 / 0 中 / 3 兼（DUTIR 用 0..3）。
_POLARITY_NAME = {0: "中性", 1: "正面", 2: "负面", 3: "兼有"}


@dataclass(frozen=True)
class LexEntry:
    emotion: str       # 七大类之一
    intensity: int     # 1..9
    polarity: int      # 0/1/2/3


def _parse_line(line: str) -> tuple[str, LexEntry] | None:
    """解析一行词典。兼容两种格式：

    1. 种子格式：``词  大类  强度  极性``（大类已是 乐/好/…）。
    2. 完整 DUTIR 本体（xlsx→TSV）：``词语 词性种类 词义数 词义序号 情感分类
       强度 极性 …`` —— 情感分类是 PA/NB… 小类码，取第 5/6/7 列。
    """
    s = line.strip()
    if not s or s.startswith("#") or s.startswith("词语"):
        return None
    cols = re.split(r"[\t,，]+", s)
    if len(cols) < 3:
        return None
    word = cols[0].strip()
    if not word:
        return None
    # 判定格式：若第 2 列是 7 大类之一 → 种子格式。
    if cols[1].strip() in EMOTION_CLASSES:
        emotion = cols[1].strip()
        intensity = _safe_int(cols[2], 5)
        polarity = _safe_int(cols[3], 0) if len(cols) > 3 else 0
    elif len(cols) >= 7:
        sub = cols[4].strip().upper()
        emotion = _SUBCAT_TO_TOP.get(sub)
        if not emotion:
            return None
        intensity = _safe_int(cols[5], 5)
        polarity = _safe_int(cols[6], 0)
    else:
        return None
    return word, LexEntry(emotion, intensity, polarity)


def _safe_int(v: str, default: int) -> int:
    try:
        return int(float(str(v).strip()))
    except Exception:
        return default


@lru_cache(maxsize=1)
def load_lexicon() -> dict[str, LexEntry]:
    """加载情感词典。完整本体 ``dutir.txt`` 优先，否则种子 ``dutir_seed.txt``。"""
    out: dict[str, LexEntry] = {}
    for fname in ("dutir.txt", "dutir_seed.txt"):
        p = _RES / fname
        if not p.exists():
            continue
        for line in p.read_text("utf-8", errors="ignore").splitlines():
            parsed = _parse_line(line)
            if parsed:
                out.setdefault(parsed[0], parsed[1])
        if out:
            logger.info("sentiment lexicon loaded from %s (%d words)", fname, len(out))
            break
    return out


def lexicon_size() -> int:
    return len(load_lexicon())


# ─────────── result ───────────


@dataclass
class SentimentResult:
    available: bool
    method: str                                   # 'dutir_lexicon' | 'bert' | ...
    emotion_counts: dict[str, int] = field(default_factory=dict)
    emotion_ratio: dict[str, float] = field(default_factory=dict)   # 归一化到 1
    polarity_ratio: dict[str, float] = field(default_factory=dict)  # 正/负/中
    dominant_emotion: str = ""
    matched: int = 0                              # 命中情感词次数
    total_tokens: int = 0
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "method": self.method,
            "emotion_counts": self.emotion_counts,
            "emotion_ratio": self.emotion_ratio,
            "polarity_ratio": self.polarity_ratio,
            "dominant_emotion": self.dominant_emotion,
            "matched": self.matched,
            "total_tokens": self.total_tokens,
            "reason": self.reason,
            "labels": EMOTION_LABELS,
        }


def _empty(method: str, reason: str) -> SentimentResult:
    return SentimentResult(
        available=False, method=method, reason=reason,
        emotion_ratio={e: 0.0 for e in EMOTION_CLASSES},
    )


# ─────────── backend interface (深度模型预留) ───────────


@runtime_checkable
class SentimentBackend(Protocol):
    """情感后端协议。词典法与未来的 BERT/RoBERTa/ERNIE 都实现它，
    供 ``get_sentiment_backend()`` 统一选择。"""

    name: str

    def is_available(self) -> bool: ...

    def analyze(self, tokens: list[str], *, text: str = "") -> SentimentResult: ...


class LexiconSentimentBackend:
    """DUTIR 词典法后端 —— 默认实现，无外部依赖、随装随用。"""

    name = "dutir_lexicon"

    def is_available(self) -> bool:
        return lexicon_size() > 0

    def analyze(self, tokens: list[str], *, text: str = "") -> SentimentResult:
        lex = load_lexicon()
        if not lex:
            return _empty(self.name, "情感词典为空")
        counts = {e: 0 for e in EMOTION_CLASSES}
        pol = {"正面": 0, "负面": 0, "中性": 0}
        matched = 0
        seen_in_tokens: set[str] = set()
        for tok in tokens:
            e = lex.get(tok)
            if e is None:
                continue
            counts[e.emotion] += 1
            pol[_POLARITY_NAME.get(e.polarity, "中性") if e.polarity in (1, 2) else "中性"] += 1
            matched += 1
            seen_in_tokens.add(tok)
        # 多字情感词（成语等）jieba 可能未整体切出 —— 对 ≥3 字词补一次子串扫描。
        if text:
            sample = text[:200_000]
            for w, e in lex.items():
                if len(w) >= 3 and w not in seen_in_tokens:
                    c = sample.count(w)
                    if c:
                        counts[e.emotion] += c
                        pol["正面" if e.polarity == 1 else "负面" if e.polarity == 2 else "中性"] += c
                        matched += c
        total_emo = sum(counts.values())
        ratio = {e: (counts[e] / total_emo if total_emo else 0.0) for e in EMOTION_CLASSES}
        pol_total = sum(pol.values())
        pol_ratio = {k: (v / pol_total if pol_total else 0.0) for k, v in pol.items()}
        dominant = max(EMOTION_CLASSES, key=lambda e: counts[e]) if total_emo else ""
        return SentimentResult(
            available=total_emo > 0,
            method=self.name,
            emotion_counts=counts,
            emotion_ratio=ratio,
            polarity_ratio=pol_ratio,
            dominant_emotion=dominant,
            matched=matched,
            total_tokens=len(tokens),
            reason="" if total_emo else "语料中未命中情感词",
        )


class DeepSentimentBackend:
    """**预留接口** —— 本地 BERT / RoBERTa / ERNIE 深度情感模型。

    现在不内置权重；``is_available()`` 在 (1) 配置了模型目录、(2) torch 可用、
    (3) 硬件检测通过 时才为真，否则工厂会自动降级到词典法。接入时只需在
    ``analyze()`` 里加载模型并把 logits 映射到七大类 ``SentimentResult``，
    其余调用方无需改动。
    """

    name = "deep_model"

    def __init__(self, model_dir: str | None = None, arch: str = "bert") -> None:
        self.model_dir = model_dir
        self.arch = arch          # 'bert' | 'roberta' | 'ernie'
        self._model = None

    def is_available(self) -> bool:
        if not self.model_dir or not Path(self.model_dir).exists():
            return False
        try:
            import torch  # noqa: F401
        except Exception:
            return False
        try:
            # 复用 app 的硬件检测模块判定算力是否够跑（与 NER 同一套降级逻辑）。
            from ...embedding.hardware_detector import detect_hardware
            caps = detect_hardware()
            return caps.has_cuda or caps.ram_mb >= 4096
        except Exception:
            return False

    def analyze(self, tokens: list[str], *, text: str = "") -> SentimentResult:
        # 预留：实际接入时在此加载 transformers 模型并推理。当前不可用即兜底。
        return _empty(self.name, "深度情感模型未接入（预留接口）")


def get_sentiment_backend(
    prefer: str = "auto", *, deep_model_dir: str | None = None, arch: str = "bert",
) -> SentimentBackend:
    """工厂：``prefer='deep'`` 且深度后端可用时用深度模型，否则一律用词典法。
    这样后续接入 BERT/RoBERTa/ERNIE 只改这里，不改调用方。"""
    if prefer in ("deep", "bert", "roberta", "ernie"):
        deep = DeepSentimentBackend(deep_model_dir, arch=(arch if prefer == "deep" else prefer))
        if deep.is_available():
            return deep
        logger.info("deep sentiment backend unavailable; falling back to DUTIR lexicon")
    return LexiconSentimentBackend()


def analyze_sentiment(
    tokens: Iterable[str], *, text: str = "", prefer: str = "auto",
) -> SentimentResult:
    """便捷入口：对已分词 tokens（可选原文 text 用于成语补扫）做情感标注。"""
    backend = get_sentiment_backend(prefer)
    return backend.analyze(list(tokens), text=text)
