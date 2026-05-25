"""
Text statistics extraction.

Provides compute_style_fingerprint() and extract_characters().
Uses jieba/SnowNLP when available, pure-regex fallback otherwise.
"""
from __future__ import annotations
import math, re
from collections import Counter, defaultdict
from typing import Any

_SENT = re.compile(r'[。！？…]+["」』）]?')
_DLG = re.compile(r'["「『]([^"」』]+)["」』]|"([^"]*?)"')
_RHET = [
    re.compile(r'(?:像|如同|仿佛|宛如)[^，。！？]{2,15}'),
    re.compile(r'难道[^？]*？'),
    re.compile(r'[^，。]{3,10}[，；][^，。]{3,10}[，；][^，。]{3,10}'),
]


def compute_style_fingerprint(chapters: list[dict]) -> dict[str, Any]:
    """6-dim style fingerprint from chapter list."""
    full = "\n".join(ch.get("content", "") for ch in chapters)
    if not full:
        return {}

    sents = [s.strip() for s in _SENT.split(full) if len(s.strip()) >= 2]
    avg_len = (sum(len(s) for s in sents) / len(sents)) if sents else 0

    dlg_chars = sum(len(m[0]) + len(m[1]) for m in _DLG.findall(full))
    dlg_ratio = dlg_chars / max(len(full), 1)

    rhet_count = sum(len(p.findall(full)) for p in _RHET)
    rhet_freq = rhet_count / max(len(full) / 1000, 1)

    # description density
    desc = len(re.findall(r'[的地得]', full)) / max(len(full), 1)
    try:
        import jieba.posseg as pseg
        words = list(pseg.cut(full[:60000]))
        desc = sum(1 for w in words if w.flag in ('a', 'ad', 'an', 'd')) / max(len(words), 1)
    except ImportError:
        pass

    # vocab complexity (root TTR)
    try:
        import jieba
        tokens = list(jieba.cut(full[:80000]))
    except ImportError:
        tokens = list(full[:80000])
    tokens = [t for t in tokens if t.strip()]
    root_ttr = len(set(tokens)) / math.sqrt(max(len(tokens), 1))
    vocab = min(1.0, max(0.0, (root_ttr - 3) / 12))

    # pacing profile
    pacing = Counter({"fast": 0, "medium": 0, "slow": 0})
    for ch in chapters:
        c = ch.get("content", "")
        ss = [s for s in _SENT.split(c) if len(s.strip()) >= 2]
        al = sum(len(s) for s in ss) / max(len(ss), 1)
        dl = sum(len(m[0]) + len(m[1]) for m in _DLG.findall(c)) / max(len(c), 1)
        if dl > 0.4 or al < 15:
            pacing["fast"] += 1
        elif al > 30:
            pacing["slow"] += 1
        else:
            pacing["medium"] += 1
    tp = sum(pacing.values()) or 1

    return {
        "avg_sentence_length": round(avg_len, 2),
        "dialogue_ratio": round(dlg_ratio, 4),
        "description_density": round(desc, 4),
        "rhetoric_frequency": round(rhet_freq, 4),
        "vocab_complexity": round(vocab, 4),
        "pacing_profile": {k: round(v / tp, 3) for k, v in pacing.items()},
    }


def compute_nlp_style(chapters: list[dict]) -> dict[str, Any]:
    """Compute ONLY the deterministic / statistical style metrics that
    don't need an LLM judgement: average sentence length, vocabulary
    richness (root TTR, normalized 0-1), and a punctuation-usage
    profile (uses per 1000 chars). The features tab pairs this with the
    LLM-discriminated metrics (dialogue ratio, rhetoric, payoff/info/
    hook density) from the reference.style prompt."""
    full = "\n".join(ch.get("content", "") for ch in chapters)
    if not full:
        return {}
    n = len(full)

    sents = [s.strip() for s in _SENT.split(full) if len(s.strip()) >= 2]
    avg_len = (sum(len(s) for s in sents) / len(sents)) if sents else 0

    # Vocabulary richness — root TTR normalized to 0-1 (same scaling as
    # compute_style_fingerprint so the two stay comparable).
    try:
        import jieba
        tokens = list(jieba.cut(full[:80000]))
    except ImportError:
        tokens = list(full[:80000])
    tokens = [t for t in tokens if t.strip()]
    root_ttr = len(set(tokens)) / math.sqrt(max(len(tokens), 1))
    vocab = min(1.0, max(0.0, (root_ttr - 3) / 12))

    # Punctuation usage — counts per 1000 chars. Ellipsis matches both
    # the CJK 省略号 (……) and ASCII (...); dash matches 破折号 (——).
    per_k = max(n / 1000.0, 1.0)
    ellipsis = len(re.findall(r'…+|\.{3,}', full))
    dash = len(re.findall(r'——|—{1,}|--+', full))
    exclaim = full.count('！') + full.count('!')
    question = full.count('？') + full.count('?')
    comma = full.count('，') + full.count(',')
    return {
        "avg_sentence_length": round(avg_len, 2),
        "vocab_complexity": round(vocab, 4),
        "punctuation_profile": {
            "ellipsis": round(ellipsis / per_k, 3),
            "dash": round(dash / per_k, 3),
            "exclamation": round(exclaim / per_k, 3),
            "question": round(question / per_k, 3),
            "comma": round(comma / per_k, 3),
        },
    }


