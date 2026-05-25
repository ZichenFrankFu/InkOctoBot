"""Platform market directive loader.

Pulls the genre/tag share conclusions from the market-database analysis
panel for the project's target publishing platform (起点 / 番茄 / both).
Result is cached per platform for 30 minutes to avoid repeated analysis
runs on every chapter generation.
"""
from __future__ import annotations

import json
import logging
import time

from ..utils import clip, section

logger = logging.getLogger("inkoctobot.services.prompt_context.platform_market")

_cache: dict[str, tuple[float, str]] = {}
_CACHE_TTL = 1800  # 30 minutes


def _build_directive(plat_code: str) -> str:
    """Run the market-analysis panel for a platform and distil into a directive."""
    cached = _cache.get(plat_code)
    if cached and time.time() - cached[0] < _CACHE_TTL:
        return cached[1]
    directive = ""
    try:
        from ui.backend.app.routers.analysis_api import run_analysis

        res = run_analysis(platform=plat_code, lookback="all", top_k=20)
        if (not res.get("empty")) or plat_code == "both":
            parts: list[str] = []
            tags = [t for t in (res.get("tag_rollup") or []) if t.get("tag")]
            if tags:
                top = sorted(tags, key=lambda t: t.get("latest_share") or 0, reverse=True)[:12]
                parts.append("高份额题材标签（份额）：" + "、".join(
                    f"{t['tag']}({(t.get('latest_share') or 0):.1%})" for t in top))
            cats = [c for c in (res.get("cat_rollup") or []) if c.get("category")]
            if cats:
                topc = sorted(cats, key=lambda c: c.get("latest_count") or 0, reverse=True)[:8]
                parts.append("主流分类：" + "、".join(str(c["category"]) for c in topc))
            opps = [o for o in (res.get("opportunities") or []) if o.get("tag")]
            if opps:
                topo = sorted(opps, key=lambda o: o.get("opportunity_score") or 0, reverse=True)[:6]
                parts.append("当前开书机会（份额上升的题材）：" + "、".join(
                    f"{o.get('category') or ''}·{o['tag']}".strip("·") for o in topo))
            directive = "\n".join(parts)
    except Exception as e:
        logger.debug("platform market analysis skipped: %s", e)
    _cache[plat_code] = (time.time(), directive)
    return directive


def load(project_id: str, exclude: set | None = None) -> str:
    """Ground the project's publishing platform in real data."""
    if exclude and "platform" in exclude:
        return ""
    try:
        from ui.backend.app.routers.json_storage_api import _col, _safe_id

        p = _col("projects") / f"{_safe_id(project_id)}.json"
        if not p.exists():
            return ""
        proj = json.loads(p.read_text("utf-8"))
        platform = str(proj.get("platform") or "").strip()
        if not platform:
            return ""
        low = platform.lower()
        if "番茄" in platform or "fanqie" in low:
            plat_code = "fanqie"
        elif "起点" in platform or "qidian" in low:
            plat_code = "qidian"
        else:
            plat_code = "both"
        directive = _build_directive(plat_code)
        if not directive and plat_code != "both":
            directive = _build_directive("both")
        if not directive:
            return ""
        body = clip(f"目标发布平台：{platform}\n{directive}", 1800)
        return section(
            "目标平台市场特性（基于市场数据库分析面板的真实数据）",
            f"{body}\n（以上为该平台真实题材 / 份额分析；与本章具体指令冲突时以章节指令为准。）",
        )
    except Exception as e:
        logger.debug("platform directive skipped: %s", e)
        return ""
