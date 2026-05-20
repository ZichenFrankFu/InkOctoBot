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
import { PromptCopyPanel } from "./AnalysisEditors";
import type {
  PlotOutline, CharacterItem, SettingItem,
} from "./AnalysisEditors";
import { useSegmentation } from "./segmentationCache";
import type { ChunkMeta } from "./segmentationCache";
import {
  resolveEventTimeMarkers, mergeEventsIntoChronicle,
  mergeCharacters, mergeSettings, aggregateStyleChunks,
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

type Phase = "idle" | "running" | "ready" | "committing" | "committed" | "failed";
interface ChunkPhase {
  status: Phase;
  source?: "ai" | "paste";
  result?: ChunkResult;
  error?: string;
  elapsedS?: number;
  startedAt?: number;
  pasteRaw?: string;
  pasteError?: string;
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
          settings: Array.isArray(obj.settings) ? obj.settings : [],
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
  onSavePlot, onSaveCharacters, onSaveSettings, onSaveStyle,
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

  const parsePaste = async (s: number, c: number, raw: string) => {
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
      patch(s, c, {
        status: "ready", source: "paste",
        result: {
          events: result.events, characters: result.characters,
          settings: result.settings,
          style: { ...(r.style || {}), ...result.style },
          nChars: r.n_chars,
        },
        pasteRaw: undefined,
      });
    } catch (e: any) {
      patch(s, c, { status: "failed", error: e?.message || "NLP 计算失败" });
    }
  };

  /** Distribute one chunk's result into the four work-level blobs.
   *  Returns the next accumulator snapshot for the bulk loop. */
  const distribute = async (
    segIdx: number, chunk: ChunkMeta, result: ChunkResult, source: "ai" | "paste",
    acc: { plot: PlotOutline | null; chars: CharacterItem[]; settings: SettingItem[];
           ledger: Record<string, StyleChunkEntry> },
  ) => {
    const seg = plan?.segments[segIdx];
    const volumeTitle = seg?.title || `第 ${segIdx + 1} 卷`;
    const range = { startCh: chunk.start_chapter, endCh: chunk.end_chapter };
    const nextPlot = mergeEventsIntoChronicle(
      acc.plot, resolveEventTimeMarkers(result.events), volumeTitle, range,
    );
    const nextChars = mergeCharacters(acc.chars, result.characters);
    const nextSettings = mergeSettings(acc.settings, result.settings);
    const entry: StyleChunkEntry = {
      chars: result.nChars || chunk.n_chars || 1,
      source,
      fp: result.style,
      counts: {
        events: result.events.length,
        characters: result.characters.length,
        settings: result.settings.length,
      },
    };
    const nextLedger = { ...acc.ledger, [ckKey(segIdx, chunk.chunk_index)]: entry };
    const nextStyle = aggregateStyleChunks(nextLedger);
    await onSavePlot(nextPlot);
    await onSaveCharacters(nextChars);
    await onSaveSettings(nextSettings);
    await onSaveStyle(nextStyle);
    return { plot: nextPlot, chars: nextChars, settings: nextSettings, ledger: nextLedger };
  };

  const commit = async (segIdx: number, chunkIdx: number, chunk: ChunkMeta) => {
    const k = ckKey(segIdx, chunkIdx);
    const phase = chunkPhases[k];
    if (!phase || phase.status !== "ready" || !phase.result) return;
    patch(segIdx, chunkIdx, { status: "committing" });
    try {
      const snap = await distribute(segIdx, chunk, phase.result, phase.source || "ai", {
        plot: plotRef.current,
        chars: charsRef.current || [],
        settings: settingsRef.current || [],
        ledger: ledgerRef.current,
      });
      plotRef.current = snap.plot;
      charsRef.current = snap.chars;
      settingsRef.current = snap.settings;
      ledgerRef.current = snap.ledger;
      patch(segIdx, chunkIdx, { status: "committed" });
      toast(`第 ${segIdx + 1} 卷 · 分段 ${chunkIdx + 1} 已入库（事件/角色/设定/风格）`, "success");
    } catch (e: any) {
      patch(segIdx, chunkIdx, { status: "ready", error: e?.message || "入库失败" });
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
          if (ledgerRef.current[k] || chunkPhases[k]?.status === "committed") continue;
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
          acc = await distribute(segIdx, chunk, result, "ai", acc);
          plotRef.current = acc.plot;
          charsRef.current = acc.chars;
          settingsRef.current = acc.settings;
          ledgerRef.current = acc.ledger;
          patch(segIdx, c, { status: "committed", result, elapsedS: r.elapsed_s });
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
      <div className="text-xs text-muted" style={{ marginBottom: 10, lineHeight: 1.6 }}>
        每个分段只调用一次 LLM，同时抽取<strong>事件 / 角色 / 设定 / 风格</strong>四类信息——同一段正文不再重复上传，省 token。入库后可在「剧情大纲 / 角色 / 设定 / 文本特征」四个 tab 浏览与编辑。
        {committedCount > 0 && ` · 已入库 ${committedCount} 个分段。`}
      </div>

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
                    const phase = chunkPhases[k]
                      || { status: (committedEntry ? "committed" : "idle") as Phase };
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
                        onParsePaste={() => parsePaste(seg.index, ck.chunk_index, phase.pasteRaw || "")}
                        onCommit={() => commit(seg.index, ck.chunk_index, ck)}
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
  onToggle, onRunAI, onPasteChange, onParsePaste, onCommit, onReset,
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
  onCommit: () => void;
  onReset: () => void;
}) {
  const [showPaste, setShowPaste] = useState(false);
  const running = phase.status === "running";
  const ready = phase.status === "ready";
  const failed = phase.status === "failed";
  const committing = phase.status === "committing";
  const committed = phase.status === "committed";
  const result = phase.result;
  const counts = committedEntry?.counts;

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
          第 {chunk.start_chapter}–{chunk.end_chapter} 章 · {chunk.n_chars.toLocaleString()} 字
        </span>
        <div style={{ flex: 1 }} />
        {committed && (
          <span className="tag" style={{
            fontSize: 10, padding: "1px 6px",
            color: "var(--jade)", border: "1px solid var(--jade)",
          }}>
            已入库{counts ? ` · ${counts.events}事件 ${counts.characters}角色 ${counts.settings}设定` : ""}
          </span>
        )}
        {ready && !committed && (
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
          {committed ? (
            <div className="text-xs text-muted" style={{ lineHeight: 1.7 }}>
              本分段已一次性提取并分发到四个 tab。
              {counts && (
                <> 事件 {counts.events} · 角色 {counts.characters} · 设定 {counts.settings} · 风格已并入。</>
              )}
              <div style={{ marginTop: 6 }}>
                <button className="btn-ghost"
                        onClick={onReset}
                        style={{ fontSize: 11, padding: "3px 10px", color: "var(--accent)" }}>
                  重新提取本段（会覆盖本段章节范围内的事件）
                </button>
              </div>
            </div>
          ) : (
            <>
              <PromptCopyPanel
                refId={refId}
                promptKey="reference.unified"
                segmentIndex={segIdx}
                chunkIndex={chunk.chunk_index}
                defaultOpen
                label={`分段 ${chunk.chunk_index + 1} 的统一 prompt（事件+角色+设定+风格，一次提取）`}
              />

              {(phase.status === "idle" || failed) && (
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
              )}

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

              {showPaste && !ready && (
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

              {ready && result && (
                <>
                  <div style={{
                    marginTop: 6, padding: "6px 10px",
                    border: "1px solid var(--border)", borderRadius: 4,
                    background: "var(--bg-surface)", fontSize: 11, lineHeight: 1.8,
                  }}>
                    <div>
                      <strong style={{ color: "var(--accent)" }}>事件</strong> {result.events.length} 条
                      {" · "}
                      <strong style={{ color: "var(--accent)" }}>角色</strong> {result.characters.length} 个
                      {" · "}
                      <strong style={{ color: "var(--accent)" }}>设定</strong> {result.settings.length} 条
                    </div>
                    <div className="text-xs text-muted">
                      风格：对话占比 {fmtPct(result.style.dialogue_ratio)} · 信息密度 {fmtPct(result.style.info_density)}
                      {" · "}爽点密度 {fmtNum(result.style.payoff_density)} · 钩子密度 {fmtNum(result.style.hook_density)}
                      {result.style.chapter_signals && result.style.chapter_signals.length > 0 &&
                        ` · 每章信号 ${result.style.chapter_signals.length} 章`}
                    </div>
                  </div>
                  <div className="flex items-center gap-6" style={{
                    justifyContent: "flex-end", marginTop: 8,
                    paddingTop: 8, borderTop: "1px dashed var(--border)",
                  }}>
                    <button className="btn" onClick={onReset} disabled={committing}
                            style={{ fontSize: 11, padding: "3px 10px" }}>重置</button>
                    <button className="btn-primary" onClick={onCommit}
                            disabled={committing}
                            style={{ fontSize: 11, padding: "3px 14px" }}>
                      {committing ? "入库中…" : "确认入库（分发到四个 tab）"}
                    </button>
                  </div>
                </>
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
