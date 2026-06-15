"""词汇丰富度 —— MATTR + MTLD（spec 语言学文本特征 §2）。

纯 Python，零外部依赖（jieba 仅用于分词，缺失时退化为字级 token）。两指标都比
裸 TTR 更抗文本长度影响，适合跨作品横比，UI 中统一展示为「用词丰富度」。

- MATTR (Moving-Average Type-Token Ratio)：定宽窗口滑动求 TTR 再平均，窗口固定
  时长度无关。
- MTLD (Measure of Textual Lexical Diversity)：顺/逆双向扫描，TTR 跌破阈值即记一
  个「factor」，丰富度 = token 数 / factor 数，对长度更稳健。
"""
from __future__ import annotations

from typing import Sequence

_MATTR_WINDOW = 50
_MTLD_THRESHOLD = 0.72


def mattr(tokens: Sequence[str], window: int = _MATTR_WINDOW) -> float:
    """Moving-Average TTR。token 数不足一个窗口时退化为整体 TTR。"""
    n = len(tokens)
    if n == 0:
        return 0.0
    if n < window:
        return len(set(tokens)) / n
    # 滑动窗口：用计数表增量维护当前窗口内的 type 数，O(n)。
    from collections import Counter
    counts: Counter[str] = Counter(tokens[:window])
    ttr_sum = len(counts) / window
    steps = 1
    for i in range(window, n):
        out_tok = tokens[i - window]
        counts[out_tok] -= 1
        if counts[out_tok] == 0:
            del counts[out_tok]
        counts[tokens[i]] += 1
        ttr_sum += len(counts) / window
        steps += 1
    return ttr_sum / steps


def _mtld_pass(tokens: Sequence[str], threshold: float) -> float:
    """单向 MTLD factor 计数 → token/factor。"""
    if not tokens:
        return 0.0
    factors = 0.0
    types: set[str] = set()
    count = 0
    for tok in tokens:
        count += 1
        types.add(tok)
        ttr = len(types) / count
        if ttr <= threshold:
            factors += 1
            types = set()
            count = 0
    # 末段不足一个完整 factor，按其 TTR 离阈值的程度折算一个部分 factor。
    if count > 0:
        ttr = len(types) / count
        denom = (1.0 - threshold)
        partial = (1.0 - ttr) / denom if denom else 1.0
        factors += max(0.0, min(1.0, partial))
    if factors == 0:
        return float(len(tokens))
    return len(tokens) / factors


def mtld(tokens: Sequence[str], threshold: float = _MTLD_THRESHOLD) -> float:
    """双向 MTLD（顺扫 + 逆扫取平均）。"""
    if len(tokens) < 10:
        # 文本过短，MTLD 不稳定；用 token 数近似（小值即低多样性）。
        return float(len(set(tokens)))
    fwd = _mtld_pass(tokens, threshold)
    bwd = _mtld_pass(list(reversed(tokens)), threshold)
    return (fwd + bwd) / 2


def compute(tokens: Sequence[str]) -> dict:
    """汇总 MATTR / MTLD / TTR 及一个 0..1 的「丰富度」综合分（供进度条/星级）。

    综合分把 MATTR（已是 0..1）与 MTLD（无上界，经验上 20~120）做线性压缩后等权
    平均，仅用于 UI 直观展示，不参与任何打分排序。
    """
    toks = [t for t in tokens if t and t.strip()]
    n = len(toks)
    if n == 0:
        return {"available": False, "mattr": 0.0, "mtld": 0.0, "ttr": 0.0,
                "token_count": 0, "type_count": 0, "richness": 0.0}
    _mattr = round(mattr(toks), 4)
    _mtld = round(mtld(toks), 2)
    _ttr = round(len(set(toks)) / n, 4)
    # MTLD 归一：把 [20,120] 线性压到 [0,1]，越界截断。
    mtld_norm = max(0.0, min(1.0, (_mtld - 20.0) / 100.0))
    richness = round((_mattr + mtld_norm) / 2, 4)
    return {
        "available": True,
        "mattr": _mattr,
        "mtld": _mtld,
        "ttr": _ttr,
        "token_count": n,
        "type_count": len(set(toks)),
        "richness": richness,
    }
