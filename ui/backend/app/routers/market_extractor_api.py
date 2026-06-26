"""HTTP API for the market extractor (Phase 6).

Endpoints (spec § 任务 6.4):
    POST   /api/market-extractor/jobs
    GET    /api/market-extractor/jobs
    GET    /api/market-extractor/jobs/:id
    GET    /api/market-extractor/representative-works
    POST   /api/market-extractor/works/:id/exclude
    GET    /api/market-extractor/chapter-features/:work_id
    GET    /api/market-extractor/neologisms/:work_id
    GET    /api/market-extractor/genre-dictionary/:category
    GET    /api/platform-profiles
    GET    /api/platform-profiles/current?platform=X&category=Y
    GET    /api/platform-profiles/:id
"""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Query

from ..services.project_paths import get_db_path
from ..services.market_extractor import (
    dictionaries, job_runner, representative_selector,
)

logger = logging.getLogger("inkoctobot.routers.market_extractor_api")


router = APIRouter(prefix="/api/market-extractor", tags=["market-extractor"])
profiles_router = APIRouter(prefix="/api/platform-profiles", tags=["platform-profiles"])


# 平台 key → 中文名（prompt / 展示用，app 内显式组装，绝不出现 spec/英文 key）。
_PLATFORM_CN = {"qidian": "起点", "fanqie": "番茄", "both": "全平台"}


def _platform_cn(platform: str) -> str:
    return _PLATFORM_CN.get((platform or "").strip(), (platform or "").strip() or "全平台")


# ─────────── jobs ───────────


@router.post("/jobs")
def create_job(body: dict = Body(...)) -> dict:
    platform = (body.get("platform") or "").strip()
    category = (body.get("category") or "").strip()
    crawler_db = (body.get("crawler_db") or "").strip() or None
    work_ids = body.get("work_ids") or None
    if isinstance(work_ids, list):
        work_ids = [str(x) for x in work_ids] or None
    if not platform:                       # category 留空＝整平台（用户只选平台）
        raise HTTPException(400, "platform required")
    job_id = job_runner.run_job_in_background(
        get_db_path(), platform, category, crawler_db=crawler_db,
        work_ids=work_ids,
    )
    return {"job_id": job_id, "state": "queued"}


@router.get("/jobs")
def list_jobs(limit: int = Query(default=50, le=200)) -> dict:
    return {"jobs": job_runner.list_jobs(get_db_path(), limit=limit)}


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = job_runner.get_job_status(get_db_path(), job_id)
    if job is None:
        raise HTTPException(404, f"job {job_id!r} not found")
    return job


@router.post("/jobs/{job_id}/cancel")
def cancel_job_endpoint(job_id: str) -> dict:
    """Cooperative cancel: state flipped to 'cancelled' so the running
    pipeline bails at the next phase checkpoint."""
    result = job_runner.cancel_job(get_db_path(), job_id)
    if result is None:
        raise HTTPException(404, f"job {job_id!r} not found")
    return result


@router.delete("/jobs/{job_id}")
def delete_job_endpoint(job_id: str) -> dict:
    """Hard-delete a job row. Refuses while the job is actively running;
    cancel + wait first. Already-written profiles / works are kept."""
    result = job_runner.delete_job(get_db_path(), job_id)
    if result is None:
        raise HTTPException(404, f"job {job_id!r} not found")
    if not result.get("deleted"):
        raise HTTPException(
            409,
            result.get("reason") or "cannot delete a running job",
        )
    return result


@router.get("/platforms")
def list_platforms() -> dict:
    """Distinct ``platform`` values from the crawler DB. ``key`` is the
    raw crawler-side slug (qidian / fanqie / …) — kept verbatim because
    downstream cache keys / job filters use it. ``label`` is translated
    to the canonical Chinese display name through
    ``platform_aliases.canonicalize_platform`` so the UI dropdown shows
    起点 / 番茄 instead of qidian / fanqie."""
    import sqlite3 as _sqlite3
    from pathlib import Path as _Path
    from ui.backend.app.utils import resolve_crawler_db_path
    from ui.backend.app.services.platform_aliases import canonicalize_platform
    crawler_db_path = resolve_crawler_db_path()
    if not crawler_db_path or not _Path(crawler_db_path).exists():
        return {"platforms": [], "warning": "crawler DB not configured"}
    try:
        with _sqlite3.connect(crawler_db_path) as con:
            con.row_factory = _sqlite3.Row
            rows = con.execute(
                "SELECT platform, COUNT(*) AS book_count "
                "FROM novels GROUP BY platform "
                "ORDER BY book_count DESC"
            ).fetchall()
        return {"platforms": [
            {
                "key":        r["platform"],
                "label":      canonicalize_platform(r["platform"]) or r["platform"],
                "book_count": r["book_count"],
            }
            for r in rows if r["platform"]
        ]}
    except _sqlite3.OperationalError as e:
        return {"platforms": [], "warning": f"crawler db read failed: {e}"}


