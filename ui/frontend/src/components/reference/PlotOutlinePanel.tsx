import React, { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost } from "../../api/client";
import { useToast } from "../shared/Toast";
import { PlotOutlineEditor, PromptCopyPanel } from "./AnalysisEditors";
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
      patchChunk(segIdx, chunkIdx, {
        pasteError: lastErrorMsg
          ? `在 JSON 中没找到任何事件（${lastErrorMsg.slice(0, 80)}）。预期形如 {events: [...]}。`
          : "在 JSON 中没找到任何事件。预期形如 {events: [...]}。",
      });
      return;
    }

    // Use the candidate with the most events. Drop event-shaped items
    // missing both name and description so we don't commit junk.
    const events = candidates
      .sort((a, b) => b.length - a.length)[0]
      .filter((e: any) => e && typeof e === "object" && (e.name || e.description));
    if (events.length === 0) {
      patchChunk(segIdx, chunkIdx, {
        pasteError: "解析到的事件都没有 name 或 description 字段。",
      });
      return;
    }
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
              {doneCount > 0 && !allDone && (
                <button className="btn" style={{ fontSize: 11, padding: "3px 10px", color: "var(--text-tertiary)" }} onClick={reset} disabled={merging}>
                  重置
                </button>
              )}
              {allDone && (
                <button className="btn-primary" style={{ fontSize: 12, padding: "4px 12px" }} onClick={finalize} disabled={merging}>
                  {merging ? "合并中..." : "合并到全书"}
                </button>
              )}
            </div>
          </div>

          {/* progress bar (hidden when there are no segments yet) */}
          {total > 0 && (
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
              const isDone = completed.has(s.index);
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
                        【{ev.subject}·{ev.category}·{ev.name}】
                      </span>
                      {ev.time_marker && (
                        <span style={{
                          marginLeft: 4, fontSize: 10, padding: "0 5px",
                          color: "var(--gold)", border: "1px solid var(--gold)",
                          borderRadius: 3,
                        }} title="故事中时间">⏱ {ev.time_marker}</span>
                      )}
                      {ev.first_chapter && (
                        <span style={{
                          marginLeft: 3, fontSize: 10, padding: "0 5px",
                          color: "var(--jade)", border: "1px solid var(--jade)",
                          borderRadius: 3,
                        }} title="首次出现章节">📖 {ev.first_chapter}</span>
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
          }}>✓ 已入库 · {eventCount} 事件</span>
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
        {extracting && (
          <span className="text-xs" style={{ color: "var(--gold)" }}>提取中…</span>
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
                  <span className="text-xs" style={{ color: "var(--error)" }}>{state.pasteError}</span>
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
                  【{ev.subject}·{ev.category}·{ev.name}】
                </span>
                {ev.time_marker && (
                  <span style={{
                    marginLeft: 4, fontSize: 10, padding: "0 5px",
                    color: "var(--gold)", border: "1px solid var(--gold)",
                    borderRadius: 3,
                  }}>⏱ {ev.time_marker}</span>
                )}
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
