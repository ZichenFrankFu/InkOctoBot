"""FastAPI router: Formula engine query — presets, aggregation, constraint conversion."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/formula", tags=["formula"])


@router.get("/health")
def health():
    return {"status": "ok", "router": "formula"}


# ── Presets ──

@router.get("/presets")
def list_presets():
    """List all available genre presets."""
    from analysis.formula_engine.presets import list_presets as _list, get_preset
    names = _list()
    return {
        "presets": [
            get_preset(n).to_dict() for n in names  # type: ignore[union-attr]
            if get_preset(n) is not None
        ],
    }


@router.get("/presets/{genre}")
def get_preset_by_genre(genre: str):
    """Get a specific genre preset."""
    from analysis.formula_engine.presets import get_preset as _get
    p = _get(genre)
    if p is None:
        raise HTTPException(404, f"preset not found: {genre}")
    return p.to_dict()


# ── Aggregation ──

class AggregateRequest(BaseModel):
    style: dict[str, Any] = {}
    narrative: dict[str, Any] = {}
    rhythm: dict[str, Any] = {}
    rhetoric: dict[str, Any] | None = None
    shuangdian: dict[str, Any] | None = None
    genre: str | None = None


@router.post("/aggregate")
def aggregate(req: AggregateRequest):
    """Aggregate multi-dimensional features into a scored summary."""
    from analysis.formula_engine.aggregator import aggregate_features
    from analysis.formula_engine.presets import get_preset as _get

    preset = _get(req.genre) if req.genre else None
    result = aggregate_features(
        style=req.style,
        narrative=req.narrative,
        rhythm=req.rhythm,
        rhetoric=req.rhetoric,
        shuangdian=req.shuangdian,
        preset=preset,
    )
    return result


# ── Constraint conversion ──

class ConvertRequest(BaseModel):
    aggregated: dict[str, Any]
    genre: str | None = None


@router.post("/convert")
def convert(req: ConvertRequest):
    """Convert aggregated features to prompt constraints."""
    from analysis.formula_engine.constraint_converter import convert_to_constraints
    from analysis.formula_engine.presets import get_preset as _get

    preset = _get(req.genre) if req.genre else None
    rules = convert_to_constraints(req.aggregated, preset=preset)
    return {"constraints": rules}