@router.get("/aggregated-stats")
def get_aggregated_stats(platform: str = Query(...), category: str = Query(...)) -> dict:
    """Return the latest ``category_aggregated_stats`` row (or empty
    fields when none). This is the data the prompt's
    ``platform_market`` / 'market overview' loaders read at generation
    time — exposing it here lets the UI show users exactly what would
    be injected into a chapter prompt."""
    import sqlite3 as _sqlite3
    try:
        with _sqlite3.connect(get_db_path()) as con:
            con.row_factory = _sqlite3.Row
            row = con.execute(
                "SELECT * FROM category_aggregated_stats "
                "WHERE platform = ? AND category = ? "
                "ORDER BY aggregated_at DESC LIMIT 1",
                (platform, category),
            ).fetchone()
    except _sqlite3.OperationalError:
        return {"platform": platform, "category": category, "stats": None}
    return {
        "platform": platform,
        "category": category,
        "stats": dict(row) if row else None,
    }


@router.get("/categories")
def list_categories(platform: str = Query("")) -> dict:
    """Distinct rank_family × rank_sub_cat from the crawler DB."""
    import sqlite3 as _sqlite3
    from pathlib import Path as _Path
    from ui.backend.app.utils import resolve_crawler_db_path
    crawler_db_path = resolve_crawler_db_path()
    if not crawler_db_path or not _Path(crawler_db_path).exists():
        return {"categories": [], "warning": "crawler DB not configured"}
    try:
        with _sqlite3.connect(crawler_db_path) as con:
            con.row_factory = _sqlite3.Row
            if platform:
                rows = con.execute(
                    "SELECT rank_family, rank_sub_cat, COUNT(*) AS list_count "
                    "FROM rank_lists WHERE platform = ? "
                    "GROUP BY rank_family, rank_sub_cat "
                    "ORDER BY list_count DESC",
                    (platform,),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT rank_family, rank_sub_cat, COUNT(*) AS list_count "
                    "FROM rank_lists "
                    "GROUP BY rank_family, rank_sub_cat "
                    "ORDER BY list_count DESC"
                ).fetchall()
        out = []
        for r in rows:
            fam = r["rank_family"] or "未知"
            sub = r["rank_sub_cat"] or ""
            label = f"{fam} · {sub}" if sub else fam
            key = sub or fam
            out.append({"key": key, "label": label,
                        "rank_family": fam, "rank_sub_cat": sub,
                        "list_count": r["list_count"]})
        return {"categories": out}
    except _sqlite3.OperationalError as e:
        return {"categories": [], "warning": f"crawler db read failed: {e}"}


