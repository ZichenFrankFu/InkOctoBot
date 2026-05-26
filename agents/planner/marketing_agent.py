"""
Marketing agent.

README §2.2.1: Market trend analysis + 选题/书名/简介 advice.
Data-driven (no LLM required). LLM enhancement is a future TODO.
"""
from __future__ import annotations
import json, logging, sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger("inkoctobot.agents.planner.marketing_agent")

def _conn(db_path: str) -> sqlite3.Connection:
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row
    return c


class MarketingAgent:

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def genre_heat(self, genre: str) -> dict[str, Any]:
        with _conn(self.db_path) as c:
            rows = c.execute("""
                SELECT n.platform,
                       COUNT(DISTINCT n.novel_uid) AS books,
                       ROUND(AVG(re.rank), 1) AS avg_rank,
                       MAX(re.total_recommend) AS max_recommend,
                       MAX(re.reading_count) AS max_reading
                FROM novels n
                JOIN rank_entries re ON n.novel_uid = re.novel_uid
                WHERE n.main_category LIKE ?
                GROUP BY n.platform
            """, (f"%{genre}%",)).fetchall()
        platforms = {r["platform"]: dict(r) for r in rows}
        total = sum(p["books"] for p in platforms.values())
        return {"genre": genre, "total_books": total, "platforms": platforms}

    def genre_trend(self, genre: str, lookback_days: int = 90) -> dict:
        with _conn(self.db_path) as c:
            rows = c.execute("""
                SELECT rs.snapshot_date,
                       COUNT(DISTINCT re.novel_uid) AS genre_books,
                       (SELECT COUNT(DISTINCT re2.novel_uid)
                        FROM rank_entries re2
                        JOIN rank_snapshots rs2 ON re2.snapshot_id = rs2.snapshot_id
                        WHERE rs2.snapshot_date = rs.snapshot_date) AS total_books
                FROM novels n
                JOIN rank_entries re ON n.novel_uid = re.novel_uid
                JOIN rank_snapshots rs ON re.snapshot_id = rs.snapshot_id
                WHERE n.main_category LIKE ?
                  AND rs.snapshot_date >= date('now', ?)
                GROUP BY rs.snapshot_date ORDER BY rs.snapshot_date
            """, (f"%{genre}%", f"-{lookback_days} days")).fetchall()
        shares = [round(r["genre_books"] / max(r["total_books"], 1), 4) for r in rows]
        trend = "stable"
        if len(shares) >= 4:
            h1 = sum(shares[:len(shares)//2]) / max(len(shares)//2, 1)
            h2 = sum(shares[len(shares)//2:]) / max(len(shares) - len(shares)//2, 1)
            if h2 > h1 * 1.1:
                trend = "rising"
            elif h2 < h1 * 0.9:
                trend = "declining"
        return {
            "genre": genre, "trend": trend,
            "dates": [r["snapshot_date"] for r in rows][-30:],
            "shares": shares[-30:],
        }

    def genre_competition(self, genre: str) -> dict:
        with _conn(self.db_path) as c:
            total = c.execute(
                "SELECT COUNT(DISTINCT novel_uid) FROM novels "
                "WHERE main_category LIKE ?", (f"%{genre}%",),
            ).fetchone()[0]
            top = c.execute("""
                SELECT nt.title, n.author, MIN(re.rank) AS best_rank,
                       n.total_words
                FROM novels n
                JOIN novel_titles nt ON n.novel_uid = nt.novel_uid
                     AND nt.is_primary = 1
                JOIN rank_entries re ON n.novel_uid = re.novel_uid
                WHERE n.main_category LIKE ?
                GROUP BY n.novel_uid ORDER BY best_rank LIMIT 10
            """, (f"%{genre}%",)).fetchall()
        level = ("极高" if total > 200 else "高" if total > 100
                 else "中" if total > 50 else "低" if total > 20 else "蓝海")
        return {
            "genre": genre, "total": total, "level": level,
            "top_works": [dict(r) for r in top],
        }

    def popular_tags(self, genre: str, limit: int = 20) -> list[dict]:
        with _conn(self.db_path) as c:
            rows = c.execute("""
                SELECT t.tag_name, COUNT(DISTINCT n.novel_uid) AS cnt
                FROM tags t
                JOIN novel_tag_map ntm ON t.tag_id = ntm.tag_id
                JOIN novels n ON ntm.novel_uid = n.novel_uid
                WHERE n.main_category LIKE ?
                GROUP BY t.tag_name ORDER BY cnt DESC LIMIT ?
            """, (f"%{genre}%", limit)).fetchall()
        return [dict(r) for r in rows]

    def advise_genre(self, genre: str) -> dict:
        heat = self.genre_heat(genre)
        trend = self.genre_trend(genre)
        comp = self.genre_competition(genre)
        tags = self.popular_tags(genre)

        t_cn = {"rising": "📈上升", "declining": "📉下降", "stable": "➡️平稳"}
        advices: list[dict] = [
            {"category": "热度", "severity": "info",
             "title": f"「{genre}」活跃作品 {heat['total_books']} 部"},
            {"category": "趋势",
             "severity": "warning" if trend["trend"] == "declining" else "info",
             "title": f"趋势: {t_cn.get(trend['trend'], '平稳')}"},
            {"category": "竞争",
             "severity": "warning" if comp["level"] in ("极高", "高") else "info",
             "title": f"竞争: {comp['level']} ({comp['total']}部)"},
        ]

        sat = [t["tag_name"] for t in tags[:5]]
        niche = [t["tag_name"] for t in tags[10:15]] if len(tags) > 10 else []
        advices.append({
            "category": "差异化", "severity": "suggestion",
            "title": "差异化建议",
            "detail": (f"饱和标签: {', '.join(sat)}\n"
                       f"潜力标签: {', '.join(niche) or '数据不足'}"),
        })
        if comp["top_works"]:
            advices.append({
                "category": "竞品", "severity": "info",
                "title": f"Top {min(5, len(comp['top_works']))} 作品",
                "detail": "\n".join(
                    f"《{w['title']}》排名{w['best_rank']}"
                    for w in comp["top_works"][:5]
                ),
            })

        return {
            "stage": "genre", "genre": genre,
            "advices": advices,
            "summary": (
                f"「{genre}」{t_cn.get(trend['trend'], '平稳')}，"
                f"竞争{comp['level']}（{comp['total']}部）。"
            ),
            "raw": {"heat": heat, "trend": trend, "competition": comp, "tags": tags},
        }

    def advise_post_chapter(self, genre: str, chapter_text: str) -> dict:
        from reference_pipeline.nlp_stats import compute_style_fingerprint

        fp = compute_style_fingerprint([{"content": chapter_text}])
        advices: list[dict] = [
            {"category": "字数", "severity": "info",
             "title": f"本章 {len(chapter_text):,} 字"},
        ]
        dr = fp.get("dialogue_ratio", 0)
        if dr < 0.15:
            advices.append({
                "category": "对话", "severity": "suggestion",
                "title": f"对话占比偏低 ({dr:.1%})",
            })
        elif dr > 0.50:
            advices.append({
                "category": "对话", "severity": "suggestion",
                "title": f"对话占比偏高 ({dr:.1%})",
            })
        sl = fp.get("avg_sentence_length", 0)
        if sl > 40:
            advices.append({
                "category": "句式", "severity": "suggestion",
                "title": f"平均句长 {sl:.0f} 字（偏长）",
            })
        return {"stage": "post_chapter", "advices": advices, "style_fingerprint": fp}