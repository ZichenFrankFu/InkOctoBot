import React, { useCallback, useEffect, useRef, useState } from "react";
import { apiGet, apiPost } from "../../api/client";
import { useToast } from "../shared/Toast";
import { PlotOutlineEditor, PromptCopyPanel, categoryLabel, timeMarkers } from "./AnalysisEditors";
import type { PlotOutline, ChronicleEpoch, ChroniclePeriod } from "./AnalysisEditors";

interface SegmentInfo {
  index: number;
  title: string;
  start_chapter: number;
  end_chapter: number;
  chapter_count?: number;
  char_count: number;
}

interface SegmentPlan {
  type: "volumes" | "chunks" | "custom";
  segments: SegmentInfo[];
  completed: number[];
  total_chapters: number;
  is_custom?: boolean;
}

function fmtChars(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)} 万字`;
  return `${n.toLocaleString()} 字`;
}

/** Chunk metadata returned from /segments/{idx}/chunks. */
interface ChunkMeta {
  chunk_index: number;
  total_chunks: number;
  start_chapter: number;
  end_chapter: number;
  n_chapters: number;
  n_chars: number;
}

/** UI state for a single chunk during the extraction step.
 *
 * Lifecycle: idle → extracting (AI path only) → ready → committing → committed.
 * The paste path skips "extracting" and jumps idle → ready on parse success.
 * "failed" stays as a terminal status with an error message — the user is
 * directed to copy-prompt + paste-back from a web LLM at that point. */
interface ChunkExtractionState {
  status: "idle" | "extracting" | "ready" | "failed" | "committing" | "committed";
  /** "ai" enables the chatbox; "paste" doesn't (no model to chat with). */
  source?: "ai" | "paste";
  events?: any[];
  error?: string;
  elapsedS?: number;
  /** Unix-ms time when the AI extraction started. Used by the row to
   *  render a live elapsed-seconds counter while status === "extracting". */
  startedAt?: number;
  /** Paste-mode local state. */
  pasteRaw?: string;
  pasteError?: string;
  /** Chat-with-AI for refining the extracted events (AI path only). */
  chat?: { role: "user" | "assistant"; content: string }[];
  chatInput?: string;
  chatSending?: boolean;
}

interface Props {
  refId: string;
  hasFullText: boolean;
  plotOutline: PlotOutline | null;
  preprocessingStatus: string;
  onSavePlot: (d: PlotOutline) => Promise<void> | void;
  onAfterMerge: () => Promise<void> | void;
  onRegenerateFromText?: () => void;
  regenerating?: boolean;
  /** Switch to the "预处理" tab — used by the "编辑分卷" button now that
   * volume creation/editing lives in the preprocess tab. */
  onGoToPreprocess?: () => void;
  /** The currently "active" segment (volume) — shared across tabs so
   * the characters / settings PromptCopyPanels know which volume to
   * render the prompt for. Defaults to 0 if omitted. */
  activeSegmentIndex?: number;
  onActiveSegmentChange?: (idx: number) => void;
}

export default function PlotOutlinePanel({
  refId,
  hasFullText,
  plotOutline,
  preprocessingStatus,
  onSavePlot,
  onAfterMerge,
  onRegenerateFromText,
  regenerating,
  onGoToPreprocess,
  activeSegmentIndex,
  onActiveSegmentChange,
}: Props) {
  const { toast } = useToast();
  const [plan, setPlan] = useState<SegmentPlan | null>(null);
  const [planLoading, setPlanLoading] = useState(false);
  const [useWebSearch, setUseWebSearch] = useState(false);
  const [webSearchCap, setWebSearchCap] = useState<{ enabled: boolean; reason: string; provider: string; model: string } | null>(null);
  const [merging, setMerging] = useState(false);
  // Per-segment expansion state: clicking a volume row reveals its
  // chunks. The chunks list is fetched lazily on first expand.
  const [openSegs, setOpenSegs] = useState<Set<number>>(new Set());
  const [segChunks, setSegChunks] = useState<Record<number, ChunkMeta[]>>({});
  const [segChunksLoading, setSegChunksLoading] = useState<Set<number>>(new Set());
  // Per-chunk state — keyed by `${segIdx}:${chunkIdx}`. Each chunk
  // tracks: which UI section is open, what events were extracted
  // (whether via AI or paste), per-chunk errors, paste-mode buffer,
  // and the chatbox state (chat-tunable only when source === "ai").
  const [openChunks, setOpenChunks] = useState<Set<string>>(new Set());
  const [chunkState, setChunkState] = useState<Record<string, ChunkExtractionState>>({});

  // Bulk-run state for the "use internal AI on every chunk" action.
  // The cancel flag is a ref so the in-flight loop can poll it
  // without re-rendering on every state update.
  const [bulkRunning, setBulkRunning] = useState(false);
  const [bulkProgress, setBulkProgress] = useState<
    { done: number; total: number; label: string; failed: number } | null
  >(null);
  const bulkCancelRef = useRef(false);
  // Refs for stable access from async loops (state updates are deferred,
  // so reading state directly mid-loop can race).
  const plotOutlineRef = useRef(plotOutline);
  useEffect(() => { plotOutlineRef.current = plotOutline; }, [plotOutline]);
  const chunkStateRef = useRef(chunkState);
  useEffect(() => { chunkStateRef.current = chunkState; }, [chunkState]);
  const segChunksRef = useRef(segChunks);
  useEffect(() => { segChunksRef.current = segChunks; }, [segChunks]);

  const loadPlan = useCallback(async () => {
    if (!hasFullText) { setPlan(null); return; }
    setPlanLoading(true);
    try {
      const p = await apiGet<SegmentPlan>(`/api/references/works/${refId}/segments/plan`);
      setPlan(p);
    } catch (e: any) {
      // silent on first load
    } finally { setPlanLoading(false); }
  }, [refId, hasFullText]);

  useEffect(() => { loadPlan(); }, [loadPlan]);

  // Capability probe so we can disable the web-search toggle with a
  // useful tooltip when the user hasn't configured a search-capable model.
  useEffect(() => {
    let cancelled = false;
    apiGet<{ enabled: boolean; reason: string; provider: string; model: string }>(
      "/api/references/web_search/capability",
    ).then(r => { if (!cancelled) setWebSearchCap(r); })
     .catch(() => { /* no-op; toggle stays disabled */ });
    return () => { cancelled = true; };
  }, []);

  const total = plan?.segments.length || 0;
  const completed = new Set(plan?.completed || []);
  const doneCount = completed.size;
  const allDone = total > 0 && doneCount >= total;
  const nextIdx = plan?.segments.find(s => !completed.has(s.index))?.index;

  // ─── Recover committed-chunk state from the persisted chronicle ───
  //
  // chunkState is in-memory only — when the user switches pages and
  // comes back, the green "已完成" badges disappear unless we rebuild
  // the status from data that IS persisted. The chronicle's
  // plot_outline_json stores periods named by first_chapter ("第N章"),
  // so we can check each chunk's chapter range against the volume's
  // epoch and mark it committed if any period inside the range carries
  // events. This persists across sessions because the chronicle itself
  // does.
  const detectCommittedChunk = useCallback(
    (volumeTitle: string, startCh: number, endCh: number): boolean => {
      const outline = plotOutlineRef.current;
      if (!outline?.epochs) return false;
      const epoch = outline.epochs.find(e => e.title === volumeTitle);
      if (!epoch) return false;
      return (epoch.periods || []).some(p => {
        if (!p.events || p.events.length === 0) return false;
        const m = (p.time || "").match(/第\s*(\d+)\s*章/);
        if (!m) return false;
        const n = parseInt(m[1], 10);
        return n >= startCh && n <= endCh;
      });
    },
    [],
  );
  useEffect(() => {
    if (!plan || !plotOutline) return;
    setChunkState(prev => {
      let changed = false;
      const next = { ...prev };
      for (const seg of plan.segments) {
        const chunks = segChunks[seg.index] || [];
        const title = seg.title || `第 ${seg.index + 1} 卷`;
        for (const ck of chunks) {
          const k = chunkKey(seg.index, ck.chunk_index);
          const currentStatus = next[k]?.status;
          // Don't clobber in-flight states. Only upgrade idle/failed →
          // committed when the chronicle confirms persistence.
          if (currentStatus === "extracting" || currentStatus === "committing"
              || currentStatus === "ready" || currentStatus === "committed") {
            continue;
          }
          const isCommitted = detectCommittedChunk(
            title, ck.start_chapter, ck.end_chapter,
          );
          if (isCommitted) {
            next[k] = { ...(next[k] || { status: "idle" }), status: "committed" };
            changed = true;
          }
        }
      }
      return changed ? next : prev;
    });
  }, [plotOutline, plan, segChunks, detectCommittedChunk]);

  /** Whether every chunk of a volume has been committed (per the
   *  client-side derived chunkState). Used to color the volume row
   *  "已完成" green without waiting for the server-side `plan.completed`
   *  set, which only tracks the legacy per-segment commit path. */
  const isVolumeFullyCommitted = (segIdx: number): boolean => {
    const chunks = segChunks[segIdx] || [];
    if (chunks.length === 0) return false;
    return chunks.every(ck => {
      const k = chunkKey(segIdx, ck.chunk_index);
      return chunkState[k]?.status === "committed";
    });
  };

  // ─── per-chunk handlers ───

  /** Lazy-load a segment's chunks the first time it's expanded. */
  const ensureChunksLoaded = useCallback(async (segIdx: number) => {
    if (segChunks[segIdx]) return;
    setSegChunksLoading(prev => new Set(prev).add(segIdx));
    try {
      const r = await apiGet<{ chunks: ChunkMeta[]; total_chunks: number }>(
        `/api/references/works/${refId}/segments/${segIdx}/chunks`,
      );
      setSegChunks(prev => ({ ...prev, [segIdx]: r.chunks || [] }));
    } catch (e: any) {
      toast(e?.message || "获取分段失败", "error");
    } finally {
      setSegChunksLoading(prev => {
        const next = new Set(prev); next.delete(segIdx); return next;
      });
    }
  }, [refId, segChunks, toast]);

  const toggleSeg = async (segIdx: number) => {
    const open = openSegs.has(segIdx);
    setOpenSegs(prev => {
      const next = new Set(prev);
      if (open) next.delete(segIdx); else next.add(segIdx);
      return next;
    });
    if (!open) {
      await ensureChunksLoaded(segIdx);
      onActiveSegmentChange?.(segIdx);
    }
  };

  const chunkKey = (s: number, c: number) => `${s}:${c}`;
  const toggleChunk = (s: number, c: number) => {
    const k = chunkKey(s, c);
    setOpenChunks(prev => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k); else next.add(k);
      return next;
    });
  };
  const patchChunk = (s: number, c: number, patch: Partial<ChunkExtractionState>) => {
    const k = chunkKey(s, c);
    setChunkState(prev => ({ ...prev, [k]: { ...(prev[k] || { status: "idle" }), ...patch } }));
  };

  /** Run the internal AI on one chunk. Success populates events +
   *  source="ai" (so the chatbox shows); failure marks the chunk as
   *  failed with an error — the user is directed to the prompt-copy
   *  path in that branch. */
  const runChunkAI = async (segIdx: number, chunkIdx: number) => {
    patchChunk(segIdx, chunkIdx, {
      status: "extracting", error: undefined, events: undefined, source: undefined,
      startedAt: Date.now(),
    });
    try {
      const r = await apiPost<{
        events: any[]; elapsed_s: number; errors: string[];
      }>(
        `/api/references/works/${refId}/segments/${segIdx}/chunks/${chunkIdx}/extract`,
        { use_web_search: useWebSearch && !!webSearchCap?.enabled },
        { timeoutMs: 600_000 },
      );
      if (r.errors && r.errors.length > 0) {
        patchChunk(segIdx, chunkIdx, {
          status: "failed",
          error: r.errors.join("; "),
          elapsedS: r.elapsed_s,
        });
      } else if (!r.events || r.events.length === 0) {
        patchChunk(segIdx, chunkIdx, {
          status: "failed",
          error: "AI 返回了 0 个事件。请改用复制 prompt 到网页 LLM 的方式。",
          elapsedS: r.elapsed_s,
        });
      } else {
        patchChunk(segIdx, chunkIdx, {
          status: "ready",
          source: "ai",
          events: r.events,
          elapsedS: r.elapsed_s,
          chat: [],
        });
      }
    } catch (e: any) {
      patchChunk(segIdx, chunkIdx, {
        status: "failed",
        error: e?.message || "AI 提取失败",
      });
    }
  };

  /** Parse the user's pasted JSON. Tolerant of the same shapes the
   *  chronicle editor accepts: {events:[…]}, bare arrays, full outlines.
   *  Specifically handles the common case where a web-LLM UI prepends
   *  prose ("Claude responded:") and/or pastes a truncated preview
   *  copy before the real JSON — we scan for ALL `{"events":` /
   *  `{"epochs":` positions, try to balanced-extract from each, and
   *  pick the candidate with the most events.
   *  On success the chunk transitions to ready/source=paste (no chatbox
   *  — the chatbox needs a model conversation, which we don't have on
   *  the paste path). */
  const parseChunkPaste = (segIdx: number, chunkIdx: number, raw: string) => {
    let s = (raw || "").replace(/<think>[\s\S]*?<\/think>/gi, "")
                       .replace(/<\/?think>/gi, "");
    // Strip a whole-response code fence
    const fence = s.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
    if (fence) s = fence[1];
    // Normalize copy-paste mangling that often breaks JSON parsing.
    // Built with \u escapes (not literal chars) so this stays readable
    // even when the source is viewed in editors that hide invisible
    // code points.
    //   - BOM (U+FEFF) at the head
    //   - Zero-width chars (U+200B – U+200D, U+FEFF mid-string)
    //   - Curly "smart" quotes (U+201C/U+201D, U+2018/U+2019) —
    //     some web UIs silently replace ASCII " when copying.
    //   - Non-breaking spaces (U+00A0) that the JSON parser refuses
    //     in whitespace position.
    s = s.replace(/[\uFEFF\u200B-\u200D]/g, "")
         .replace(/[\u201C\u201D]/g, '"')
         .replace(/[\u2018\u2019]/g, "'")
         .replace(/\u00A0/g, " ");
    s = s.trim();
    if (!s) {
      patchChunk(segIdx, chunkIdx, { pasteError: "请先粘贴 LLM 返回的 JSON" });
      return;
    }

    // Balanced extraction: walk from `start`, tracking JSON string and
    // brace depth, return the substring of the matching balanced
    // object/array (or null if it never balances).
    const balancedExtract = (start: number): string | null => {
      if (s[start] !== "{" && s[start] !== "[") return null;
      let depth = 0;
      let inString = false;
      let escape = false;
      for (let i = start; i < s.length; i++) {
        const c = s[i];
        if (escape) { escape = false; continue; }
        if (c === "\\") { escape = true; continue; }
        if (c === '"') { inString = !inString; continue; }
        if (inString) continue;
        if (c === "{" || c === "[") depth++;
        else if (c === "}" || c === "]") {
          depth--;
          if (depth === 0) return s.slice(start, i + 1);
        }
      }
      return null;
    };

    const eventsFromParsed = (parsed: any): any[] => {
      if (Array.isArray(parsed)) {
        // Either a bare events array, or an array of {events: [...]} chunks.
        if (parsed.length > 0 && parsed[0] && typeof parsed[0] === "object"
            && Array.isArray(parsed[0].events)) {
          const out: any[] = [];
          for (const obj of parsed) for (const ev of (obj.events || [])) out.push(ev);
          return out;
        }
        return parsed.filter((e: any) => e && typeof e === "object" && (e.name || e.description));
      }
      if (parsed && typeof parsed === "object") {
        if (Array.isArray(parsed.events)) return parsed.events;
        if (Array.isArray(parsed.epochs)) {
          const flat: any[] = [];
          for (const ep of parsed.epochs)
            for (const per of (ep.periods || []))
              for (const ev of (per.events || [])) flat.push(ev);
          return flat;
        }
      }
      return [];
    };

    // Collect all candidate parse positions, prioritising known
    // structural patterns. Multi-pass with a `tried` set so we don't
    // re-attempt the same start position.
    const candidates: any[][] = [];
    const tried = new Set<number>();
    let lastErrorMsg = "";

    for (const pattern of [/\{\s*"events"\s*:/g, /\{\s*"epochs"\s*:/g]) {
      let m: RegExpExecArray | null;
      while ((m = pattern.exec(s)) !== null) {
        if (tried.has(m.index)) continue;
        tried.add(m.index);
        const extracted = balancedExtract(m.index);
        if (!extracted) continue;
        try {
          const parsed = JSON.parse(extracted);
          const events = eventsFromParsed(parsed);
          if (events.length > 0) candidates.push(events);
        } catch (e: any) {
          lastErrorMsg = e?.message || String(e);
        }
      }
    }

    // Fallback: try any { or [ position. Useful when the LLM returned
    // a bare array of events instead of {"events":[...]}.
    if (candidates.length === 0) {
      for (let i = 0; i < s.length; i++) {
        if (s[i] !== "{" && s[i] !== "[") continue;
        if (tried.has(i)) continue;
        tried.add(i);
        const extracted = balancedExtract(i);
        if (!extracted) continue;
        try {
          const parsed = JSON.parse(extracted);
          const events = eventsFromParsed(parsed);
          if (events.length > 0) candidates.push(events);
        } catch (e: any) {
          lastErrorMsg = e?.message || String(e);
        }
      }
    }

    if (candidates.length === 0) {
      // When the parse failed at a specific position, surface ~50 chars
      // of context around it so the user can spot a mangled escape /
      // smart quote / extra char. The most common silent breakage is
      // an inner `"` inside a description that the LLM forgot to escape.
      let extra = "";
      const posMatch = lastErrorMsg.match(/position\s+(\d+)/i);
      if (posMatch) {
        const pos = parseInt(posMatch[1], 10);
        if (pos >= 0 && pos < s.length) {
          const a = Math.max(0, pos - 30);
          const b = Math.min(s.length, pos + 30);
          const before = s.slice(a, pos);
          const at = s.slice(pos, pos + 1);
          const after = s.slice(pos + 1, b);
          extra = `\n出错位置附近：…${before}【${at}】${after}…`;
        }
      }
      patchChunk(segIdx, chunkIdx, {
        pasteError: lastErrorMsg
          ? `JSON 解析失败：${lastErrorMsg.slice(0, 120)}${extra}\n` +
            "常见原因：LLM 在描述里写了未转义的引号（应为 \\\"…\\\" 而不是 \"…\"），或复制时把直引号变成了弯引号。"
          : "在 JSON 中没找到任何事件。预期形如 {events: [...]}。",
      });
      return;
    }

    // Use the candidate with the most events. Drop event-shaped items
    // missing both name and description so we don't commit junk.
    const rawEvents = candidates
      .sort((a, b) => b.length - a.length)[0]
      .filter((e: any) => e && typeof e === "object" && (e.name || e.description));
    if (rawEvents.length === 0) {
      patchChunk(segIdx, chunkIdx, {
        pasteError: "解析到的事件都没有 name 或 description 字段。",
      });
      return;
    }
    // Post-process time markers:
    //   1. Split each event's `time_marker` on common multi-stamp
    //      separators (·, ；, /, |, ＋, +) so events with multiple
    //      timestamps in one string become an array.
    //   2. Classify each piece as "absolute" (contains a year or
    //      explicit date) or "relative" (同日 / 次日 / 倒计时…).
    //   3. When an event has no absolute timestamp of its own, copy
    //      the LAST absolute timestamp seen — that way "同日傍晚" gets
    //      stored alongside the inherited "2022 年秋某周二傍晚" so the
    //      reader can cross-reference.
    // Both the original strings and the inherited one are kept in
    // `time_markers` (deduped); `time_marker` stays populated with the
    // first entry for backward compat with anything that still reads it.
    const SEP = /\s*[·／/|｜＋+；;]\s*/;
    const isAbsoluteTime = (s: string): boolean => {
      if (!s) return false;
      // Any 4-digit year, any 公元/纪元 marker, or full date patterns.
      return /\d{4}\s*年|公元|纪元|世纪|\d{4}[-/]\d{1,2}/.test(s);
    };
    let lastAbsolute: string | null = null;
    const events = rawEvents.map((ev: any) => {
      const next: any = { ...ev };
      const incoming = typeof ev.time_marker === "string" ? ev.time_marker.trim() : "";
      const incomingList = Array.isArray(ev.time_markers)
        ? ev.time_markers.map((s: any) => (typeof s === "string" ? s.trim() : "")).filter(Boolean)
        : [];
      const pieces: string[] = [];
      const seen = new Set<string>();
      const add = (s: string) => {
        const v = s.trim();
        if (v && !seen.has(v)) { seen.add(v); pieces.push(v); }
      };
      for (const t of incomingList) add(t);
      if (incoming) {
        // Only split if the string actually contains a separator —
        // otherwise relative tokens like "同日傍晚" stay intact.
        if (SEP.test(incoming)) {
          for (const part of incoming.split(SEP)) add(part);
        } else {
          add(incoming);
        }
      }
      const hasOwnAbsolute = pieces.some(isAbsoluteTime);
      if (!hasOwnAbsolute && lastAbsolute) {
        // Prepend the inherited absolute so the reader sees "1954 年春"
        // ahead of the relative "同日傍晚".
        if (!seen.has(lastAbsolute)) {
          pieces.unshift(lastAbsolute);
          seen.add(lastAbsolute);
        }
      }
      // Update lastAbsolute trailer for the next event.
      for (const p of pieces) {
        if (isAbsoluteTime(p)) lastAbsolute = p;
      }
      if (pieces.length > 0) {
        next.time_markers = pieces;
        next.time_marker = pieces[0];
      }
      return next;
    });
    patchChunk(segIdx, chunkIdx, {
      status: "ready",
      source: "paste",
      events,
      pasteError: undefined,
      pasteRaw: undefined,
    });
  };

  /** Merge a chunk's events into the current chronicle: one epoch per
   *  volume (matched by title), one period per first_chapter. New
   *  events are appended to existing periods if the chapter already
   *  has an entry. */
  const mergeEventsIntoChronicle = (
    base: PlotOutline | null,
    newEvents: any[],
    volumeTitle: string,
  ): PlotOutline => {
    const next: PlotOutline = base
      ? {
          ...base,
          epochs: (base.epochs || []).map(e => ({
            ...e,
            periods: (e.periods || []).map(p => ({
              ...p, events: [...(p.events || [])],
            })),
          })),
        }
      : { logline: "", epochs: [] };
    let epoch = (next.epochs || []).find(e => e.title === volumeTitle);
    if (!epoch) {
      epoch = { title: volumeTitle, periods: [] };
      next.epochs = [...(next.epochs || []), epoch];
    }
    for (const ev of newEvents) {
      const key = ((ev.first_chapter || "") + "").trim() || "(未指定章节)";
      let period = (epoch.periods || []).find(p => (p.time || "") === key);
      if (!period) {
        period = { time: key, events: [] };
        epoch.periods = [...(epoch.periods || []), period];
      }
      period.events = [...(period.events || []), ev];
    }
    return next;
  };

  const commitChunk = async (segIdx: number, chunkIdx: number) => {
    const st = chunkState[chunkKey(segIdx, chunkIdx)];
    if (!st || st.status !== "ready" || !st.events || st.events.length === 0) return;
    const seg = plan?.segments[segIdx];
    if (!seg) return;
    patchChunk(segIdx, chunkIdx, { status: "committing" });
    try {
      const merged = mergeEventsIntoChronicle(
        plotOutline, st.events,
        seg.title || `第 ${segIdx + 1} 卷`,
      );
      await onSavePlot(merged);
      patchChunk(segIdx, chunkIdx, { status: "committed" });
      toast(`第 ${segIdx + 1} 卷 · 分段 ${chunkIdx + 1} 已入库（${st.events.length} 事件）`, "success");
    } catch (e: any) {
      patchChunk(segIdx, chunkIdx, { status: "ready", error: e?.message || "入库失败" });
      toast(e?.message || "入库失败", "error");
    }
  };

  const sendChunkChat = async (segIdx: number, chunkIdx: number) => {
    const k = chunkKey(segIdx, chunkIdx);
    const st = chunkState[k];
    if (!st || st.source !== "ai" || !st.events) return;
    const text = (st.chatInput || "").trim();
    if (!text || st.chatSending) return;
    const userMsg = { role: "user" as const, content: text };
    const nextChat = [...(st.chat || []), userMsg];
    patchChunk(segIdx, chunkIdx, {
      chat: nextChat, chatInput: "", chatSending: true,
    });
    try {
      // Reuse the existing /segments/chat endpoint with a synthetic
      // segment result so we don't need a new backend route just for
      // chunk-level chat refinement.
      const r = await apiPost<{
        assistant_message: string;
        revised: { plot_outline?: { epochs?: any[] }; characters?: any[]; settings?: any[] };
      }>(
        `/api/references/works/${refId}/segments/chat`,
        {
          segment_index: segIdx,
          messages: nextChat,
          current_result: {
            index: segIdx,
            plot_outline: {
              logline: "",
              epochs: [{ title: "(本段)", periods: [{ time: "", events: st.events }] }],
            },
            characters: [], settings: [],
          },
        },
        { timeoutMs: 300_000 },
      );
      const revisedEvents = (() => {
        const eps = r.revised?.plot_outline?.epochs;
        if (!Array.isArray(eps)) return null;
        const flat: any[] = [];
        for (const ep of eps)
          for (const per of (ep.periods || []))
            for (const ev of (per.events || [])) flat.push(ev);
        return flat;
      })();
      patchChunk(segIdx, chunkIdx, {
        chat: [...nextChat, { role: "assistant", content: r.assistant_message || "（无回复）" }],
        events: revisedEvents && revisedEvents.length > 0 ? revisedEvents : st.events,
        chatSending: false,
      });
    } catch (e: any) {
      patchChunk(segIdx, chunkIdx, {
        chat: [...nextChat, { role: "assistant", content: `（出错）${e?.message || "对话失败"}` }],
        chatSending: false,
      });
    }
  };

  /** One-click run: walks every (segment, chunk) that hasn't been
   *  committed yet, runs the internal AI extraction, and merges results
   *  into the chronicle in sequence. Updates a progress bar; can be
   *  cancelled mid-loop (the current chunk finishes, then the rest are
   *  skipped). Already-committed chunks are skipped so the action is
   *  safe to re-run after a partial failure. */
  const runAllChunksAI = async () => {
    if (!plan || bulkRunning) return;
    setBulkRunning(true);
    bulkCancelRef.current = false;
    try {
      // 1. Make sure every segment's chunk list is loaded. We load
      //    sequentially (not Promise.all) to give the user immediate
      //    visual feedback and to avoid hammering the backend.
      const loaded: Array<{ seg: SegmentInfo; chunks: ChunkMeta[] }> = [];
      for (const s of plan.segments) {
        let cs = segChunksRef.current[s.index];
        if (!cs) {
          try {
            const r = await apiGet<{ chunks: ChunkMeta[] }>(
              `/api/references/works/${refId}/segments/${s.index}/chunks`,
            );
            cs = r.chunks || [];
            setSegChunks(prev => ({ ...prev, [s.index]: cs! }));
            segChunksRef.current = { ...segChunksRef.current, [s.index]: cs };
          } catch (e: any) {
            toast(`第 ${s.index + 1} 卷分段加载失败：${e?.message || e}`, "error");
            continue;
          }
        }
        loaded.push({ seg: s, chunks: cs });
      }

      // 2. Build the work list, skipping chunks that are already
      //    committed so the user can safely retry a partial run.
      const tasks: Array<{ seg: SegmentInfo; chunk: ChunkMeta }> = [];
      for (const { seg, chunks } of loaded) {
        for (const ck of chunks) {
          const k = chunkKey(seg.index, ck.chunk_index);
          if (chunkStateRef.current[k]?.status === "committed") continue;
          tasks.push({ seg, chunk: ck });
        }
      }
      if (tasks.length === 0) {
        toast("所有分段都已入库，无需重复处理。", "info");
        setBulkRunning(false);
        return;
      }

      // 3. Iterate, accumulating into a local mirror of the chronicle
      //    so each commit's merge sees the prior chunks' events even
      //    before onSavePlot's async parent update lands.
      let acc: PlotOutline = plotOutlineRef.current
        ? JSON.parse(JSON.stringify(plotOutlineRef.current))
        : { logline: "", epochs: [] };
      let failed = 0;
      setBulkProgress({
        done: 0, total: tasks.length,
        label: `第 ${tasks[0].seg.index + 1} 卷 · 分段 ${tasks[0].chunk.chunk_index + 1}`,
        failed: 0,
      });

      for (let i = 0; i < tasks.length; i++) {
        if (bulkCancelRef.current) break;
        const { seg, chunk } = tasks[i];
        const k = chunkKey(seg.index, chunk.chunk_index);
        setBulkProgress({
          done: i, total: tasks.length, failed,
          label: `第 ${seg.index + 1} 卷 · 分段 ${chunk.chunk_index + 1}`,
        });
        patchChunk(seg.index, chunk.chunk_index, {
          status: "extracting", error: undefined,
          startedAt: Date.now(),
        });
        try {
          const r = await apiPost<{ events: any[]; elapsed_s: number; errors: string[] }>(
            `/api/references/works/${refId}/segments/${seg.index}/chunks/${chunk.chunk_index}/extract`,
            { use_web_search: useWebSearch && !!webSearchCap?.enabled },
            { timeoutMs: 600_000 },
          );
          if ((r.errors && r.errors.length > 0) || !r.events || r.events.length === 0) {
            failed++;
            patchChunk(seg.index, chunk.chunk_index, {
              status: "failed",
              error: (r.errors && r.errors.join("; ")) || "AI 返回 0 事件",
              elapsedS: r.elapsed_s,
            });
            continue;
          }
          acc = mergeEventsIntoChronicle(
            acc, r.events, seg.title || `第 ${seg.index + 1} 卷`,
          );
          try {
            await onSavePlot(acc);
          } catch (saveErr: any) {
            failed++;
            patchChunk(seg.index, chunk.chunk_index, {
              status: "failed",
              error: `入库失败：${saveErr?.message || saveErr}`,
            });
            continue;
          }
          patchChunk(seg.index, chunk.chunk_index, {
            status: "committed", source: "ai",
            events: r.events, elapsedS: r.elapsed_s,
          });
        } catch (e: any) {
          failed++;
          patchChunk(seg.index, chunk.chunk_index, {
            status: "failed",
            error: e?.message || "AI 提取失败",
          });
        }
      }
      setBulkProgress({
        done: tasks.length, total: tasks.length, failed,
        label: bulkCancelRef.current ? "已取消" : "完成",
      });
      const succeeded = tasks.length - failed;
      const cancelled = bulkCancelRef.current;
      toast(
        cancelled
          ? `已取消（成功 ${succeeded}，失败 ${failed}）`
          : failed === 0
            ? `批量处理完成：${succeeded} 个分段全部入库`
            : `批量处理完成：成功 ${succeeded}，失败 ${failed}`,
        failed > 0 && !cancelled ? "info" : "success",
      );
    } finally {
      setBulkRunning(false);
      // Leave the progress visible for a beat so the user can read it.
      setTimeout(() => setBulkProgress(null), 2000);
    }
  };

  const cancelBulk = () => { bulkCancelRef.current = true; };

  const finalize = async () => {
    setMerging(true);
    try {
      const r = await apiPost<any>(`/api/references/works/${refId}/segments/finalize`, {});
      toast(`已合并 ${r.merge?.merged_segments || 0} 段到全书`, "success");
      await onAfterMerge();
      await loadPlan();
    } catch (e: any) {
      toast(e?.message || "合并失败", "error");
    } finally { setMerging(false); }
  };

  const reset = async () => {
    if (!confirm("确认清空所有已完成的分段进度？")) return;
    try {
      await apiPost(`/api/references/works/${refId}/segments/reset`, {});
      toast("已重置分段进度", "success");
      await loadPlan();
    } catch (e: any) {
      toast(e?.message || "重置失败", "error");
    }
  };

  // ── render ──
  return (
    <div className="flex flex-col gap-12">
      {/* ════════ Section 1: 大纲提取 ════════
        * Per-volume, per-chunk event extraction. Each chunk produces
        * a flat events array (in textual / chapter order). The merged
        * result lands in the chronicle display section below; from
        * there the user can run the 全时间线总结 step. */}
      {hasFullText && plan && (
        <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 8 }}>
          大纲提取
        </div>
      )}

      {/* Segment plan + preview (only if work has full text and not in standalone manual mode) */}
      {hasFullText && plan && (
        <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", padding: 12, background: "var(--bg-surface)" }}>
          <div className="flex items-center justify-between" style={{ marginBottom: 8, flexWrap: "wrap", gap: 8 }}>
            <div>
              <div className="flex items-center gap-8">
                <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
                  分段提取大纲
                </span>
                {plan.is_custom && (
                  <span className="tag" style={{
                    fontSize: 10, padding: "1px 6px",
                    color: "var(--accent)",
                    background: "var(--accent-subtle)",
                    border: "1px solid var(--accent)",
                  }} title="已使用自定义分段">已自定义</span>
                )}
              </div>
              <div className="text-xs text-muted" style={{ marginTop: 2 }}>
                {total === 0
                  ? `共 ${plan.total_chapters} 章 · 尚未划分卷`
                  : plan.is_custom
                    ? `自定义 · ${plan.segments.length} 段 · ${doneCount}/${total} 已完成`
                    : (plan.type === "volumes" ? "按卷处理" : "按 ~10 万字分块")
                      + ` · ${doneCount}/${total} 已完成`}
              </div>
            </div>
            <div className="flex items-center gap-8" style={{ flexWrap: "wrap" }}>
              <label
                className="flex items-center gap-6"
                style={{
                  fontSize: 11,
                  cursor: webSearchCap?.enabled ? "pointer" : "not-allowed",
                  color: webSearchCap?.enabled ? "var(--text-secondary)" : "var(--text-tertiary)",
                  padding: "3px 8px",
                  borderRadius: 3,
                  border: `1px solid ${useWebSearch && webSearchCap?.enabled ? "var(--accent)" : "var(--border)"}`,
                  background: useWebSearch && webSearchCap?.enabled ? "var(--accent-subtle)" : "transparent",
                }}
                title={
                  webSearchCap?.enabled
                    ? `开启后 AI 会用 ${webSearchCap.provider}/${webSearchCap.model} 联网验证抽取结果，降低幻觉。`
                    : (webSearchCap?.reason || "未配置联网模型；请到「设置 → Pipeline 配置 → 参考作品 AI 联网补全」选择支持 web search 的模型")
                }
              >
                <input
                  type="checkbox"
                  checked={useWebSearch && !!webSearchCap?.enabled}
                  onChange={e => setUseWebSearch(e.target.checked)}
                  disabled={!webSearchCap?.enabled}
                  style={{ width: 13, height: 13 }}
                />
                AI 联网验证
              </label>
              {total > 0 && (
                <button
                  className="btn-primary"
                  style={{ fontSize: 12, padding: "4px 14px" }}
                  onClick={runAllChunksAI}
                  disabled={bulkRunning || merging}
                  title="对每一卷的每一分段都调用内置 AI，已入库的分段会跳过"
                >
                  {bulkRunning ? "批量处理中…" : "使用内置 AI 一键处理全部分段"}
                </button>
              )}
              {doneCount > 0 && !allDone && (
                <button className="btn" style={{ fontSize: 11, padding: "3px 10px", color: "var(--text-tertiary)" }} onClick={reset} disabled={merging || bulkRunning}>
                  重置
                </button>
              )}
              {allDone && (
                <button className="btn-primary" style={{ fontSize: 12, padding: "4px 12px" }} onClick={finalize} disabled={merging || bulkRunning}>
                  {merging ? "合并中..." : "合并到全书"}
                </button>
              )}
            </div>
          </div>

          {/* Bulk-run progress bar */}
          {bulkProgress && (
            <div style={{
              marginBottom: 10, padding: "8px 10px",
              border: "1px solid var(--accent)", borderRadius: 4,
              background: "var(--bg-card)",
            }}>
              <div className="flex items-center" style={{ gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
                <span style={{ fontSize: 11, fontWeight: 600, color: "var(--accent)" }}>
                  {bulkRunning ? "批量处理中" : "已完成"}
                </span>
                <span className="text-xs text-muted">
                  {bulkProgress.done}/{bulkProgress.total}
                  {bulkProgress.label ? ` · ${bulkProgress.label}` : ""}
                  {bulkProgress.failed > 0 ? ` · 失败 ${bulkProgress.failed}` : ""}
                </span>
                <div style={{ flex: 1 }} />
                {bulkRunning && (
                  <button className="btn" onClick={cancelBulk}
                          style={{ fontSize: 11, padding: "2px 10px", color: "var(--error)" }}>
                    取消
                  </button>
                )}
              </div>
              <div style={{
                height: 6, background: "var(--bg-surface-2)",
                borderRadius: 3, overflow: "hidden",
              }}>
                <div style={{
                  height: "100%",
                  width: `${bulkProgress.total > 0 ? (bulkProgress.done / bulkProgress.total) * 100 : 0}%`,
                  background: "var(--accent)",
                  transition: "width 0.25s",
                }} />
              </div>
            </div>
          )}

          {/* Per-segment commit-status bar (hidden when there are no
            * segments yet, and hidden while the bulk run is showing
            * its own progress to avoid two stacked progress bars). */}
          {total > 0 && !bulkProgress && (
            <div style={{ height: 5, background: "var(--bg-surface-2)", borderRadius: 3, overflow: "hidden", marginBottom: 10 }}>
              <div style={{ height: "100%", width: `${(doneCount / total) * 100}%`, background: "var(--jade)", borderRadius: 3, transition: "width 0.3s" }} />
            </div>
          )}

          {/* EMPTY STATE — no volumes yet. Direct user to the 预处理 tab. */}
          {total === 0 && (
            <div style={{
              padding: 16, textAlign: "center",
              border: "1px dashed var(--border)", borderRadius: 4,
              background: "var(--bg-surface)",
            }}>
              <div className="text-xs text-muted" style={{ marginBottom: 12, lineHeight: 1.6 }}>
                还没有分卷。请到「预处理」tab 划分卷后再进行特征提取。
              </div>
              {onGoToPreprocess && (
                <button className="btn-primary" style={{ fontSize: 12, padding: "5px 14px" }}
                        onClick={onGoToPreprocess}>
                  去「预处理」tab 编辑分卷
                </button>
              )}
            </div>
          )}

          {/* ─── Per-volume chunk list ───
            * Each volume row is collapsible. When expanded, shows the
            * list of chunks. Each chunk is an independent unit of
            * work: view prompt → pick AI or paste-from-web → preview
            * events → 确认入库.
            *
            * Volume rename and 编辑分段 actions live in the preprocess
            * tab — this section is purely about extracting events. */}
          {total > 0 && (
          <div className="flex flex-col gap-4">
            {plan.segments.map(s => {
              // Volume is "done" either via the legacy server-side
              // per-segment commit (plan.completed) OR when every chunk
              // has been committed via the per-chunk flow (derived
              // client-side from the chronicle).
              const isDone = completed.has(s.index) || isVolumeFullyCommitted(s.index);
              const isOpen = openSegs.has(s.index);
              const chunks = segChunks[s.index] || [];
              const chunksLoading = segChunksLoading.has(s.index);
              return (
                <div key={s.index}>
                  <button
                    className="btn-ghost w-full"
                    onClick={() => toggleSeg(s.index)}
                    style={{
                      display: "flex", alignItems: "center", gap: 8,
                      padding: "6px 10px",
                      background: isDone ? "rgba(52,168,83,0.06)" : "transparent",
                      border: "1px solid var(--border)", borderRadius: 4,
                      justifyContent: "flex-start", textAlign: "left",
                    }}>
                    <span style={{
                      transition: "transform 0.15s",
                      transform: isOpen ? "rotate(90deg)" : "none",
                      display: "inline-block", color: "var(--text-tertiary)",
                    }}>▶</span>
                    <span className="tag" style={{
                      fontSize: 10, minWidth: 36, textAlign: "center",
                      color: isDone ? "var(--jade)" : "var(--text-secondary)",
                      background: "transparent",
                      border: `1px solid ${isDone ? "var(--jade)" : "var(--border)"}`,
                    }}>
                      {isDone ? "已完成" : `#${s.index + 1}`}
                    </span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="truncate" style={{ fontSize: 12, fontWeight: 500, color: "var(--text-primary)" }}>
                        {s.title}
                      </div>
                      <div className="text-xs text-muted">
                        第 {s.start_chapter}–{s.end_chapter} 章 · 共 {s.chapter_count ?? (s.end_chapter - s.start_chapter + 1)} 章 · {fmtChars(s.char_count)}
                      </div>
                    </div>
                  </button>

                  {isOpen && (
                    <div style={{ marginTop: 6, marginLeft: 18, paddingLeft: 10, borderLeft: "2px solid var(--border)" }}>
                      {chunksLoading && (
                        <div className="text-xs text-muted" style={{ padding: 6 }}>正在划分本卷分段…</div>
                      )}
                      {!chunksLoading && chunks.length === 0 && (
                        <div className="text-xs text-muted" style={{ padding: 6 }}>本卷为空。</div>
                      )}
                      {!chunksLoading && chunks.length > 0 && chunks.map(ck => {
                        const k = chunkKey(s.index, ck.chunk_index);
                        const st = chunkState[k] || { status: "idle" as const };
                        const isChunkOpen = openChunks.has(k);
                        return (
                          <ChunkRow
                            key={k}
                            refId={refId}
                            segIdx={s.index}
                            chunk={ck}
                            state={st}
                            open={isChunkOpen}
                            onToggle={() => toggleChunk(s.index, ck.chunk_index)}
                            onRunAI={() => runChunkAI(s.index, ck.chunk_index)}
                            onPasteRawChange={(v) => patchChunk(s.index, ck.chunk_index, { pasteRaw: v, pasteError: undefined })}
                            onParsePaste={() => parseChunkPaste(s.index, ck.chunk_index, st.pasteRaw || "")}
                            onCommit={() => commitChunk(s.index, ck.chunk_index)}
                            onChatInputChange={(v) => patchChunk(s.index, ck.chunk_index, { chatInput: v })}
                            onSendChat={() => sendChunkChat(s.index, ck.chunk_index)}
                            onResetChunk={() => patchChunk(s.index, ck.chunk_index, {
                              status: "idle", source: undefined, events: undefined,
                              error: undefined, chat: undefined, chatInput: undefined,
                              pasteRaw: undefined, pasteError: undefined,
                            })}
                          />
                        );
                      })}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          )}

        </div>
      )}

      {planLoading && !plan && (
        <div className="text-xs text-muted" style={{ padding: 8 }}>加载分段计划中...</div>
      )}

      {/* ════════ Section 2: 编年史展示 ════════
        * The merged chronicle viewer/editor. Once the extraction
        * section above has populated events from every segment, the
        * user can run the "全时间线总结" feature inside this editor
        * to story-time-reorder the whole book. */}
      <div style={{ marginTop: 16, paddingTop: 12, borderTop: "2px solid var(--border)" }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 8 }}>
          编年史
        </div>
        <PlotOutlineEditor
          data={plotOutline}
          onSave={onSavePlot}
          onExtract={hasFullText && onRegenerateFromText ? onRegenerateFromText : undefined}
          extracting={regenerating}
          refId={refId}
        />
      </div>
    </div>
  );
}

/* ──────────────── Compact read-only chronicle preview (no editing) ──────────────── */
function ChroniclePreview({ epochs }: { epochs: ChronicleEpoch[] }) {
  if (!epochs || epochs.length === 0) {
    return <div className="text-xs text-muted">未生成任何大纲条目。</div>;
  }
  return (
    <div className="flex flex-col gap-10" style={{ maxHeight: 360, overflowY: "auto" }}>
      {epochs.map((ep, ei) => (
        <div key={ei}>
          {ep.title && (
            <div style={{ fontWeight: 700, fontSize: 13, color: "var(--text-primary)", marginBottom: 4 }}>
              {ep.title}
            </div>
          )}
          {(ep.periods || []).map((per: ChroniclePeriod, pi: number) => (
            <div key={pi} style={{ marginBottom: 8, paddingLeft: ep.title ? 8 : 0 }}>
              <div style={{ fontWeight: 600, fontSize: 12, color: "var(--accent)", marginBottom: 4 }}>
                {per.time || "(未填写时间)"}
              </div>
              <div className="flex flex-col gap-4" style={{ paddingLeft: 8, borderLeft: "2px solid var(--border)" }}>
                {(per.events || []).map((ev: any, evi: number) => (
                  <div key={evi} style={{ paddingLeft: 8 }}>
                    <div style={{ fontSize: 11, lineHeight: 1.55 }}>
                      <span style={{ fontWeight: 600, color: "var(--accent)" }}>
                        【{ev.subject}·{categoryLabel(ev.category)}·{ev.name}】
                      </span>
                      {timeMarkers(ev).map((t, ti) => (
                        <span key={`t${ti}`} style={{
                          marginLeft: ti === 0 ? 4 : 2, fontSize: 10, padding: "0 5px",
                          color: "var(--gold)", border: "1px solid var(--gold)",
                          borderRadius: 3,
                        }} title="故事中时间">{t}</span>
                      ))}
                      {ev.first_chapter && (
                        <span style={{
                          marginLeft: 3, fontSize: 10, padding: "0 5px",
                          color: "var(--jade)", border: "1px solid var(--jade)",
                          borderRadius: 3,
                        }} title="首次出现章节">{ev.first_chapter}</span>
                      )}
                      {" "}
                      <span style={{ color: "var(--text-secondary)" }}>{ev.description}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}


/* ─── Per-chunk extraction row ─────────────────────────────────────
 * Renders one chunk inside an expanded volume. Three primary states:
 *   - idle:     show prompt panel + [AI 提取] / [解析网页 LLM 回复] buttons
 *   - extracting (AI only): show spinner
 *   - ready:    show events preview, [chatbox if AI], [确认入库] button
 *   - failed:   show error + reminder to copy-prompt to a web LLM
 *   - committed: collapsed success state with [重新提取] reset button
 * Paste-mode buffer lives in the chunk state too so it survives the
 * row being collapsed/re-expanded mid-edit. */
function ChunkRow({
  refId, segIdx, chunk, state, open,
  onToggle, onRunAI, onPasteRawChange, onParsePaste,
  onCommit, onChatInputChange, onSendChat, onResetChunk,
}: {
  refId: string;
  segIdx: number;
  chunk: ChunkMeta;
  state: ChunkExtractionState;
  open: boolean;
  onToggle: () => void;
  onRunAI: () => void;
  onPasteRawChange: (raw: string) => void;
  onParsePaste: () => void;
  onCommit: () => void;
  onChatInputChange: (v: string) => void;
  onSendChat: () => void;
  onResetChunk: () => void;
}) {
  const [showPaste, setShowPaste] = useState(false);
  const ready = state.status === "ready";
  const failed = state.status === "failed";
  const extracting = state.status === "extracting";
  const committing = state.status === "committing";
  const committed = state.status === "committed";

  const eventCount = (state.events || []).length;

  return (
    <div style={{
      marginTop: 6, marginBottom: 6,
      border: `1px solid ${committed ? "var(--jade)" : ready ? "var(--accent)" : failed ? "var(--error)" : "var(--border)"}`,
      borderRadius: 4,
      background: committed ? "rgba(52,168,83,0.04)" : "var(--bg-card)",
    }}>
      <button
        className="btn-ghost w-full"
        onClick={onToggle}
        style={{
          display: "flex", alignItems: "center", gap: 8,
          padding: "5px 10px", textAlign: "left",
          justifyContent: "flex-start", borderRadius: 0,
        }}>
        <span style={{
          transition: "transform 0.15s",
          transform: open ? "rotate(90deg)" : "none",
          display: "inline-block", fontSize: 9, color: "var(--text-tertiary)",
        }}>▶</span>
        <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-primary)" }}>
          分段 {chunk.chunk_index + 1}/{chunk.total_chunks}
        </span>
        <span className="text-xs text-muted">
          第 {chunk.start_chapter}–{chunk.end_chapter} 章 · {chunk.n_chapters} 章 · {chunk.n_chars.toLocaleString()} 字
        </span>
        <div style={{ flex: 1 }} />
        {committed && (
          <span className="tag" style={{
            fontSize: 10, padding: "1px 6px",
            color: "var(--jade)", border: "1px solid var(--jade)",
          }}>已入库 · {eventCount} 事件</span>
        )}
        {ready && !committed && (
          <span className="tag" style={{
            fontSize: 10, padding: "1px 6px",
            color: "var(--accent)", border: "1px solid var(--accent)",
          }}>{state.source === "ai" ? "AI 已生成" : "已解析"} · {eventCount} 事件</span>
        )}
        {failed && (
          <span className="tag" style={{
            fontSize: 10, padding: "1px 6px",
            color: "var(--error)", border: "1px solid var(--error)",
          }}>AI 失败</span>
        )}
        {extracting && state.startedAt && (
          <ExtractionTimer startedAt={state.startedAt} totalChars={chunk.n_chars} />
        )}
      </button>

      {open && (
        <div style={{ padding: "8px 10px", borderTop: "1px dashed var(--border)" }}>
          {/* 1. Prompt copy panel — always available so the user can
            *    inspect what would be sent before running anything, AND
            *    can copy it for use in a web LLM. The shared panel
            *    handles fetching from /preview_chunks. */}
          {!committed && (
            <PromptCopyPanel
              refId={refId}
              promptKey="reference.outline"
              segmentIndex={segIdx}
              chunkIndex={chunk.chunk_index}
              defaultOpen
              label={`分段 ${chunk.chunk_index + 1} 的 prompt（含本段 ${chunk.n_chapters} 章正文）`}
            />
          )}

          {/* 2. Action area — varies by status */}
          {(state.status === "idle" || failed) && !committed && (
            <div className="flex items-center gap-6" style={{ flexWrap: "wrap", marginBottom: 6 }}>
              <button className="btn-primary"
                      onClick={onRunAI}
                      disabled={extracting}
                      style={{ fontSize: 11, padding: "3px 12px" }}>
                {extracting ? "AI 提取中…" : "用内置 AI 提取本段"}
              </button>
              <button className="btn"
                      onClick={() => setShowPaste(p => !p)}
                      style={{ fontSize: 11, padding: "3px 10px" }}>
                {showPaste ? "收起" : "解析网页 LLM 回复"}
              </button>
            </div>
          )}

          {failed && state.error && (
            <div style={{
              padding: "6px 10px", marginBottom: 6,
              background: "var(--bg-surface)", border: "1px solid var(--error)",
              borderRadius: 3, fontSize: 11, color: "var(--error)", lineHeight: 1.55,
            }}>
              <div style={{ fontWeight: 600, marginBottom: 2 }}>内置 AI 提取失败</div>
              <div style={{ wordBreak: "break-word" }}>{state.error}</div>
              <div style={{ marginTop: 4, color: "var(--text-secondary)" }}>
                请点上方「复制本段」把 prompt 拷到 ChatGPT / Claude.ai，再用「解析网页 LLM 回复」入库。
              </div>
            </div>
          )}

          {/* 3. Paste-reply form (manual web-LLM path) */}
          {showPaste && !ready && !committed && (
            <div style={{ marginBottom: 8 }}>
              <textarea className="input font-mono"
                        rows={5}
                        value={state.pasteRaw || ""}
                        onChange={e => onPasteRawChange(e.target.value)}
                        placeholder='{"events":[{"first_chapter":"第1章",...}]}'
                        style={{
                          fontSize: 11, lineHeight: 1.5, resize: "vertical",
                          background: "var(--bg-app)", marginBottom: 6,
                        }} />
              <div className="flex items-center gap-6">
                <button className="btn-primary"
                        onClick={onParsePaste}
                        disabled={!(state.pasteRaw && state.pasteRaw.trim())}
                        style={{ fontSize: 11, padding: "3px 10px" }}>
                  解析并预览
                </button>
                {state.pasteError && (
                  <pre className="text-xs" style={{
                    color: "var(--error)", whiteSpace: "pre-wrap",
                    wordBreak: "break-word", margin: "4px 0 0",
                    flexBasis: "100%", lineHeight: 1.55,
                  }}>{state.pasteError}</pre>
                )}
              </div>
            </div>
          )}

          {/* 4. Preview (success path: AI or paste) */}
          {ready && !committed && state.events && (
            <ChunkEventsPreview events={state.events} />
          )}

          {/* 5. Chat-with-AI (only when source === "ai") */}
          {ready && !committed && state.source === "ai" && (
            <div style={{
              marginTop: 8, borderTop: "1px dashed var(--border)", paddingTop: 8,
            }}>
              <div className="flex items-center justify-between" style={{ marginBottom: 6 }}>
                <span style={{ fontSize: 11, fontWeight: 600, color: "var(--accent)" }}>与 AI 对话调整本段</span>
                <span className="text-xs text-muted">修改会即时应用到上方预览</span>
              </div>
              {(state.chat || []).length > 0 && (
                <div style={{
                  maxHeight: 180, overflowY: "auto",
                  border: "1px solid var(--border)", borderRadius: 4,
                  padding: 6, marginBottom: 6, background: "var(--bg-app)",
                }}>
                  {(state.chat || []).map((m, i) => (
                    <div key={i} style={{
                      marginBottom: 6, display: "flex",
                      flexDirection: m.role === "user" ? "row-reverse" : "row",
                    }}>
                      <div style={{
                        maxWidth: "85%", padding: "5px 10px", borderRadius: 6,
                        fontSize: 12, lineHeight: 1.55, whiteSpace: "pre-wrap",
                        background: m.role === "user" ? "var(--accent-subtle)" : "var(--bg-surface)",
                        color: m.role === "user" ? "var(--accent)" : "var(--text-primary)",
                        border: `1px solid ${m.role === "user" ? "var(--accent)" : "var(--border)"}`,
                      }}>{m.content}</div>
                    </div>
                  ))}
                </div>
              )}
              <div className="flex gap-6">
                <textarea className="input" rows={2}
                          value={state.chatInput || ""}
                          onChange={e => onChatInputChange(e.target.value)}
                          placeholder="例：合并第 3 章里两条重复的事件…"
                          onKeyDown={e => {
                            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                              e.preventDefault();
                              onSendChat();
                            }
                          }}
                          disabled={!!state.chatSending}
                          style={{ flex: 1, fontSize: 12, resize: "vertical" }} />
                <button className="btn-primary"
                        onClick={onSendChat}
                        disabled={!!state.chatSending || !(state.chatInput && state.chatInput.trim())}
                        style={{ fontSize: 11, padding: "3px 12px", alignSelf: "stretch" }}>
                  {state.chatSending ? "发送中" : "发送"}
                </button>
              </div>
            </div>
          )}

          {/* 6. Commit + reset row (visible in ready state, both AI and paste) */}
          {ready && !committed && (
            <div className="flex items-center gap-6" style={{
              justifyContent: "flex-end", marginTop: 8,
              paddingTop: 8, borderTop: "1px dashed var(--border)",
            }}>
              <button className="btn"
                      onClick={onResetChunk}
                      disabled={committing}
                      style={{ fontSize: 11, padding: "3px 10px" }}>
                重置
              </button>
              <button className="btn-primary"
                      onClick={onCommit}
                      disabled={committing || eventCount === 0}
                      style={{ fontSize: 11, padding: "3px 14px" }}>
                {committing ? "入库中…" : `确认入库（${eventCount} 事件）`}
              </button>
            </div>
          )}

          {/* 7. Committed state — let the user redo if needed */}
          {committed && (
            <div className="flex items-center gap-6" style={{ justifyContent: "flex-end", marginTop: 4 }}>
              <button className="btn-ghost"
                      onClick={onResetChunk}
                      style={{ fontSize: 11, padding: "3px 10px", color: "var(--text-tertiary)" }}>
                重新提取本段（不会自动删除已入库事件）
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** Compact preview list of the events about to be committed. */
function ChunkEventsPreview({ events }: { events: any[] }) {
  if (!events || events.length === 0) {
    return <div className="text-xs text-muted">未生成任何事件。</div>;
  }
  // Group by first_chapter so the preview matches how they'll be
  // stored in the chronicle (one period per chapter).
  const groups = new Map<string, any[]>();
  for (const ev of events) {
    const key = ((ev.first_chapter || "") + "").trim() || "(未指定章节)";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(ev);
  }
  return (
    <div style={{
      marginTop: 6, marginBottom: 4,
      maxHeight: 260, overflowY: "auto",
      padding: 6, background: "var(--bg-app)",
      border: "1px solid var(--border)", borderRadius: 3,
    }}>
      {Array.from(groups.entries()).map(([ch, evs], gi) => (
        <div key={gi} style={{ marginBottom: 6 }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: "var(--gold)" }}>{ch}</div>
          <div style={{ paddingLeft: 8, borderLeft: "2px solid var(--border)" }}>
            {evs.map((ev, i) => (
              <div key={i} style={{ fontSize: 11, lineHeight: 1.55, marginTop: 2 }}>
                <span style={{ fontWeight: 600, color: "var(--accent)" }}>
                  【{ev.subject}·{categoryLabel(ev.category)}·{ev.name}】
                </span>
                {timeMarkers(ev).map((t, ti) => (
                  <span key={`t${ti}`} style={{
                    marginLeft: ti === 0 ? 4 : 2, fontSize: 10, padding: "0 5px",
                    color: "var(--gold)", border: "1px solid var(--gold)",
                    borderRadius: 3,
                  }}>{t}</span>
                ))}
                {" "}
                <span style={{ color: "var(--text-secondary)" }}>{ev.description}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

/** Live elapsed-seconds counter for an in-flight extraction. Re-renders
 * every 500 ms so the user sees a moving timer instead of a static
 * spinner. ETA is a deliberately rough heuristic (≈ 60 chars/sec on
 * a typical local LLM); it's labelled "估计" so users don't take it
 * as a guarantee. */
function ExtractionTimer({ startedAt, totalChars }: {
  startedAt: number;
  totalChars?: number;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(t);
  }, []);
  const elapsedS = Math.max(0, Math.floor((now - startedAt) / 1000));
  const etaS = totalChars && totalChars > 0
    ? Math.max(elapsedS, Math.round(totalChars / 60))
    : null;
  return (
    <span className="text-xs" style={{ color: "var(--gold)", fontFamily: "var(--font-mono)" }}>
      提取中… 已用 {elapsedS}s
      {etaS != null && elapsedS < etaS && (
        <span style={{ color: "var(--text-tertiary)" }}>
          {" / 估计 "}{etaS}s
        </span>
      )}
    </span>
  );
}