def _load_rep_works(
    platform: str, category: str, work_ids: list[str] | None = None,
) -> list[dict]:
    """Selected representative works, hydrated with 书名/作者/简介/标签
    from the crawler DB. Auto-runs selection when the pool is empty
    (spec 机制1/机制2). When ``work_ids`` is given, filters to that set."""
    works = representative_selector.list_selected(
        get_db_path(), platform, category, include_holdout=False,
    )
    if not works:
        try:
            from ..utils import resolve_crawler_db_path as _rcdp_sel
            import zlib as _zlib
            # Stable seed → reproducible holdout/selection across repeated
            # clicks (spec 机制5) instead of a different work set each time.
            _seed = _zlib.crc32(f"{platform}:{category}".encode("utf-8"))
            representative_selector.select(
                get_db_path(), platform, category,
                crawler_db=_rcdp_sel(), seed=_seed,
            )
            works = representative_selector.list_selected(
                get_db_path(), platform, category, include_holdout=False,
            )
        except Exception as _sel_err:
            logger.debug("auto representative selection failed: %s", _sel_err)

    if work_ids:
        keep = set(work_ids)
        works = [w for w in works if str(w.get("work_id")) in keep]

    try:
        from ..utils import resolve_crawler_db_path as _rcdp
        import sqlite3 as _sq
        with _sq.connect(_rcdp()) as _con:
            _con.row_factory = _sq.Row
            for w in works:
                nid = str(w.get("source_db_novel_id") or "")
                if not nid:
                    continue
                row = _con.execute(
                    "SELECT n.author, n.intro, n.main_category, n.total_words, "
                    "       nt.title FROM novels n "
                    "LEFT JOIN novel_titles nt ON nt.novel_uid = n.novel_uid "
                    " AND nt.is_primary = 1 WHERE n.novel_uid = ?",
                    (nid,),
                ).fetchone()
                if row:
                    w.setdefault("title", row["title"])
                    w.setdefault("author", row["author"])
                    w.setdefault("intro", row["intro"])
                    w.setdefault("main_category", row["main_category"])
                    w.setdefault("total_words", row["total_words"])
                tag_rows = _con.execute(
                    "SELECT t.tag_name FROM tags t "
                    "JOIN novel_tag_map m ON m.tag_id = t.tag_id "
                    "WHERE m.novel_uid = ? LIMIT 6",
                    (nid,),
                ).fetchall()
                if tag_rows:
                    w.setdefault("tags", [r["tag_name"] for r in tag_rows])
    except Exception as _hyd_err:
        logger.debug("work hydration skipped: %s", _hyd_err)
    return works


def _head_tail(text: str, head: int, tail: int) -> str:
    """开头 N 字 + 结尾 M 字 (spec 高级特征提取 节选改为前2章开头+结尾)."""
    t = (text or "").strip().replace("\r", "")
    if len(t) <= head + tail:
        return t
    return f"{t[:head].strip()}\n……（中略）……\n{t[-tail:].strip()}"


def _first2_excerpt(chapters: dict[int, str], head: int = 600, tail: int = 400) -> str:
    """前 2 章各取 开头+结尾 拼成节选块。"""
    out: list[str] = []
    for cn in (1, 2):
        body = chapters.get(cn)
        if body:
            out.append(f"[第{cn}章 开头+结尾]\n{_head_tail(body, head, tail)}")
    return "\n\n".join(out)


@router.get("/representative-candidates")
def representative_candidates(
    platform: str = Query(...),
    category: str = Query(default=""),
) -> dict:
    """整平台多维度代表作（总排行最高 / 上榜最多 / 热度最高 / 新书抽样）+ 每部
    前2章「开头+结尾」节选，供高级特征提取页展示并让用户 select/deselect，再据
    勾选生成 prompt / 调 API。``category`` 留空＝整平台（用户只选平台）。"""
    works = _load_rep_works(platform, category)
    out: list[dict] = []
    try:
        from ..utils import resolve_crawler_db_path
        from ..services.market_extractor import chapter_fetcher
        crawler_db = resolve_crawler_db_path()
        for w in works:
            nid = str(w.get("source_db_novel_id") or "")
            chapters = (
                chapter_fetcher.fetch_first_n_chapters(crawler_db, nid, n=2)
                if nid else {}
            )
            tags = w.get("tags") or []
            out.append({
                "work_id": w.get("work_id"),
                "source_db_novel_id": nid,
                "title": w.get("title") or nid or "(无题)",
                "author": w.get("author") or "—",
                "category": w.get("main_category") or category or "—",
                "total_words": w.get("total_words"),
                "tags": tags if isinstance(tags, list) else [],
                "rank_score": w.get("rank_score"),
                "reason": w.get("selection_reason") or "",
                "is_holdout": w.get("is_holdout"),
                "excerpt": _first2_excerpt(chapters),
                "has_chapters": bool(chapters),
            })
    except Exception as _e:
        logger.debug("representative-candidates excerpt build failed: %s", _e)
        for w in works:
            out.append({
                "work_id": w.get("work_id"),
                "title": w.get("title") or "(无题)",
                "author": w.get("author") or "—",
                "category": w.get("main_category") or category or "—",
                "tags": w.get("tags") if isinstance(w.get("tags"), list) else [],
                "reason": w.get("selection_reason") or "",
                "excerpt": "", "has_chapters": False,
            })
    return {"platform": platform, "category": category, "candidates": out}


