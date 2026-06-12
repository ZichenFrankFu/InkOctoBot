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

from fastapi import APIRouter, Body, HTTPException, Query

from ..services.project_paths import get_db_path
from ..services.market_extractor import (
    dictionaries, job_runner, representative_selector,
)

logger = logging.getLogger("inkoctobot.routers.market_extractor_api")


router = APIRouter(prefix="/api/market-extractor", tags=["market-extractor"])
profiles_router = APIRouter(prefix="/api/platform-profiles", tags=["platform-profiles"])


# ─────────── jobs ───────────


@router.post("/jobs")
def create_job(body: dict = Body(...)) -> dict:
    platform = (body.get("platform") or "").strip()
    category = (body.get("category") or "").strip()
    crawler_db = (body.get("crawler_db") or "").strip() or None
    if not platform or not category:
        raise HTTPException(400, "platform + category required")
    job_id = job_runner.run_job_in_background(
        get_db_path(), platform, category, crawler_db=crawler_db,
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
    """Distinct ``platform`` values from the crawler DB. Uses the
    same path resolver as the rest of the market endpoints so the
    user-configured market-DB path takes effect."""
    import sqlite3 as _sqlite3
    from pathlib import Path as _Path
    from ui.backend.app.utils import resolve_crawler_db_path
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
            {"key": r["platform"], "label": r["platform"], "book_count": r["book_count"]}
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


@router.post("/manual-prompt")
def build_manual_prompt(body: dict = Body(...)) -> dict:
    """Assemble the LLM prompt for manual-mode usage. The user copies
    the returned prompt into a browser LLM, pastes the response back
    via /manual-submit. The resulting JSON must populate every column
    that the platform_market loader reads — most importantly
    ``loader_payload`` (the rendered directive that actually gets
    injected into chapter prompts at generation time)."""
    platform = (body.get("platform") or "").strip()
    category = (body.get("category") or "").strip()
    if not platform or not category:
        raise HTTPException(400, "platform + category required")

    works = representative_selector.list_selected(
        get_db_path(), platform, category, include_holdout=False,
    )
    # 真实数据选取机制: 池子为空时立即按 spec 机制1/机制2 从爬虫库
    # 选取代表作（高weight榜单高rank + 随机新书榜），再读一次。
    if not works:
        try:
            representative_selector.select(get_db_path(), platform, category)
            works = representative_selector.list_selected(
                get_db_path(), platform, category, include_holdout=False,
            )
        except Exception as _sel_err:
            logger.debug("auto representative selection failed: %s", _sel_err)

    # Hydrate pool rows with novel info from the crawler DB — the pool
    # only stores ids/scores; the prompt needs 书名/作者/简介/标签.
    try:
        from ..utils import resolve_crawler_db_path as _rcdp
        import sqlite3 as _sq
        _cdb = _rcdp()
        with _sq.connect(_cdb) as _con:
            _con.row_factory = _sq.Row
            for w in works:
                nid = str(w.get("source_db_novel_id") or "")
                if not nid:
                    continue
                row = _con.execute(
                    "SELECT n.author, n.intro, n.main_category, n.total_words, "
                    "       nt.title "
                    "FROM novels n "
                    "LEFT JOIN novel_titles nt ON nt.novel_uid = n.novel_uid "
                    " AND nt.is_primary = 1 "
                    "WHERE n.novel_uid = ?",
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
        if len(intro) > 140:
            intro = intro[:140] + "……"
        parts = [
            f"{i}. 《{title}》",
            f"   作者：{author} · 类目：{cat} · 体量：{words_disp}",
        ]
        if tags:
            parts.append(f"   标签：{tags}")
        if intro:
            parts.append(f"   简介：{intro}")
        return "\n".join(parts)

    work_block = "\n".join(_fmt_work(w, i + 1) for i, w in enumerate(works[:12]))
    if not work_block:
        work_block = "（暂无候选代表作 — 请凭你对该榜单的常识答题。）"

    # ── 真实数据注入 (spec 2.1.3.2): 开篇章节 NLP 统计 + 章节原文节选 ──
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
        for w in works[:10]:
            novel_id = str(w.get("source_db_novel_id") or "")
            if not novel_id:
                continue
            chapters = chapter_fetcher.fetch_first_n_chapters(
                crawler_db, novel_id, n=5,
            )
            for cn, text in chapters.items():
                stat_rows.append({"chapter_num": cn, "text": text})
            # 原文节选（更长更全 — 单次 LLM context 内最优化判断材料）：
            # top 5 部作品首章 ~1500 字；其中 top 2 再附第二章 ~600 字。
            title = w.get("title") or novel_id
            if len(excerpts) < 5 and chapters.get(1):
                excerpts.append(
                    f"### 《{title}》第一章节选\n"
                    f"{chapters[1].strip()[:1500]}……"
                )
                if len(excerpts) <= 2 and chapters.get(2):
                    excerpts.append(
                        f"### 《{title}》第二章节选\n"
                        f"{chapters[2].strip()[:600]}……"
                    )
        if stat_rows:
            nlp_block = render_stats_for_prompt(
                compute_opening_stats(stat_rows),
            )
        if excerpts:
            excerpt_block = "\n\n".join(excerpts)
    except Exception as _e:
        logger.debug("manual-prompt real-data injection skipped: %s", _e)

    prompt = (
        f"# 任务：为「{platform} × {category}」生成一份完整的「平台风格基线档案 (platform profile)」\n\n"
        "你是一名资深的网络文学市场分析师。下面给出该平台 × 榜单下的代表作清单、"
        "开篇章节的真实 NLP 统计、以及部分作品的章节原文节选，请综合分析后输出一份"
        "**结构化、可直接注入正文生成 prompt 的**平台风格档案。\n\n"
        f"## 代表作清单（top {min(len(works), 12)} / 共 {len(works)} 部）\n\n"
        f"{work_block}\n\n"
        "## 开篇章节分析（对已采集开篇章节的真实统计）\n\n"
        f"{nlp_block}\n\n"
        "## 章节原文节选（用于风格与生造词判断）\n\n"
        f"{excerpt_block}\n\n"
        "## 分析维度要求\n\n"
        "请按以下维度组织你的分析（输出仍压缩为下方 JSON 的 6 个字段）：\n"
        "1. 生造词Step2：从上面的生造词Step1候选与原文节选中复核真正的专有名词/"
        "人名/地名，总结该榜单生造词的常见模式与常见字\n"
        "2. 行文风格七组维度：主角维度（登场位置/形象/金手指/能动性/驱动力）、"
        "社会维度（关系网络/配角群像）、世界维度（世界观类型/铺展策略/反差点）、"
        "钩子维度（开篇钩子/早期爽点/章末钩子）、风格维度（写作风格关键词/情绪基调）、"
        "信息维度（信息揭露策略/第一卷概念锁定）、节奏维度（节奏类型/前期节奏策略）\n"
        "3. 所有结论须以上方真实统计与原文节选为依据，避免凭空泛谈\n\n"
        "## 输出要求\n\n"
        "请严格输出**纯 JSON**（不要 markdown 围栏、不要前后多余文字），并包含以下 6 个字段；缺一不可：\n\n"
        "```\n"
        "{\n"
        '  "profile_summary": "（200-400字）该平台 × 榜单的整体画像：主流题材脉络、受众偏好、'
        '常见走向。不要罗列书名。",\n\n'
        '  "style_baseline": {\n'
        '    "narration_pov": "第三人称限知 / 第一人称 / 全知…（择一为主）",\n'
        '    "tone": "热血 / 阴郁 / 轻松 / 冷峻…（2-3个关键词）",\n'
        '    "language_register": "口语化 / 半文半白 / 网感强 / 正剧…",\n'
        '    "sentence_rhythm": "短句为主 / 长短交错 / 偏长句…",\n'
        '    "dialogue_ratio": "约X%（0-1之间的数）",\n'
        '    "vocabulary_features": ["该题材高频词1", "高频词2", "高频词3"]\n'
        '  },\n\n'
        '  "signature_devices_description": "（200-400字）该榜单代表作反复使用的招牌叙事手法：'
        '常见钩子、爽点机制、人设套路、伏笔结构、反转节奏等。请举具体手法名，不要泛泛而谈。",\n\n'
        '  "pacing_guidance": {\n'
        '    "first_chapter_words": "建议首章字数区间，如 2000-3000",\n'
        '    "chapter_words": "常态章节字数区间，如 2500-3500",\n'
        '    "first_hook_chapter": "首个爆点应在第几章前出现",\n'
        '    "antagonist_intro_chapter": "首位反派应在第几章前出场",\n'
        '    "first_face_slap_chapter": "首次打脸/反击应在第几章前到位",\n'
        '    "info_release_strategy": "信息释放策略：一次性铺陈 / 逐步揭示 / 悬念驱动…"\n'
        '  },\n\n'
        '  "recommended_openings": [\n'
        '    "开篇套路1（一句话描述）",\n'
        '    "开篇套路2",\n'
        '    "开篇套路3",\n'
        '    "开篇套路4"\n'
        '  ],\n\n'
        '  "loader_payload": "★ 最关键字段 ★ 这是会被**逐字注入正文生成 prompt** 的'
        '「平台风格基线」段落正文。要求：\\n'
        '- 600-1200 字的中文段落（不是 JSON、不是 markdown 列表，是连贯成段的指令性正文）；\\n'
        '- 第二人称对生成者说话，例如「在写本章时请注意…」「该平台读者偏好…」「叙述请保持…」；\\n'
        '- 必须覆盖：题材定位、视角与人称、语言风格、句式节奏、对白比、首章/常态章字数、'
        '钩子与爽点节奏、反派出场与首次反击的时机、信息释放策略、招牌叙事手法清单、'
        '可借鉴的开篇套路；\\n'
        '- 不要出现具体书名、作者名、品牌名；\\n'
        '- 不要给出取名/起名建议；\\n'
        '- 结尾给一句「写作时严格遵循以上风格基线，不要写成其他平台/榜单的风格」收束。"\n'
        "}\n"
        "```\n\n"
        "## 校验清单\n"
        "1. 所有 6 个字段都已填写，没有空字符串、没有占位符 (`...`)。\n"
        "2. `style_baseline` 和 `pacing_guidance` 是 JSON 对象，不是字符串。\n"
        "3. `recommended_openings` 至少 4 条。\n"
        "4. `loader_payload` 长度 ≥ 600 字，是连续中文段落而非 JSON / 列表。\n"
        "5. 整体只输出 JSON，前后不带任何说明文字。\n"
    )
    return {"prompt": prompt, "platform": platform, "category": category,
            "work_count": len(works)}


@router.post("/manual-submit")
def submit_manual_extraction(body: dict = Body(...)) -> dict:
    """Persist a manual-mode response as a new platform_profile row."""
    import uuid as _uuid, json as _json, sqlite3 as _sqlite3
    platform = (body.get("platform") or "").strip()
    category = (body.get("category") or "").strip()
    raw = (body.get("response_raw") or "").strip()
    if not platform or not category or not raw:
        raise HTTPException(400, "platform + category + response_raw required")
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
    profile_id = f"pp_manual_{_uuid.uuid4().hex[:10]}"
    with _sqlite3.connect(get_db_path()) as con:
        con.execute(
            """INSERT INTO platform_profiles
               (profile_id, platform, category, profile_version,
                profile_summary, style_baseline, signature_devices_description,
                pacing_guidance, recommended_openings_json,
                loader_payload, confidence_label,
                extraction_started_at, extraction_completed_at)
               VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, 'manual',
                       CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)""",
            (
                profile_id, platform, category,
                parsed.get("profile_summary", ""),
                parsed.get("style_baseline", ""),
                parsed.get("signature_devices_description", ""),
                parsed.get("pacing_guidance", ""),
                _json.dumps(parsed.get("recommended_openings", []),
                            ensure_ascii=False),
                raw,
            ),
        )
        con.commit()
    return {"profile_id": profile_id, "platform": platform,
            "category": category, "parsed_keys": list(parsed.keys())}


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
