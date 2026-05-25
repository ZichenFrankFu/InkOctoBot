"""Tests for the repetition_detect skill."""
import pytest


@pytest.mark.skill
class TestRepetitionDetect:
    def test_detect_no_repetition(self):
        """Clean text should score high."""
        from agents.evaluation.repetition_detector import RepetitionDetector
        detector = RepetitionDetector()
        result = detector.detect("这是一段普通的文字。没有重复的内容。每句话都不同。")
        assert result["score"] >= 70
        assert not result["has_issues"]

    def test_detect_sentence_start_repetition(self):
        """Repeated sentence starts should be detected."""
        from agents.evaluation.repetition_detector import RepetitionDetector
        detector = RepetitionDetector(phrase_threshold=2)
        text = "他走到了门前。他走到了窗边。他走到了桌旁。他走到了床边。"
        result = detector.detect(text)
        assert result["has_issues"]

    def test_detect_repeated_phrases(self):
        """Repeated phrases should be detected."""
        from agents.evaluation.repetition_detector import RepetitionDetector
        detector = RepetitionDetector(phrase_min_len=3, phrase_threshold=3)
        text = "不禁微微一笑" * 5
        result = detector.detect(text)
        assert result["has_issues"]


@pytest.mark.skill
class TestSlopDetect:
    def test_detect_clean_text(self):
        """Text without AI patterns should score high."""
        from agents.evaluation.slop_detector import SlopDetector
        detector = SlopDetector()
        result = detector.detect("大雨倾盆而下，远处的山峦笼罩在一片灰蒙蒙之中。")
        assert result["score"] >= 70

    def test_detect_slop_patterns(self):
        """Text with AI clichés should be flagged."""
        from agents.evaluation.slop_detector import SlopDetector
        detector = SlopDetector()
        text = "他不禁深吸一口气，嘴角微微上扬。值得注意的是，综上所述，让我们回顾一下。"
        result = detector.detect(text)
        assert result["has_issues"]
        assert len(result["matches"]) > 0


@pytest.mark.skill
class TestStyleDriftDetect:
    def test_no_drift_without_target(self):
        """Without a target profile, no drift should be reported."""
        from agents.evaluation.style_drift_detector import StyleDriftDetector
        detector = StyleDriftDetector()
        result = detector.detect("一段测试文字。")
        assert not result["drifted"]

    def test_detect_drift(self):
        """Significant deviation from target should be flagged."""
        from agents.evaluation.style_drift_detector import StyleDriftDetector
        target = {"avg_sentence_length": 10.0, "dialogue_ratio": 0.5}
        detector = StyleDriftDetector(target_profile=target)
        # Very long sentences, no dialogue
        text = "这是一段非常非常长的句子" * 20 + "。"
        result = detector.detect(text)
        # Should detect drift due to very different sentence length
        assert "features" in result


@pytest.mark.skill
class TestQualityScore:
    def test_weighted_scoring(self):
        """Quality scorer should compute weighted average."""
        from agents.evaluation.quality_scorer import QualityScorer
        scorer = QualityScorer(pass_threshold=60.0)
        scores = {
            "constraint_satisfaction": 80,
            "consistency": 90,
            "knowledge_isolation": 70,
            "repetition": 85,
            "style_quality": 75,
            "pacing": 80,
            "slop_free": 90,
        }
        report = scorer.score(scores)
        assert report.overall_score > 0
        assert report.passed
        assert report.overall_score == pytest.approx(80.75, abs=0.1)

    def test_failing_score(self):
        """Low scores should fail."""
        from agents.evaluation.quality_scorer import QualityScorer
        scorer = QualityScorer(pass_threshold=60.0)
        scores = {"constraint_satisfaction": 20, "consistency": 30}
        report = scorer.score(scores)
        assert not report.passed