# spec 2.1.3.2 高级特征提取 — 专有名词（原"生造词Step2"）+ 行文风格七组
# (A1-G2) 的输出契约。机制4: 专有名词 与行文风格组装进同一个 prompt 一次产出。
#
# 重要约束（写在 prompt 里强调）：
#  · 行文风格必须输出"该平台×榜单的通用风格画像"，不能列具体作品里的人物
#    清单或人物关系网 —— 否则下游正文生成会被锁死到那几本参考书上。
#  · B_social 因此只保留聚焦度 / 反派类型这类抽象描述，B1_network（前5章
#    登场角色清单+关系）被删除。
_ADVANCED_SCHEMA_SPEC = (
    "## 输出要求\n\n"
    "请严格输出**纯 JSON**（不要 markdown 围栏、不要前后多余文字），"
    "包含以下顶层字段；缺一不可：\n\n"
    "```json\n"
    "{\n"
    '  "neologism_step2": {\n'
    '    "proper_nouns": ["复核确认的专有名词/虚构概念，如 灰雾、源石…"],\n'
    '    "person_names": ["人名"],\n'
    '    "place_names": ["地名"],\n'
    '    "naming_patterns": "该榜单专有名词的常见构词模式（如 单字+宗/门、'
    '叠音、古风双字、音译外来词…）",\n'
    '    "common_chars": ["高频用字，如 玄、天、灵、域…"]\n'
    "  },\n"
    '  "style_dimensions": {\n'
    '    "A_protagonist": {\n'
    '      "A1_appearance": "主角登场的通用模式：常见登场位置（第几章/段/句）、'
    '常见场景类型(动作/对话/描写/心理/旁白)、是否惯用第一句出场、首章戏份占比区间",\n'
    '      "A2_image": "实力定位的通用倾向(强势/弱势/隐藏强势/隐藏弱势/待发掘)、'
    '常见性格关键词(3-5个抽象标签)、外貌描述详细度、常见年龄段、是否有反差点",\n'
    '      "A3_cheat": "金手指类型分布(系统/血脉/重生记忆/神秘传承/特殊体质/灵宝/'
    '学霸记忆/模拟器/其它)、常见来源、出现章节区间、揭露程度、抽象描述",\n'
    '      "A4_agency": "Proactive/Reactive/Mixed 倾向、首个主动决策的常见章节、'
    '常见决策代价",\n'
    '      "A5_drive": "冲突类型分布(生存/复仇/保护/探索/称霸/还债/追寻/揭秘)、'
    '紧迫度、常见冲突对象类型、冲突方式(武力/智斗/政治/情感/混合)、抽象描述"\n'
    "    },\n"
    '    "B_social": {\n'
    '      "B2_ensemble": "主角聚焦度倾向(单主角/双主角/群像)、前5章登场角色总数区间、'
    '反派首次登场章节区间、反派类型(明面/潜在/体制/未现身)"\n'
    "    },\n"
    '    "C_world": {\n'
    '      "C1_type": "能力体系倾向、时空起点(现代/古代/异界/穿越/重生/未来)、'
    '跨界混合、体系新颖度",\n'
    '      "C2_unfold": "设定字数占比、铺展方式(信息倾倒/渐进揭露/随剧情自然/完全不解释)、'
    '第一章设定密度、是否前5章揭露核心设定",\n'
    '      "C3_contrast": "题材内常见突破点、反常识设定方向、抽象 selling point"\n'
    "    },\n"
    '    "D_hook": {\n'
    '      "D1_opening_hook": "开篇钩子类型分布(开篇即死/开篇成神/谜团式/极端处境/'
    '重生穿越/系统降临/末日/觉醒突破/误会/失忆…)、抽象钩子描述、常见触发位置",\n'
    '      "D2_early_payoff": "前5章是否惯用打脸、首次打脸常见章节、爽点类型分布'
    '(解气/装逼/揭秘/暧昧/复仇/救赎)、爽点密度(前5章平均几次)",\n'
    '      "D3_chapter_end_hooks": "前5章章末钩子类型分布(悬念/高潮/反转/揭示/'
    '平静收尾)、强度分布(强/中/弱)、回收周期(本章/2-5章/跨卷)"\n'
    "    },\n"
    '    "E_style": {\n'
    '      "E1_writing_style": "写作风格关键词(浮夸/酷/吐槽/冷酷/热血/文艺/黑色幽默/'
    '中二/老练/严肃/戏谑/装逼/反讽/古典/现代…)；只给关键词，不抄具体作品句子。",\n'
    '      "E2_emotion": "主导情绪倾向、情绪起伏度(持续高强度/张弛有度/持续低强度)、'
    '黑暗度(阳光/中性/灰暗/黑暗)"\n'
    "    },\n"
    '    "F_info": {\n'
    '      "F1_disclosure": "信息揭露策略倾向(全知预告/渐进揭露/反转伏笔/谜团驱动)、'
    '前5章伏笔密度、已揭露vs已埋设比例、章末是否常用悬念",\n'
    '      "F2_volume_concept": "第一卷常见目标/要解决的问题类型、是否第一章就锁定第一卷概念"\n'
    "    },\n"
    '    "G_pacing": {\n'
    '      "G1_rhythm": "节奏类型(紧凑/适中/缓慢)、章节字数区间、章节字数波动度",\n'
    '      "G2_early_strategy": "前5章事件数量区间、前5章主线事件数量、前5章是否有'
    '明显节奏起伏(如先慢后快)"\n'
    "    }\n"
    "  },\n"
    '  "profile_summary": "（200-400字）该平台×榜单整体画像：题材脉络、受众偏好、常见走向。",\n'
    '  "style_baseline": {\n'
    '    "narration_pov": "第三人称限知/第一人称/全知…",\n'
    '    "tone": "2-3个关键词", "language_register": "口语化/半文半白/网感强…",\n'
    '    "sentence_rhythm": "短句为主/长短交错/偏长句…",\n'
    '    "dialogue_ratio": 0.3, "vocabulary_features": ["高频词1","高频词2","高频词3"]\n'
    "  },\n"
    '  "signature_devices_description": "（200-400字）反复使用的招牌叙事手法，举具体手法名，'
    '不抄具体作品的人物 / 情节细节。",\n'
    '  "pacing_guidance": {\n'
    '    "first_chapter_words": "如 2000-3000", "chapter_words": "如 2500-3500",\n'
    '    "first_hook_chapter": "首爆点章", "antagonist_intro_chapter": "反派出场章",\n'
    '    "first_face_slap_chapter": "首次反击章", "info_release_strategy": "信息释放策略"\n'
    "  },\n"
    '  "recommended_openings": ["开篇套路1","开篇套路2","开篇套路3","开篇套路4"],\n'
    '  "loader_payload": "（备份字段，可填可不填）600-1200字、可被注入正文生成prompt '
    '的中文段落正文：第二人称对生成者说话，覆盖题材定位/视角人称/语言风格/句式节奏/对白比/'
    '字数/钩子爽点节奏/反派与首次反击时机/信息释放策略/招牌手法清单/可借鉴开篇套路；'
    '不出现书名作者名、不列举具体人物 / 关系网，不给起名建议；'
    '结尾以[写作时严格遵循以上风格基线]收束。"\n'
    "}\n"
    "```\n\n"
    "## 要求\n"
    "1. 所有结论须以上方真实统计与原文节选为依据，避免凭空泛谈。\n"
    "2. style_dimensions 的七组(A-G)与 neologism_step2 必须填写，是 JSON 对象；"
    "B_social 仅保留 B2_ensemble，禁止再输出 B1_network。\n"
    "3. **行文风格七组（style_dimensions）所有字段必须输出 \"该平台×榜单的通用画像\"**："
    "用倾向 / 区间 / 分布 / 抽象关键词描述，禁止粘贴具体作品里的人名 / 关系网 / 情节细节。"
    "下游 writer prompt 会复用这些字段，过细的人物信息会把生成锁死在参考书上。\n"
    "4. 专有名词（neologism_step2）只能列出该榜单代表作里复核确认存在的词；"
    "如果某一类（人名/地名/常见字）为空，留 [] 即可。\n"
    "5. 如果你具备联网搜索能力，请在网络中检索这些作品与该平台题材的公开信息，"
    "作为上方所给信息的补充，并确保所输出的信息真实可靠、不编造。\n"
    "6. 整体只输出 JSON，前后不带任何说明文字。\n"
)


