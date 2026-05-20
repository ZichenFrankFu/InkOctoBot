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


def _load_chapter_patterns() -> list[dict]:
    """Return the user's custom chapter patterns from settings.json (key
    ``chapter_patterns``). Empty list when none configured or unreadable —
    never raises, so a missing/corrupt settings file doesn't break
    chapter splitting."""
    try:
        from pathlib import Path
        root = Path(__file__).resolve().parents[2]
        p = root / "data" / "settings.json"
        if not p.exists():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        pats = data.get("chapter_patterns")
        return [x for x in pats if isinstance(x, dict)] if isinstance(pats, list) else []
    except Exception:
        return []


def build_work_ctx(work: dict, segment: dict, segment_index: int) -> dict[str, Any]:
    """Build the work / volume context that gets threaded into every AI
    extraction prompt. The prompts reference these keys via {placeholder}
    substitution; missing/empty values fall back to defaults inside
    ``ai_extractor._ctx``."""
    return {
        "title": (work.get("title") or "").strip() or "(未命名)",
        "author": (work.get("creator") or "").strip() or "(未知)",
        "platform": (work.get("platform") or "").strip() or "(未知)",
        "volume_index": segment_index + 1,
        "volume_title": (segment.get("title") or "").strip() or f"第 {segment_index + 1} 卷",
        "start_chapter": segment.get("start_chapter") or 1,
        "end_chapter": segment.get("end_chapter") or 1,
    }


