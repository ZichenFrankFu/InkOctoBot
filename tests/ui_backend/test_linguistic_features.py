"""语言学文本特征 —— 词性分布 / MDD / 词汇丰富度 / 情感分析（spec §1/§2/§3）。"""
from __future__ import annotations

from ui.backend.app.services.market_extractor import (
    lexical_diversity as ld,
    linguistics as lg,
    sentiment as sent,
)


class TestLexicalDiversity:
    def test_mattr_bounds_and_diverse_beats_repetitive(self) -> None:
        diverse = [f"词{i}" for i in range(200)]
        repetitive = ["词"] * 200
        d = ld.compute(diverse)
        r = ld.compute(repetitive)
        assert d["available"] and r["available"]
        assert 0.0 <= d["mattr"] <= 1.0
        assert d["mattr"] > r["mattr"]      # 多样文本 MATTR 更高
        assert d["mtld"] > r["mtld"]        # MTLD 同向
        assert d["richness"] > r["richness"]

    def test_empty(self) -> None:
        out = ld.compute([])
        assert out["available"] is False and out["mattr"] == 0.0


class TestPosDistribution:
    def test_semantic_aliases_present(self) -> None:
        stream = lg.posseg_stream("他猛地挥剑劈砍，巨大的山门轰然炸裂，剑气凌厉。")
        pos = lg.pos_distribution(stream)
        assert pos["available"] is True
        # 动作场面/修饰描写密度/设定密度三个面向读者的语义别名都在
        for key in ("action_scene", "description_density", "setting_density"):
            assert key in pos and 0.0 <= pos[key] <= 1.0
        # 动作文本动词占比应 > 0
        assert pos["verb_ratio"] > 0


class TestMdd:
    def test_estimate_fallback_without_ltp(self) -> None:
        # 本环境无 LTP → 走估算路径，仍给出 0..1 的句式复杂度。
        out = lg.mean_dependency_distance([
            "他走了。",
            "在那座云雾缭绕、终年不见天日的青云山深处，矗立着一座古老宗门。",
        ])
        assert out["method"] == "estimate"
        assert out["is_estimate"] is True
        assert 0.0 <= out["complexity"] <= 1.0

    def test_longer_clauses_more_complex(self) -> None:
        simple = lg.mean_dependency_distance(["他笑。", "她哭。", "风停。"])
        complex_ = lg.mean_dependency_distance([
            "当那道横贯天际的剑光终于落下时，所有人都屏住了呼吸，不敢发出半点声响。"])
        assert complex_["complexity"] >= simple["complexity"]


class TestSentimentDutir:
    def test_seed_lexicon_loaded(self) -> None:
        assert sent.lexicon_size() > 0

    def test_seven_classes_and_dominant(self) -> None:
        r = sent.analyze_sentiment(
            ["愤怒", "愤怒", "暴怒", "高兴"], text="他怒不可遏，咬牙切齿。")
        d = r.to_dict()
        assert d["available"] is True
        assert set(d["emotion_ratio"].keys()) == set(sent.EMOTION_CLASSES)
        assert d["dominant_emotion"] == "怒"      # 愤怒类占多
        assert d["method"] == "dutir_lexicon"

    def test_backend_factory_falls_back_to_lexicon(self) -> None:
        # prefer='deep' 但无模型/torch → 工厂降级到词典法。
        backend = sent.get_sentiment_backend("deep")
        assert backend.name == "dutir_lexicon"

    def test_idiom_substring_scan(self) -> None:
        # 成语未被分词整体切出时，靠子串补扫仍命中。
        r = sent.analyze_sentiment([], text="听到这个消息，他心花怒放。")
        assert r.matched >= 1