# ── Character extraction ──

_COMMON_SURNAMES = set(
    "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜"
    "戚谢邹喻柏水窦章云苏潘葛奚范彭郎鲁韦昌马苗凤花方俞任袁柳鲍史唐"
    "费廉岑薛雷贺倒汤殷罗毕郝邬安常乐于时傅皮齐康伍余元卜顾孟黄萧尹"
    "姚邵湛汪祁毛禹狄米贝明臧计伏成戴宋茅庞熊纪舒屈项祝董梁杜阮蓝闵"
    "季贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯昝管"
    "卢莫经房裘缪干解应宗丁宣邓郁单杭洪包诸左石崔吉龚程嵇邢滑裴陆荣"
    "翁荀羊於惠甄魏加封芮靳"
)
_SPEAKER_RE = re.compile(
    r'(?:([^\n"「『]{1,8}?)'
    r'(?:说道?|道|喊道?|笑道?|怒道?|冷声道?|问道?|答道?)[\s：:]*)?'
    r'["「『]([^"」』]+)["」』]'
)


def extract_characters(chapters: list[dict], min_mentions: int = 2,
                       max_chars: int = 30) -> list[dict]:
    """Extract characters from chapter text.

    Returns per-character dicts with:
        name, mentions, intro, speech_samples,
        appearance_chapters (number of chapters/episodes the name appears in),
        appearance_word_count (sum of chars in those chapters, fallback metric).
    The intro field is left empty by the NLP extractor; the AI extractor fills it.
    """
    full = "\n".join(ch.get("content", "") for ch in chapters)

    try:
        import jieba.posseg as pseg
        ctr: Counter = Counter()
        for w, flag in pseg.cut(full[:200000]):
            if flag == "nr" and 1 < len(w) <= 4:
                ctr[w] += 1
    except ImportError:
        pat = re.compile(
            r'([' + ''.join(_COMMON_SURNAMES) + r'][\u4e00-\u9fff]{1,2})'
            r'(?:说|道|笑|喊|问|答|想|看)'
        )
        ctr = Counter(pat.findall(full[:200000]))

    names = {n: c for n, c in ctr.items() if c >= min_mentions and 1 < len(n) <= 4}
    top = sorted(names.items(), key=lambda x: -x[1])[:max_chars]
    name_set = {n for n, _ in top}

    # dialogue attribution
    dlg_by: dict[str, list[str]] = defaultdict(list)
    for m in _SPEAKER_RE.finditer(full):
        sp, txt = m.group(1), m.group(2)
        if sp and txt and len(txt) >= 2:
            for nm in name_set:
                if nm in sp or sp in nm:
                    dlg_by[nm].append(txt)
                    break

    # appearance counts + first-seen marker per character
    appearance_chapters: dict[str, int] = defaultdict(int)
    appearance_word_count: dict[str, int] = defaultdict(int)
    first_seen_chapter: dict[str, int] = {}
    cumulative_chars = 0
    cumulative_at_first: dict[str, int] = {}
    for i, ch in enumerate(chapters, start=1):
        content = ch.get("content", "")
        clen = len(content)
        for nm in name_set:
            if nm in content:
                appearance_chapters[nm] += 1
                appearance_word_count[nm] += clen
                if nm not in first_seen_chapter:
                    first_seen_chapter[nm] = i
                    cumulative_at_first[nm] = cumulative_chars
        cumulative_chars += clen

    # Format first_seen marker: prefer date hint in the first chapter's
    # opening 200 chars; else "第 N 章"; else "约 M 万字处".
    try:
        from reference_pipeline.narrative_extractor import _DATE_HINT_PAT
    except ImportError:
        _DATE_HINT_PAT = None  # type: ignore

    def _format_first_seen(nm: str) -> str:
        ch_idx = first_seen_chapter.get(nm)
        if not ch_idx:
            return ""
        if _DATE_HINT_PAT is not None and ch_idx - 1 < len(chapters):
            head = (chapters[ch_idx - 1].get("content") or "")[:200]
            m = _DATE_HINT_PAT.search(head)
            if m:
                return m.group(1).strip()
        return f"第 {ch_idx} 章"

    results = []
    for name, count in top:
        results.append({
            "name": name,
            "mentions": count,
            "intro": "",
            "speech_samples": dlg_by.get(name, [])[:3],
            "appearance_chapters": appearance_chapters.get(name, 0),
            "appearance_word_count": appearance_word_count.get(name, 0),
            "first_seen_at": _format_first_seen(name),
        })
    return results