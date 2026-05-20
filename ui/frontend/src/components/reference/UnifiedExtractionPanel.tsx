/* 特征提取 tab — unified per-chunk extraction.
 *
 * The chronicle, characters, settings and style fingerprints all need
 * the SAME chunk of chapter text. Running four separate LLM calls
 * re-uploads that text four times. This panel runs ONE call per chunk
 * (reference.unified prompt) that returns events + characters +
 * settings + style together, then distributes the result into the
 * four work-level JSON blobs on commit.
 *
 * The four browse tabs (剧情大纲 / 角色 / 设定 / 文本特征) keep showing
 * the merged result read/edit-only; extraction happens only here.
 *
 * Committed chunks are remembered via style_fingerprint_json._chunks —
 * every unified commit writes a style chunk entry, so that ledger
 * doubles as the "which chunks are done" record.
 */
import React, { useEffect, useRef, useState } from "react";
import { apiPost } from "../../api/client";
import { useToast } from "../shared/Toast";
import { PromptCopyPanel, categoryLabel } from "./AnalysisEditors";
import type {
  PlotOutline, CharacterItem, SettingItem, RhythmJson,
} from "./AnalysisEditors";
import { useSegmentation } from "./segmentationCache";
import type { ChunkMeta } from "./segmentationCache";
import {
  resolveEventTimeMarkers, mergeEventsIntoChronicle,
  mergeCharacters, mergeSettings, aggregateStyleChunks,
  buildRhythmFromSignals, normalizeUnifiedStyle,
} from "./referenceMerge";
import type { StyleFingerprint, StyleChunkEntry } from "./referenceMerge";

interface ChunkResult {
  events: any[];
  characters: CharacterItem[];
  settings: SettingItem[];
  /** Combined NLP + LLM style fingerprint for this chunk. */
  style: StyleFingerprint;
  nChars: number;
}

/** The four feature sections — each can be committed independently. */
type Section = "events" | "characters" | "settings" | "style";
const SECTION_LABEL: Record<Section, string> = {
  events: "事件", characters: "角色", settings: "设定", style: "风格 / 节奏",
};

type Phase = "idle" | "running" | "ready" | "failed";
interface ChunkPhase {
  status: Phase;
  source?: "ai" | "paste";
  result?: ChunkResult;
  error?: string;
  elapsedS?: number;
  startedAt?: number;
  pasteRaw?: string;
  pasteError?: string;
  /** Section whose 确认入库 is currently in flight (disables that button). */
  committingSection?: Section;
}

/** Settings text must read as plain prose — no parentheses/brackets.
 *  Mirrors the backend `_strip_brackets`: an opening bracket becomes a
 *  comma separator, closing brackets are dropped. */
function stripBrackets(text: string): string {
  if (!text) return text;
  const open = "（(【[｛{［〔";
  const close = "）)】]｝}］〕";
  const noSep = "，,、；;：: \t　";
  let out = "";
  for (const ch of text) {
    if (open.includes(ch)) {
      if (out && !noSep.includes(out[out.length - 1])) out += "，";
    } else if (close.includes(ch)) {
      continue;
    } else {
      out += ch;
    }
  }
  return out.replace(/，{2,}/g, "，")
            .replace(/，([。；！？，、])/g, "$1")
            .replace(/^[，、\s]+|[，、\s]+$/g, "");
}

/** Coerce a pasted setting object into a SettingItem: map the new
 *  `summary` 简介 field onto `content`, strip brackets everywhere. The
 *  AI path is normalized server-side; this covers the paste path. */
function normalizeSetting(s: any): SettingItem {
  if (!s || typeof s !== "object") {
    return { category: "other", title: "", content: "", updates: [] };
  }
  const updates = (Array.isArray(s.updates) ? s.updates : [])
    .map((u: any) => ({
      chapter: ((u && u.chapter) || "").toString(),
      text: stripBrackets(((u && u.text) || "").toString()),
    }))
    .filter((u: { text: string }) => u.text);
  const content =
    stripBrackets(((s.summary || s.content) || "").toString())
    || (updates[0]?.text || "");
  return {
    category: (s.category || "other").toString(),
    title: stripBrackets((s.title || "").toString()),
    content,
    updates,
    first_introduced_at: (s.first_introduced_at || "").toString(),
    first_chapter: (s.first_chapter || "").toString(),
  };
}

/** Lenient parser for a pasted unified web-LLM response. Strips
 *  <think>/code-fences, normalizes smart quotes, repairs unescaped
 *  inner quotes, finds the balanced object carrying events/characters. */