def _events_to_chapter_order_chronicle(
    events: list[dict],
    volume_title: str = "本卷",
) -> dict[str, Any]:
    """Group a flat events list into the chronicle shape used by the
    rest of the pipeline + the editor. One epoch per volume, one
    period per ``first_chapter`` (in the order chapters first appear),
    events within a period in the order the AI returned them.

    This is the "chapter-order chronicle" produced by the extraction
    step. The story-time reordering happens later via the chronicle
    summary action."""
    if not events:
        return {"logline": "", "epochs": []}
    by_chapter: dict[str, list[dict]] = {}
    order: list[str] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        key = (ev.get("first_chapter") or "").strip() or "(未指定章节)"
        if key not in by_chapter:
            by_chapter[key] = []
            order.append(key)
        by_chapter[key].append(ev)
    periods = [{"time": k, "events": by_chapter[k]} for k in order]
    return {
        "logline": "",
        "epochs": [{"title": volume_title or "本卷", "periods": periods}],
    }


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

    # Module-level cache for _split_chapters: the chapter detection
    # (multi-format regex scan + scoring) is the dominant cost of the
    # /segments/plan load + save path, and the same text is re-split on
    # every call. Keyed by a hash of (text, chapter-patterns) so it's
    # invalidated automatically when the novel text or the user's custom
    # patterns change. Bounded to the few most-recent works.
    _CHAPTER_CACHE: dict[str, list[dict]] = {}
    _CHAPTER_CACHE_ORDER: list[str] = []
    _CHAPTER_CACHE_MAX = 6

    @staticmethod
    def _split_chapters(text: str) -> list[dict]:
        """Smart chapter splitter — tries multiple formats (第N章 / 第N回 /
        1、标题 / Chapter N / …) and picks the best-scoring one. Falls back
        to ~3000-char chunks when no clear structure exists.

        Also honors user-defined patterns from
        ``settings.json["chapter_patterns"]`` so users can add their own
        format without code changes.

        Result is memoized on a hash of (text, patterns) — repeated
        calls for the same novel (the /segments/plan load/save path) are
        served from cache instead of re-running detection."""
        from analysis.feature_extraction.chapter_parser import detect_chapters
        import hashlib
        extras = _load_chapter_patterns()
        key = hashlib.md5(
            (text or "").encode("utf-8", "ignore")
        ).hexdigest() + ":" + hashlib.md5(
            json.dumps(extras, sort_keys=True, ensure_ascii=False).encode("utf-8", "ignore")
        ).hexdigest()
        cls = FeatureExtractionPipeline
        cached = cls._CHAPTER_CACHE.get(key)
        if cached is not None:
            return cached
        result = detect_chapters(text, extra_patterns=extras)
        # Strip extra metadata to keep the shape compatible with existing
        # callers that only read {index, title, volume, content}.
        out: list[dict] = []
        for c in result["chapters"]:
            out.append({
                "index": c.get("index"),
                "title": c.get("title") or "",
                "volume": c.get("volume") or "",
                "content": c.get("content") or "",
            })
        cls._CHAPTER_CACHE[key] = out
        cls._CHAPTER_CACHE_ORDER.append(key)
        if len(cls._CHAPTER_CACHE_ORDER) > cls._CHAPTER_CACHE_MAX:
            old = cls._CHAPTER_CACHE_ORDER.pop(0)
            cls._CHAPTER_CACHE.pop(old, None)
        return out

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

    def render_volume_detect_prompt(self, ref_id: str) -> dict[str, Any]:
        """Return the exact prompt that ``ai_suggest_volume_plan`` would
        send. Surfaced via API so the user can copy it into a web LLM
        (ChatGPT / Claude.ai) when the configured model is unable to
        produce parseable output. Also returns chapter count for context."""
        from rag.reference_db import ReferenceDB
        from analysis.feature_extraction.volume_detector import build_volume_prompt

        rdb = ReferenceDB(self.db_path)
        work = rdb.get_work(ref_id)
        if not work:
            raise ValueError(f"reference work not found: {ref_id}")
        text = self._load_text(work)
        chapters = self._split_chapters(text) if text else []
        if not chapters:
            raise ValueError("作品尚未上传正文或未识别出章节")
        prompt = build_volume_prompt(work, chapters)
        return {
            "ref_id": ref_id,
            "title": work.get("title") or "",
            "total_chapters": len(chapters),
            "prompt": prompt,
            "prompt_key": "reference.volume_detect",
        }

    async def ai_suggest_volume_plan(self, ref_id: str) -> dict[str, Any]:
        """Detect volumes for a work using the best available source.

        Priority chain:
          1. Web-search-capable LLM (highest accuracy on indexed works)
          2. Local LLM without web search
          3. Rule-based scan of titles + chapter heads (offline)
          4. Existing parser-tag detection (the old behavior)

        Returns ``{type: "volumes", segments: [...], used_method: "...",
        prompt_used: "...", total_chapters: N, is_custom: False}``.
        ``used_method`` is one of ``ai_web_search`` / ``ai_local`` /
        ``text_scan`` / ``parser_tags`` / ``none``.
        """
        from rag.reference_db import ReferenceDB
        from analysis.feature_extraction.volume_detector import (
            ai_detect_volumes, text_detect_volumes, build_volume_prompt,
        )

        rdb = ReferenceDB(self.db_path)
        work = rdb.get_work(ref_id)
        if not work:
            raise ValueError(f"reference work not found: {ref_id}")
        text = self._load_text(work)
        chapters = self._split_chapters(text) if text else []
        if not chapters:
            return {
                "type": "volumes", "segments": [], "total_chapters": 0,
                "used_method": "none", "is_custom": False,
                "prompt_used": "",
                "warning": "作品尚未上传正文或未识别出章节",
            }

        prompt_used = build_volume_prompt(work, chapters)
        used_method = "none"
        volumes: list[dict] | None = None

        # ── Try AI first when a router is available ──
        try:
            from models.router import ModelRouter
            router = ModelRouter()
        except Exception as e:
            logger.info("[volume_detect] router unavailable, skipping AI: %s", e)
            router = None

        if router is not None:
            try:
                volumes, used_method = await ai_detect_volumes(
                    work, chapters, router, prefer_web_search=True,
                )
            except Exception as e:
                logger.warning("[volume_detect] AI layer crashed: %s", e)
                volumes, used_method = None, "ai_error"

        # ── Text-based fallback ──
        if not volumes:
            text_vols = text_detect_volumes(chapters)
            if text_vols:
                volumes = text_vols
                used_method = "text_scan"

        # ── Last resort: legacy parser-tag detector ──
        if not volumes:
            parser_vols = self._detect_volumes(chapters)
            if parser_vols:
                volumes = parser_vols
                used_method = "parser_tags"

        if not volumes:
            return {
                "type": "volumes", "segments": [], "total_chapters": len(chapters),
                "used_method": used_method, "is_custom": False,
                "prompt_used": prompt_used,
                "warning": (
                    "未能识别到分卷结构。请使用「复制 prompt」按钮把提示发到任意"
                    "网页版 LLM（如 ChatGPT / Claude.ai），再把回复粘回章节范围。"
                ),
            }

        # Enrich with chapter_count / char_count for the UI
        segments: list[dict] = []
        for i, v in enumerate(volumes):
            sc, ec = v["start_chapter"], v["end_chapter"]
            segments.append({
                "index": i,
                "title": v["title"],
                "start_chapter": sc,
                "end_chapter": ec,
                "chapter_count": ec - sc + 1,
                "char_count": sum(
                    len(chapters[j - 1].get("content") or "")
                    for j in range(sc, ec + 1)
                ),
            })
        return {
            "type": "volumes",
            "segments": segments,
            "total_chapters": len(chapters),
            "used_method": used_method,
            "is_custom": False,
            "prompt_used": prompt_used,
        }

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
        #
        # CRITICAL: preserve top-level keys this function doesn't own —
        # `uploads` (the file-tracking ledger maintained by the upload
        # endpoint), `preprocess` (cached chapter-parser output), and
        # anything else a future feature may store here. Rebuilding the
        # dict from scratch was silently nuking the uploads ledger,
        # which is why the Files tab showed "未跟踪" after restart and
        # the chunk count looked wrong.
        try:
            existing = json.loads(work.get("segments_json") or "{}")
        except Exception:
            existing = {}
        if not isinstance(existing, dict):
            existing = {}
        # Whitelist of keys this function explicitly owns. Everything
        # else (uploads, preprocess, etc.) carries over untouched.
        OWNED = {"type", "plan", "custom_plan", "results", "completed"}
        new_state = {k: v for k, v in existing.items() if k not in OWNED}
        new_state.update({
            "type": plan_type,
            "plan": cleaned,
            "custom_plan": cleaned,
            "results": {},
            "completed": [],
        })
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
        Characters / settings / rhythm use AI via ModelRouter. If the AI
        call fails (model unavailable, JSON parse error, etc.), the
        failure is surfaced as an error — there is **no** NLP fallback,
        because rule-based extraction was producing materially worse
        results and silently masking model misconfiguration.
        Plot outline (chronicle skeleton) uses NLP rules built off the
        AI rhythm result; if AI rhythm failed, plot will be empty too.
        """
        from rag.reference_db import ReferenceDB

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

        # Build the per-volume context that the prompts reference. This
        # gets threaded into every AI extractor call so the model sees
        # the same book metadata (title/author/platform/volume name) on
        # every shot — improves disambiguation and gives the user the
        # exact prompt they need when copying to a web LLM.
        work_ctx = build_work_ctx(work, seg, segment_index)

        from analysis.feature_extraction.nlp_stats import compute_style_fingerprint
        from analysis.feature_extraction.narrative_extractor import extract_plot_outline

        # Style: ALWAYS NLP (per spec — deterministic statistics, not LLM)
        fp: dict = {}
        try:
            fp = compute_style_fingerprint(seg_chapters)
        except Exception as e:
            errors.append(f"style: {e}")

        # Get an AI router (lazy import; may fail if no LLM configured).
        # When AI is unavailable, the whole segment extraction fails fast —
        # no silent NLP fallback. The user must configure a working model.
        router = None
        if use_ai:
            try:
                from models.router import ModelRouter
                router = ModelRouter()
            except Exception as e:
                detail = str(e).strip().replace("\n", " ")[:200] or type(e).__name__
                hint = _diagnose_ai_error(e)
                msg = f"AI 路由初始化失败 · {detail}"
                if hint:
                    msg += f" · 提示：{hint}"
                errors.append(msg)
        else:
            errors.append("use_ai=False — 已禁用 AI，且未启用 NLP 回退；本段提取被跳过")

        ai_methods_used: list[str] = []
        ai_methods_fallback: list[str] = []  # kept for response-shape compat (always [])

        async def _ai_only(name: str, ai_fn, *, prompt_key: str | None = None):
            """Invoke an AI extractor; on failure, surface a structured
            error and return an empty result. There is no NLP fallback —
            extraction failures should NOT silently degrade to rule-based
            output. Callers handle the empty result downstream."""
            if router is None:
                return None
            try:
                override = (prompt_overrides or {}).get(prompt_key) if prompt_key else None
                kw: dict[str, Any] = {
                    "use_web_search": use_web_search,
                    "work_ctx": work_ctx,
                }
                if override is not None:
                    kw["prompt_override"] = override
                result = await ai_fn(seg_chapters, router, **kw)
                ai_methods_used.append(name)
                return result
            except Exception as e:
                logger.warning("[ai_extractor] %s failed: %s", name, e)
                detail = str(e).strip().replace("\n", " ")[:200] or type(e).__name__
                hint = _diagnose_ai_error(e)
                msg = f"{name}: AI 提取失败 · {detail}"
                if hint:
                    msg += f" · 提示：{hint}"
                errors.append(msg)
                return None

        from analysis.feature_extraction import ai_extractor

        # Characters — AI only, no NLP fallback.
        chars: list = []
        ai_chars = await _ai_only(
            "characters",
            ai_extractor.ai_extract_characters,
            prompt_key="reference.characters",
        )
        if ai_chars:
            try:
                chars = _enrich_characters_with_appearance(ai_chars, seg_chapters)
            except Exception as e:
                errors.append(f"characters enrichment: {e}")

        # Settings — AI only, no fallback.
        settings_items: list = []
        ai_settings = await _ai_only(
            "settings",
            ai_extractor.ai_extract_settings,
            prompt_key="reference.settings",
        )
        if ai_settings:
            try:
                settings_items = _enrich_settings_with_timestamp(
                    ai_settings, seg_chapters, seg.get("start_chapter") or 1,
                )
            except Exception as e:
                errors.append(f"settings enrichment: {e}")

        # Rhythm v2 — AI only, no fallback.
        rhythm: dict = {}
        ai_rhythm = await _ai_only(
            "rhythm",
            ai_extractor.ai_extract_rhythm_v2,
            prompt_key="reference.rhythm",
        )
        if ai_rhythm:
            rhythm = ai_rhythm

        # Plot outline — chunked extraction. The extraction step
        # produces "chronicle in chapter order" (events grouped by
        # first_chapter, not yet reordered by story-time). The
        # story-time summary is a separate user-driven action from the
        # chronicle display section.
        #
        # Iterate per-chunk so even very long volumes finish without
        # tripping the 32k prompt cap. Failures on individual chunks
        # surface as separate errors but don't poison the whole volume.
        plot: dict = {}
        ai_outline_override = (prompt_overrides or {}).get("reference.outline")
        try:
            chunks = ai_extractor.build_segment_text_chunks(
                seg_chapters,
                segment_start_chapter=seg.get("start_chapter") or 1,
            )
        except Exception as e:
            chunks = []
            errors.append(f"outline chunking: {e}")

        chunk_total = len(chunks)
        all_events: list[dict] = []
        outline_chunks_used = 0
        if router is not None and chunk_total > 0:
            # `seg_chapters` is the volume's full chapter list with the
            # `index` field set relative to the volume start. We slice
            # it per chunk by absolute chapter number so each AI call
            # sees only the chunk's chapters.
            for ci, chunk in enumerate(chunks):
                lo = chunk["start_chapter"]
                hi = chunk["end_chapter"]
                chunk_chapters = [
                    c for c in seg_chapters
                    if (c.get("index") or 0) + (seg.get("start_chapter") or 1) >= lo
                    and (c.get("index") or 0) + (seg.get("start_chapter") or 1) <= hi
                ]
                if not chunk_chapters:
                    continue
                try:
                    events = await ai_extractor.ai_extract_outline_events(
                        chunk_chapters, router,
                        prompt_override=ai_outline_override,
                        use_web_search=use_web_search,
                        work_ctx=work_ctx,
                    )
                except Exception as e:
                    logger.warning(
                        "[ai_extractor] outline chunk %d/%d failed: %s",
                        ci + 1, chunk_total, e,
                    )
                    detail = str(e).strip().replace("\n", " ")[:200] or type(e).__name__
                    hint = _diagnose_ai_error(e)
                    msg = f"outline chunk {ci + 1}/{chunk_total}: {detail}"
                    if hint:
                        msg += f" · 提示：{hint}"
                    errors.append(msg)
                    continue
                if events:
                    all_events.extend(events)
                    outline_chunks_used += 1
            if outline_chunks_used > 0:
                ai_methods_used.append(f"outline ({outline_chunks_used}/{chunk_total} 段)")

        if all_events:
            # Group events into chronicle shape: one epoch per volume,
            # one period per first_chapter, events in textual order.
            plot = _events_to_chapter_order_chronicle(
                all_events,
                volume_title=(work_ctx or {}).get("volume_title")
                            or seg.get("title") or "本卷",
            )
        else:
            # No AI events. Build a minimal NLP skeleton so the editor
            # isn't empty (rhythm climaxes / shuangdian give the user
            # SOMETHING to start with).
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

    def persist_segment(self, ref_id: str, result: dict,
                          merge_after: bool = False) -> dict[str, Any]:
        """Persist a previously-computed segment result into segments_json.

        When ``merge_after`` is True, also runs ``finalize_segments``
        immediately so the top-level ``plot_outline_json`` / characters /
        settings reflect this commit. The UI uses ``merge_after=True``
        on the "确认并入库" button so users don't need a second click
        to see the full chronicle update after each segment.
        """
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

        # Same data-integrity guard as save_custom_plan: keep top-level
        # keys this function doesn't own (uploads, preprocess, …)
        # untouched so they survive a per-segment commit.
        OWNED = {"type", "plan", "results", "completed"}
        new_state = {k: v for k, v in existing.items() if k not in OWNED}
        new_state.update({
            "type": plan["type"],
            "plan": [
                {k: v for k, v in s.items() if k in (
                    "index", "title", "start_chapter", "end_chapter",
                    "chapter_count", "char_count",
                )}
                for s in segs
            ],
            "results": results,
            "completed": sorted(int(k) for k in results.keys()),
        })
        # Preserve the user's saved custom plan across commits so it
        # keeps overriding auto-detection on subsequent extractions.
        if existing.get("custom_plan"):
            new_state["custom_plan"] = existing["custom_plan"]
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

        info: dict[str, Any] = {
            "ref_id": ref_id,
            "segment_index": seg_index,
            "total_segments": len(segs),
            "completed_count": len(results),
            "all_done": new_status == "done",
        }
        if merge_after:
            try:
                info["merge"] = self.finalize_segments(ref_id)
            except Exception as e:
                logger.warning("[persist_segment] auto-merge failed: %s", e)
                info["merge_error"] = str(e)
        return info

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
        # Events keep their own `time_marker` (story-time) and
        # `first_chapter` (real-text reference) so the editor can show
        # both as tags. Logline picks the first segment's logline as a
        # representative summary (multi-volume works rarely have one
        # global logline; the per-volume ones at least anchor the view).
        agg_plot_epochs: list[dict] = []
        agg_logline = ""
        for it in items:
            po = it.get("plot_outline") or {}
            if not agg_logline:
                lg = (po.get("logline") or "").strip()
                if lg:
                    agg_logline = lg
            for ep in (po.get("epochs") or []):
                title = ep.get("title") or it.get("title") or ""
                agg_plot_epochs.append({"title": title, "periods": ep.get("periods") or []})
        agg_plot = {"logline": agg_logline, "epochs": agg_plot_epochs}

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
                fc = (s.get("first_chapter") or "").strip()
                if cur is None or len((s.get("content") or "")) > len(cur.get("content") or ""):
                    settings_map[key] = {
                        "category": key[0],
                        "title": key[1],
                        "content": (s.get("content") or "").strip(),
                        "first_introduced_at": fi or (cur.get("first_introduced_at") if cur else ""),
                        "first_chapter": fc or (cur.get("first_chapter") if cur else ""),
                    }
                else:
                    if fi and not cur.get("first_introduced_at"):
                        cur["first_introduced_at"] = fi
                    if fc and not cur.get("first_chapter"):
                        cur["first_chapter"] = fc
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