/* Features-tab style-fingerprint extraction.
 *
 * Mirrors the per-volume / per-chunk pattern of the chronicle,
 * characters and settings tabs. Each chunk produces a per-chunk
 * fingerprint that's a UNION of two halves:
 *
 *   - NLP half (offline, instant, no tokens): avg_sentence_length,
 *     vocab_complexity, punctuation_profile — from compute_nlp_style.
 *   - LLM half (needs judgement): dialogue_ratio, rhetoric_frequency,
 *     description_density, payoff_density, info_density, hook_density,
 *     pacing_profile — via the reference.style prompt, run either with
 *     the built-in AI or by pasting back a web-LLM response.
 *
 * Committed chunks are remembered in the work's style_fingerprint_json
 * under a `_chunks` ledger keyed by "segIdx:chunkIdx"; the visible
 * top-level metrics are a char-weighted average recomputed from that
 * ledger on every commit. That makes "已提取的分段" survive reloads
 * without a separate persistence field.
 */
import React, { useEffect, useRef, useState } from "react";
import { apiPost } from "../../api/client";
import { useToast } from "../shared/Toast";
import { PromptCopyPanel } from "./AnalysisEditors";
import { useSegmentation } from "./segmentationCache";
import type { ChunkMeta } from "./segmentationCache";

interface PacingProfile { fast?: number; medium?: number; slow?: number }
interface PunctProfile {
  ellipsis?: number; dash?: number; exclamation?: number;
  question?: number; comma?: number;
}
interface StyleFingerprint {
  avg_sentence_length?: number;
  vocab_complexity?: number;
  punctuation_profile?: PunctProfile;
  dialogue_ratio?: number;
  rhetoric_frequency?: number;
  description_density?: number;
  payoff_density?: number;
  info_density?: number;
  hook_density?: number;
  pacing_profile?: PacingProfile;
  /** Per-chunk ledger. Keyed by "segIdx:chunkIdx". The top-level
   *  metrics above are a char-weighted average of every entry's fp. */
  _chunks?: Record<string, ChunkEntry>;
}
interface ChunkEntry {
  chars: number;
  source: "ai" | "paste";
  fp: StyleFingerprint;
}

type Phase = "idle" | "running" | "ready" | "committing" | "committed" | "failed";
interface ChunkPhase {
  status: Phase;
  source?: "ai" | "paste";
  fp?: StyleFingerprint;     // combined NLP + LLM
  nChars?: number;
  error?: string;
  elapsedS?: number;
  startedAt?: number;
  pasteRaw?: string;
  pasteError?: string;
}

function r2(v: number): number { return Math.round(v * 100) / 100; }
function r4(v: number): number { return Math.round(v * 10000) / 10000; }

/** Char-weighted average of every committed chunk's fingerprint.
 *  Larger chunks pull the aggregate harder, which is what you want for
 *  document-level statistics. The `_chunks` ledger is carried through
 *  unchanged so reloads can re-derive committed state. */
function aggregateChunks(chunks: Record<string, ChunkEntry>): StyleFingerprint {
  const entries = Object.values(chunks);
  if (entries.length === 0) return { _chunks: chunks };
  const total = entries.reduce((s, e) => s + (e.chars || 1), 0) || 1;
  const w = (sel: (fp: StyleFingerprint) => number | undefined): number =>
    entries.reduce((s, e) => s + (sel(e.fp) || 0) * (e.chars || 1), 0) / total;
  return {
    avg_sentence_length: r2(w(f => f.avg_sentence_length)),
    vocab_complexity:    r4(w(f => f.vocab_complexity)),
    dialogue_ratio:      r4(w(f => f.dialogue_ratio)),
    rhetoric_frequency:  r4(w(f => f.rhetoric_frequency)),
    description_density: r4(w(f => f.description_density)),
    payoff_density:      r4(w(f => f.payoff_density)),
    info_density:        r4(w(f => f.info_density)),
    hook_density:        r4(w(f => f.hook_density)),
    pacing_profile: {
      fast:   r4(w(f => f.pacing_profile?.fast)),
      medium: r4(w(f => f.pacing_profile?.medium)),
      slow:   r4(w(f => f.pacing_profile?.slow)),
    },
    punctuation_profile: {
      ellipsis:    r4(w(f => f.punctuation_profile?.ellipsis)),
      dash:        r4(w(f => f.punctuation_profile?.dash)),
      exclamation: r4(w(f => f.punctuation_profile?.exclamation)),
      question:    r4(w(f => f.punctuation_profile?.question)),
      comma:       r4(w(f => f.punctuation_profile?.comma)),
    },
    _chunks: chunks,
  };
}

