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


def _enrich_characters_with_appearance(chars: list[dict], chapters: list[dict]) -> list[dict]:
    """For each character returned by the AI extractor, compute deterministic
    appearance_chapters and appearance_word_count by scanning chapter content.
    Preserves the AI-provided intro and speech_samples."""
    out: list[dict] = []
    for c in chars:
        name = (c.get("name") or "").strip()
        if not name:
            continue
        ap_chapters = 0
        ap_chars = 0
        for ch in chapters:
            content = ch.get("content", "")
            if name in content:
                ap_chapters += 1
                ap_chars += len(content)
        out.append({
            "name": name,
            "mentions": int(c.get("mentions") or 0),
            "intro": (c.get("intro") or "").strip(),
            "speech_samples": list(c.get("speech_samples") or [])[:3],
            "appearance_chapters": ap_chapters,
            "appearance_word_count": ap_chars,
        })
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

    def plan_segments(self, chapters: list[dict],
                      segment_chars: int | None = None) -> dict[str, Any]:
        """Plan segments for incremental processing. Returns:
            {"type": "volumes"|"chunks", "segments": [{...}], "total_chapters": N}
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
                                use_ai: bool = True) -> dict[str, Any]:
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
        plan = self.plan_segments(all_chapters, segment_chars=segment_chars)
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
            extract_narrative, extract_rhythm, extract_plot_outline,
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
                warnings.append(f"AI 不可用（{e}），所有项回退到 NLP")

        ai_methods_used: list[str] = []
        ai_methods_fallback: list[str] = []

        async def _ai_or_nlp(name: str, ai_fn, nlp_fn):
            """Try AI; on failure, fall back to NLP and record a warning."""
            if router is None:
                ai_methods_fallback.append(name)
                return nlp_fn()
            try:
                result = await ai_fn(seg_chapters, router)
                ai_methods_used.append(name)
                return result
            except Exception as e:
                logger.warning("[ai_extractor] %s failed (%s); falling back to NLP", name, e)
                warnings.append(f"{name}: AI 失败 → NLP（{type(e).__name__}）")
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
            )
            chars = _enrich_characters_with_appearance(chars, seg_chapters)
        except Exception as e:
            errors.append(f"characters: {e}")

        # Settings (AI preferred). No NLP fallback — return empty if AI fails.
        settings_items: list = []
        try:
            if router is not None:
                try:
                    settings_items = await ai_extractor.ai_extract_settings(seg_chapters, router)
                    ai_methods_used.append("settings")
                except Exception as e:
                    logger.warning("[ai_extractor] settings failed: %s", e)
                    warnings.append(f"settings: AI 失败（{type(e).__name__}），返回空")
                    ai_methods_fallback.append("settings")
            else:
                ai_methods_fallback.append("settings")
        except Exception as e:
            errors.append(f"settings: {e}")

        # Narrative (AI preferred)
        narr: dict = {}
        try:
            narr = await _ai_or_nlp(
                "narrative",
                ai_extractor.ai_extract_narrative,
                lambda: extract_narrative(seg_chapters),
            )
        except Exception as e:
            errors.append(f"narrative: {e}")

        # Rhythm (AI preferred)
        rhythm: dict = {}
        try:
            rhythm = await _ai_or_nlp(
                "rhythm",
                ai_extractor.ai_extract_rhythm,
                lambda: extract_rhythm(seg_chapters),
            )
        except Exception as e:
            errors.append(f"rhythm: {e}")

        # Plot outline (chronicle skeleton — always NLP, uses narrative output)
        plot: dict = {}
        try:
            plot = extract_plot_outline(seg_chapters, narrative=narr)
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
            "narrative": narr,
            "characters": chars,
            "rhythm": rhythm,
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

        # Refresh plan to know total
        text = self._load_text(work)
        chapters = self._split_chapters(text) if text else []
        plan = self.plan_segments(chapters)
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
            "results": results,
            "completed": sorted(int(k) for k in results.keys()),
        }
        new_status = "done" if len(results) >= len(segs) else "processing"
        rdb.update_work(
            ref_id,
            segments_json=json.dumps(new_state, ensure_ascii=False),
            preprocessing_status=new_status,
        )
        return {
            "ref_id": ref_id,
            "segment_index": seg_index,
            "total_segments": len(segs),
            "completed_count": len(results),
            "all_done": new_status == "done",
        }

    async def run_segment(self, ref_id: str, segment_index: int,
                            segment_chars: int | None = None,
                            use_ai: bool = True) -> dict[str, Any]:
        """Compute + persist in one call (kept for backwards compat)."""
        result = await self.compute_segment(
            ref_id, segment_index, segment_chars=segment_chars, use_ai=use_ai,
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

        # ── narrative: concat with chapter offsets ──
        agg_narr = {
            "opening_pattern": (items[0].get("narrative") or {}).get("opening_pattern", ""),
            "climax_positions": [],
            "hook_density": 0.0,
            "shuangdian": [],
            "chapter_beats": [],
        }
        for it in items:
            offset = (it.get("start_chapter") or 1) - 1
            n = it.get("narrative") or {}
            for c in (n.get("climax_positions") or []):
                if isinstance(c, int):
                    agg_narr["climax_positions"].append(c + offset)
            for sd in (n.get("shuangdian") or []):
                if isinstance(sd, dict) and isinstance(sd.get("chapter"), int):
                    agg_narr["shuangdian"].append({"chapter": sd["chapter"] + offset, "type": sd.get("type", "")})
            for b in (n.get("chapter_beats") or []):
                if isinstance(b, dict) and isinstance(b.get("chapter"), int):
                    agg_narr["chapter_beats"].append({**b, "chapter": b["chapter"] + offset})
        # hook density = char-weighted average
        hd_sum = sum(((it.get("narrative") or {}).get("hook_density", 0.0) or 0.0) * max(it.get("char_count") or 1, 1) for it in items)
        agg_narr["hook_density"] = round(hd_sum / total_chars, 3)

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
                })
                entry["mentions"] += int(ch.get("mentions") or 0)
                entry["appearance_chapters"] += int(ch.get("appearance_chapters") or 0)
                entry["appearance_word_count"] += int(ch.get("appearance_word_count") or 0)
                new_intro = (ch.get("intro") or "").strip()
                if new_intro and len(new_intro) > len(entry["intro"]):
                    entry["intro"] = new_intro
                for s in (ch.get("speech_samples") or [])[:5]:
                    if s and s not in entry["speech_samples"] and len(entry["speech_samples"]) < 5:
                        entry["speech_samples"].append(s)
        agg_chars = sorted(char_map.values(), key=lambda x: -x.get("mentions", 0))

        # ── rhythm: concat tension curve, concat pacing segments with offsets ──
        agg_rhythm = {"tension_curve": [], "pacing_segments": []}
        for it in items:
            offset = (it.get("start_chapter") or 1) - 1
            r = it.get("rhythm") or {}
            agg_rhythm["tension_curve"].extend(r.get("tension_curve") or [])
            for ps in (r.get("pacing_segments") or []):
                if isinstance(ps, dict):
                    agg_rhythm["pacing_segments"].append({
                        **ps,
                        "start": (ps.get("start") or 1) + offset,
                        "end": (ps.get("end") or 1) + offset,
                    })

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
                if cur is None or len((s.get("content") or "")) > len(cur.get("content") or ""):
                    settings_map[key] = {
                        "category": key[0],
                        "title": key[1],
                        "content": (s.get("content") or "").strip(),
                        "hidden": (s.get("hidden") or "").strip(),
                    }
                elif s.get("hidden") and not cur.get("hidden"):
                    cur["hidden"] = (s.get("hidden") or "").strip()
        agg_settings = list(settings_map.values())

        rdb.update_work(
            ref_id,
            style_fingerprint_json=json.dumps(agg_fp, ensure_ascii=False),
            narrative_structure_json=json.dumps(agg_narr, ensure_ascii=False),
            extracted_characters_json=json.dumps(agg_chars, ensure_ascii=False),
            rhythm_template_json=json.dumps(agg_rhythm, ensure_ascii=False),
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