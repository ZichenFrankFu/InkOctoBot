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
        # spec-2.1.3.2 真实统计 (render_stats_for_prompt 注入)
        assert "章中位字数" in prompt
        assert "标点密度" in prompt
        assert "生造词Step1" in prompt
        # 真实章节原文节选（前2章 开头+结尾）
        assert "章节原文节选" in prompt
        assert "陈玄" in prompt          # actual chapter text reached the prompt
        # 高级特征提取 schema: 生造词Step2 + 行文风格七组 (A1-G2)
        assert "生造词Step2" in prompt
        assert "neologism_step2" in prompt
        assert "style_dimensions" in prompt
        # 七组都在 schema 里 (A 主角 … G 节奏)
        for marker in ("A1_appearance", "B1_network", "C1_type",
                       "D1_opening_hook", "E1_writing_style",
                       "F1_disclosure", "G1_rhythm"):
            assert marker in prompt, marker


class TestRealSelectionMechanism:
    """真实数据选取 (spec 市场数据库特征提取·机制1/机制2)."""

    @pytest.fixture
    def env2(self, tmp_path: Path, monkeypatch):
        crawler = tmp_path / "crawler2.db"
        con = sqlite3.connect(str(crawler))
        create_all(con)
        # 3 novels on the 月票榜·玄幻 board + 1 novel only on 新书榜
        for uid, title in ((1, "榜首之作"), (2, "第二名"), (3, "第三名"),
                           (4, "新书黑马")):
            con.execute(
                "INSERT INTO novels (novel_uid, platform, platform_novel_id, "
                "author, author_norm, intro, main_category, status, "
                "total_words, url, created_date, last_seen_date) "
                "VALUES (?, 'qidian', ?, '作者', '作者', '简介', '玄幻', "
                "'ongoing', 500000, 'http://x', date('now'), date('now'))",
                (uid, f"q{uid}"),
            )
            con.execute(
                "INSERT INTO novel_titles (novel_uid, title, title_norm, "
                "is_primary, first_seen_date, last_seen_date) "
                "VALUES (?, ?, ?, 1, date('now'), date('now'))",
                (uid, title, title),
            )
            con.execute(
                "INSERT INTO first_n_chapters (novel_uid, chapter_num, "
                "chapter_title, chapter_content, word_count, publish_date) "
                "VALUES (?, 1, '第一章', ?, 100, date('now'))",
                (uid, f"《{title}》的第一章正文。" * 30),
            )
        con.execute(
            "INSERT INTO rank_lists (rank_list_id, platform, rank_family, "
            "rank_sub_cat) VALUES (10, 'qidian', '月票榜', '玄幻')",
        )
        con.execute(
            "INSERT INTO rank_lists (rank_list_id, platform, rank_family, "
            "rank_sub_cat) VALUES (11, 'qidian', '新人签约新书榜', '玄幻')",
        )
        con.execute(
            "INSERT INTO rank_snapshots (snapshot_id, rank_list_id, "
            "snapshot_date, item_count) VALUES (100, 10, date('now'), 3)",
        )
        con.execute(
            "INSERT INTO rank_snapshots (snapshot_id, rank_list_id, "
            "snapshot_date, item_count) VALUES (101, 11, date('now'), 1)",
        )
        for uid, rank, rec in ((1, 1, 9000), (2, 2, 5000), (3, 3, 1000)):
            con.execute(
                "INSERT INTO rank_entries (snapshot_id, novel_uid, rank, "
                "total_recommend) VALUES (100, ?, ?, ?)",
                (uid, rank, rec),
            )
        con.execute(
            "INSERT INTO rank_entries (snapshot_id, novel_uid, rank, "
            "total_recommend) VALUES (101, 4, 1, 50)",
        )
        con.commit()
        con.close()

        proj = tmp_path / "project2.db"
        pcon = sqlite3.connect(str(proj))
        from storage.project_schema import ensure_creation_tables
        ensure_creation_tables(pcon)
        pcon.commit()
        pcon.close()

        monkeypatch.setattr(
            "ui.backend.app.routers.market_extractor_api.get_db_path",
            lambda: str(proj),
        )
        # 只 patch canonical resolver — 不设 WN_CRAWLER_DB。验证
        # selector / manual-prompt 真正走 resolve_crawler_db_path()
        # （曾经 selector 自行解析出另一个路径 → 「暂无候选代表作」）。
        monkeypatch.setattr(
            "ui.backend.app.utils.resolve_crawler_db_path",
            lambda: str(crawler),
        )
        monkeypatch.delenv("WN_CRAWLER_DB", raising=False)
        from ui.backend.app.main import app
        return TestClient(app), str(proj)

    def test_board_selection_rank_heat_plus_newbook(self, env2) -> None:
        from ui.backend.app.services.market_extractor import (
            representative_selector as rs,
        )
        client, proj = env2
        out = rs.select(proj, "qidian", "玄幻")
        assert out["selected"] >= 4
        rows = rs.list_selected(proj, "qidian", "玄幻", include_holdout=True)
        by_id = {r["source_db_novel_id"]: r for r in rows}
        # 机制1: 高 rank + 高热度 → 榜首分数最高
        assert by_id["1"]["rank_score"] > by_id["3"]["rank_score"]
        # 机制2: 新书榜作品被随机抽入（仅在新书榜出现的 uid=4）
        assert "4" in by_id

    def test_empty_pool_auto_selects_and_hydrates(self, env2) -> None:
        """manual-prompt 在池子为空时自动运行真实选取，并用爬虫库
        水合书名/作者 — 不再出现「暂无候选代表作」。"""
        client, proj = env2
        r = client.post("/api/market-extractor/manual-prompt",
                        json={"platform": "qidian", "category": "玄幻"})
        assert r.status_code == 200
        prompt = r.json()["prompt"]
        assert "暂无候选代表作" not in prompt
        assert "暂无已采集的开篇章节" not in prompt
        assert "暂无已采集的章节原文" not in prompt
        assert "《榜首之作》" in prompt          # hydrated title
        assert "榜首之作》的第一章正文" in prompt  # real excerpt
        assert "章中位字数" in prompt             # real stats