/** Lenient parser for a pasted web-LLM style response — strips
 *  <think>/code-fences, normalizes smart quotes, repairs the common
 *  unescaped-inner-quote breakage, then finds the first balanced object
 *  carrying the LLM-side fields. */
function parseStylePaste(raw: string): { fp: StyleFingerprint | null; error?: string } {
  let s = (raw || "").replace(/<think>[\s\S]*?<\/think>/gi, "")
                     .replace(/<\/?think>/gi, "");
  const fence = s.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  if (fence) s = fence[1];
  s = s.replace(/[﻿​-‍]/g, "")
       .replace(/[“”]/g, '"')
       .replace(/[‘’]/g, "'")
       .replace(/ /g, " ")
       .trim();
  if (!s) return { fp: null, error: "请先粘贴 LLM 返回的 JSON" };

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
  const isFingerprint = (o: any): boolean =>
    o && typeof o === "object" && (
      "dialogue_ratio" in o || "payoff_density" in o ||
      "info_density" in o || "hook_density" in o || "pacing_profile" in o
    );
  let lastErr = "";
  for (let i = 0; i < s.length; i++) {
    if (s[i] !== "{") continue;
    const ext = balancedExtract(i);
    if (!ext) continue;
    try {
      const o = JSON.parse(ext);
      if (isFingerprint(o)) return { fp: o as StyleFingerprint };
    } catch (e: any) {
      lastErr = e?.message || "";
      const fixed = repair(ext);
      if (fixed !== ext) {
        try {
          const o = JSON.parse(fixed);
          if (isFingerprint(o)) return { fp: o as StyleFingerprint };
        } catch { /* fall through */ }
      }
    }
  }
  return {
    fp: null,
    error: lastErr
      ? `JSON 解析失败：${lastErr.slice(0, 120)}`
      : "在 JSON 中没找到风格字段（需含 dialogue_ratio / payoff_density 等）。",
  };
}

/** Normalize an LLM-side fingerprint object into canonical numbers. */
function normalizeAi(o: any): StyleFingerprint {
  const f = (k: string): number => {
    const v = o?.[k];
    const n = typeof v === "number" ? v : parseFloat(v);
    return Number.isFinite(n) ? n : 0;
  };
  const pp = (o?.pacing_profile && typeof o.pacing_profile === "object") ? o.pacing_profile : {};
  return {
    dialogue_ratio:      r4(f("dialogue_ratio")),
    rhetoric_frequency:  r4(f("rhetoric_frequency")),
    description_density: r4(f("description_density")),
    payoff_density:      r4(f("payoff_density")),
    info_density:        r4(f("info_density")),
    hook_density:        r4(f("hook_density")),
    pacing_profile: {
      fast:   r4(typeof pp.fast === "number" ? pp.fast : parseFloat(pp.fast) || 0),
      medium: r4(typeof pp.medium === "number" ? pp.medium : parseFloat(pp.medium) || 0),
      slow:   r4(typeof pp.slow === "number" ? pp.slow : parseFloat(pp.slow) || 0),
    },
  };
}

function fmtNum(v: number | undefined, d = 2): string {
  return typeof v === "number" ? v.toFixed(d) : "—";
}
function fmtPct(v: number | undefined): string {
  return typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "—";
}