@router.post("/manual-prompt")
def build_manual_prompt(body: dict = Body(...)) -> dict:
    """Assemble the LLM prompt for manual-mode usage. The user copies
    the returned prompt into a browser LLM, pastes the response back
    via /manual-submit. The prompt requests the full spec-2.1.3.2
    高级特征提取 schema: 专有名词（旧名 生造词Step2）+ 行文风格七组
    (A1-G2)，plus the platform-profile injection payload the loader reads."""
    platform = (body.get("platform") or "").strip()
    category = (body.get("category") or "").strip()   # 留空＝整平台
    work_ids = body.get("work_ids") or None
    if isinstance(work_ids, list):
        work_ids = [str(x) for x in work_ids] or None
    if not platform:
        raise HTTPException(400, "platform required")

    plat_cn = _platform_cn(platform)
    scope_cn = f"{plat_cn}" + (f" · {category}" if category else "（整平台）")
    works = _load_rep_works(platform, category, work_ids)

    def _fmt_work(w: dict, i: int) -> str:
        title = w.get("title") or w.get("source_db_novel_id") or w.get("work_id") or "(无题)"
        author = w.get("author") or "—"
        cat = w.get("main_category") or category
        words = w.get("total_words")
        words_disp = f"{round((words or 0) / 10000, 1)}万字" if words else "—"
        tags = w.get("tags") or w.get("tag_list") or ""
        if isinstance(tags, list):
            tags = "、".join(tags[:6])
        intro = (w.get("intro") or "").strip().replace("\n", " ")
        parts = [
            f"{i}. 《{title}》",
            f"   作者：{author} · 类目：{cat} · 体量：{words_disp}",
        ]
        if tags:
            parts.append(f"   标签：{tags}")
        if intro:
            parts.append(f"   简介：{intro}")
        return "\n".join(parts)

    # 含全部已选代表作（不再截断 top12）— 用户勾选多少就注入多少。
    work_block = "\n".join(_fmt_work(w, i + 1) for i, w in enumerate(works))
    if not work_block:
        work_block = "（暂无候选代表作 — 请凭你对该榜单的常识答题。）"

    # ── 真实数据注入 (spec 2.1.3.2): 开篇章节 NLP 统计 + 章节原文节选
    #    （改为前2章「开头+结尾」，覆盖更多作品而非单部长截断）──
    nlp_block = "（暂无已采集的开篇章节，无法计算统计）"
    excerpt_block = "（暂无已采集的章节原文）"
    try:
        from ..utils import resolve_crawler_db_path
        from ..services.market_extractor import chapter_fetcher
        from ..services.market_extractor.opening_stats import (
            compute_opening_stats, render_stats_for_prompt,
        )
        crawler_db = resolve_crawler_db_path()
        stat_rows: list[dict] = []
        excerpts: list[str] = []
        for w in works:
            novel_id = str(w.get("source_db_novel_id") or "")
            if not novel_id:
                continue
            chapters = chapter_fetcher.fetch_first_n_chapters(
                crawler_db, novel_id, n=5,
            )
            title = w.get("title") or novel_id
            for cn, text in chapters.items():
                stat_rows.append({"chapter_num": cn, "text": text, "title": title})
            block = _first2_excerpt(chapters, head=600, tail=400)
            if block and len(excerpts) < 20:
                excerpts.append(f"### 《{title}》\n{block}")
        if stat_rows:
            nlp_block = render_stats_for_prompt(
                compute_opening_stats(stat_rows, db_path=get_db_path()),
            )
        if excerpts:
            excerpt_block = "\n\n".join(excerpts)
    except Exception as _e:
        logger.debug("manual-prompt real-data injection skipped: %s", _e)

    # 平台高频词同步：读取「基础特征提取」(opening_nlp) 已缓存的结果 —— 用户每次在
    # 基础tab点「重新分析」即刷新该缓存，这里读出注入，确保 prompt 高频词与页面一致。
    keyword_block = ""
    try:
        from ..services import compute_cache
        from ..services.project_paths import get_db_path as _gdp
        from ..utils import crawler_db_version as _cdv
        env = compute_cache.get_or_compute(
            _gdp(), f"opening_nlp:{platform}", _cdv() or "x",
            lambda: {}, cached_only=True,
        )
        if env.get("state") == "ready" and isinstance(env.get("payload"), dict):
            tw = ((env["payload"].get("spec_stats") or {}).get("top_words")) or []
            kws = "、".join(
                f"{w.get('word')}({w.get('count')})"
                for w in tw[:40] if w.get("word")
            )
            if kws:
                keyword_block = (
                    "## 平台高频词（来自基础特征提取，跨多部作品统计，须作为风格基线的重要依据）\n\n"
                    + kws + "\n\n"
                )
    except Exception:
        pass

    prompt = (
        f"# 任务：为[{scope_cn}]做高级特征提取（专有名词 + 行文风格七组）\n\n"
        f"你是一名资深的网络文学市场分析师。下面给出{plat_cn}平台多维度精选的代表作"
        "清单（覆盖总排行最高、上榜最稳定、热度最高、新书等不同类型，以代表整个平台"
        "风格）、开篇章节的真实 NLP 统计、以及各作品前 2 章[开头+结尾]的原文节选。"
        "请综合分析后，按下列全部维度输出结构化 JSON。\n\n"
        "⚠️ 注意：本次提取的目标是『该平台×榜单的通用风格画像』，**不是**任何一本"
        "具体作品的设定簿。**禁止**在 style_dimensions / signature_devices_description "
        "等字段里粘贴具体作品的人物清单、关系网或情节细节 —— 用倾向 / 区间 / 抽象"
        "关键词替代。\n\n"
        f"## 代表作清单（已选 {len(works)} 部，全部纳入分析）\n\n"
        f"{work_block}\n\n"
        f"{keyword_block}"
        "## 开篇章节真实统计（脚本计算，含 生造词Step1 候选 — Step1 仍沿用旧名，"
        "Step2 已正式更名为『专有名词』）\n\n"
        f"{nlp_block}\n\n"
        "## 章节原文节选（各作品前2章 开头+结尾，用于风格与专有名词判断）\n\n"
        f"{excerpt_block}\n\n"
        + _ADVANCED_SCHEMA_SPEC
    )
    return {"prompt": prompt, "platform": platform, "category": category,
            "work_count": len(works),
            "work_ids": [w.get("work_id") for w in works]}