function parseUnifiedPaste(raw: string): {
  result: Omit<ChunkResult, "nChars"> | null; error?: string;
} {
  let s = (raw || "").replace(/<think>[\s\S]*?<\/think>/gi, "")
                     .replace(/<\/?think>/gi, "");
  const fence = s.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  if (fence) s = fence[1];
  s = s.replace(/[﻿​-‍]/g, "")
       .replace(/[“”]/g, '"')
       .replace(/[‘’]/g, "'")
       .replace(/ /g, " ")
       .trim();
  if (!s) return { result: null, error: "请先粘贴 LLM 返回的 JSON" };

  const balancedExtract = (start: number): string | null => {
    if (s[start] !== "{") return null;
    let depth = 0, inString = false, escape = false;
    for (let i = start; i < s.length; i++) {
      const c = s[i];
      if (escape) { escape = false; continue; }
      if (c === "\\") { escape = true; continue; }
      if (c === '"') { inString = !inString; continue; }
      if (inString) continue;
      if (c === "{") depth++;
      else if (c === "}") { depth--; if (depth === 0) return s.slice(start, i + 1); }
    }
    return null;
  };
  const repair = (input: string): string => {
    const out: string[] = [];
    let inString = false, escape = false;
    for (let i = 0; i < input.length; i++) {
      const c = input[i];
      if (escape) { out.push(c); escape = false; continue; }
      if (c === "\\") { out.push(c); escape = true; continue; }
      if (c !== '"') { out.push(c); continue; }
      if (!inString) { out.push(c); inString = true; continue; }
      let j = i + 1;
      while (j < input.length && /\s/.test(input[j])) j++;
      const nxt = j < input.length ? input[j] : "";
      if (nxt === "" || nxt === "," || nxt === "}" || nxt === "]" || nxt === ":") {
        out.push(c); inString = false;
      } else { out.push('\\"'); }
    }
    return out.join("");
  };
  const isUnified = (o: any): boolean =>
    o && typeof o === "object" && (
      Array.isArray(o.events) || Array.isArray(o.characters) ||
      Array.isArray(o.settings) || (o.style && typeof o.style === "object")
    );
  let lastErr = "";
  for (let i = 0; i < s.length; i++) {
    if (s[i] !== "{") continue;
    const ext = balancedExtract(i);
    if (!ext) continue;
    let obj: any = null;
    try {
      obj = JSON.parse(ext);
    } catch (e: any) {
      lastErr = e?.message || "";
      const fixed = repair(ext);
      if (fixed !== ext) {
        try { obj = JSON.parse(fixed); } catch { /* skip */ }
      }
    }
    if (obj && isUnified(obj)) {
      return {
        result: {
          events: Array.isArray(obj.events) ? obj.events : [],
          characters: Array.isArray(obj.characters) ? obj.characters : [],
          settings: Array.isArray(obj.settings) ? obj.settings.map(normalizeSetting) : [],
          style: (obj.style && typeof obj.style === "object") ? obj.style : {},
        },
      };
    }
  }
  return {
    result: null,
    error: lastErr
      ? `JSON 解析失败：${lastErr.slice(0, 120)}`
      : "在 JSON 中没找到统一结构（需含 events / characters / settings / style）。",
  };
}