/** Compact metric grid used both in chunk previews and the aggregate. */
function StyleMetricGrid({ fp }: { fp: StyleFingerprint }) {
  const pp = fp.pacing_profile || {};
  const pu = fp.punctuation_profile || {};
  const row = (label: string, value: string, group: "nlp" | "ai") => (
    <div className="flex" key={label} style={{
      fontSize: 11, padding: "2px 0",
      borderBottom: "1px dashed var(--border)",
    }}>
      <span className="tag" style={{
        fontSize: 9, padding: "0 4px", marginRight: 6,
        color: group === "nlp" ? "var(--jade)" : "var(--accent)",
        border: `1px solid ${group === "nlp" ? "var(--jade)" : "var(--accent)"}`,
        borderRadius: 2,
      }}>{group === "nlp" ? "NLP" : "AI"}</span>
      <span style={{ flex: 1, color: "var(--text-secondary)" }}>{label}</span>
      <span style={{ fontFamily: "var(--font-mono)", fontWeight: 600, color: "var(--text-primary)" }}>
        {value}
      </span>
    </div>
  );
  return (
    <div style={{ marginTop: 6 }}>
      {row("平均句长（字）", fmtNum(fp.avg_sentence_length, 1), "nlp")}
      {row("词汇丰富度", fmtPct(fp.vocab_complexity), "nlp")}
      {row("标点 · 省略号（每千字）", fmtNum(pu.ellipsis, 2), "nlp")}
      {row("标点 · 破折号（每千字）", fmtNum(pu.dash, 2), "nlp")}
      {row("标点 · 感叹号（每千字）", fmtNum(pu.exclamation, 2), "nlp")}
      {row("标点 · 问号（每千字）", fmtNum(pu.question, 2), "nlp")}
      {row("对话占比", fmtPct(fp.dialogue_ratio), "ai")}
      {row("修辞频率（每千字）", fmtNum(fp.rhetoric_frequency, 2), "ai")}
      {row("描写密度", fmtPct(fp.description_density), "ai")}
      {row("爽点密度（每万字）", fmtNum(fp.payoff_density, 2), "ai")}
      {row("信息密度", fmtPct(fp.info_density), "ai")}
      {row("钩子密度（每章）", fmtNum(fp.hook_density, 2), "ai")}
      {row("节奏 快/中/慢",
        `${fmtPct(pp.fast)} / ${fmtPct(pp.medium)} / ${fmtPct(pp.slow)}`, "ai")}
    </div>
  );
}

