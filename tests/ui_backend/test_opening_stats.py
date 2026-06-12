"""开篇章节 NLP 分析 (spec 2.1.3.2 §1) + 启动提取 prompt 真实数据注入."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from storage.market_schema import create_all
from ui.backend.app.services.market_extractor.opening_stats import (
    compute_opening_stats, render_stats_for_prompt,
)


_CH1 = (
    "陈玄看着面前的青云宗山门，握紧了手里的玄天剑。"
    "「师兄，我们真的要进去吗？」李慕白问道。"
    "陈玄点头：「玄天剑认主，此事【宗门大比】之前必须了结！」"
    "远处，青云宗的钟声响起……他迈步走了进去。"
) * 4
_CH2 = (
    "第二日清晨，陈玄在演武场练剑。玄天剑嗡鸣不止，剑气纵横。"
    "李慕白远远看着，叹了口气——这位师兄，怕是要在宗门大比上掀起风浪了！"
) * 6


def _rows():
    return [
        {"chapter_num": 1, "text": _CH1},
        {"chapter_num": 2, "text": _CH2},
    ]


class TestComputeStats:
    def test_spec_dimensions_present(self) -> None:
        out = compute_opening_stats(_rows())
        assert out["available"] is True
        assert out["chapters_analyzed"] == 2
        # 首章 / 章均 / 章中位 字数
        assert out["first_chapter_words_avg"] == len(_CH1)
        assert out["chapter_words_avg"] == round((len(_CH1) + len(_CH2)) / 2)
        assert out["chapter_words_median"] == round((len(_CH1) + len(_CH2)) / 2)
        # 平均句长
        assert out["avg_sentence_length"] and out["avg_sentence_length"] > 0
        # 分类型标点密度（spec 列出的各类都在）
        pd = out["punctuation_density_per_1k"]
        for label in ("逗号", "感叹号", "问号", "省略号", "破折号",
                      "双引号", "方括号"):
            assert label in pd, label
        assert pd["感叹号"] > 0 and pd["方括号"] > 0
        # 高频词 + 生造词Step1
        assert any(w["word"] for w in out["top_words"])
        neo_terms = {n["term"] for n in out["neologism_step1"]}
        assert any("玄天剑" in t or "青云宗" in t for t in neo_terms)

    def test_empty_input(self) -> None:
        out = compute_opening_stats([])
        assert out["available"] is False

    def test_render_block_carries_dims(self) -> None:
        text = render_stats_for_prompt(compute_opening_stats(_rows()))
        for needle in ("首章字数", "章中位字数", "平均句长", "标点密度",
                       "高频词", "生造词Step1"):
            assert needle in text, needle


class TestManualPromptInjection:
    @pytest.fixture
    def env(self, tmp_path: Path, monkeypatch):
        # crawler DB with one novel + 2 opening chapters
        crawler = tmp_path / "crawler.db"
        con = sqlite3.connect(str(crawler))
        create_all(con)
        con.execute(
            "INSERT INTO novels (novel_uid, platform, platform_novel_id, "
            "author, author_norm, intro, main_category, status, total_words, "
            "url, created_date, last_seen_date) "
            "VALUES (1, 'qidian', 'q1', '作者甲', '作者甲', '一个修仙故事', "
            "'玄幻', 'ongoing', 100000, 'http://x', date('now'), date('now'))",
        )
        con.execute(
            "INSERT INTO novel_titles (novel_uid, title, title_norm, "
            "is_primary, first_seen_date, last_seen_date) "
            "VALUES (1, '玄天剑主', '玄天剑主', 1, date('now'), date('now'))",
        )
        for cn, text in ((1, _CH1), (2, _CH2)):
            con.execute(
                "INSERT INTO first_n_chapters (novel_uid, chapter_num, "
                "chapter_title, chapter_content, word_count, publish_date) "
                "VALUES (1, ?, ?, ?, ?, date('now'))",
                (cn, f"第{cn}章", text, len(text)),
            )
        con.commit()
        con.close()

        # project DB with the representative pool row
        proj = tmp_path / "project.db"
        pcon = sqlite3.connect(str(proj))
        from storage.project_schema import ensure_creation_tables
        ensure_creation_tables(pcon)
        pcon.execute(
            "INSERT INTO representative_works_pool "
            "(work_id, platform, category, source_db_novel_id, rank_score, "
            " selected_for_extraction, is_holdout) "
            "VALUES ('w1', 'qidian', '玄幻', '1', 9.0, 1, 0)",
        )
        pcon.commit()
        pcon.close()

        monkeypatch.setattr(
            "ui.backend.app.routers.market_extractor_api.get_db_path",
            lambda: str(proj),
        )
        monkeypatch.setattr(
            "ui.backend.app.utils.resolve_crawler_db_path",
            lambda: str(crawler),
        )
        from ui.backend.app.main import app
        return TestClient(app)

    def test_prompt_contains_real_stats_and_excerpts(self, env) -> None:
        r = env.post("/api/market-extractor/manual-prompt",
                     json={"platform": "qidian", "category": "玄幻"})
        assert r.status_code == 200
        prompt = r.json()["prompt"]
        # spec-2.1.3.2 维度 section
        assert "开篇章节分析" in prompt
        assert "章中位字数" in prompt
        assert "标点密度" in prompt
        assert "生造词Step1" in prompt
        # 真实章节原文节选
        assert "章节原文节选" in prompt
        assert "陈玄" in prompt          # actual chapter text reached the prompt
        # 分析维度要求: 生造词Step2 + 行文风格七组
        assert "生造词Step2" in prompt
        assert "钩子维度" in prompt and "节奏维度" in prompt
