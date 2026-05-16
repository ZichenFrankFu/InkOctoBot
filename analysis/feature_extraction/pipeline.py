"""
Feature extraction pipeline.

Orchestrates: chapter split → style fingerprint → narrative structure
→ character extraction → rhythm analysis.
Results written back to reference_works JSON columns.
"""
from __future__ import annotations
import json, logging, re, sqlite3, time
from pathlib import Path
from typing import Any

logger = logging.getLogger("inkoctobot.analysis.feature_extraction.pipeline")


def _diagnose_ai_error(e: Exception) -> str:
    """Translate a raw exception from the LLM call into an actionable hint
    the user can act on without reading a stack trace. Empty string when no
    hint applies."""
    cls = type(e).__name__
    msg = str(e).lower()
    if cls in ("ConnectionError", "ConnectError", "ReadTimeout", "ConnectTimeout"):
        return "无法连接到模型服务，请检查 Ollama 是否运行或网络连接"
    if "connection" in msg or "refused" in msg or "11434" in msg:
        return "Ollama 服务可能未启动（默认端口 11434）；运行 `ollama serve` 后重试"
    if ("api key" in msg or "apikey" in msg or "api_key" in msg
            or "unauthorized" in msg or "401" in msg or "invalid_api_key" in msg
            or cls == "AuthenticationError"):
        return "API Key 未配置或无效，请到「设置 → 模型供应商」检查"
    if "not found" in msg and "model" in msg:
        return "所配置的模型名不存在；请到「设置 → 模型供应商」选择已安装的模型"
    if "rate limit" in msg or "rate_limit" in msg or cls == "RateLimitError":
        return "请求频率过高被服务商限流，请稍后重试"
    if (cls == "JSONDecodeError"
            or "expecting value" in msg
            or ("json" in msg and ("expecting" in msg or "decode" in msg))):
        return ("模型返回的内容不是合法 JSON。常见原因：DeepSeek-R1 / o1 / Qwen-thinking "
                "类模型把 <think>...</think> 推理放在了答案前——更新到最新版本通常会自动剥离；"
                "也可换一个非 thinking 模型，或在「设置 → LLM Prompt」中加严输出格式约束。")
    if cls in ("ModuleNotFoundError", "ImportError"):
        return "依赖缺失，请在服务器侧 pip install 对应 SDK（如 openai/anthropic）"
    return ""


def _enrich_characters_with_appearance(chars: list[dict], chapters: list[dict]) -> list[dict]:
    """For each character returned by the AI extractor, compute deterministic
    appearance_chapters / appearance_word_count by scanning chapter content,
    and fill `first_seen_at` if the AI didn't provide one.

    Time-marker fallback: first chapter that contains the name → look for a
    date regex in its opening 200 chars; if found, use that; else "第 N 章".
    """
    try:
        from analysis.feature_extraction.narrative_extractor import _DATE_HINT_PAT
    except ImportError:
        _DATE_HINT_PAT = None  # type: ignore

    out: list[dict] = []
    for c in chars:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        ap_chapters = 0
        ap_chars = 0
        first_seen_idx: int | None = None
        for i, ch in enumerate(chapters, start=1):
            content = ch.get("content", "")
            if name in content:
                ap_chapters += 1
                ap_chars += len(content)
                if first_seen_idx is None:
                    first_seen_idx = i

        # Time marker: prefer AI-provided, else derive from chapter scan
        first_seen = (c.get("first_seen_at") or "").strip()
        if not first_seen and first_seen_idx is not None:
            head = (chapters[first_seen_idx - 1].get("content") or "")[:200]
            if _DATE_HINT_PAT is not None:
                m = _DATE_HINT_PAT.search(head)
                if m:
                    first_seen = m.group(1).strip()
            if not first_seen:
                first_seen = f"第 {first_seen_idx} 章"

        out.append({
            "name": name,
            "mentions": int(c.get("mentions") or 0),
            "intro": (c.get("intro") or "").strip(),
            "speech_samples": list(c.get("speech_samples") or [])[:3],
            "appearance_chapters": ap_chapters,
            "appearance_word_count": ap_chars,
            "first_seen_at": first_seen,
        })
    return out


def _enrich_settings_with_timestamp(settings_items: list[dict], chapters: list[dict],
                                      segment_start_chapter: int) -> list[dict]:
    """Fill `first_introduced_at` on settings items when AI didn't provide one.
    Falls back to the segment's start chapter — we can't reliably localize
    a setting within a segment, but at least the marker is honest."""
    out: list[dict] = []
    for s in settings_items:
        ts = (s.get("first_introduced_at") or "").strip()
        if not ts:
            ts = f"第 {segment_start_chapter} 章"
        out.append({**s, "first_introduced_at": ts})
    return out


