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


def _chapter_count(ref_id: str) -> int:
    import sqlite3
    from ._common import reference_db_path
    try:
        with sqlite3.connect(reference_db_path()) as con:
            row = con.execute(
                "SELECT COUNT(*) FROM reference_chapters WHERE ref_id = ?",
                (ref_id,),
            ).fetchone()
        return int(row[0]) if row else 0
    except sqlite3.OperationalError:
        return 0


@router.get("/works/{ref_id}/index/estimate")
def estimate_work_index(ref_id: str, include_l3: bool = False):
    """性能预期·机制1: ETA before the indexing run, based on
    same-hardware history of past runs (机制3)."""
    if not db().get_work(ref_id):
        raise HTTPException(404, "参考作品不存在")
    chapters = max(1, _chapter_count(ref_id))
    from ...services.duration_estimator import estimate as _estimate
    from ...services.project_paths import get_db_path
    task_type = "reference_index_l3" if include_l3 else "reference_index_l2"
    return _estimate(get_db_path(), task_type, chapters)


@router.post("/works/{ref_id}/index/run")
async def run_work_index(ref_id: str, body: IndexRunRequest):
    """Build / refresh the vector index for one work.

    L1+L2 are cheap and finish in-line; L3 is heavier but resumable
    (progress is persisted to ``work_index_progress`` so the call can
    be killed and restarted). Finished runs feed the duration history
    so future ETAs reflect real throughput (性能预期·机制3).
    """
    w = db().get_work(ref_id)
    if not w:
        raise HTTPException(404, "参考作品不存在")
    indexer = _indexer()
    import time as _time
    t0 = _time.monotonic()
    try:
        if body.level == "L1":
            result = await indexer.index_l1(ref_id)
        elif body.level == "L2":
            result = await indexer.index_l2(ref_id)
        elif body.level == "L3":
            result = await indexer.index_l3(ref_id)
        else:
            result = await indexer.index_all(ref_id, include_l3=body.include_l3)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except ImportError as e:
        # Typical: sentence-transformers ↔ transformers version mismatch
        # (the "cannot import EncoderDecoderCache" path). The embedding
        # factory auto-falls-back to TF-IDF in this case, so reaching here
        # means the user picked `local_strict` or even TF-IDF deps are
        # missing — surface an actionable message instead of a raw 500.
        raise HTTPException(503,
            f"embedding 后端不可用：{e}\n"
            "可在 Settings → 模型设置 切换到 OpenAI / TF-IDF 后端，"
            "或运行 pip install -U 'transformers>=4.41' 'sentence-transformers>=2.7' "
            "修复本地环境。",
        )
    except Exception as e:
        raise HTTPException(500, f"索引失败：{e}")
    try:
        from ...services.duration_estimator import record_duration
        from ...services.project_paths import get_db_path
        task_type = (
            "reference_index_l3"
            if body.level == "L3" or (body.level not in ("L1", "L2") and body.include_l3)
            else "reference_index_l2"
        )
        record_duration(
            get_db_path(), task_type, max(1, _chapter_count(ref_id)),
            _time.monotonic() - t0,
        )
    except Exception:
        pass
    return result


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
