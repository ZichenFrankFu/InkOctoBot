"""词性分布 (POS) + 平均依存距离 (MDD)（spec 语言学文本特征 §1）。

- POS 分布：一次 jieba.posseg 扫描得到动/形/名/副词占比。UI 把它们翻译成读者
  能懂的话（动词→「动作场面」、形容词→「修饰描写密度」、名词→「设定密度」）。
- MDD（平均依存距离）：真依存距离需句法分析器。有 LTP（且硬件允许）时算真值；
  否则用「每小句 token 数」做句式复杂度的诚实估算，并在 method 里标明是估算，
  UI 据此解释成「句式复杂度」。

本模块只做一次 posseg 扫描并把有序 (word, flag) 流暴露出去，供 opening_stats 同时
喂给词汇丰富度与情感分析，避免重复分词。
"""
from __future__ import annotations

import logging
import re

logger = logging.getLogger("inkoctobot.market_extractor.linguistics")

try:
    import jieba.posseg as _posseg
    _HAS_JIEBA = True
except Exception:
    _HAS_JIEBA = False

_CJK_TOKEN = re.compile(r"[一-鿿]+")
_SENT_SPLIT = re.compile(r"[。！？!?…]+")
_CLAUSE_SPLIT = re.compile(r"[，,。！？!?；;：:…—]+")


def posseg_stream(text: str, *, char_cap: int = 120_000) -> list[tuple[str, str]]:
    """一次 posseg 扫描 → 有序 (word, flag)。jieba 缺失时退化为字级（flag='x'）。"""
    if not text:
        return []
    t = text[:char_cap]
    if not _HAS_JIEBA:
        return [(c, "x") for c in t if _CJK_TOKEN.match(c)]
    out: list[tuple[str, str]] = []
    for tok in _posseg.cut(t):
        w = tok.word
        if w and w.strip():
            out.append((w, tok.flag))
    return out


def content_tokens(stream: list[tuple[str, str]]) -> list[str]:
    """从词性流取实词 token（去标点/空白/纯符号），供词汇丰富度与情感分析复用。"""
    return [w for w, f in stream if f not in ("x", "w", "eng", "m") and _CJK_TOKEN.search(w)]


def pos_distribution(stream: list[tuple[str, str]]) -> dict:
    """动/形/名/副词在实词中的占比 + 语义别名（动作场面/修饰描写/设定密度）。"""
    toks = [(w, f) for w, f in stream if f not in ("x", "w") and _CJK_TOKEN.search(w)]
    total = len(toks)
    if total == 0:
        return {"available": False, "total": 0,
                "noun_ratio": 0.0, "verb_ratio": 0.0,
                "adjective_ratio": 0.0, "adverb_ratio": 0.0}
    n_noun = sum(1 for _, f in toks if f.startswith("n"))
    n_verb = sum(1 for _, f in toks if f.startswith("v"))
    n_adj = sum(1 for _, f in toks if f.startswith("a"))
    n_adv = sum(1 for _, f in toks if f.startswith("d"))
    noun_r = round(n_noun / total, 4)
    verb_r = round(n_verb / total, 4)
    adj_r = round(n_adj / total, 4)
    adv_r = round(n_adv / total, 4)
    return {
        "available": True,
        "total": total,
        "counts": {"noun": n_noun, "verb": n_verb, "adjective": n_adj, "adverb": n_adv},
        "noun_ratio": noun_r,
        "verb_ratio": verb_r,
        "adjective_ratio": adj_r,
        "adverb_ratio": adv_r,
        # 面向读者的语义别名（UI 直接展示这套话术）。
        "action_scene": verb_r,          # 动作场面 ← 动词密度
        "description_density": adj_r,    # 修饰描写密度 ← 形容词密度
        "setting_density": noun_r,       # 设定密度 ← 名词密度
    }


def _avg_clause_tokens(texts: list[str], stream_cap: int = 60_000,
                       max_clauses: int = 3000) -> float:
    """每小句平均 token 数（按标点切小句）—— MDD 缺位时的句式复杂度代理量。
    采样上限 ``max_clauses`` 个小句即够稳定均值，避免在大语料上二次分词开销失控。"""
    clause_lens: list[int] = []
    for t in texts:
        for clause in _CLAUSE_SPLIT.split(t[:stream_cap]):
            c = clause.strip()
            if not c:
                continue
            if _HAS_JIEBA:
                import jieba
                ntok = sum(1 for w in jieba.cut(c) if w.strip())
            else:
                ntok = len(_CJK_TOKEN.findall(c))
            if ntok:
                clause_lens.append(ntok)
            if len(clause_lens) >= max_clauses:
                break
        if len(clause_lens) >= max_clauses:
            break
    if not clause_lens:
        return 0.0
    return sum(clause_lens) / len(clause_lens)


def _ltp_mdd(texts: list[str], max_sentences: int = 200) -> float | None:
    """有 LTP 时算真 MDD：对采样句子做依存分析，平均 |i-head_i|。"""
    try:
        from .ner_backend import get_ltp_pipeline
    except Exception:
        return None
    pipe = get_ltp_pipeline()
    if pipe is None:
        return None
    sents: list[str] = []
    for t in texts:
        for s in _SENT_SPLIT.split(t):
            s = s.strip()
            if 2 <= len(s) <= 120:
                sents.append(s)
            if len(sents) >= max_sentences:
                break
        if len(sents) >= max_sentences:
            break
    if not sents:
        return None
    try:
        return pipe.mean_dependency_distance(sents)
    except Exception as e:  # pragma: no cover - depends on optional LTP
        logger.debug("LTP MDD failed: %s", e)
        return None


def mean_dependency_distance(texts: list[str]) -> dict:
    """MDD + 句式复杂度。LTP 可用算真值，否则诚实估算并标 method='estimate'。"""
    real = _ltp_mdd(texts)
    if real is not None:
        # 中文 MDD 经验区间约 [1.5, 5] → 归一化成 0..1 的句式复杂度。
        complexity = max(0.0, min(1.0, (real - 1.5) / 3.5))
        return {"available": True, "mdd": round(real, 3), "method": "ltp_parse",
                "is_estimate": False, "complexity": round(complexity, 4),
                "avg_clause_tokens": None}
    avg_clause = _avg_clause_tokens(texts)
    if avg_clause <= 0:
        return {"available": False, "mdd": None, "method": "estimate",
                "is_estimate": True, "complexity": 0.0, "avg_clause_tokens": 0.0}
    # 小句 token 数经验区间约 [3, 18] → 句式复杂度。
    complexity = max(0.0, min(1.0, (avg_clause - 3.0) / 15.0))
    return {"available": True, "mdd": None, "method": "estimate", "is_estimate": True,
            "complexity": round(complexity, 4),
            "avg_clause_tokens": round(avg_clause, 2)}
