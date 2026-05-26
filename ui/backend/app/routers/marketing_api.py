"""
/api/marketing — Marketing Agent 市场分析建议

README §2.2.1: 基于市场数据给出选题/书名/简介建议。
独立于已有的 /api/analysis (TrendAnalyzer 包装)。
"""
from __future__ import annotations
from fastapi import APIRouter
from pydantic import BaseModel
from ..settings import settings
from ..utils import resolve_crawler_db_path

router = APIRouter(prefix="/marketing", tags=["marketing"])

def _agent():
    from agents.planner.marketing_agent import MarketingAgent
    return MarketingAgent(resolve_crawler_db_path())

@router.get("/genre-heat/{genre}")
def genre_heat(genre: str):
    return _agent().genre_heat(genre)

@router.get("/genre-trend/{genre}")
def genre_trend(genre: str, lookback_days: int = 90):
    return _agent().genre_trend(genre, lookback_days)

@router.get("/genre-competition/{genre}")
def genre_competition(genre: str):
    return _agent().genre_competition(genre)

@router.get("/popular-tags/{genre}")
def popular_tags(genre: str, limit: int = 20):
    return {"tags": _agent().popular_tags(genre, limit)}

class GenreAdviceReq(BaseModel):
    genre: str

class PostChapterReq(BaseModel):
    genre: str
    chapter_text: str

@router.post("/advise/genre")
def advise_genre(body: GenreAdviceReq):
    """选题建议：热度 + 趋势 + 竞争 + 差异化。"""
    return _agent().advise_genre(body.genre)

@router.post("/advise/post-chapter")
def advise_post_chapter(body: PostChapterReq):
    """每章生成后的市场对比建议。"""
    return _agent().advise_post_chapter(body.genre, body.chapter_text)