class FeatureExtractionPipeline:
    """Run feature extraction on a single reference work or batch."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    def run(self, ref_id: str) -> dict[str, Any]:
        from rag.reference_db import ReferenceDB

        rdb = ReferenceDB(self.db_path)
        work = rdb.get_work(ref_id)
        if not work:
            raise ValueError(f"reference work not found: {ref_id}")

        rdb.update_work(ref_id, preprocessing_status="processing")
        t0 = time.perf_counter()
        errors: list[str] = []

        text = self._load_text(work)
        if not text:
            rdb.update_work(ref_id, preprocessing_status="error")
            return {"ref_id": ref_id, "error": "no text content available"}

        chapters = self._split_chapters(text)
        logger.info("[FE] '%s': %d chapters loaded", work["title"], len(chapters))

        # style fingerprint
        fp: dict = {}
        try:
            from analysis.feature_extraction.nlp_stats import compute_style_fingerprint
            fp = compute_style_fingerprint(chapters)
        except Exception as e:
            errors.append(f"style: {e}")

        # narrative structure
        narr: dict = {}
        try:
            from analysis.feature_extraction.narrative_extractor import extract_narrative
            narr = extract_narrative(chapters)
        except Exception as e:
            errors.append(f"narrative: {e}")

        # characters
        chars: list = []
        try:
            from analysis.feature_extraction.nlp_stats import extract_characters
            chars = extract_characters(chapters)
        except Exception as e:
            errors.append(f"characters: {e}")

        # rhythm
        rhythm: dict = {}
        try:
            from analysis.feature_extraction.narrative_extractor import extract_rhythm
            rhythm = extract_rhythm(chapters)
        except Exception as e:
            errors.append(f"rhythm: {e}")

        # plot outline (depends on narrative analysis)
        plot: dict = {}
        try:
            from analysis.feature_extraction.narrative_extractor import extract_plot_outline
            plot = extract_plot_outline(chapters, narrative=narr)
        except Exception as e:
            errors.append(f"plot_outline: {e}")

        rdb.update_work(
            ref_id,
            style_fingerprint_json=json.dumps(fp, ensure_ascii=False),
            narrative_structure_json=json.dumps(narr, ensure_ascii=False),
            extracted_characters_json=json.dumps(chars, ensure_ascii=False),
            rhythm_template_json=json.dumps(rhythm, ensure_ascii=False),
            plot_outline_json=json.dumps(plot, ensure_ascii=False),
            preprocessing_status="done",
        )

        elapsed = time.perf_counter() - t0
        logger.info("[FE] '%s' done in %.1fs", work["title"], elapsed)
        return {
            "ref_id": ref_id, "title": work["title"],
            "chapters": len(chapters),
            "elapsed_s": round(elapsed, 2),
            "style_fingerprint": fp, "narrative": narr,
            "plot_outline": plot,
            "characters_count": len(chars), "errors": errors,
        }

    def run_all_pending(self) -> list[dict]:
        from rag.reference_db import ReferenceDB

        rdb = ReferenceDB(self.db_path)
        results: list[dict] = []
        for w in rdb.get_pending(limit=200):
            try:
                results.append(self.run(w["ref_id"]))
            except Exception as e:
                logger.error("[FE] failed %s: %s", w["ref_id"], e)
                rdb.update_work(w["ref_id"], preprocessing_status="error")
        return results

    # ── internal ──

    def _load_text(self, work: dict) -> str:
        # Try file_path first regardless of source type
        if work.get("file_path"):
            fp = Path(work["file_path"])
            if fp.exists():
                return fp.read_text(encoding="utf-8", errors="replace")
        if work.get("source") == "platform_crawl" and work.get("novel_uid"):
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT chapter_title, chapter_content "
                "FROM first_n_chapters WHERE novel_uid=? ORDER BY chapter_num",
                (work["novel_uid"],),
            ).fetchall()
            conn.close()
            return "\n\n".join(
                f"{r['chapter_title'] or ''}\n{r['chapter_content'] or ''}"
                for r in rows
            )
        return ""

    @staticmethod
    def _split_chapters(text: str) -> list[dict]:
        chap_pat = re.compile(
            r"^[　\s]*第[零一二三四五六七八九十百千万\d]+章[\s：:　]*(.*)",
            re.MULTILINE,
        )
        vol_pat = re.compile(
            r"^[　\s]*第[零一二三四五六七八九十百千万\d]+卷[\s：:　]*(.*)",
            re.MULTILINE,
        )
        matches = list(chap_pat.finditer(text))
        if len(matches) < 2:
            # fallback: chunk by ~3000 chars
            return [
                {"index": i, "title": f"段落{i+1}",
                 "content": text[i*3000:(i+1)*3000]}
                for i in range(max(1, len(text) // 3000))
            ]
        vol_marks = [(m.start(), m.group(0).strip()[:60]) for m in vol_pat.finditer(text)]
        chapters: list[dict] = []
        cur_vol = ""
        for i, m in enumerate(matches):
            end = matches[i+1].start() if i+1 < len(matches) else len(text)
            # advance cur_vol to the latest volume marker that appears before this chapter
            while vol_marks and vol_marks[0][0] <= m.start():
                cur_vol = vol_marks.pop(0)[1]
            chapters.append({
                "index": i,
                "title": m.group(0).strip()[:60],
                "volume": cur_vol,
                "content": text[m.end():end].strip(),
            })
        return chapters

    # ── Segment planning & per-segment extraction (incremental) ──

    DEFAULT_SEGMENT_CHARS = 100_000  # ~10万字 per chunk when no volumes

    @staticmethod
    def _detect_volumes(chapters: list[dict]) -> list[dict] | None:
        """Detect volume boundaries using the per-chapter `volume` tag set by
        _split_chapters. Returns list of {title, start_chapter, end_chapter}
        (1-based), or None if there are fewer than 2 distinct volumes."""
        if not chapters:
            return None
        cur_vol = None
        starts: list[tuple[int, str]] = []
        for i, ch in enumerate(chapters):
            vol = (ch.get("volume") or "").strip()
            if not vol:
                continue
            if vol != cur_vol:
                starts.append((i + 1, vol))
                cur_vol = vol
        if len(starts) < 2:
            return None
        volumes: list[dict] = []
        for i, (start, title) in enumerate(starts):
            end = (starts[i + 1][0] - 1) if i + 1 < len(starts) else len(chapters)
            volumes.append({"title": title or f"第{i+1}卷", "start_chapter": start, "end_chapter": end})
        return volumes

    def get_effective_plan(self, ref_id: str, chapters: list[dict],
                            segment_chars: int | None = None) -> dict[str, Any]:
        """Return the user's saved custom plan if present in segments_json,
        else fall back to the auto-detected plan. Use this from any code
        path that needs to honor user-edited volume titles / ranges."""
        from rag.reference_db import ReferenceDB
        try:
            work = ReferenceDB(self.db_path).get_work(ref_id)
            if work and work.get("segments_json"):
                state = json.loads(work["segments_json"])
                cp = state.get("custom_plan")
                if isinstance(cp, list) and len(cp) > 0:
                    # Re-derive char_count from the actual chapters so
                    # the UI stays accurate after the user resizes ranges.
                    cleaned: list[dict] = []
                    for i, seg in enumerate(cp):
                        if not isinstance(seg, dict):
                            continue
                        sc = max(1, int(seg.get("start_chapter") or 1))
                        ec = max(sc, int(seg.get("end_chapter") or sc))
                        ec = min(ec, len(chapters))
                        chars = sum(
                            len(chapters[j - 1].get("content") or "")
                            for j in range(sc, ec + 1)
                        )
                        cleaned.append({
                            "index": i,
                            "title": (seg.get("title") or f"第 {sc}–{ec} 章").strip(),
                            "start_chapter": sc,
                            "end_chapter": ec,
                            "chapter_count": ec - sc + 1,
                            "char_count": chars,
                        })
                    if cleaned:
                        return {
                            "type": state.get("type") or "custom",
                            "segments": cleaned,
                            "total_chapters": len(chapters),
                            "is_custom": True,
                        }
        except Exception as e:
            logger.warning("[plan] custom_plan read failed: %s", e)
        # No saved custom plan → return an EMPTY plan so the user creates
        # their own volumes. The auto-detected layout is still available
        # on demand via /segments/plan/auto_detect (it's a one-click
        # button, not the silent default).
        return {
            "type": "custom",
            "segments": [],
            "total_chapters": len(chapters),
            "is_custom": False,
        }

    def suggest_auto_plan(self, ref_id: str,
                           segment_chars: int | None = None) -> dict[str, Any]:
        """Compute the auto-detected plan (第X卷 markers OR ~100k-char chunks)
        without persisting it. The UI uses this for the "auto-suggest"
        button so users can adopt the suggestion as a starting point."""
        from rag.reference_db import ReferenceDB
        rdb = ReferenceDB(self.db_path)
        work = rdb.get_work(ref_id)
        if not work:
            raise ValueError(f"reference work not found: {ref_id}")
        text = self._load_text(work)
        chapters = self._split_chapters(text) if text else []
        plan = self.plan_segments(chapters, segment_chars=segment_chars)
        plan["is_custom"] = False
        plan["total_chapters"] = len(chapters)
        return plan

    def rename_segment_title(self, ref_id: str, index: int, title: str) -> dict[str, Any]:
        """Rename a single segment's title in-place without resetting any
        already-completed extraction results. Used by inline title edit in
        the timeline so renaming "第 1–8 章" → "1954 年" is non-destructive."""
        from rag.reference_db import ReferenceDB
        rdb = ReferenceDB(self.db_path)
        work = rdb.get_work(ref_id)
        if not work:
            raise ValueError(f"reference work not found: {ref_id}")
        try:
            state = json.loads(work.get("segments_json") or "{}")
        except Exception:
            state = {}
        if not isinstance(state, dict):
            state = {}
        cp = state.get("custom_plan")
        if not isinstance(cp, list) or not cp:
            raise ValueError("no custom plan to rename; create one first")
        if index < 0 or index >= len(cp):
            raise ValueError(f"segment index {index} out of range (0..{len(cp) - 1})")
        new_title = (title or "").strip()
        if not new_title:
            seg = cp[index]
            sc = int(seg.get("start_chapter") or 1)
            ec = int(seg.get("end_chapter") or sc)
            new_title = f"第 {sc}–{ec} 章"
        cp[index] = {**cp[index], "title": new_title}
        state["custom_plan"] = cp
        if isinstance(state.get("plan"), list) and index < len(state["plan"]):
            state["plan"][index] = {**state["plan"][index], "title": new_title}
        rdb.update_work(ref_id, segments_json=json.dumps(state, ensure_ascii=False))
        return {"ok": True, "index": index, "title": new_title}

    def save_custom_plan(self, ref_id: str, segments: list[dict],
                          plan_type: str = "custom") -> dict[str, Any]:
        """Persist a user-edited plan (volume titles + chapter ranges)
        into segments_json["custom_plan"]. Clears any per-segment
        extraction results because the segmentation has changed."""
        from rag.reference_db import ReferenceDB
        rdb = ReferenceDB(self.db_path)
        work = rdb.get_work(ref_id)
        if not work:
            raise ValueError(f"reference work not found: {ref_id}")
        # Validate the user payload — defensive, since clients may send
        # overlapping / out-of-order ranges and we need stable behavior.
        text = self._load_text(work)
        chapters = self._split_chapters(text) if text else []
        total = len(chapters)
        cleaned: list[dict] = []
        for i, seg in enumerate(segments):
            if not isinstance(seg, dict):
                continue
            try:
                sc = max(1, int(seg.get("start_chapter") or 1))
                ec = int(seg.get("end_chapter") or sc)
            except (TypeError, ValueError):
                continue
            if total > 0:
                sc = min(sc, total)
                ec = min(max(ec, sc), total)
            else:
                ec = max(ec, sc)
            title = str(seg.get("title") or f"第 {sc}–{ec} 章").strip()
            cleaned.append({
                "index": i,
                "title": title,
                "start_chapter": sc,
                "end_chapter": ec,
            })
        if not cleaned:
            raise ValueError("segments list is empty after validation")
        cleaned.sort(key=lambda x: x["start_chapter"])
        for i, seg in enumerate(cleaned):
            seg["index"] = i

        # Editing the plan invalidates prior per-segment results.
        new_state = {
            "type": plan_type,
            "plan": cleaned,
            "custom_plan": cleaned,
            "results": {},
            "completed": [],
        }
        rdb.update_work(
            ref_id,
            segments_json=json.dumps(new_state, ensure_ascii=False),
            preprocessing_status="pending",
        )
        return {
            "type": plan_type,
            "segments": [
                {**s,
                 "chapter_count": s["end_chapter"] - s["start_chapter"] + 1,
                 "char_count": sum(
                     len(chapters[j - 1].get("content") or "")
                     for j in range(s["start_chapter"], s["end_chapter"] + 1)
                 )}
                for s in cleaned
            ],
            "total_chapters": total,
            "is_custom": True,
        }

    def plan_segments(self, chapters: list[dict],
                      segment_chars: int | None = None) -> dict[str, Any]:
        """Auto-detect plan from chapter content. Use ``get_effective_plan``
        when you want to honor the user's saved custom plan.

        Returns ``{"type": "volumes"|"chunks", "segments": [{...}], "total_chapters": N}``.
        Volumes take priority; otherwise chapters are grouped into ~segment_chars chunks.
        """
        if not chapters:
            return {"type": "chunks", "segments": [], "total_chapters": 0}
        vols = self._detect_volumes(chapters)
        if vols is not None:
            for i, v in enumerate(vols):
                v["index"] = i
                v["chapter_count"] = v["end_chapter"] - v["start_chapter"] + 1
                v["char_count"] = sum(
                    len(chapters[j - 1].get("content") or "")
                    for j in range(v["start_chapter"], v["end_chapter"] + 1)
                )
            return {"type": "volumes", "segments": vols, "total_chapters": len(chapters)}

        # Chunk by chapter such that each chunk ≈ segment_chars characters
        limit = max(10_000, int(segment_chars or self.DEFAULT_SEGMENT_CHARS))
        segments: list[dict] = []
        cur_start = 1
        cur_chars = 0
        for i, ch in enumerate(chapters):
            cur_chars += len(ch.get("content") or "")
            is_last = (i == len(chapters) - 1)
            if cur_chars >= limit or is_last:
                start = cur_start
                end = i + 1
                segments.append({
                    "index": len(segments),
                    "title": f"第{start}–{end}章",
                    "start_chapter": start,
                    "end_chapter": end,
                    "chapter_count": end - start + 1,
                    "char_count": cur_chars,
                })
                cur_start = end + 1
                cur_chars = 0
        return {"type": "chunks", "segments": segments, "total_chapters": len(chapters)}

    async def compute_segment(self, ref_id: str, segment_index: int,
                                segment_chars: int | None = None,
                                use_ai: bool = True,
                                use_web_search: bool = False,
                                prompt_overrides: dict[str, str] | None = None) -> dict[str, Any]:
        """Extract features for one segment but DO NOT persist anything.

        Style fingerprint uses NLP (rule-based, fast, deterministic).
        Characters / settings / narrative / rhythm use AI via ModelRouter
        when use_ai is True, falling back to NLP rules on failure.
        Plot outline (chronicle skeleton) uses NLP rules and is then
        enriched by the AI characters/settings results.
        """
        from rag.reference_db import ReferenceDB
        import asyncio

        rdb = ReferenceDB(self.db_path)
        work = rdb.get_work(ref_id)
        if not work:
            raise ValueError(f"reference work not found: {ref_id}")

        text = self._load_text(work)
        if not text:
            return {"error": "no text content available"}

        all_chapters = self._split_chapters(text)
        plan = self.get_effective_plan(ref_id, all_chapters, segment_chars=segment_chars)
        segs = plan["segments"]
        if segment_index < 0 or segment_index >= len(segs):
            raise ValueError(f"segment_index out of range: {segment_index} (have {len(segs)})")
        seg = segs[segment_index]

        seg_chapters = [
            {**all_chapters[j - 1], "index": j - seg["start_chapter"]}
            for j in range(seg["start_chapter"], seg["end_chapter"] + 1)
        ]

        t0 = time.perf_counter()
        errors: list[str] = []
        warnings: list[str] = []

        from analysis.feature_extraction.nlp_stats import (
            compute_style_fingerprint, extract_characters,
        )
        from analysis.feature_extraction.narrative_extractor import (
            extract_narrative, extract_rhythm, extract_rhythm_v2,
            extract_plot_outline,
        )

        # Style: ALWAYS NLP (per spec)
        fp: dict = {}
        try:
            fp = compute_style_fingerprint(seg_chapters)
        except Exception as e:
            errors.append(f"style: {e}")

        # Get an AI router (lazy import; may fail if no LLM configured)
        router = None
        if use_ai:
            try:
                from models.router import ModelRouter
                router = ModelRouter()
            except Exception as e:
                detail = str(e).strip().replace("\n", " ")[:200] or type(e).__name__
                hint = _diagnose_ai_error(e)
                msg = f"AI 不可用，所有项回退 NLP · {detail}"
                if hint:
                    msg += f" · 提示：{hint}"
                warnings.append(msg)

        ai_methods_used: list[str] = []
        ai_methods_fallback: list[str] = []

        async def _ai_or_nlp(name: str, ai_fn, nlp_fn, *, prompt_key: str | None = None):
            """Try AI; on failure, fall back to NLP and record a warning.
            When prompt_key is set, the per-call override (if any) is
            passed through to the AI extractor as `prompt_override`.
            When the segment-level use_web_search flag is set, the call
            is routed through the reference_web_search role."""
            if router is None:
                ai_methods_fallback.append(name)
                return nlp_fn()
            try:
                override = (prompt_overrides or {}).get(prompt_key) if prompt_key else None
                kw: dict[str, Any] = {"use_web_search": use_web_search}
                if override is not None:
                    kw["prompt_override"] = override
                result = await ai_fn(seg_chapters, router, **kw)
                ai_methods_used.append(name)
                return result
            except Exception as e:
                logger.warning("[ai_extractor] %s failed (%s); falling back to NLP", name, e)
                detail = str(e).strip().replace("\n", " ")[:200] or type(e).__name__
                hint = _diagnose_ai_error(e)
                msg = f"{name}: AI 失败 → 回退 NLP · {detail}"
                if hint:
                    msg += f" · 提示：{hint}"
                warnings.append(msg)
                ai_methods_fallback.append(name)
                return nlp_fn()

        from analysis.feature_extraction import ai_extractor

        # Characters (AI preferred). After AI, intersect with chapter texts
        # to compute appearance_chapters / appearance_word_count deterministically.
        chars: list = []
        try:
            chars = await _ai_or_nlp(
                "characters",
                ai_extractor.ai_extract_characters,
                lambda: extract_characters(seg_chapters),
                prompt_key="reference.characters",
            )
            chars = _enrich_characters_with_appearance(chars, seg_chapters)
        except Exception as e:
            errors.append(f"characters: {e}")

        # Settings (AI preferred). No NLP fallback — return empty if AI fails.
        settings_items: list = []
        try:
            if router is not None:
                try:
                    settings_override = (prompt_overrides or {}).get("reference.settings")
                    kw: dict[str, Any] = {"use_web_search": use_web_search}
                    if settings_override is not None:
                        kw["prompt_override"] = settings_override
                    settings_items = await ai_extractor.ai_extract_settings(
                        seg_chapters, router, **kw,
                    )
                    ai_methods_used.append("settings")
                except Exception as e:
                    logger.warning("[ai_extractor] settings failed: %s", e)
                    detail = str(e).strip().replace("\n", " ")[:200] or type(e).__name__
                    hint = _diagnose_ai_error(e)
                    msg = f"settings: AI 失败，返回空 · {detail}"
                    if hint:
                        msg += f" · 提示：{hint}"
                    warnings.append(msg)
                    ai_methods_fallback.append("settings")
            else:
                ai_methods_fallback.append("settings")
            settings_items = _enrich_settings_with_timestamp(
                settings_items, seg_chapters, seg.get("start_chapter") or 1,
            )
        except Exception as e:
            errors.append(f"settings: {e}")

        # Rhythm v2 (AI preferred, merges narrative + rhythm + per-chapter features)
        rhythm: dict = {}
        try:
            rhythm = await _ai_or_nlp(
                "rhythm",
                ai_extractor.ai_extract_rhythm_v2,
                lambda: extract_rhythm_v2(seg_chapters),
                prompt_key="reference.rhythm",
            )
        except Exception as e:
            errors.append(f"rhythm: {e}")

        # Plot outline (chronicle skeleton — always NLP)
        # extract_plot_outline expects the legacy narrative shape; derive
        # it from the unified rhythm so the chronicle skeleton stays correct.
        plot: dict = {}
        try:
            narr_compat = {
                "opening_pattern": rhythm.get("opening_pattern", "character_intro"),
                "climax_positions": rhythm.get("climax_positions", []),
                "shuangdian": rhythm.get("shuangdian", []),
            }
            plot = extract_plot_outline(seg_chapters, narrative=narr_compat)
        except Exception as e:
            errors.append(f"plot_outline: {e}")

        return {
            "index": segment_index,
            "title": seg.get("title"),
            "start_chapter": seg.get("start_chapter"),
            "end_chapter": seg.get("end_chapter"),
            "char_count": seg.get("char_count"),
            "elapsed_s": round(time.perf_counter() - t0, 2),
            "errors": errors,
            "warnings": warnings,
            "ai_methods_used": ai_methods_used,
            "ai_methods_fallback": ai_methods_fallback,
            "style_fingerprint": fp,
            "characters": chars,
            "rhythm": rhythm,  # NOTE: rhythm now carries the unified narrative+rhythm shape
            "plot_outline": plot,
            "settings": settings_items,
        }

    def persist_segment(self, ref_id: str, result: dict) -> dict[str, Any]:
        """Persist a previously-computed segment result into segments_json."""
        from rag.reference_db import ReferenceDB
        rdb = ReferenceDB(self.db_path)
        work = rdb.get_work(ref_id)
        if not work:
            raise ValueError(f"reference work not found: {ref_id}")
        seg_index = result.get("index")
        if not isinstance(seg_index, int):
            raise ValueError("result.index missing or invalid")

        # Refresh plan to know total; honor any user-saved custom plan.
        text = self._load_text(work)
        chapters = self._split_chapters(text) if text else []
        plan = self.get_effective_plan(ref_id, chapters)
        segs = plan["segments"]

        try:
            existing = json.loads(work.get("segments_json") or "{}")
        except Exception:
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
        results = existing.get("results") or {}
        if not isinstance(results, dict):
            results = {}
        results[str(seg_index)] = result

        new_state = {
            "type": plan["type"],
            "plan": [
                {k: v for k, v in s.items() if k in (
                    "index", "title", "start_chapter", "end_chapter",
                    "chapter_count", "char_count",
                )}
                for s in segs
            ],
            # Preserve the user's saved custom plan across commits so it
            # keeps overriding auto-detection on subsequent extractions.
            **({"custom_plan": existing["custom_plan"]} if existing.get("custom_plan") else {}),
            "results": results,
            "completed": sorted(int(k) for k in results.keys()),
        }
        new_status = "done" if len(results) >= len(segs) else "processing"
        rdb.update_work(
            ref_id,
            segments_json=json.dumps(new_state, ensure_ascii=False),
            preprocessing_status=new_status,
        )

        # Fire-and-forget L1 indexing — best-effort, never blocks commit.
        # On any failure (missing deps, no embedding backend, etc.) we
        # log and move on; the user can re-trigger from the search page.
        try:
            import asyncio
            async def _bg_index_l1():
                try:
                    from rag.work_index import make_indexer
                    indexer = make_indexer(self.db_path)
                    await indexer.index_l1(ref_id)
                except Exception as e:
                    logger.warning("[work_index] L1 auto-index skipped: %s", e)
            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(_bg_index_l1())
        except Exception as e:
            logger.debug("[work_index] auto-index hook skipped: %s", e)

        return {
            "ref_id": ref_id,
            "segment_index": seg_index,
            "total_segments": len(segs),
            "completed_count": len(results),
            "all_done": new_status == "done",
        }

    async def run_segment(self, ref_id: str, segment_index: int,
                            segment_chars: int | None = None,
                            use_ai: bool = True,
                            use_web_search: bool = False,
                            prompt_overrides: dict[str, str] | None = None) -> dict[str, Any]:
        """Compute + persist in one call (kept for backwards compat)."""
        result = await self.compute_segment(
            ref_id, segment_index, segment_chars=segment_chars, use_ai=use_ai,
            use_web_search=use_web_search, prompt_overrides=prompt_overrides,
        )
        if "error" in result and len(result) <= 2:
            return {"ref_id": ref_id, **result}
        info = self.persist_segment(ref_id, result)
        return {**info, "result": result}

    def finalize_segments(self, ref_id: str) -> dict[str, Any]:
        """Merge all per-segment results into top-level fields:
            - style fingerprint: weighted-average by char_count
            - narrative: aggregate climaxes/shuangdian with chapter offsets
            - characters: merge by name (sum mentions, union samples/relationships)
            - rhythm: concat tension_curve, concat pacing_segments
            - plot_outline: concat epochs/periods
        """
        from rag.reference_db import ReferenceDB
        rdb = ReferenceDB(self.db_path)
        work = rdb.get_work(ref_id)
        if not work:
            raise ValueError(f"reference work not found: {ref_id}")
        try:
            state = json.loads(work.get("segments_json") or "{}")
        except Exception:
            state = {}
        results = state.get("results") or {}
        if not results:
            return {"ref_id": ref_id, "error": "no segments to merge"}

        items = [results[k] for k in sorted(results.keys(), key=int)]

        # ── style fingerprint: weighted average ──
        total_chars = sum(max(it.get("char_count") or 1, 1) for it in items)
        agg_fp_keys = (
            "avg_sentence_length", "dialogue_ratio", "description_density",
            "rhetoric_frequency", "vocab_complexity",
        )
        agg_fp: dict = {k: 0.0 for k in agg_fp_keys}
        pacing_acc = {"fast": 0.0, "medium": 0.0, "slow": 0.0}
        for it in items:
            fp = it.get("style_fingerprint") or {}
            w = max(it.get("char_count") or 1, 1)
            for k in agg_fp_keys:
                v = fp.get(k)
                if isinstance(v, (int, float)):
                    agg_fp[k] += float(v) * w
            pp = fp.get("pacing_profile") or {}
            for k in pacing_acc:
                v = pp.get(k)
                if isinstance(v, (int, float)):
                    pacing_acc[k] += float(v) * w
        for k in agg_fp:
            agg_fp[k] = round(agg_fp[k] / total_chars, 4)
        pacing_total = sum(pacing_acc.values()) or 1.0
        agg_fp["pacing_profile"] = {k: round(v / pacing_total, 3) for k, v in pacing_acc.items()}

        # ── characters: merge by name (sum mentions/appearances, longest intro wins) ──
        char_map: dict[str, dict] = {}
        for it in items:
            for ch in (it.get("characters") or []):
                name = (ch or {}).get("name") or ""
                if not name:
                    continue
                entry = char_map.setdefault(name, {
                    "name": name, "mentions": 0,
                    "intro": "",
                    "speech_samples": [],
                    "appearance_chapters": 0,
                    "appearance_word_count": 0,
                    "role_tag": "",
                })
                entry["mentions"] += int(ch.get("mentions") or 0)
                entry["appearance_chapters"] += int(ch.get("appearance_chapters") or 0)
                entry["appearance_word_count"] += int(ch.get("appearance_word_count") or 0)
                new_intro = (ch.get("intro") or "").strip()
                if new_intro and len(new_intro) > len(entry["intro"]):
                    entry["intro"] = new_intro
                fs = (ch.get("first_seen_at") or "").strip()
                if fs and not entry.get("first_seen_at"):
                    entry["first_seen_at"] = fs
                # role_tag: stronger labels (主角/女主角/反派) trump weaker
                # ones (路人/其他) when merging across segments.
                rt = (ch.get("role_tag") or "").strip()
                if rt:
                    _RANK = {
                        "主角": 100, "女主角": 90, "反派": 80,
                        "男配": 60, "女配": 60, "师长": 60,
                        "重要配角": 40, "路人": 20, "其他": 10,
                    }
                    if _RANK.get(rt, 30) >= _RANK.get(entry.get("role_tag") or "", 0):
                        entry["role_tag"] = rt
                for s in (ch.get("speech_samples") or [])[:5]:
                    if s and s not in entry["speech_samples"] and len(entry["speech_samples"]) < 5:
                        entry["speech_samples"].append(s)
        # Sort: protagonist tags first, then by appearance_chapters desc,
        # then by mentions desc (so chapter-coverage outranks raw count).
        _ROLE_ORDER = {
            "主角": 0, "女主角": 1, "反派": 2,
            "男配": 3, "女配": 3, "师长": 3,
            "重要配角": 4, "路人": 8, "其他": 9, "": 9,
        }
        agg_chars = sorted(
            char_map.values(),
            key=lambda x: (
                _ROLE_ORDER.get(x.get("role_tag") or "", 9),
                -int(x.get("appearance_chapters") or 0),
                -int(x.get("mentions") or 0),
            ),
        )

        # ── rhythm_json (unified): merge per-segment rhythm v2 blocks with
        # chapter offsets so the resulting structure references global chapter
        # numbers across the whole work.
        agg_rhythm: dict = {
            "coverage": {"chapters": 0, "chars": 0},
            "opening_pattern": "",
            "climax_positions": [],
            "shuangdian": [],
            "chapter_features": [],
            "info_density_curve": [],
            "pacing_segments": [],
        }
        opening_set = False
        for it in items:
            offset = (it.get("start_chapter") or 1) - 1
            r = it.get("rhythm") or {}
            if not opening_set:
                op = r.get("opening_pattern")
                if op:
                    agg_rhythm["opening_pattern"] = op
                    opening_set = True
            for c in (r.get("climax_positions") or []):
                if isinstance(c, int):
                    agg_rhythm["climax_positions"].append(c + offset)
            for sd in (r.get("shuangdian") or []):
                if isinstance(sd, dict) and isinstance(sd.get("chapter"), int):
                    agg_rhythm["shuangdian"].append({
                        "chapter": sd["chapter"] + offset,
                        "type": sd.get("type", ""),
                    })
            for cf in (r.get("chapter_features") or []):
                if isinstance(cf, dict) and isinstance(cf.get("chapter"), int):
                    agg_rhythm["chapter_features"].append({
                        **cf,
                        "chapter": cf["chapter"] + offset,
                    })
            agg_rhythm["info_density_curve"].extend(r.get("info_density_curve") or [])
            for ps in (r.get("pacing_segments") or []):
                if isinstance(ps, dict):
                    agg_rhythm["pacing_segments"].append({
                        **ps,
                        "start": (ps.get("start") or 1) + offset,
                        "end": (ps.get("end") or 1) + offset,
                    })
        agg_rhythm["coverage"] = {
            "chapters": max(
                (it.get("end_chapter") or 0) for it in items
            ),
            "chars": sum(int(it.get("char_count") or 0) for it in items),
        }

        # ── plot outline: each segment becomes its own epoch ──
        agg_plot_epochs: list[dict] = []
        for it in items:
            po = it.get("plot_outline") or {}
            for ep in (po.get("epochs") or []):
                title = ep.get("title") or it.get("title") or ""
                agg_plot_epochs.append({"title": title, "periods": ep.get("periods") or []})
        agg_plot = {"logline": "", "epochs": agg_plot_epochs}

        # ── settings: dedupe by (category, title); longest content wins ──
        settings_map: dict[tuple[str, str], dict] = {}
        for it in items:
            for s in (it.get("settings") or []):
                if not isinstance(s, dict):
                    continue
                key = ((s.get("category") or "other"), (s.get("title") or "").strip())
                if not key[1]:
                    continue
                cur = settings_map.get(key)
                fi = (s.get("first_introduced_at") or "").strip()
                if cur is None or len((s.get("content") or "")) > len(cur.get("content") or ""):
                    settings_map[key] = {
                        "category": key[0],
                        "title": key[1],
                        "content": (s.get("content") or "").strip(),
                        "hidden": (s.get("hidden") or "").strip(),
                        "first_introduced_at": fi or (cur.get("first_introduced_at") if cur else ""),
                    }
                else:
                    if s.get("hidden") and not cur.get("hidden"):
                        cur["hidden"] = (s.get("hidden") or "").strip()
                    if fi and not cur.get("first_introduced_at"):
                        cur["first_introduced_at"] = fi
        agg_settings = list(settings_map.values())

        rdb.update_work(
            ref_id,
            style_fingerprint_json=json.dumps(agg_fp, ensure_ascii=False),
            extracted_characters_json=json.dumps(agg_chars, ensure_ascii=False),
            rhythm_json=json.dumps(agg_rhythm, ensure_ascii=False),
            plot_outline_json=json.dumps(agg_plot, ensure_ascii=False),
            settings_json=json.dumps(agg_settings, ensure_ascii=False),
            preprocessing_status="done",
        )
        return {
            "ref_id": ref_id,
            "merged_segments": len(items),
            "characters_count": len(agg_chars),
            "settings_count": len(agg_settings),
        }