"""Style calibration loader.

Reads the project's 风格校准 (style_params) and turns the four sliders
+ perspective + audience into a short prose-style directive.
"""
from __future__ import annotations

import logging

from ..utils import section

logger = logging.getLogger("inkoctobot.services.prompt_context.style_calibration")


def load(project_id: str) -> str:
    """Build a style note from the project's 风格校准 settings."""
    try:
        from ui.backend.app.services import project_store
        from ui.backend.app.services.project_paths import get_db_path

        cal = project_store.get_blob(get_db_path(), project_id, "calibration")
        sp = cal.get("style_params") or {}
        if not sp:
            return ""
        tone = sp.get("tone", 50)
        pacing = sp.get("pacing", 50)
        rhetoric = sp.get("rhetoric", 50)
        persp = sp.get("perspective", "third")
        aud = sp.get("audience", "general")
        tone_d = "轻松幽默" if tone < 30 else ("严肃深沉" if tone > 70 else "均衡")
        pacing_d = "快节奏" if pacing < 30 else ("慢热" if pacing > 70 else "中等节奏")
        rhet_d = "白描直接" if rhetoric < 30 else ("华丽修辞" if rhetoric > 70 else "适度修辞")
        persp_d = {"first": "第一人称", "third": "第三人称", "omniscient": "全知视角"}.get(persp, persp)
        aud_d = {"male": "男频", "female": "女频", "general": "大众"}.get(aud, aud)
        note = f"文风：{tone_d}；节奏：{pacing_d}；修辞：{rhet_d}；视角：{persp_d}；受众：{aud_d}"
        return section("文风校准", note)
    except Exception as e:
        logger.debug("style calibration skipped: %s", e)
        return ""