export function UnifiedExtractionPanel({
  refId, hasFullText,
  plot, characters, settings, style,
  onSavePlot, onSaveCharacters, onSaveSettings, onSaveStyle, onSaveRhythm,
}: {
  refId: string;
  hasFullText: boolean;
  plot: PlotOutline | null;
  characters: CharacterItem[] | null;
  settings: SettingItem[] | null;
  style: StyleFingerprint | null;
  onSavePlot: (d: PlotOutline) => Promise<void> | void;
  onSaveCharacters: (d: CharacterItem[]) => Promise<void> | void;
  onSaveSettings: (d: SettingItem[]) => Promise<void> | void;
  onSaveStyle: (d: StyleFingerprint) => Promise<void> | void;
  /** Rhythm is fully derived from the aggregated chapter_signals — the
   *  unified commit rebuilds rhythm_json so the 节奏 section stays in
   *  sync with每章爽点/信息密度/钩子. */
  onSaveRhythm: (d: RhythmJson) => Promise<void> | void;
}) {
  const { toast } = useToast();
  const { plan, chunks: segChunks, chunkLoading, ensureChunks } =
    useSegmentation(refId, hasFullText);
  const [openSegs, setOpenSegs] = useState<Set<number>>(new Set());
  const [openChunks, setOpenChunks] = useState<Set<string>>(new Set());
  const [chunkPhases, setChunkPhases] = useState<Record<string, ChunkPhase>>({});
  const [bulkRunning, setBulkRunning] = useState(false);
  const [bulkProgress, setBulkProgress] = useState<
    { done: number; total: number; label: string; failed: number } | null
  >(null);

  // The persisted per-chunk style ledger doubles as the "committed"
  // record — every unified commit writes one entry.
  const ledger: Record<string, StyleChunkEntry> = style?._chunks || {};

  // Refs for race-free reads inside the bulk loop (parent props update
  // asynchronously after each onSave).
  const bulkCancelRef = useRef(false);
  const plotRef = useRef(plot);
  const charsRef = useRef(characters);
  const settingsRef = useRef(settings);
  const ledgerRef = useRef(ledger);
  const segChunksRef = useRef(segChunks);
  useEffect(() => { plotRef.current = plot; }, [plot]);
  useEffect(() => { charsRef.current = characters; }, [characters]);
  useEffect(() => { settingsRef.current = settings; }, [settings]);
  useEffect(() => { ledgerRef.current = style?._chunks || {}; }, [style]);
  useEffect(() => { segChunksRef.current = segChunks; }, [segChunks]);

  const ckKey = (s: number, c: number) => `${s}:${c}`;
  const toggleSeg = async (i: number) => {
    const isOpen = openSegs.has(i);
    setOpenSegs(prev => {
      const next = new Set(prev);
      if (isOpen) next.delete(i); else next.add(i);
      return next;
    });
    if (!isOpen) await ensureChunks(i);
  };
  const toggleChunk = (s: number, c: number) => {
    const k = ckKey(s, c);
    setOpenChunks(prev => {
      const next = new Set(prev);
      if (next.has(k)) next.delete(k); else next.add(k);
      return next;
    });
  };
  const patch = (s: number, c: number, p: Partial<ChunkPhase>) => {
    const k = ckKey(s, c);
    setChunkPhases(prev => ({ ...prev, [k]: { ...(prev[k] || { status: "idle" }), ...p } }));
  };

  const runAI = async (s: number, c: number) => {
    patch(s, c, { status: "running", source: "ai", error: undefined, result: undefined, startedAt: Date.now() });
    try {
      const r = await apiPost<{
        events: any[]; characters: CharacterItem[]; settings: SettingItem[];
        style: StyleFingerprint; n_chars: number; elapsed_s: number; errors: string[];
      }>(
        `/api/references/works/${refId}/segments/${s}/chunks/${c}/extract_all`,
        { use_ai: true },
        { timeoutMs: 600_000 },
      );
      if (r.errors && r.errors.length > 0) {
        patch(s, c, { status: "failed", error: r.errors.join("; "), elapsedS: r.elapsed_s });
        return;
      }
      patch(s, c, {
        status: "ready", source: "ai",
        result: {
          events: r.events || [], characters: r.characters || [],
          settings: r.settings || [], style: r.style || {}, nChars: r.n_chars,
        },
        elapsedS: r.elapsed_s,
      });
    } catch (e: any) {
      patch(s, c, { status: "failed", error: e?.message || "AI 提取失败" });
    }
  };

  const parsePaste = async (s: number, chunk: ChunkMeta, raw: string) => {
    const c = chunk.chunk_index;
    const { result, error } = parseUnifiedPaste(raw);
    if (error || !result) {
      patch(s, c, { pasteError: error || "解析失败" });
      return;
    }
    patch(s, c, { status: "running", source: "paste", pasteError: undefined });
    try {
      // Pull the offline NLP style half + char count (no LLM call).
      const r = await apiPost<{ style: StyleFingerprint; n_chars: number }>(
        `/api/references/works/${refId}/segments/${s}/chunks/${c}/extract_all`,
        { use_ai: false },
        { timeoutMs: 120_000 },
      );
      // Normalize the pasted style (payoffs/hooks → arrays, derive
      // densities) so the rhythm builder never sees a stray number.
      const llmStyle = normalizeUnifiedStyle(result.style, r.n_chars, chunk.n_chapters);
      patch(s, c, {
        status: "ready", source: "paste",
        result: {
          events: result.events, characters: result.characters,
          settings: result.settings,
          style: { ...(r.style || {}), ...llmStyle },
          nChars: r.n_chars,
        },
        pasteRaw: undefined,
      });
    } catch (e: any) {
      patch(s, c, { status: "failed", error: e?.message || "NLP 计算失败" });
    }
  };

  /** Commit a SET of sections of one chunk into the work-level blobs.
   *  Each section's blob is saved only if it's in `sections`; the
   *  style_fingerprint_json ledger is always updated so per-section
   *  「已入库」 state survives reloads. Returns the next accumulator. */
  const commitSections = async (
    segIdx: number, chunk: ChunkMeta, result: ChunkResult, source: "ai" | "paste",
    sections: Section[],
    acc: { plot: PlotOutline | null; chars: CharacterItem[]; settings: SettingItem[];
           ledger: Record<string, StyleChunkEntry> },
  ) => {
    const seg = plan?.segments[segIdx];
    const volumeTitle = seg?.title || `第 ${segIdx + 1} 卷`;
    const range = { startCh: chunk.start_chapter, endCh: chunk.end_chapter };
    let { plot, chars, settings, ledger } = acc;

    if (sections.includes("events")) {
      plot = mergeEventsIntoChronicle(
        plot, resolveEventTimeMarkers(result.events), volumeTitle, range,
      );
      await onSavePlot(plot);
    }
    if (sections.includes("characters")) {
      chars = mergeCharacters(chars, result.characters);
      await onSaveCharacters(chars);
    }
    if (sections.includes("settings")) {
      settings = mergeSettings(settings, result.settings);
      await onSaveSettings(settings);
    }
    // Ledger entry — records which sections are done + the style fp.
    const key = ckKey(segIdx, chunk.chunk_index);
    const prev = ledger[key];
    const entry: StyleChunkEntry = {
      chars: result.nChars || prev?.chars || chunk.n_chars || 1,
      source: prev?.source,
      fp: prev?.fp,
      done: { ...(prev?.done || {}) },
      counts: {
        events: result.events.length,
        characters: result.characters.length,
        settings: result.settings.length,
      },
    };
    for (const sec of sections) entry.done![sec] = true;
    if (sections.includes("style")) {
      entry.fp = result.style;
      entry.source = source;
    }
    ledger = { ...ledger, [key]: entry };
    const nextStyle = aggregateStyleChunks(ledger);
    await onSaveStyle(nextStyle);
    if (sections.includes("style")) {
      // Rhythm derives from the aggregated per-chapter signals.
      await onSaveRhythm(buildRhythmFromSignals(nextStyle.chapter_signals));
    }
    return { plot, chars, settings, ledger };
  };

  /** Commit a single section of one chunk (the per-section 确认入库). */
  const commitSection = async (
    segIdx: number, chunk: ChunkMeta, section: Section,
  ) => {
    const k = ckKey(segIdx, chunk.chunk_index);
    const phase = chunkPhases[k];
    if (!phase?.result) return;
    patch(segIdx, chunk.chunk_index, { committingSection: section });
    try {
      const snap = await commitSections(
        segIdx, chunk, phase.result, phase.source || "ai", [section],
        {
          plot: plotRef.current,
          chars: charsRef.current || [],
          settings: settingsRef.current || [],
          ledger: ledgerRef.current,
        },
      );
      plotRef.current = snap.plot;
      charsRef.current = snap.chars;
      settingsRef.current = snap.settings;
      ledgerRef.current = snap.ledger;
      patch(segIdx, chunk.chunk_index, { committingSection: undefined });
      toast(`第 ${segIdx + 1} 卷 · 分段 ${chunk.chunk_index + 1} · ${SECTION_LABEL[section]} 已入库`, "success");
    } catch (e: any) {
      patch(segIdx, chunk.chunk_index, { committingSection: undefined });
      toast(e?.message || "入库失败", "error");
    }
  };

  /** Bulk: run the built-in AI on every not-yet-committed chunk. */
  const runAll = async () => {
    if (!plan || bulkRunning) return;
    setBulkRunning(true);
    bulkCancelRef.current = false;
    try {
      const loaded: Array<{ segIdx: number; chunks: ChunkMeta[] }> = [];
      for (const seg of plan.segments) {
        let cs = segChunksRef.current[seg.index];
        if (!cs) {
          try {
            await ensureChunks(seg.index);
            cs = segChunksRef.current[seg.index] || [];
          } catch (e: any) {
            toast(`第 ${seg.index + 1} 卷分段加载失败：${e?.message || e}`, "error");
            continue;
          }
        }
        loaded.push({ segIdx: seg.index, chunks: cs });
      }
      const tasks: Array<{ segIdx: number; chunk: ChunkMeta }> = [];
      for (const { segIdx, chunks } of loaded) {
        for (const ck of chunks) {
          const k = ckKey(segIdx, ck.chunk_index);
          // Skip chunks already committed (any section recorded).
          if (ledgerRef.current[k]) continue;
          tasks.push({ segIdx, chunk: ck });
        }
      }
      if (tasks.length === 0) {
        toast("所有分段都已提取，无需重复处理。", "info");
        return;
      }
      let acc = {
        plot: plotRef.current,
        chars: charsRef.current || [],
        settings: settingsRef.current || [],
        ledger: ledgerRef.current,
      };
      let failed = 0;
      setBulkProgress({
        done: 0, total: tasks.length, failed: 0,
        label: `第 ${tasks[0].segIdx + 1} 卷 · 分段 ${tasks[0].chunk.chunk_index + 1}`,
      });
      for (let i = 0; i < tasks.length; i++) {
        if (bulkCancelRef.current) break;
        const { segIdx, chunk } = tasks[i];
        const c = chunk.chunk_index;
        setBulkProgress({
          done: i, total: tasks.length, failed,
          label: `第 ${segIdx + 1} 卷 · 分段 ${c + 1}`,
        });
        patch(segIdx, c, { status: "running", source: "ai", startedAt: Date.now(), error: undefined });
        try {
          const r = await apiPost<{
            events: any[]; characters: CharacterItem[]; settings: SettingItem[];
            style: StyleFingerprint; n_chars: number; elapsed_s: number; errors: string[];
          }>(
            `/api/references/works/${refId}/segments/${segIdx}/chunks/${c}/extract_all`,
            { use_ai: true },
            { timeoutMs: 600_000 },
          );
          if (r.errors && r.errors.length > 0) {
            failed++;
            patch(segIdx, c, { status: "failed", error: r.errors.join("; "), elapsedS: r.elapsed_s });
            continue;
          }
          const result: ChunkResult = {
            events: r.events || [], characters: r.characters || [],
            settings: r.settings || [], style: r.style || {}, nChars: r.n_chars,
          };
          acc = await commitSections(
            segIdx, chunk, result, "ai",
            ["events", "characters", "settings", "style"], acc,
          );
          plotRef.current = acc.plot;
          charsRef.current = acc.chars;
          settingsRef.current = acc.settings;
          ledgerRef.current = acc.ledger;
          patch(segIdx, c, { status: "ready", result, elapsedS: r.elapsed_s });
        } catch (e: any) {
          failed++;
          patch(segIdx, c, { status: "failed", error: e?.message || "AI 提取失败" });
        }
      }
      setBulkProgress({ done: tasks.length, total: tasks.length, failed, label: "" });
      toast(
        bulkCancelRef.current
          ? `已取消，完成 ${tasks.length - failed}/${tasks.length}`
          : `批量处理完成：${tasks.length - failed} 成功${failed ? ` · ${failed} 失败` : ""}`,
        failed > 0 ? "info" : "success",
      );
    } finally {
      setBulkRunning(false);
    }
  };

  if (!hasFullText) {
    return (
      <div className="text-xs text-muted" style={{ padding: 12 }}>
        上传正文后才能提取。请到「原始文件」tab 上传。
      </div>
    );
  }
  if (!plan) {
    return <div className="text-xs text-muted" style={{ padding: 12 }}>加载分段计划中…</div>;
  }
  if (plan.segments.length === 0) {
    return (
      <div className="text-xs text-muted" style={{ padding: 12 }}>
        尚未划分卷。请到「预处理」tab 创建分卷后再来这里提取。
      </div>
    );
  }

  const committedCount = Object.keys(ledger).length;

  return (
    <div style={{
      border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
      padding: 12, background: "var(--bg-surface)",
    }}>
      <div className="flex items-center justify-between" style={{ marginBottom: 4, gap: 8, flexWrap: "wrap" }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)" }}>
          统一特征提取
        </div>
        <button
          className="btn-primary"
          style={{ fontSize: 12, padding: "4px 14px" }}
          onClick={runAll}
          disabled={bulkRunning}
          title="对每一卷的每一分段都调用内置 AI 一次性提取事件/角色/设定/风格，已提取的分段会跳过">
          {bulkRunning ? "批量处理中…" : "使用内置 AI 一键处理全部分段"}
        </button>
      </div>
      {committedCount > 0 && (
        <div className="text-xs text-muted" style={{ marginBottom: 8 }}>
          已入库 {committedCount} 个分段
        </div>
      )}

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
              <button className="btn" onClick={() => { bulkCancelRef.current = true; }}
                      style={{ fontSize: 11, padding: "2px 10px", color: "var(--error)" }}>
                取消
              </button>
            )}
          </div>
          <div style={{ height: 6, background: "var(--bg-surface-2)", borderRadius: 3, overflow: "hidden" }}>
            <div style={{
              height: "100%",
              width: `${bulkProgress.total > 0 ? (bulkProgress.done / bulkProgress.total) * 100 : 0}%`,
              background: "var(--jade)", borderRadius: 3, transition: "width 0.3s",
            }} />
          </div>
        </div>
      )}

      <div className="flex flex-col gap-4">
        {plan.segments.map(seg => {
          const isOpen = openSegs.has(seg.index);
          const chunks = segChunks[seg.index] || [];
          const loading = chunkLoading.has(seg.index);
          const segDone = chunks.filter(ck => ledger[ckKey(seg.index, ck.chunk_index)]).length;
          return (
            <div key={seg.index}>
              <button
                className="btn-ghost w-full"
                onClick={() => toggleSeg(seg.index)}
                style={{
                  display: "flex", alignItems: "center", gap: 8,
                  padding: "6px 10px",
                  border: "1px solid var(--border)", borderRadius: 4,
                  background: "transparent",
                  justifyContent: "flex-start", textAlign: "left",
                }}>
                <span style={{
                  transition: "transform 0.15s",
                  transform: isOpen ? "rotate(90deg)" : "none",
                  display: "inline-block", color: "var(--text-tertiary)",
                }}>▶</span>
                <span className="tag" style={{
                  fontSize: 10, minWidth: 36, textAlign: "center",
                  color: "var(--text-secondary)", border: "1px solid var(--border)",
                }}>{`#${seg.index + 1}`}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="truncate" style={{ fontSize: 12, fontWeight: 500, color: "var(--text-primary)" }}>
                    {seg.title}
                  </div>
                  <div className="text-xs text-muted">
                    第 {seg.start_chapter}–{seg.end_chapter} 章
                    {chunks.length > 0 && ` · ${segDone}/${chunks.length} 分段已入库`}
                  </div>
                </div>
              </button>
              {isOpen && (
                <div style={{
                  marginTop: 6, marginLeft: 18, paddingLeft: 10,
                  borderLeft: "2px solid var(--border)",
                }}>
                  {loading && <div className="text-xs text-muted" style={{ padding: 6 }}>正在划分分段…</div>}
                  {!loading && chunks.length === 0 && (
                    <div className="text-xs text-muted" style={{ padding: 6 }}>本卷为空。</div>
                  )}
                  {!loading && chunks.map(ck => {
                    const k = ckKey(seg.index, ck.chunk_index);
                    const committedEntry = ledger[k] || null;
                    const phase = chunkPhases[k] || { status: "idle" as Phase };
                    return (
                      <UnifiedChunkRow
                        key={k}
                        refId={refId}
                        segIdx={seg.index}
                        chunk={ck}
                        phase={phase}
                        committedEntry={committedEntry}
                        open={openChunks.has(k)}
                        onToggle={() => toggleChunk(seg.index, ck.chunk_index)}
                        onRunAI={() => runAI(seg.index, ck.chunk_index)}
                        onPasteChange={(v) => patch(seg.index, ck.chunk_index, { pasteRaw: v, pasteError: undefined })}
                        onParsePaste={() => parsePaste(seg.index, ck, phase.pasteRaw || "")}
                        onCommitSection={(section) => commitSection(seg.index, ck, section)}
                        onReset={() => patch(seg.index, ck.chunk_index, {
                          status: "idle", source: undefined, result: undefined,
                          error: undefined, pasteRaw: undefined, pasteError: undefined,
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
    </div>
  );
}

function UnifiedChunkRow({
  refId, segIdx, chunk, phase, committedEntry, open,
  onToggle, onRunAI, onPasteChange, onParsePaste, onCommitSection, onReset,
}: {
  refId: string;
  segIdx: number;
  chunk: ChunkMeta;
  phase: ChunkPhase;
  committedEntry: StyleChunkEntry | null;
  open: boolean;
  onToggle: () => void;
  onRunAI: () => void;
  onPasteChange: (v: string) => void;
  onParsePaste: () => void;
  onCommitSection: (section: Section) => void;
  onReset: () => void;
}) {
  const [showPaste, setShowPaste] = useState(false);
  const running = phase.status === "running";
  const ready = phase.status === "ready";
  const failed = phase.status === "failed";
  const result = phase.result;
  const done = committedEntry?.done || {};
  const doneCount = (["events", "characters", "settings", "style"] as Section[])
    .filter(s => done[s]).length;
  const anyDone = doneCount > 0;

  return (
    <div style={{
      marginTop: 6, marginBottom: 6,
      border: `1px solid ${doneCount === 4 ? "var(--jade)" : ready ? "var(--accent)" : failed ? "var(--error)" : anyDone ? "var(--jade)" : "var(--border)"}`,
      borderRadius: 4,
      background: anyDone ? "rgba(52,168,83,0.04)" : "var(--bg-card)",
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
          第 {chunk.start_chapter}–{chunk.end_chapter} 章 · {chunk.n_chars.toLocaleString()} 字
        </span>
        <div style={{ flex: 1 }} />
        {anyDone && (
          <span className="tag" style={{
            fontSize: 10, padding: "1px 6px",
            color: "var(--jade)", border: "1px solid var(--jade)",
          }}>已入库 {doneCount}/4</span>
        )}
        {ready && !anyDone && (
          <span className="tag" style={{
            fontSize: 10, padding: "1px 6px",
            color: "var(--accent)", border: "1px solid var(--accent)",
          }}>{phase.source === "ai" ? "AI 已生成" : "已解析"}</span>
        )}
        {running && <span className="text-xs" style={{ color: "var(--gold)" }}>处理中…</span>}
        {failed && (
          <span className="tag" style={{
            fontSize: 10, padding: "1px 6px",
            color: "var(--error)", border: "1px solid var(--error)",
          }}>失败</span>
        )}
      </button>

      {open && (
        <div style={{ padding: "8px 10px", borderTop: "1px dashed var(--border)" }}>
          {running && (
            <div style={{ marginBottom: 8 }}>
              <div className="text-xs" style={{ color: "var(--accent)", marginBottom: 4 }}>
                {phase.source === "paste" ? "正在解析网页返回结果…" : "内置 AI 提取中…"}
              </div>
              <div className="ink-indeterminate" />
            </div>
          )}
          {result ? (
            <>
              <UnifiedResultPreview
                result={result}
                done={done}
                committingSection={phase.committingSection}
                onCommitSection={onCommitSection}
              />
              <div className="flex items-center gap-6" style={{
                justifyContent: "flex-end", marginTop: 8,
                paddingTop: 8, borderTop: "1px dashed var(--border)",
              }}>
                <button className="btn" onClick={onReset}
                        disabled={!!phase.committingSection}
                        style={{ fontSize: 11, padding: "3px 10px" }}>重新提取</button>
              </div>
            </>
          ) : (
            <>
              {anyDone && (
                <div className="text-xs text-muted" style={{ marginBottom: 6, lineHeight: 1.7 }}>
                  本分段已入库：
                  {(["events", "characters", "settings", "style"] as Section[]).map(s => (
                    <span key={s} style={{
                      marginLeft: 6,
                      color: done[s] ? "var(--jade)" : "var(--text-tertiary)",
                    }}>{done[s] ? "✓" : "○"} {SECTION_LABEL[s]}</span>
                  ))}
                  。可重新提取以补全或更新。
                </div>
              )}
              <PromptCopyPanel
                refId={refId}
                promptKey="reference.unified"
                segmentIndex={segIdx}
                chunkIndex={chunk.chunk_index}
                defaultOpen
                label={`分段 ${chunk.chunk_index + 1} 的统一 prompt（事件+角色+设定+风格，一次提取）`}
              />

              <div className="flex items-center gap-6" style={{ flexWrap: "wrap", marginBottom: 6 }}>
                <button className="btn-primary"
                        onClick={onRunAI}
                        disabled={running}
                        style={{ fontSize: 11, padding: "3px 12px" }}>
                  {running ? "AI 提取中…" : "用内置 AI 提取本段"}
                </button>
                <button className="btn"
                        onClick={() => setShowPaste(p => !p)}
                        style={{ fontSize: 11, padding: "3px 10px" }}>
                  {showPaste ? "收起" : "解析网页 LLM 回复"}
                </button>
              </div>

              {failed && phase.error && (
                <div style={{
                  padding: "6px 10px", marginBottom: 6,
                  background: "var(--bg-surface)", border: "1px solid var(--error)",
                  borderRadius: 3, fontSize: 11, color: "var(--error)", lineHeight: 1.55,
                }}>
                  <div style={{ fontWeight: 600, marginBottom: 2 }}>提取失败</div>
                  <div style={{ wordBreak: "break-word" }}>{phase.error}</div>
                </div>
              )}

              {showPaste && (
                <div style={{ marginBottom: 8 }}>
                  <textarea className="input font-mono"
                            rows={5}
                            value={phase.pasteRaw || ""}
                            placeholder='粘贴 LLM 返回的 {"events":[...],"characters":[...],"settings":[...],"style":{...}}'
                            onChange={e => onPasteChange(e.target.value)}
                            style={{
                              fontSize: 11, lineHeight: 1.5, resize: "vertical",
                              background: "var(--bg-app)", marginBottom: 6,
                            }} />
                  <div className="flex items-center gap-6">
                    <button className="btn-primary"
                            onClick={onParsePaste}
                            disabled={!(phase.pasteRaw && phase.pasteRaw.trim()) || running}
                            style={{ fontSize: 11, padding: "3px 10px" }}>
                      解析并预览
                    </button>
                    {phase.pasteError && (
                      <span className="text-xs" style={{ color: "var(--error)", flex: 1 }}>
                        {phase.pasteError}
                      </span>
                    )}
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function fmtNum(v: number | undefined, d = 2): string {
  return typeof v === "number" ? v.toFixed(d) : "—";
}
function fmtPct(v: number | undefined): string {
  return typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "—";
}

/** Per-section 确认入库 button shown in each PreviewSection header. */
function CommitBtn({ section, done, committing, onCommit }: {
  section: Section; done: boolean; committing: boolean;
  onCommit: (s: Section) => void;
}) {
  return (
    <button
      className={done ? "btn" : "btn-primary"}
      onClick={(e) => { e.stopPropagation(); onCommit(section); }}
      disabled={committing}
      style={{
        fontSize: 10, padding: "2px 10px",
        color: done ? "var(--jade)" : undefined,
      }}>
      {committing ? "入库中…" : done ? "已入库 · 重新入库" : "确认入库"}
    </button>
  );
}

/** Collapsible preview section with an optional header action (the
 *  per-section 确认入库 button). */
function PreviewSection({ title, count, headerAction, children, defaultOpen = true }: {
  title: string; count: number;
  headerAction?: React.ReactNode;
  children: React.ReactNode; defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 4, background: "var(--bg-surface)" }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 6,
        padding: "3px 6px 3px 8px",
      }}>
        <button className="btn-ghost" onClick={() => setOpen(o => !o)}
                style={{
                  display: "flex", alignItems: "center", gap: 6, flex: 1,
                  padding: "2px 0", justifyContent: "flex-start", borderRadius: 0,
                }}>
          <span style={{
            transition: "transform 0.15s", transform: open ? "rotate(90deg)" : "none",
            display: "inline-block", fontSize: 9, color: "var(--text-tertiary)",
          }}>▶</span>
          <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-primary)" }}>{title}</span>
          <span className="text-xs text-muted">{count}</span>
        </button>
        {headerAction}
      </div>
      {open && (
        <div style={{
          padding: "4px 8px 8px", borderTop: "1px dashed var(--border)",
          maxHeight: 260, overflowY: "auto",
        }}>{children}</div>
      )}
    </div>
  );
}

/** Full preview of one chunk's unified extraction result — events,
 *  characters, settings and style/rhythm — each section with its own
 *  确认入库 button so the user can commit one part at a time. */
function UnifiedResultPreview({
  result, done, committingSection, onCommitSection,
}: {
  result: ChunkResult;
  done: NonNullable<StyleChunkEntry["done"]>;
  committingSection?: Section;
  onCommitSection: (s: Section) => void;
}) {
  const st = result.style;
  const sigs = st.chapter_signals || [];
  const btn = (section: Section) => (
    <CommitBtn section={section} done={!!done[section]}
               committing={committingSection === section}
               onCommit={onCommitSection} />
  );
  return (
    <div className="flex flex-col gap-6" style={{ marginTop: 6 }}>
      <PreviewSection title="事件" count={result.events.length} headerAction={btn("events")}>
        {result.events.length === 0
          ? <span className="text-xs text-muted">（无）</span>
          : (
            <div className="flex flex-col gap-3">
              {result.events.map((ev, i) => (
                <div key={i} style={{ fontSize: 11, lineHeight: 1.55 }}>
                  <span style={{ fontWeight: 600, color: "var(--accent)" }}>
                    【{ev.subject || "—"}·{categoryLabel(ev.category)}·{ev.name || "—"}】
                  </span>
                  {ev.first_chapter && (
                    <span className="tag" style={{
                      marginLeft: 4, fontSize: 9, padding: "0 5px",
                      color: "var(--jade)", border: "1px solid var(--jade)",
                    }}>{ev.first_chapter}</span>
                  )}
                  {ev.time_marker && (
                    <span className="tag" style={{
                      marginLeft: 3, fontSize: 9, padding: "0 5px",
                      color: "var(--gold)", border: "1px solid var(--gold)",
                    }}>{ev.time_marker}</span>
                  )}
                  {" "}
                  <span style={{ color: "var(--text-secondary)" }}>{ev.description}</span>
                </div>
              ))}
            </div>
          )}
      </PreviewSection>

      <PreviewSection title="角色" count={result.characters.length} headerAction={btn("characters")}>
        {result.characters.length === 0
          ? <span className="text-xs text-muted">（无）</span>
          : (
            <div className="flex flex-col gap-3">
              {result.characters.map((c, i) => (
                <div key={i} style={{ fontSize: 11, lineHeight: 1.5 }}>
                  <span style={{ fontWeight: 700, color: "var(--text-primary)" }}>{c.name}</span>
                  {c.role_tag && (
                    <span className="tag" style={{
                      marginLeft: 4, fontSize: 9, padding: "0 5px",
                      color: "var(--accent)", border: "1px solid var(--accent)",
                    }}>{c.role_tag}</span>
                  )}
                  {c.first_chapter && (
                    <span className="text-xs text-muted" style={{ marginLeft: 4 }}>{c.first_chapter}</span>
                  )}
                  <span className="text-xs text-muted" style={{ marginLeft: 4 }}>
                    外貌{c.appearance?.length || 0}·性格{c.personality?.length || 0}·经历{c.experiences?.length || 0}
                  </span>
                  {c.intro && (
                    <div style={{ color: "var(--text-secondary)", marginTop: 1 }}>{c.intro}</div>
                  )}
                </div>
              ))}
            </div>
          )}
      </PreviewSection>

      <PreviewSection title="设定" count={result.settings.length} headerAction={btn("settings")}>
        {result.settings.length === 0
          ? <span className="text-xs text-muted">（无）</span>
          : (
            <div className="flex flex-col gap-3">
              {result.settings.map((s, i) => (
                <div key={i} style={{ fontSize: 11, lineHeight: 1.5 }}>
                  <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>{s.title}</span>
                  {s.first_chapter && (
                    <span className="text-xs text-muted" style={{ marginLeft: 4 }}>{s.first_chapter}</span>
                  )}
                  <span className="text-xs text-muted" style={{ marginLeft: 4 }}>
                    {s.updates?.length || 0} 条更新
                  </span>
                  {s.content && (
                    <div style={{ color: "var(--text-secondary)", marginTop: 1 }}>
                      简介：{s.content}
                    </div>
                  )}
                  {(s.updates || []).map((u, ui) => (
                    <div key={ui} style={{ color: "var(--text-secondary)", marginTop: 1 }}>
                      {u.chapter && (
                        <span style={{ color: "var(--gold)", marginRight: 4, fontFamily: "var(--font-mono)" }}>
                          {u.chapter}
                        </span>
                      )}
                      {u.text}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          )}
      </PreviewSection>

      <PreviewSection title="风格 / 节奏" count={sigs.length} headerAction={btn("style")}>
        <div className="text-xs" style={{ lineHeight: 1.9, color: "var(--text-secondary)" }}>
          对话占比 {fmtPct(st.dialogue_ratio)} · 描写密度 {fmtPct(st.description_density)}
          {" · "}修辞 {fmtNum(st.rhetoric_frequency)}/千字
          <br />
          信息密度 {fmtPct(st.info_density)} · 爽点密度 {fmtNum(st.payoff_density)}/万字
          {" · "}钩子密度 {fmtNum(st.hook_density)}/章
          {st.pacing_profile && (
            <>
              <br />
              节奏 快{fmtPct(st.pacing_profile.fast)} / 中{fmtPct(st.pacing_profile.medium)} / 慢{fmtPct(st.pacing_profile.slow)}
            </>
          )}
        </div>
        {sigs.length > 0 && (
          <div className="flex flex-col gap-4" style={{ marginTop: 6 }}>
            {sigs.map((s, i) => (
              <div key={i} style={{
                fontSize: 10, lineHeight: 1.6,
                paddingBottom: 3, borderBottom: "1px dashed var(--border)",
              }}>
                <div className="flex items-center" style={{ gap: 5, flexWrap: "wrap" }}>
                  <span style={{ fontWeight: 700, color: "var(--text-primary)" }}>{s.chapter}</span>
                  <span style={{ color: "var(--accent)" }}>
                    信息密度 {typeof s.info_density === "number" ? `${(s.info_density * 100).toFixed(0)}%` : "—"}
                  </span>
                  {(s.chapter_types || []).map((t, ti) => (
                    <span key={ti} className="tag" style={{
                      fontSize: 9, padding: "0 5px",
                      color: "var(--text-tertiary)", border: "1px solid var(--border)",
                    }}>{t}</span>
                  ))}
                </div>
                {(s.payoffs || []).map((p, pi) => (
                  <div key={pi} style={{ marginTop: 1, lineHeight: 1.5 }}>
                    <span className="tag" style={{
                      fontSize: 9, padding: "0 5px", marginRight: 5,
                      color: "var(--gold)", border: "1px solid var(--gold)",
                    }}>爽点 · {p.type}</span>
                    <span style={{ color: "var(--text-secondary)" }}>
                      {p.plot || "（未提供具体情节）"}
                    </span>
                  </div>
                ))}
                {(s.hooks || []).map((h, hi) => (
                  <div key={hi} style={{ marginTop: 1, color: "var(--text-secondary)" }}>
                    <span style={{ color: "var(--accent)", marginRight: 4 }}>钩子 · {h.position}</span>
                    {h.content || "（无描述）"}
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}
      </PreviewSection>
    </div>
  );
}