export function FeaturesExtractionPanel({
  refId, hasFullText, current, onSave,
}: {
  refId: string;
  hasFullText: boolean;
  current: StyleFingerprint | null;
  onSave: (next: StyleFingerprint) => Promise<void> | void;
}) {
  const { toast } = useToast();
  const { plan, chunks: segChunks, chunkLoading, ensureChunks } =
    useSegmentation(refId, hasFullText);
  const [openSegs, setOpenSegs] = useState<Set<number>>(new Set());
  const [openChunks, setOpenChunks] = useState<Set<string>>(new Set());
  const [chunkPhases, setChunkPhases] = useState<Record<string, ChunkPhase>>({});

  // The persisted per-chunk ledger. Committed chunks live here, so the
  // "已提取" markers survive reloads.
  const ledger: Record<string, ChunkEntry> = current?._chunks || {};
  const ledgerRef = useRef(ledger);
  useEffect(() => { ledgerRef.current = current?._chunks || {}; }, [current]);

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

  /** Built-in AI: one call returns NLP + LLM halves already combined. */
  const runAI = async (s: number, c: number) => {
    patch(s, c, { status: "running", source: "ai", error: undefined, fp: undefined, startedAt: Date.now() });
    try {
      const r = await apiPost<{
        nlp: StyleFingerprint; ai: StyleFingerprint;
        n_chars: number; elapsed_s: number; errors: string[];
      }>(
        `/api/references/works/${refId}/segments/${s}/chunks/${c}/extract_style`,
        { use_ai: true },
        { timeoutMs: 600_000 },
      );
      if (r.errors && r.errors.length > 0) {
        patch(s, c, { status: "failed", error: r.errors.join("; "), elapsedS: r.elapsed_s });
        return;
      }
      patch(s, c, {
        status: "ready", source: "ai",
        fp: { ...(r.nlp || {}), ...normalizeAi(r.ai || {}) },
        nChars: r.n_chars, elapsedS: r.elapsed_s,
      });
    } catch (e: any) {
      patch(s, c, { status: "failed", error: e?.message || "AI 提取失败" });
    }
  };

  /** Paste path: parse the LLM JSON locally, then fetch the NLP half
   *  (offline, fast) and combine the two. */
  const parsePaste = async (s: number, c: number, raw: string) => {
    const { fp, error } = parseStylePaste(raw);
    if (error || !fp) {
      patch(s, c, { pasteError: error || "解析失败" });
      return;
    }
    patch(s, c, { status: "running", source: "paste", pasteError: undefined });
    try {
      const r = await apiPost<{ nlp: StyleFingerprint; n_chars: number }>(
        `/api/references/works/${refId}/segments/${s}/chunks/${c}/extract_style`,
        { use_ai: false },
        { timeoutMs: 120_000 },
      );
      patch(s, c, {
        status: "ready", source: "paste",
        fp: { ...(r.nlp || {}), ...normalizeAi(fp) },
        nChars: r.n_chars, pasteRaw: undefined,
      });
    } catch (e: any) {
      patch(s, c, { status: "failed", error: e?.message || "NLP 计算失败" });
    }
  };

  const commit = async (s: number, c: number) => {
    const k = ckKey(s, c);
    const phase = chunkPhases[k];
    if (!phase || phase.status !== "ready" || !phase.fp) return;
    patch(s, c, { status: "committing" });
    try {
      const entry: ChunkEntry = {
        chars: phase.nChars || 1,
        source: phase.source || "ai",
        fp: phase.fp,
      };
      const nextLedger = { ...ledgerRef.current, [k]: entry };
      await onSave(aggregateChunks(nextLedger));
      ledgerRef.current = nextLedger;
      patch(s, c, { status: "committed" });
      toast(`第 ${s + 1} 卷 · 分段 ${c + 1} 已入库`, "success");
    } catch (e: any) {
      patch(s, c, { status: "ready", error: e?.message || "入库失败" });
      toast(e?.message || "入库失败", "error");
    }
  };

  /** Remove a committed chunk from the ledger and re-aggregate. */
  const removeCommitted = async (s: number, c: number) => {
    const k = ckKey(s, c);
    const nextLedger = { ...ledgerRef.current };
    delete nextLedger[k];
    try {
      await onSave(aggregateChunks(nextLedger));
      ledgerRef.current = nextLedger;
      patch(s, c, { status: "idle", fp: undefined, source: undefined });
      toast(`已移除第 ${s + 1} 卷 · 分段 ${c + 1}`, "info");
    } catch (e: any) {
      toast(e?.message || "移除失败", "error");
    }
  };

  if (!hasFullText) {
    return (
      <div className="text-xs text-muted" style={{ padding: 12 }}>
        上传正文后才能分段提取风格指纹。
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
          风格指纹提取
        </div>
        {committedCount > 0 && (
          <span className="text-xs text-muted">已入库 {committedCount} 个分段</span>
        )}
      </div>
      <div className="text-xs text-muted" style={{ marginBottom: 10, lineHeight: 1.6 }}>
        平均句长、词汇丰富度、标点特征由 NLP 离线计算；对话占比、修辞频率、爽点、信息密度、钩子等需理解判断的维度由 LLM 提取（可用内置 AI，或复制 prompt 到网页 LLM 再粘回）。每个分段入库后会按字数加权汇总到全书风格指纹。
      </div>
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
                    const committed = ledger[k];
                    const phase = chunkPhases[k]
                      || { status: committed ? "committed" as Phase : "idle" as Phase };
                    return (
                      <StyleChunkRow
                        key={k}
                        refId={refId}
                        segIdx={seg.index}
                        chunk={ck}
                        phase={phase}
                        committedEntry={committed || null}
                        open={openChunks.has(k)}
                        onToggle={() => toggleChunk(seg.index, ck.chunk_index)}
                        onRunAI={() => runAI(seg.index, ck.chunk_index)}
                        onPasteChange={(v) => patch(seg.index, ck.chunk_index, { pasteRaw: v, pasteError: undefined })}
                        onParsePaste={() => parsePaste(seg.index, ck.chunk_index, phase.pasteRaw || "")}
                        onCommit={() => commit(seg.index, ck.chunk_index)}
                        onReset={() => patch(seg.index, ck.chunk_index, {
                          status: "idle", source: undefined, fp: undefined,
                          error: undefined, pasteRaw: undefined, pasteError: undefined,
                        })}
                        onRemoveCommitted={() => removeCommitted(seg.index, ck.chunk_index)}
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

function StyleChunkRow({
  refId, segIdx, chunk, phase, committedEntry, open,
  onToggle, onRunAI, onPasteChange, onParsePaste, onCommit, onReset, onRemoveCommitted,
}: {
  refId: string;
  segIdx: number;
  chunk: ChunkMeta;
  phase: ChunkPhase;
  committedEntry: ChunkEntry | null;
  open: boolean;
  onToggle: () => void;
  onRunAI: () => void;
  onPasteChange: (v: string) => void;
  onParsePaste: () => void;
  onCommit: () => void;
  onReset: () => void;
  onRemoveCommitted: () => void;
}) {
  const [showPaste, setShowPaste] = useState(false);
  const running = phase.status === "running";
  const ready = phase.status === "ready";
  const failed = phase.status === "failed";
  const committing = phase.status === "committing";
  const committed = phase.status === "committed";

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
          }}>已入库{committedEntry ? ` · ${committedEntry.source === "ai" ? "AI" : "粘贴"}` : ""}</span>
        )}
        {ready && !committed && (
          <span className="tag" style={{
            fontSize: 10, padding: "1px 6px",
            color: "var(--accent)", border: "1px solid var(--accent)",
          }}>{phase.source === "ai" ? "AI 已生成" : "已解析"}</span>
        )}
        {running && (
          <span className="text-xs" style={{ color: "var(--gold)" }}>处理中…</span>
        )}
        {failed && (
          <span className="tag" style={{
            fontSize: 10, padding: "1px 6px",
            color: "var(--error)", border: "1px solid var(--error)",
          }}>失败</span>
        )}
      </button>

      {open && (
        <div style={{ padding: "8px 10px", borderTop: "1px dashed var(--border)" }}>
          {committed && committedEntry ? (
            <>
              <StyleMetricGrid fp={committedEntry.fp} />
              <div className="flex" style={{ justifyContent: "flex-end", marginTop: 8 }}>
                <button className="btn-ghost"
                        onClick={onRemoveCommitted}
                        style={{ fontSize: 11, padding: "3px 10px", color: "var(--error)" }}>
                  移除并重新提取
                </button>
              </div>
            </>
          ) : (
            <>
              <PromptCopyPanel
                refId={refId}
                promptKey="reference.style"
                segmentIndex={segIdx}
                chunkIndex={chunk.chunk_index}
                defaultOpen
                label={`分段 ${chunk.chunk_index + 1} 的 prompt（LLM 评估对话/修辞/爽点/信息密度/钩子）`}
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
                            placeholder='粘贴 LLM 返回的 {"dialogue_ratio": ..., "payoff_density": ..., "pacing_profile": {...}}'
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

              {ready && phase.fp && (
                <>
                  <StyleMetricGrid fp={phase.fp} />
                  <div className="flex items-center gap-6" style={{
                    justifyContent: "flex-end", marginTop: 8,
                    paddingTop: 8, borderTop: "1px dashed var(--border)",
                  }}>
                    <button className="btn" onClick={onReset} disabled={committing}
                            style={{ fontSize: 11, padding: "3px 10px" }}>重置</button>
                    <button className="btn-primary" onClick={onCommit}
                            disabled={committing}
                            style={{ fontSize: 11, padding: "3px 14px" }}>
                      {committing ? "入库中…" : "确认入库"}
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