def _as_text(v: Any) -> str:
    """SQLite can only bind str/num/None — JSON-encode dict/list so the
    LLM returning ``style_baseline``/``pacing_guidance`` as objects (which
    it legitimately does) doesn't blow up the INSERT (was: ProgrammingError
    'type dict is not supported')."""
    import json as _json
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, (dict, list)):
        return _json.dumps(v, ensure_ascii=False)
    return str(v)


@router.post("/manual-submit")
def submit_manual_extraction(body: dict = Body(...)) -> dict:
    """Persist a manual-mode response as a new platform_profile row."""
    import uuid as _uuid, json as _json, sqlite3 as _sqlite3
    platform = (body.get("platform") or "").strip()
    category = (body.get("category") or "").strip()
    raw = (body.get("response_raw") or "").strip()
    if not platform or not raw:        # category 留空＝整平台（用户只选平台）
        raise HTTPException(400, "platform + response_raw required")
    parsed: dict = {}
    try:
        t = raw
        if t.startswith("```"):
            t = t.lstrip("`")
            if t.lower().startswith("json"):
                t = t[4:]
            t = t.strip()
            if t.endswith("```"):
                t = t[:-3].strip()
        parsed = _json.loads(t)
    except Exception:
        i, j = raw.find("{"), raw.rfind("}")
        if i >= 0 and j > i:
            try:
                parsed = _json.loads(raw[i:j + 1])
            except Exception:
                parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}
    if not parsed:
        raise HTTPException(
            422, "无法解析为 JSON — 请确认粘贴了大模型返回的完整 JSON 结果")
    # loader_payload is injected verbatim into generation prompts — it must
    # be the prose paragraph, not the whole JSON blob. Fall back to the raw
    # text only when the model omitted the field.
    loader_payload = _as_text(parsed.get("loader_payload") or "").strip() or raw
    profile_id = f"pp_manual_{_uuid.uuid4().hex[:10]}"
    from storage.market_extractor_schema import ensure_market_extractor_tables
    with _sqlite3.connect(get_db_path()) as con:
        ensure_market_extractor_tables(con)
        con.execute(
            """INSERT INTO platform_profiles
               (profile_id, platform, category, profile_version,
                profile_summary, style_baseline, signature_devices_description,
                pacing_guidance, recommended_openings_json,
                loader_payload, style_dimensions_json, neologism_step2_json,
                confidence_label,
                extraction_started_at, extraction_completed_at)
               VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, 'manual',
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            (
                profile_id, platform, category,
                _as_text(parsed.get("profile_summary", "")),
                _as_text(parsed.get("style_baseline", "")),
                _as_text(parsed.get("signature_devices_description", "")),
                _as_text(parsed.get("pacing_guidance", "")),
                _json.dumps(parsed.get("recommended_openings", []),
                            ensure_ascii=False),
                loader_payload,
                _as_text(parsed.get("style_dimensions")
                         or parsed.get("行文风格") or ""),
                _as_text(parsed.get("neologism_step2")
                         or parsed.get("生造词Step2") or ""),
            ),
        )
        con.commit()
    return {"profile_id": profile_id, "platform": platform,
            "category": category, "parsed_keys": list(parsed.keys())}


@router.post("/api-extract")
async def api_extract(body: dict = Body(...)) -> dict:
    """用配置的大模型 API 直接跑（可能已被用户手动编辑的）高级提取 prompt，返回原始
    回复，供前端预览/编辑后再走 /manual-submit 解析入库 —— 与「网页版」共用同一份 prompt，
    只是把「人工粘贴」换成「自动调用」。"""
    platform = (body.get("platform") or "").strip()
    prompt = (body.get("prompt") or "").strip()
    if not platform or not prompt:
        raise HTTPException(400, "platform + prompt required")
    from llm.call_site import LLMCallSite
    cs = LLMCallSite(
        call_site_id="market_extractor.advanced_api_extract",
        primary_role="post_commit",
        parsed_target_table="platform_profiles",
        default_max_tokens=8000, default_temperature=0.3,
    )
    try:
        raw = await cs.invoke(
            prompt=prompt,
            system="你是资深网络文学市场分析师。严格只输出 JSON，不要解释、不要 markdown 围栏。",
            project_id=platform, db_path=get_db_path(), manual_mode=False,
        )
    except Exception as e:
        raise HTTPException(502, f"大模型 API 调用失败：{e}")
    return {"response_raw": raw or ""}


# ─────────── representative works ───────────


@router.get("/representative-works")
def list_works(
    platform: str = Query(...),
    category: str = Query(...),
    include_holdout: bool = Query(default=False),
) -> dict:
    works = representative_selector.list_selected(
        get_db_path(), platform, category,
        include_holdout=include_holdout,
    )
    return {"platform": platform, "category": category, "works": works}


@router.post("/works/{work_id}/exclude")
def exclude_work(work_id: str) -> dict:
    ok = representative_selector.exclude_work(get_db_path(), work_id)
    if not ok:
        raise HTTPException(404, f"work {work_id!r} not found")
    return {"ok": True, "work_id": work_id, "selected_for_extraction": False}


# ─────────── inspect ───────────


@router.get("/chapter-features/{work_id}")
def list_chapter_features(work_id: str) -> dict:
    with sqlite3.connect(get_db_path()) as con:
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM chapter_features WHERE work_id = ? "
            "ORDER BY chapter_num",
            (work_id,),
        ).fetchall()]
    return {"work_id": work_id, "chapters": rows}


@router.get("/neologisms/{work_id}")
def list_neologisms(work_id: str) -> dict:
    with sqlite3.connect(get_db_path()) as con:
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM work_neologisms WHERE work_id = ? "
            "ORDER BY frequency_in_5_chapters DESC",
            (work_id,),
        ).fetchall()]
    return {"work_id": work_id, "neologisms": rows}


@router.get("/genre-dictionary/{category}")
def get_genre_dict(category: str) -> dict:
    words = sorted(dictionaries.load_genre_dict(category))
    return {"category": category, "word_count": len(words), "words": words}


# ─────────── platform profiles ───────────


@profiles_router.get("")
def list_profiles(
    platform: str | None = Query(default=None),
    category: str | None = Query(default=None),
) -> dict:
    where = []
    params: list = []
    if platform:
        where.append("platform = ?")
        params.append(platform)
    if category:
        where.append("category = ?")
        params.append(category)
    sql = "SELECT * FROM platform_profiles"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY valid_from DESC"
    with sqlite3.connect(get_db_path()) as con:
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(sql, params).fetchall()]
    return {"profiles": rows}


@profiles_router.get("/current")
def get_current_profile(
    platform: str = Query(...),
    category: str = Query(...),
) -> dict:
    with sqlite3.connect(get_db_path()) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM platform_profiles "
            "WHERE platform = ? AND category = ? "
            "AND superseded_by_profile_id IS NULL "
            "ORDER BY profile_version DESC LIMIT 1",
            (platform, category),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "no active profile for that platform/category")
    return dict(row)


@profiles_router.get("/{profile_id}")
def get_profile(profile_id: str) -> dict:
    with sqlite3.connect(get_db_path()) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM platform_profiles WHERE profile_id = ?",
            (profile_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(404, f"profile {profile_id!r} not found")
    return dict(row)
