"""Vector index build + similarity search.

Three index levels (L1 summary, L2 chapter, L3 raw chunks). The L1+L2
build is fast and inline; L3 is heavier but resumable via
``work_index_progress``. Search is two-stage: cross-work L1+L2 by
default, single-work L3 when ``ref_id`` is specified.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ._common import db

router = APIRouter()


class IndexRunRequest(BaseModel):
    level: str = "all"   # 'L1' | 'L2' | 'L3' | 'all' (= L1 + L2)
    include_l3: bool = False


def _indexer():
    """Lazily build a WorkIndexer using the configured embedding backend.

    Raises HTTPException(503) with a clear message if any dep is missing.
    """
    try:
        from knowledge.work_index import make_indexer
        return make_indexer(db().db_path)
    except ImportError as e:
        raise HTTPException(503, f"向量索引依赖缺失：{e}")
    except Exception as e:
        raise HTTPException(500, f"索引器初始化失败：{e}")


@router.post("/works/{ref_id}/index/run")
async def run_work_index(ref_id: str, body: IndexRunRequest):
    """Build / refresh the vector index for one work.

    L1+L2 are cheap and finish in-line; L3 is heavier but resumable
    (progress is persisted to ``work_index_progress`` so the call can
    be killed and restarted).
    """
    w = db().get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    indexer = _indexer()
    try:
        if body.level == "L1":
            return await indexer.index_l1(ref_id)
        if body.level == "L2":
            return await indexer.index_l2(ref_id)
        if body.level == "L3":
            return await indexer.index_l3(ref_id)
        return await indexer.index_all(ref_id, include_l3=body.include_l3)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"索引失败：{e}")


@router.get("/works/{ref_id}/index/progress")
def get_index_progress(ref_id: str):
    """Per-level progress (L1/L2/L3) for resumable indexing UI."""
    return {"items": _indexer().get_progress(ref_id)}


@router.delete("/works/{ref_id}/index")
def clear_work_index(ref_id: str, level: Optional[str] = None):
    """Drop a work's vectors (optionally limit to one level)."""
    levels = [level] if level else None
    _indexer().clear_work(ref_id, levels=levels)
    return {"ok": True}


@router.get("/search")
async def search_works(
    q: str = Query(..., description="自然语言查询"),
    k: int = Query(10, ge=1, le=50),
    levels: str = Query("L1,L2", description="L1,L2 (默认) 或 L3 (单作品深度搜索)"),
    ref_id: Optional[str] = None,
):
    """Two-stage retrieval. Default is Stage 1 (L1+L2 across all works).

    Pass ``levels=L3&ref_id=...`` to drill into one work's raw chunks.
    """
    indexer = _indexer()
    level_list = [s.strip() for s in (levels or "L1,L2").split(",") if s.strip()]
    if "L3" in level_list and not ref_id:
        raise HTTPException(400, "L3 深度搜索需要指定 ref_id")
    try:
        hits = await indexer.search(q, k=k, levels=level_list, ref_id=ref_id)
        return {"q": q, "k": k, "levels": level_list, "hits": hits}
    except Exception as e:
        raise HTTPException(500, f"搜索失败：{e}")
