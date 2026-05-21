import React, { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost } from "../../api/client";
import { useDialog } from "../shared/Dialog";

/* ════════════════════════════════════════════════════════════
 * Human-readable editors for reference-work analysis fields.
 * Each editor displays the data in a structured layout and lets
 * the user edit individual values. On save, calls onSave(data).
 * ════════════════════════════════════════════════════════════ */

/** A compact pencil-icon button — the standard per-entry「编辑」
 *  affordance across the 剧情大纲 / 角色 / 设定 / 文本特征 tabs. */
export function EditIconButton({ onClick, title = "编辑" }: {
  onClick: () => void; title?: string;
}) {
  return (
    <button className="btn-ghost" onClick={onClick} title={title} aria-label={title}
            style={{
              padding: "2px 6px", color: "var(--text-tertiary)",
              display: "inline-flex", alignItems: "center", justifyContent: "center",
            }}>
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor"
           strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M12 20h9" />
        <path d="M16.5 3.5a2.121 2.121 0 1 1 3 3L7 19l-4 1 1-4 12.5-12.5z" />
      </svg>
    </button>
  );
}

/* ─── Shared: copyable per-volume prompt panel ─────────────────
 * Lives next to the characters / settings tabs so the user can
 * paste the exact same prompt the pipeline would use into a web
 * LLM (ChatGPT / Claude.ai) when the configured model fails.
 *
 * Segment-scoped: needs refId + segmentIndex to render the prompt
 * with chapter text spliced in. Without segmentIndex it shows a hint
 * pointing the user to the outline tab to set context.
 *
 * Chunked: when the volume is too long for one LLM call, the panel
 * pulls the multi-chunk preview from `/preview_chunks` and offers
 * per-chunk copy buttons + "copy all" so the user can run each
 * chunk through a web LLM and paste the merged results back. */
interface ChunkInfo {
  chunk_index: number;
  rendered: string;
  start_chapter: number;
  end_chapter: number;
  n_chapters: number;
  n_chars: number;
}

export function PromptCopyPanel({
  refId, promptKey, segmentIndex, label, chunked = true, chunkIndex,
  defaultOpen = false,
}: {
  refId: string;
  promptKey: "reference.characters" | "reference.settings" | "reference.outline" | "reference.style" | "reference.unified";
  segmentIndex: number | null;
  label: string;
  /** Allow the multi-chunk endpoint. Defaults to true; pass false to
   * always render a single (possibly truncated) prompt — useful for
   * characters / settings, where each call needs the full segment text. */
  chunked?: boolean;
  /** When set, render ONLY this chunk's prompt — hides the chunk
   * navigator and the "copy all" button. Used by per-chunk extraction
   * rows where the surrounding UI already knows which chunk it is. */
  chunkIndex?: number;
  /** Start expanded instead of collapsed. Useful for chunk rows that
   * are already user-expanded and want their prompt visible immediately. */
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const [loading, setLoading] = useState(false);
  const [chunks, setChunks] = useState<ChunkInfo[]>([]);
  const [activeChunk, setActiveChunk] = useState(0);
  const [error, setError] = useState("");
  const [copyState, setCopyState] = useState<"idle" | "copied" | "fail">("idle");

  const loadPrompt = useCallback(async () => {
    if (segmentIndex == null) {
      setError("请先到「剧情大纲」tab 选择一卷开始预览，本卷的 prompt 将自动渲染");
      setChunks([]);
      return;
    }
    setLoading(true);
    setError("");
    try {
      if (chunked) {
        const params = new URLSearchParams({
          ref_id: refId, segment_index: String(segmentIndex),
        });
        const r = await apiGet<{ chunks: ChunkInfo[]; total_chunks: number }>(
          `/api/references/prompts/${promptKey}/preview_chunks?${params}`,
        );
        const all = r.chunks || [];
        // If a specific chunk was requested, keep only that one. The
        // surrounding row already labels itself with the chunk number,
        // so the panel doesn't need a navigator.
        const filtered = chunkIndex != null
          ? all.filter(c => c.chunk_index === chunkIndex)
          : all;
        setChunks(filtered);
        setActiveChunk(0);
      } else {
        const params = new URLSearchParams({
          ref_id: refId, segment_index: String(segmentIndex),
        });
        const r = await apiGet<{ rendered: string }>(
          `/api/references/prompts/${promptKey}/preview?${params}`,
        );
        setChunks([{
          chunk_index: 0, rendered: r.rendered || "",
          start_chapter: 0, end_chapter: 0, n_chapters: 0, n_chars: 0,
        }]);
        setActiveChunk(0);
      }
    } catch (e: any) {
      setError(e?.message || "获取 prompt 失败");
      setChunks([]);
    } finally {
      setLoading(false);
    }
  }, [refId, promptKey, segmentIndex, chunked, chunkIndex]);

  // Auto-fetch when the panel mounts in defaultOpen mode (per-chunk rows).
  useEffect(() => {
    if (defaultOpen && chunks.length === 0 && !error && !loading) {
      loadPrompt();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [defaultOpen]);

  const toggle = async () => {
    const willOpen = !open;
    setOpen(willOpen);
    if (willOpen && chunks.length === 0 && !error) await loadPrompt();
  };

  const current = chunks[activeChunk];
  const total = chunks.length;
  // Hide the per-chunk navigator + "copy all" when the caller has
  // restricted the panel to a single chunk.
  const showChunkNavigator = chunkIndex == null;

  const copy = async (text: string) => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopyState("copied");
      setTimeout(() => setCopyState("idle"), 1500);
    } catch {
      setCopyState("fail");
      setTimeout(() => setCopyState("idle"), 1500);
    }
  };

  const copyCurrent = () => current && copy(current.rendered);
  const copyAll = () => {
    if (chunks.length === 0) return;
    // Separated with a clear divider so paste-back can split them.
    const joined = chunks.map((c, i) =>
      `===== 分段 ${i + 1}/${chunks.length}（第 ${c.start_chapter}–${c.end_chapter} 章） =====\n${c.rendered}`
    ).join("\n\n");
    copy(joined);
  };

  return (
    <div style={{ marginBottom: 10, border: "1px dashed var(--border)", borderRadius: 4 }}>
      <div className="flex items-center" style={{
        padding: "4px 8px", gap: 6, flexWrap: "wrap",
        borderBottom: open ? "1px dashed var(--border)" : "none",
      }}>
        <button className="btn-ghost" onClick={toggle}
                style={{
                  padding: "2px 4px", fontSize: 11, fontWeight: 600,
                  color: "var(--text-secondary)", borderRadius: 0,
                }}>
          <span style={{
            transition: "transform 0.15s",
            transform: open ? "rotate(180deg)" : "none",
            display: "inline-block", marginRight: 4,
          }}>&#x25BC;</span>
          {label}
        </button>
        <div style={{ flex: 1 }} />
        {segmentIndex != null && (
          <span className="text-xs text-muted">当前卷 #{segmentIndex + 1}</span>
        )}
        <button className="btn" onClick={loadPrompt}
                disabled={loading || segmentIndex == null}
                style={{ padding: "2px 8px", fontSize: 10 }}
                title="重新渲染（如卷信息有变）">刷新</button>
        <button className="btn" onClick={copyCurrent}
                disabled={!current?.rendered || loading}
                style={{ padding: "2px 10px", fontSize: 10 }}
                title="复制当前段 prompt 到剪贴板">
          {copyState === "copied" ? "已复制" : copyState === "fail" ? "复制失败" : "复制本段"}
        </button>
        {showChunkNavigator && total > 1 && (
          <button className="btn-ghost" onClick={copyAll}
                  disabled={loading}
                  style={{ padding: "2px 8px", fontSize: 10 }}
                  title="复制全部 N 段（带分段分隔符）">
            复制全部 {total} 段
          </button>
        )}
      </div>
      {showChunkNavigator && open && total > 1 && (
        <div className="flex items-center" style={{
          padding: "4px 8px", gap: 4, flexWrap: "wrap",
          background: "var(--bg-surface)",
          borderBottom: "1px dashed var(--border)",
          fontSize: 10,
        }}>
          <span className="text-muted">本卷字数超出单次 prompt 上限，已拆分：</span>
          {chunks.map((c, i) => (
            <button
              key={i}
              className="btn-ghost"
              onClick={() => setActiveChunk(i)}
              style={{
                padding: "2px 8px", fontSize: 10,
                fontWeight: activeChunk === i ? 600 : 400,
                color: activeChunk === i ? "var(--accent)" : "var(--text-tertiary)",
                background: activeChunk === i ? "var(--accent-subtle)" : "transparent",
                borderRadius: 3,
              }}
              title={`第 ${c.start_chapter}–${c.end_chapter} 章 · ${c.n_chars} 字`}>
              {i + 1}/{total}
            </button>
          ))}
        </div>
      )}
      {open && (
        <div style={{ padding: 8, background: "var(--bg-surface)" }}>
          {loading ? (
            <div className="text-xs text-muted" style={{ padding: 6 }}>加载中…</div>
          ) : error ? (
            <div className="text-xs" style={{ padding: 6, color: "var(--text-tertiary)" }}>{error}</div>
          ) : (
            <>
              {current && showChunkNavigator && total > 1 && (
                <div className="text-xs text-muted" style={{ marginBottom: 6 }}>
                  本段覆盖第 <strong>{current.start_chapter}</strong>–<strong>{current.end_chapter}</strong> 章
                  （{current.n_chapters} 章 · {current.n_chars.toLocaleString()} 字）
                </div>
              )}
              <pre className="font-mono" style={{
                margin: 0, padding: 8, fontSize: 11, lineHeight: 1.55,
                background: "var(--bg-card)", borderRadius: 3,
                color: "var(--text-secondary)",
                maxHeight: 320, overflow: "auto",
                whiteSpace: "pre-wrap", wordBreak: "break-word",
              }}>{current?.rendered || "（无）"}</pre>
            </>
          )}
        </div>
      )}
    </div>
  );
}

const OPENING_LABELS: Record<string, string> = {
  in_medias_res: "高潮开局 (in medias res)",
  dialogue_open: "对话开局",
  worldbuilding: "世界观铺陈",
  character_intro: "人物登场",
};

const OPENING_DESC: Record<string, string> = {
  in_medias_res: "直接切入冲突或关键场面，事后再补叙背景。",
  dialogue_open: "以人物对话开场，靠台词带出信息与张力。",
  worldbuilding: "先铺陈世界观与背景设定，再引入主线人物。",
  character_intro: "以主要人物的登场和日常切入故事。",
};

/** 开篇模式 — read-only display of how the work opens. The pattern is
 *  system-computed by the unified extraction; there is nothing to edit. */
export function OpeningPatternView({ pattern }: { pattern: string }) {
  if (!pattern) {
    return (
      <div className="text-xs text-muted text-center" style={{ padding: 14 }}>
        暂无开篇模式数据。请到「特征提取」tab 提取首段后查看。
      </div>
    );
  }
  return (
    <div className="flex items-center gap-12" style={{ flexWrap: "wrap", padding: "2px 0" }}>
      <span className="tag" style={{
        fontSize: 13, fontWeight: 700, padding: "3px 12px",
        color: "var(--accent)", background: "var(--accent-subtle)",
        border: "1px solid var(--accent)",
      }}>
        {OPENING_LABELS[pattern] || pattern}
      </span>
      <span className="text-xs text-muted" style={{ lineHeight: 1.6, flex: 1, minWidth: 220 }}>
        {OPENING_DESC[pattern] || ""}
      </span>
    </div>
  );
}

const PACING_LABEL: Record<string, string> = {
  fast: "快节奏",
  medium: "中节奏",
  slow: "慢节奏",
};

const PACING_COLOR: Record<string, string> = {
  fast: "var(--accent)",
  medium: "var(--gold)",
  slow: "var(--jade)",
};

const SHUANGDIAN_LABELS: Record<string, string> = {
  face_slap: "打脸/反转",
  power_reveal: "实力展现",
  treasure_gain: "突破/晋级",
  mystery_reveal: "谜底揭开",
};

interface SectionProps {
  title: string;
  subtitle?: string;
  onExtract?: () => void;
  extractLabel?: string;
  extracting?: boolean;
  children: React.ReactNode;
  empty?: boolean;
  emptyHint?: string;
  defaultOpen?: boolean;
}

export function Section({ title, subtitle, onExtract, extractLabel, extracting, children, empty, emptyHint, defaultOpen = true }: SectionProps) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", overflow: "hidden" }}>
      <button
        className="btn-ghost w-full"
        style={{
          justifyContent: "space-between",
          padding: "10px 12px",
          background: "var(--bg-surface)",
          fontWeight: 600,
          borderRadius: 0,
        }}
        onClick={() => setOpen(!open)}
      >
        <span style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
          <span>{title}</span>
          {subtitle && <span className="text-xs text-muted" style={{ fontWeight: 400 }}>{subtitle}</span>}
        </span>
        <span
          className="text-xs text-muted"
          style={{ transition: "transform 0.15s", transform: open ? "rotate(180deg)" : "none", display: "inline-block" }}
        >
          &#x25BC;
        </span>
      </button>
      {open && (
        <div style={{ padding: 14, background: "var(--bg-card)" }}>
          {onExtract && (
            <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 10 }}>
              <button
                className="btn"
                style={{ fontSize: 11, padding: "3px 10px" }}
                onClick={onExtract}
                disabled={extracting}
              >
                {extracting ? "提取中..." : (extractLabel || "重新提取")}
              </button>
            </div>
          )}
          {empty ? (
            <div className="text-xs text-muted text-center" style={{ padding: "12px 0" }}>
              {emptyHint || "暂无数据"}
            </div>
          ) : children}
        </div>
      )}
    </div>
  );
}

/* ──────────────── Style Fingerprint ──────────────── */

interface StyleFingerprint {
  // NLP-computed (deterministic / statistical):
  avg_sentence_length?: number;
  vocab_complexity?: number;
  punctuation_profile?: {
    ellipsis?: number; dash?: number; exclamation?: number;
    question?: number; comma?: number;
  };
  // LLM-discriminated (need semantic judgement):
  dialogue_ratio?: number;
  description_density?: number;
  rhetoric_frequency?: number;
  payoff_density?: number;
  info_density?: number;
  hook_density?: number;
  pacing_profile?: { fast?: number; medium?: number; slow?: number };
  /** Per-chapter signals from the unified extraction (also feed 节奏). */
  chapter_signals?: {
    chapter: string;
    info_density?: number;
    chapter_types?: string[];
    summary?: string;
    payoffs?: { type: string; plot?: string }[];
    hooks?: { position: string; content: string }[];
  }[];
}

function MetricRow({ label, value, unit, max, hint }: { label: string; value: number | undefined; unit?: string; max?: number; hint?: string }) {
  const v = typeof value === "number" ? value : 0;
  const pct = max ? Math.max(0, Math.min(100, (v / max) * 100)) : 0;
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4, fontSize: 12 }}>
        <span style={{ color: "var(--text-secondary)" }}>{label}{hint && <span className="text-muted" style={{ marginLeft: 6, fontSize: 11 }}>({hint})</span>}</span>
        <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>{typeof value === "number" ? value.toFixed(unit === "字" ? 1 : 4) : "—"}{unit ? ` ${unit}` : ""}</span>
      </div>
      {max !== undefined && (
        <div style={{ height: 6, background: "var(--bg-surface-2)", borderRadius: 3, overflow: "hidden" }}>
          <div style={{ height: "100%", width: `${pct}%`, background: "var(--accent)", borderRadius: 3, transition: "width 0.3s" }} />
        </div>
      )}
    </div>
  );
}

function Slider01({ label, value, onChange, hint }: { label: string; value: number | undefined; onChange: (v: number) => void; hint?: string }) {
  const v = typeof value === "number" ? Math.max(0, Math.min(1, value)) : 0;
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
        <label style={{ fontSize: 12, color: "var(--text-secondary)" }}>{label}{hint && <span className="text-muted" style={{ marginLeft: 6, fontSize: 11 }}>({hint})</span>}</label>
        <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 600, color: "var(--accent)" }}>{v.toFixed(2)}</span>
      </div>
      <input
        type="range"
        min={0}
        max={1}
        step={0.01}
        value={v}
        onChange={e => onChange(parseFloat(e.target.value) || 0)}
        style={{ width: "100%", accentColor: "var(--accent)" }}
      />
    </div>
  );
}

function NumInput({ label, value, onChange, step = 0.01, min, max }: { label: string; value: number | undefined; onChange: (v: number) => void; step?: number; min?: number; max?: number }) {
  return (
    <div className="field">
      <label className="label">{label}</label>
      <input
        className="input"
        type="number"
        step={step}
        min={min}
        max={max}
        value={typeof value === "number" ? value : 0}
        onChange={(e) => onChange(parseFloat(e.target.value) || 0)}
      />
    </div>
  );
}

export function StyleFingerprintEditor({ data, onSave }: { data: StyleFingerprint | null; onSave: (d: StyleFingerprint) => Promise<void> | void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<StyleFingerprint>(data || {});
  const [saving, setSaving] = useState(false);

  const start = () => { setDraft(data || {}); setEditing(true); };
  const cancel = () => { setDraft(data || {}); setEditing(false); };
  const save = async () => {
    setSaving(true);
    try { await onSave(draft); setEditing(false); } finally { setSaving(false); }
  };

  const d = editing ? draft : (data || {});
  const pacing = d.pacing_profile || {};

  if (editing) {
    const setPacing = (k: "fast" | "medium" | "slow", v: number) =>
      setDraft({ ...draft, pacing_profile: { ...(draft.pacing_profile || {}), [k]: v } });
    return (
      <div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginBottom: 12 }}>
          <NumInput label="平均句长 (字)" step={0.1} min={0} value={d.avg_sentence_length} onChange={v => setDraft({ ...draft, avg_sentence_length: v })} />
          <NumInput label="修辞频率 (每千字)" min={0} value={d.rhetoric_frequency} onChange={v => setDraft({ ...draft, rhetoric_frequency: v })} />
          <NumInput label="爽点密度 (每万字)" min={0} value={d.payoff_density} onChange={v => setDraft({ ...draft, payoff_density: v })} />
          <NumInput label="钩子密度 (每章)" min={0} value={d.hook_density} onChange={v => setDraft({ ...draft, hook_density: v })} />
        </div>
        <Slider01 label="对话占比" hint="对话内容 / 全文" value={d.dialogue_ratio} onChange={v => setDraft({ ...draft, dialogue_ratio: v })} />
        <Slider01 label="描写密度" hint="环境/外貌/动作描写" value={d.description_density} onChange={v => setDraft({ ...draft, description_density: v })} />
        <Slider01 label="信息密度" hint="单位篇幅有效信息量" value={d.info_density} onChange={v => setDraft({ ...draft, info_density: v })} />
        <Slider01 label="词汇丰富度" hint="归一化 TTR" value={d.vocab_complexity} onChange={v => setDraft({ ...draft, vocab_complexity: v })} />
        <div className="label" style={{ marginTop: 12, marginBottom: 6 }}>节奏分布（占比，总和应为 1）</div>
        <Slider01 label="快节奏" value={pacing.fast} onChange={v => setPacing("fast", v)} />
        <Slider01 label="中节奏" value={pacing.medium} onChange={v => setPacing("medium", v)} />
        <Slider01 label="慢节奏" value={pacing.slow} onChange={v => setPacing("slow", v)} />
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 8 }}>
          <button className="btn" onClick={cancel} disabled={saving}>取消</button>
          <button className="btn-primary" onClick={save} disabled={saving}>{saving ? "保存中..." : "保存"}</button>
        </div>
      </div>
    );
  }

  const punct = d.punctuation_profile || {};
  return (
    <div>
      <div className="label" style={{ marginBottom: 6, color: "var(--jade)" }}>
        NLP 统计特征
      </div>
      <MetricRow label="平均句长" value={d.avg_sentence_length} unit="字" max={60} hint="越长越书面化" />
      <MetricRow label="词汇丰富度" value={d.vocab_complexity} max={1} hint="归一化 TTR" />
      <MetricRow label="省略号" value={punct.ellipsis} unit="/千字" max={10} hint="标点使用特征" />
      <MetricRow label="破折号" value={punct.dash} unit="/千字" max={10} />
      <MetricRow label="感叹号" value={punct.exclamation} unit="/千字" max={20} />
      <MetricRow label="问号" value={punct.question} unit="/千字" max={20} />
      <div className="label" style={{ marginTop: 12, marginBottom: 6, color: "var(--accent)" }}>
        LLM 语义维度
      </div>
      <MetricRow label="对话占比" value={d.dialogue_ratio} max={1} hint="对话内容 / 全文" />
      <MetricRow label="描写密度" value={d.description_density} max={1} hint="环境/外貌/动作描写" />
      <MetricRow label="修辞频率" value={d.rhetoric_frequency} max={20} hint="每千字使用次数" />
      <MetricRow label="爽点密度" value={d.payoff_density} max={10} hint="每万字爽点个数" />
      <MetricRow label="信息密度" value={d.info_density} max={1} hint="单位篇幅有效信息量" />
      <MetricRow label="钩子密度" value={d.hook_density} max={5} hint="每章悬念/张力点" />
      <div className="label" style={{ marginTop: 12, marginBottom: 6 }}>节奏分布</div>
      <div style={{ display: "flex", height: 24, borderRadius: 4, overflow: "hidden", background: "var(--bg-surface-2)" }}>
        {(["fast", "medium", "slow"] as const).map(k => {
          const v = pacing[k] || 0;
          if (v <= 0) return null;
          return (
            <div key={k} style={{
              width: `${v * 100}%`,
              background: PACING_COLOR[k],
              color: "white",
              fontSize: 11,
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontWeight: 600,
            }}>
              {`${PACING_LABEL[k]} ${(v * 100).toFixed(0)}%`}
            </div>
          );
        })}
      </div>
      {Array.isArray(d.chapter_signals) && d.chapter_signals.length > 0 && (
        <ChapterSignalsTable signals={d.chapter_signals} />
      )}
      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 12 }}>
        <EditIconButton onClick={start} />
      </div>
    </div>
  );
}

/** Per-chapter信息密度/爽点/钩子 from the unified extraction. Collapsed
 *  by default — the table can be long for a whole book. */
function ChapterSignalsTable({ signals }: {
  signals: NonNullable<StyleFingerprint["chapter_signals"]>;
}) {
  const [open, setOpen] = useState(false);
  const totalPayoffs = signals.reduce((n, s) => n + (Array.isArray(s.payoffs) ? s.payoffs.length : 0), 0);
  const totalHooks = signals.reduce((n, s) => n + (Array.isArray(s.hooks) ? s.hooks.length : 0), 0);
  return (
    <div style={{ marginTop: 14, border: "1px dashed var(--border)", borderRadius: 4 }}>
      <button className="btn-ghost w-full" onClick={() => setOpen(o => !o)}
              style={{
                justifyContent: "space-between", padding: "6px 10px",
                fontSize: 12, fontWeight: 600, color: "var(--text-secondary)", borderRadius: 0,
              }}>
        <span>每章信号（信息密度 / 爽点 / 钩子 · 共 {signals.length} 章）</span>
        <span className="text-xs text-muted">
          爽点 {totalPayoffs} · 钩子 {totalHooks}
          <span style={{
            marginLeft: 8, transition: "transform 0.15s",
            transform: open ? "rotate(180deg)" : "none", display: "inline-block",
          }}>&#x25BC;</span>
        </span>
      </button>
      {open && (
        <div style={{ maxHeight: 340, overflowY: "auto", padding: "4px 8px 8px" }}>
          {signals.map((s, i) => (
            <div key={i} style={{
              fontSize: 11, padding: "4px 4px",
              borderBottom: "1px dashed var(--border)",
            }}>
              <div className="flex items-center" style={{ gap: 6, flexWrap: "wrap" }}>
                <span style={{ fontWeight: 600, color: "var(--text-primary)", minWidth: 56 }}>
                  {s.chapter || "—"}
                </span>
                <span style={{ fontFamily: "var(--font-mono)", color: "var(--accent)" }}>
                  信息密度 {typeof s.info_density === "number" ? `${(s.info_density * 100).toFixed(0)}%` : "—"}
                </span>
                {(Array.isArray(s.chapter_types) ? s.chapter_types : []).map((t, ti) => (
                  <span key={`t${ti}`} className="tag" style={{
                    fontSize: 9, padding: "0 5px",
                    color: "var(--text-tertiary)", border: "1px solid var(--border)",
                  }}>{t}</span>
                ))}
              </div>
              {s.summary && (
                <div className="text-xs text-muted" style={{ marginTop: 2 }}>{s.summary}</div>
              )}
              {(Array.isArray(s.payoffs) ? s.payoffs : []).map((p, pi) => (
                <div key={`p${pi}`} style={{ marginTop: 2, lineHeight: 1.5 }}>
                  <span className="tag" style={{
                    fontSize: 9, padding: "0 5px", marginRight: 5,
                    color: "var(--gold)", border: "1px solid var(--gold)",
                  }}>爽点 · {p?.type || "其他"}</span>
                  <span style={{ color: "var(--text-secondary)" }}>
                    {p?.plot || "（未提供具体情节）"}
                  </span>
                </div>
              ))}
              {(Array.isArray(s.hooks) ? s.hooks : []).map((h, hi) => (
                <div key={`h${hi}`} className="text-xs" style={{ marginTop: 2, color: "var(--text-secondary)" }}>
                  <span style={{ color: "var(--accent)", marginRight: 4 }}>钩子·{h?.position || "章末"}</span>
                  {h?.content}
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** 分段视图 for the 文本特征 tab — one card per extracted chunk, read
 *  from the style_fingerprint_json._chunks ledger. The 全书视图 is the
 *  aggregated StyleFingerprintEditor; this shows each segment's own
 *  fingerprint so the user can compare卷与卷之间的风格差异. */
export function StyleByChunkView({ chunks, chunkList }: {
  chunks: Record<string, {
    chars: number; source?: string;
    fp?: StyleFingerprint;
    counts?: { events: number; characters: number; settings: number };
  }>;
  /** Chunk metadata — used to label "segIdx:chunkIdx" keys as
   *  「第 N 段 · 第 X–Y 章（卷名）」. */
  chunkList: { key: string; globalIndex: number; volumeTitle: string;
               startChapter: number; endChapter: number }[];
}) {
  const locByKey: Record<string, typeof chunkList[number]> = {};
  for (const c of chunkList) locByKey[c.key] = c;
  // Only chunks whose 风格 section was committed carry an `fp`.
  const keys = Object.keys(chunks)
    .filter(k => chunks[k]?.fp && Object.keys(chunks[k].fp!).length > 0)
    .sort((a, b) => {
      const [sa, ca] = a.split(":").map(Number);
      const [sb, cb] = b.split(":").map(Number);
      return sa !== sb ? sa - sb : ca - cb;
    });
  if (keys.length === 0) {
    return (
      <div className="text-xs text-muted text-center" style={{ padding: 16, lineHeight: 1.7 }}>
        暂无分段风格数据。请到「特征提取」tab 分段提取。
      </div>
    );
  }
  return (
    <div className="flex flex-col gap-8">
      {keys.map(k => {
        const [segIdx, chunkIdx] = k.split(":").map(Number);
        const entry = chunks[k];
        const fp = entry.fp || {};
        const pp = fp.pacing_profile || {};
        const loc = locByKey[k];
        const title = loc
          ? `第 ${loc.globalIndex} 段 · 第 ${loc.startChapter}–${loc.endChapter} 章（${loc.volumeTitle}）`
          : `第 ${segIdx + 1} 卷 · 分段 ${chunkIdx + 1}`;
        return (
          <div key={k} style={{
            border: "1px solid var(--border)", borderRadius: 4,
            background: "var(--bg-card)", padding: "8px 10px",
          }}>
            <div className="flex items-center" style={{ gap: 8, marginBottom: 4, flexWrap: "wrap" }}>
              <span style={{ fontSize: 12, fontWeight: 700, color: "var(--text-primary)" }}>
                {title}
              </span>
              {entry.source && (
                <span className="tag" style={{
                  fontSize: 9, padding: "0 5px",
                  color: "var(--text-tertiary)", border: "1px solid var(--border)",
                }}>{entry.source === "ai" ? "AI" : "粘贴"}</span>
              )}
              {entry.counts && (
                <span className="text-xs text-muted">
                  {entry.counts.events} 事件 · {entry.counts.characters} 角色 · {entry.counts.settings} 设定
                </span>
              )}
            </div>
            <div className="text-xs" style={{ lineHeight: 1.9, color: "var(--text-secondary)" }}>
              对话占比 {fmtFp(fp.dialogue_ratio, "pct")} · 描写密度 {fmtFp(fp.description_density, "pct")}
              {" · "}修辞 {fmtFp(fp.rhetoric_frequency)}/千字
              <br />
              信息密度 {fmtFp(fp.info_density, "pct")} · 爽点密度 {fmtFp(fp.payoff_density)}/万字
              {" · "}钩子密度 {fmtFp(fp.hook_density)}/章
              <br />
              节奏 快{fmtFp(pp.fast, "pct")} / 中{fmtFp(pp.medium, "pct")} / 慢{fmtFp(pp.slow, "pct")}
              {" · "}平均句长 {fmtFp(fp.avg_sentence_length)} 字 · 词汇丰富度 {fmtFp(fp.vocab_complexity, "pct")}
            </div>
            {Array.isArray(fp.chapter_signals) && fp.chapter_signals.length > 0 && (
              <ChapterSignalsTable signals={fp.chapter_signals} />
            )}
          </div>
        );
      })}
    </div>
  );
}

function fmtFp(v: number | undefined, kind?: "pct"): string {
  if (typeof v !== "number") return "—";
  return kind === "pct" ? `${(v * 100).toFixed(1)}%` : v.toFixed(2);
}

/* ──────────────── Narrative Structure ──────────────── */

interface ShuangdianItem { chapter: number; type: string }
interface NarrativeStructure {
  opening_pattern?: string;
  climax_positions?: number[];
  hook_density?: number;
  shuangdian?: ShuangdianItem[];
  chapter_beats?: { chapter: number; function: string; tension: number; hooks: number }[];
}

const BEAT_LABEL: Record<string, string> = {
  intro: "引入",
  rising: "上升",
  climax: "高潮",
  falling: "下降",
  resolution: "收束",
};

export function NarrativeStructureEditor({ data, onSave }: { data: NarrativeStructure | null; onSave: (d: NarrativeStructure) => Promise<void> | void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<NarrativeStructure>(data || {});
  const [saving, setSaving] = useState(false);

  const start = () => { setDraft(data || {}); setEditing(true); };
  const cancel = () => { setDraft(data || {}); setEditing(false); };
  const save = async () => {
    setSaving(true);
    try { await onSave(draft); setEditing(false); } finally { setSaving(false); }
  };

  const d = editing ? draft : (data || {});
  const sd = d.shuangdian || [];
  const beats = data?.chapter_beats || []; // beats are derived — show read-only

  if (editing) {
    const climaxStr = (draft.climax_positions || []).join(",");
    return (
      <div>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginBottom: 12 }}>
          <div className="field">
            <label className="label">开篇模式</label>
            <select
              className="select w-full"
              value={draft.opening_pattern || "character_intro"}
              onChange={e => setDraft({ ...draft, opening_pattern: e.target.value })}
            >
              {Object.entries(OPENING_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </div>
          <NumInput
            label="钩子密度 (每章)"
            min={0}
            step={0.01}
            value={draft.hook_density}
            onChange={v => setDraft({ ...draft, hook_density: v })}
          />
        </div>
        <div className="field" style={{ marginBottom: 12 }}>
          <label className="label">高潮章节（逗号分隔）</label>
          <input
            className="input"
            value={climaxStr}
            onChange={e => {
              const list = e.target.value.split(/[,，\s]+/).map(s => parseInt(s, 10)).filter(n => Number.isFinite(n) && n > 0);
              setDraft({ ...draft, climax_positions: list });
            }}
            placeholder="例：12, 28, 45"
          />
        </div>
        <div className="label" style={{ marginBottom: 6 }}>爽点 (打脸/反转/突破...)</div>
        <div className="flex flex-col gap-6" style={{ marginBottom: 10 }}>
          {(draft.shuangdian || []).map((s, i) => (
            <div key={i} className="flex gap-6">
              <input
                className="input"
                type="number"
                min={1}
                placeholder="章节"
                value={s.chapter}
                onChange={e => {
                  const list = [...(draft.shuangdian || [])];
                  list[i] = { ...list[i], chapter: parseInt(e.target.value, 10) || 0 };
                  setDraft({ ...draft, shuangdian: list });
                }}
                style={{ width: 90 }}
              />
              <select
                className="select"
                value={s.type}
                onChange={e => {
                  const list = [...(draft.shuangdian || [])];
                  list[i] = { ...list[i], type: e.target.value };
                  setDraft({ ...draft, shuangdian: list });
                }}
                style={{ flex: 1 }}
              >
                {Object.entries(SHUANGDIAN_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                {!SHUANGDIAN_LABELS[s.type] && <option value={s.type}>{s.type}</option>}
              </select>
              <button
                className="btn-icon"
                onClick={() => {
                  const list = [...(draft.shuangdian || [])];
                  list.splice(i, 1);
                  setDraft({ ...draft, shuangdian: list });
                }}
                style={{ fontSize: 14 }}
              >&times;</button>
            </div>
          ))}
          <button
            className="btn"
            style={{ fontSize: 12, padding: "4px 10px", alignSelf: "flex-start" }}
            onClick={() => setDraft({ ...draft, shuangdian: [...(draft.shuangdian || []), { chapter: 1, type: "face_slap" }] })}
          >+ 新增爽点</button>
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button className="btn" onClick={cancel} disabled={saving}>取消</button>
          <button className="btn-primary" onClick={save} disabled={saving}>{saving ? "保存中..." : "保存"}</button>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 12 }}>
        <div>
          <div className="text-xs text-muted">开篇模式</div>
          <div style={{ fontSize: 13, fontWeight: 600, marginTop: 2 }}>
            {OPENING_LABELS[d.opening_pattern || ""] || d.opening_pattern || "—"}
          </div>
        </div>
        <div>
          <div className="text-xs text-muted">钩子密度（每章）</div>
          <div style={{ fontSize: 13, fontWeight: 600, marginTop: 2 }}>
            {typeof d.hook_density === "number" ? d.hook_density.toFixed(2) : "—"}
          </div>
        </div>
      </div>
      <div style={{ marginBottom: 12 }}>
        <div className="text-xs text-muted" style={{ marginBottom: 4 }}>高潮章节</div>
        <div className="flex gap-4" style={{ flexWrap: "wrap" }}>
          {(d.climax_positions || []).length === 0
            ? <span className="text-xs text-muted">—</span>
            : (d.climax_positions || []).map((c, i) => (
              <span key={i} className="tag accent" style={{ fontSize: 11 }}>第{c}章</span>
            ))}
        </div>
      </div>
      <div style={{ marginBottom: 8 }}>
        <div className="text-xs text-muted" style={{ marginBottom: 4 }}>爽点</div>
        {sd.length === 0 ? <span className="text-xs text-muted">—</span> : (
          <div className="flex gap-4" style={{ flexWrap: "wrap" }}>
            {sd.map((s, i) => (
              <span key={i} className="tag" style={{ fontSize: 11 }}>
                第{s.chapter}章 · {SHUANGDIAN_LABELS[s.type] || s.type}
              </span>
            ))}
          </div>
        )}
      </div>
      {beats.length > 0 && (
        <details style={{ marginTop: 12 }}>
          <summary className="text-xs text-muted" style={{ cursor: "pointer" }}>查看章节节拍 ({beats.length} 章)</summary>
          <div style={{ maxHeight: 200, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 4, marginTop: 6, padding: 8 }}>
            <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ color: "var(--text-tertiary)" }}>
                  <th style={{ textAlign: "left", padding: 2 }}>章</th>
                  <th style={{ textAlign: "left", padding: 2 }}>功能</th>
                  <th style={{ textAlign: "right", padding: 2 }}>张力</th>
                  <th style={{ textAlign: "right", padding: 2 }}>钩子</th>
                </tr>
              </thead>
              <tbody>
                {beats.map(b => (
                  <tr key={b.chapter}>
                    <td style={{ padding: 2 }}>{b.chapter}</td>
                    <td style={{ padding: 2 }}>{BEAT_LABEL[b.function] || b.function}</td>
                    <td style={{ padding: 2, textAlign: "right" }}>{b.tension?.toFixed(2)}</td>
                    <td style={{ padding: 2, textAlign: "right" }}>{b.hooks}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      )}
      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 12 }}>
        <EditIconButton onClick={start} />
      </div>
    </div>
  );
}

/* ──────────────── Characters ──────────────── */

type CharacterRoleTag =
  | "主角" | "女主角" | "反派"
  | "男配" | "女配" | "师长"
  | "重要配角" | "路人" | "其他" | "";

const ROLE_TAG_COLOR: Record<string, string> = {
  主角:     "var(--accent)",
  女主角:   "#f472b6",
  反派:     "var(--error)",
  男配:     "var(--indigo)",
  女配:     "var(--purple)",
  师长:     "var(--gold)",
  重要配角: "var(--jade)",
  路人:     "var(--text-tertiary)",
  其他:     "var(--text-tertiary)",
};

/** A single chapter-tagged fact about a character (appearance,
 *  personality trait, or experience). Chapter is "第 N 章" or empty. */
export interface CharacterListItem {
  chapter: string;
  text: string;
}

export interface CharacterItem {
  name: string;
  mentions?: number;
  intro?: string;
  speech_samples?: string[];
  appearance_chapters?: number;
  appearance_word_count?: number;
  first_seen_at?: string;
  first_chapter?: string;
  role_tag?: CharacterRoleTag;
  // Rich per-category fact lists with chapter tags. Populated by the
  // new per-chunk extraction prompt; older data simply has these
  // empty or absent and the editor falls back to the intro/legacy view.
  appearance?: CharacterListItem[];  // 外貌
  personality?: CharacterListItem[]; // 性格
  experiences?: CharacterListItem[]; // 经历
}

function fmtAppearance(c: CharacterItem): string {
  const ap = c.appearance_chapters || 0;
  if (ap > 0) return `出场 ${ap} 章/集`;
  const w = c.appearance_word_count || 0;
  if (w >= 10000) return `约 ${(w / 10000).toFixed(1)} 万字`;
  if (w > 0) return `约 ${w.toLocaleString()} 字`;
  if (c.mentions) return `提及 ${c.mentions} 次`;
  return "";
}

export function CharactersEditor({ data, onSave }: { data: CharacterItem[] | null; onSave: (d: CharacterItem[]) => Promise<void> | void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<CharacterItem[]>(data || []);
  const [saving, setSaving] = useState(false);

  const start = () => { setDraft(data || []); setEditing(true); };
  const cancel = () => { setDraft(data || []); setEditing(false); };
  const save = async () => {
    setSaving(true);
    try { await onSave(draft); setEditing(false); } finally { setSaving(false); }
  };

  const list = editing ? draft : (data || []);

  if (editing) {
    return (
      <div>
        <div className="flex flex-col gap-8" style={{ marginBottom: 12 }}>
          {draft.map((c, i) => (
            <div key={i} style={{ border: "1px solid var(--border)", borderRadius: 4, padding: 10 }}>
              <div className="flex gap-6 mb-6">
                <input
                  className="input"
                  placeholder="姓名"
                  value={c.name}
                  onChange={e => {
                    const list = [...draft]; list[i] = { ...c, name: e.target.value }; setDraft(list);
                  }}
                  style={{ flex: 1 }}
                />
                <input
                  className="input"
                  type="number"
                  placeholder="出场章数"
                  value={c.appearance_chapters ?? 0}
                  onChange={e => {
                    const list = [...draft]; list[i] = { ...c, appearance_chapters: parseInt(e.target.value, 10) || 0 }; setDraft(list);
                  }}
                  style={{ width: 110 }}
                  title="该角色出现的章节/集数"
                />
                <button
                  className="btn-icon"
                  onClick={() => { const list = [...draft]; list.splice(i, 1); setDraft(list); }}
                  style={{ fontSize: 14 }}
                >&times;</button>
              </div>
              <div className="flex gap-6 mb-6">
                <select
                  className="select"
                  value={c.role_tag || ""}
                  onChange={e => {
                    const list = [...draft]; list[i] = { ...c, role_tag: e.target.value as CharacterRoleTag }; setDraft(list);
                  }}
                  style={{ flex: "0 0 130px" }}
                  title="角色定位"
                >
                  <option value="">（未标注）</option>
                  {(["主角","女主角","反派","男配","女配","师长","重要配角","路人","其他"] as const).map(rt =>
                    <option key={rt} value={rt}>{rt}</option>
                  )}
                </select>
                <input
                  className="input"
                  placeholder='首次出场时间锚点'
                  value={c.first_seen_at || ""}
                  onChange={e => {
                    const list = [...draft]; list[i] = { ...c, first_seen_at: e.target.value }; setDraft(list);
                  }}
                  style={{ flex: 1, fontSize: 12 }}
                />
              </div>
              <textarea
                className="input"
                rows={3}
                placeholder="角色简介（身份 / 能力 / 关键设定，1-3 句）"
                value={c.intro || ""}
                onChange={e => {
                  const list = [...draft]; list[i] = { ...c, intro: e.target.value }; setDraft(list);
                }}
                style={{ marginBottom: 6 }}
              />
              <textarea
                className="input"
                rows={2}
                placeholder="对白样本（每行一条）"
                value={(c.speech_samples || []).join("\n")}
                onChange={e => {
                  const list = [...draft];
                  list[i] = { ...c, speech_samples: e.target.value.split("\n").map(s => s.trim()).filter(Boolean) };
                  setDraft(list);
                }}
              />
            </div>
          ))}
          <button
            className="btn"
            style={{ fontSize: 12, padding: "4px 10px", alignSelf: "flex-start" }}
            onClick={() => setDraft([...draft, { name: "", mentions: 0, intro: "", speech_samples: [], appearance_chapters: 0, appearance_word_count: 0, first_seen_at: "" }])}
          >+ 新增角色</button>
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button className="btn" onClick={cancel} disabled={saving}>取消</button>
          <button className="btn-primary" onClick={save} disabled={saving}>{saving ? "保存中..." : "保存"}</button>
        </div>
      </div>
    );
  }

  return (
    <CharactersReadOnlyList
      list={list}
      onEdit={start}
      onSaveOne={async (idx, next) => {
        const updated = [...(data || [])];
        updated[idx] = next;
        await onSave(updated);
      }}
    />
  );
}

function CharactersReadOnlyList({
  list, onEdit, onSaveOne,
}: {
  list: CharacterItem[];
  onEdit: () => void;
  onSaveOne?: (originalIndex: number, next: CharacterItem) => Promise<void> | void;
}) {
  const [showAll, setShowAll] = useState(false);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  // Per-card inline edit state: original-list index of the char being
  // edited + a working draft. Editing one card leaves the rest untouched.
  const [cardEdit, setCardEdit] = useState<{ idx: number; draft: CharacterItem } | null>(null);
  const [cardSaving, setCardSaving] = useState(false);
  const INITIAL = 5;

  // Sort with protagonist tags first, then by appearance_chapters desc,
  // then mentions desc. We pair each item with its original index so
  // per-card edits can write back to the right slot.
  const _ROLE_ORDER: Record<string, number> = {
    主角: 0, 女主角: 1, 反派: 2,
    男配: 3, 女配: 3, 师长: 3,
    重要配角: 4, 路人: 8, 其他: 9, "": 9,
  };
  const sortedWithIdx = list.map((c, idx) => ({ c, idx })).sort((a, b) => {
    const ra = _ROLE_ORDER[a.c.role_tag || ""] ?? 9;
    const rb = _ROLE_ORDER[b.c.role_tag || ""] ?? 9;
    if (ra !== rb) return ra - rb;
    const apA = a.c.appearance_chapters || 0;
    const apB = b.c.appearance_chapters || 0;
    if (apA !== apB) return apB - apA;
    return (b.c.mentions || 0) - (a.c.mentions || 0);
  });
  const visible = showAll ? sortedWithIdx : sortedWithIdx.slice(0, INITIAL);

  const saveCard = async () => {
    if (!cardEdit || !onSaveOne) return;
    setCardSaving(true);
    try {
      await onSaveOne(cardEdit.idx, cardEdit.draft);
      setCardEdit(null);
    } finally { setCardSaving(false); }
  };

  return (
    <div>
      <div className="flex flex-col gap-6" style={{ marginBottom: 12 }}>
        {list.length === 0 && <div className="text-xs text-muted text-center" style={{ padding: 8 }}>暂无角色</div>}
        {visible.map(({ c, idx }) => {
          const isOpen = !!expanded[idx];
          const hasSpeech = (c.speech_samples || []).length > 0;
          const isInline = cardEdit?.idx === idx;
          if (isInline) {
            const ed = cardEdit!.draft;
            const patch = (p: Partial<CharacterItem>) => setCardEdit({ idx, draft: { ...ed, ...p } });
            return (
              <div key={idx} style={{
                border: "1px solid var(--accent)", borderRadius: 4,
                padding: 10, background: "var(--bg-card)",
              }}>
                <div className="flex gap-6" style={{ marginBottom: 6 }}>
                  <input className="input" placeholder="姓名" value={ed.name}
                    onChange={e => patch({ name: e.target.value })}
                    style={{ flex: 1, fontSize: 12 }} />
                  <select className="select" value={ed.role_tag || ""}
                    onChange={e => patch({ role_tag: e.target.value as CharacterRoleTag })}
                    style={{ flex: "0 0 130px" }} title="角色定位">
                    <option value="">（未标注）</option>
                    {(["主角","女主角","反派","男配","女配","师长","重要配角","路人","其他"] as const).map(rt =>
                      <option key={rt} value={rt}>{rt}</option>)}
                  </select>
                </div>
                <input className="input" placeholder="首次出场时间锚点"
                  value={ed.first_seen_at || ""}
                  onChange={e => patch({ first_seen_at: e.target.value })}
                  style={{ marginBottom: 6, fontSize: 12 }} />
                <textarea className="input" rows={3}
                  placeholder="角色简介（身份 / 能力 / 关键设定，1-3 句）"
                  value={ed.intro || ""}
                  onChange={e => patch({ intro: e.target.value })}
                  style={{ marginBottom: 6, fontSize: 12 }} />
                <textarea className="input" rows={2}
                  placeholder="对白样本（每行一条）"
                  value={(ed.speech_samples || []).join("\n")}
                  onChange={e => patch({
                    speech_samples: e.target.value.split("\n").map(s => s.trim()).filter(Boolean),
                  })}
                  style={{ marginBottom: 6, fontSize: 12 }} />
                <div className="flex gap-6" style={{ justifyContent: "flex-end" }}>
                  <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
                          onClick={() => setCardEdit(null)} disabled={cardSaving}>取消</button>
                  <button className="btn-primary" style={{ fontSize: 11, padding: "3px 10px" }}
                          onClick={saveCard} disabled={cardSaving}>
                    {cardSaving ? "保存中..." : "保存"}
                  </button>
                </div>
              </div>
            );
          }
          return (
            <div key={idx} className="ref-card-row" style={{ border: "1px solid var(--border)", borderRadius: 4 }}>
              <button
                className="btn-ghost w-full"
                style={{ justifyContent: "space-between", padding: "8px 10px", borderRadius: 0, fontWeight: 500, textAlign: "left" }}
                onClick={() => setExpanded(prev => ({ ...prev, [idx]: !prev[idx] }))}
                disabled={!hasSpeech && !c.intro}
              >
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span className="flex items-center gap-8" style={{ marginBottom: c.intro ? 4 : 0, flexWrap: "wrap" }}>
                    <span style={{ fontWeight: 600 }}>{c.name || "(未命名)"}</span>
                    {c.role_tag && c.role_tag !== "其他" && c.role_tag !== "路人" && (
                      <span className="tag" style={{
                        fontSize: 10, padding: "1px 7px",
                        color: "white",
                        background: ROLE_TAG_COLOR[c.role_tag] || "var(--text-tertiary)",
                        border: `1px solid ${ROLE_TAG_COLOR[c.role_tag] || "var(--text-tertiary)"}`,
                        fontWeight: 600,
                      }}>{c.role_tag}</span>
                    )}
                    <span className="text-xs text-muted">{fmtAppearance(c)}</span>
                    {c.first_seen_at && (
                      <span className="tag" style={{
                        fontSize: 10, padding: "1px 6px",
                        color: "var(--accent)",
                        background: "var(--accent-subtle)",
                        border: "1px solid var(--accent)",
                      }} title="首次出场时间锚点">{c.first_seen_at}</span>
                    )}
                  </span>
                  {c.intro && !isOpen && (
                    <span className="truncate" style={{
                      display: "block",
                      fontSize: 12, fontWeight: 400,
                      color: "var(--text-secondary)",
                      lineHeight: 1.4,
                    }}>{c.intro}</span>
                  )}
                </span>
                {(hasSpeech || c.intro) && (
                  <span className="text-xs text-muted" style={{ transition: "transform 0.15s", transform: isOpen ? "rotate(180deg)" : "none", display: "inline-block", marginLeft: 6 }}>&#x25BC;</span>
                )}
              </button>
              {onSaveOne && (
                <button
                  className="ref-inline-edit"
                  onClick={(e) => { e.stopPropagation(); setCardEdit({ idx, draft: { ...c } }); }}
                  title="单独编辑这个角色"
                  aria-label="单独编辑这个角色"
                >
                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                       stroke="currentColor" strokeWidth="2"
                       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M12 20h9" />
                    <path d="M16.5 3.5a2.121 2.121 0 1 1 3 3L7 19l-4 1 1-4 12.5-12.5z" />
                  </svg>
                </button>
              )}
              {isOpen && (
                <div style={{ padding: "0 10px 10px" }}>
                  {c.intro && (
                    <div style={{ marginBottom: 8 }}>
                      <div className="text-xs text-muted" style={{ marginBottom: 2 }}>简介</div>
                      <div style={{ fontSize: 12, lineHeight: 1.6, color: "var(--text-secondary)" }}>{c.intro}</div>
                    </div>
                  )}
                  {hasSpeech && (
                    <div>
                      <div className="text-xs text-muted" style={{ marginBottom: 2 }}>对白样本</div>
                      <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12, color: "var(--text-secondary)" }}>
                        {(c.speech_samples || []).slice(0, 3).map((s, j) => <li key={j} style={{ marginBottom: 2 }}>{s}</li>)}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
        {list.length > INITIAL && (
          <button
            className="btn-ghost"
            style={{ fontSize: 12, padding: "4px 10px", alignSelf: "center", color: "var(--accent)" }}
            onClick={() => setShowAll(!showAll)}
          >
            {showAll ? "收起" : `展开剩余 ${list.length - INITIAL} 位角色`}
          </button>
        )}
      </div>
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <button className="btn-ghost" style={{ fontSize: 11, color: "var(--text-tertiary)" }} onClick={onEdit}>编辑</button>
      </div>
    </div>
  );
}

/* ──────────────── Rhythm Template ──────────────── */

interface PacingSegment { start: number; end: number; pacing: string; avg_tension: number }
interface RhythmTemplate {
  tension_curve?: number[];
  pacing_segments?: PacingSegment[];
}

function TensionSparkline({ data }: { data: number[] }) {
  if (!data || data.length === 0) return null;
  const w = 600, h = 60;
  const step = data.length > 1 ? w / (data.length - 1) : w;
  const pts = data.map((v, i) => `${i * step},${h - v * h}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: "100%", height: 60, background: "var(--bg-surface-2)", borderRadius: 4 }}>
      <polyline points={pts} fill="none" stroke="var(--accent)" strokeWidth={1.5} />
    </svg>
  );
}

export function RhythmTemplateEditor({ data, onSave }: { data: RhythmTemplate | null; onSave: (d: RhythmTemplate) => Promise<void> | void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<RhythmTemplate>(data || {});
  const [saving, setSaving] = useState(false);

  const start = () => { setDraft(data || {}); setEditing(true); };
  const cancel = () => { setDraft(data || {}); setEditing(false); };
  const save = async () => {
    setSaving(true);
    try { await onSave(draft); setEditing(false); } finally { setSaving(false); }
  };

  const d = editing ? draft : (data || {});
  const curve = d.tension_curve || [];
  const segs = d.pacing_segments || [];

  if (editing) {
    return (
      <div>
        <div className="label" style={{ marginBottom: 6 }}>节奏分段（拖动滑块设置张力）</div>
        <div className="flex flex-col gap-10" style={{ marginBottom: 12 }}>
          {(draft.pacing_segments || []).map((s, i) => (
            <div key={i} style={{ border: "1px solid var(--border)", borderRadius: 4, padding: 10 }}>
              <div className="flex gap-6 items-center" style={{ marginBottom: 8 }}>
                <input className="input" type="number" placeholder="起" value={s.start} style={{ width: 80 }}
                  onChange={e => { const list = [...(draft.pacing_segments || [])]; list[i] = { ...s, start: parseInt(e.target.value, 10) || 0 }; setDraft({ ...draft, pacing_segments: list }); }} />
                <span className="text-xs text-muted">至</span>
                <input className="input" type="number" placeholder="止" value={s.end} style={{ width: 80 }}
                  onChange={e => { const list = [...(draft.pacing_segments || [])]; list[i] = { ...s, end: parseInt(e.target.value, 10) || 0 }; setDraft({ ...draft, pacing_segments: list }); }} />
                <select className="select" value={s.pacing}
                  onChange={e => { const list = [...(draft.pacing_segments || [])]; list[i] = { ...s, pacing: e.target.value }; setDraft({ ...draft, pacing_segments: list }); }}>
                  {(["fast", "medium", "slow"] as const).map(p => <option key={p} value={p}>{PACING_LABEL[p]}</option>)}
                </select>
                <div style={{ flex: 1 }} />
                <button className="btn-icon" onClick={() => { const list = [...(draft.pacing_segments || [])]; list.splice(i, 1); setDraft({ ...draft, pacing_segments: list }); }} style={{ fontSize: 14 }}>&times;</button>
              </div>
              <Slider01
                label="平均张力"
                value={s.avg_tension}
                onChange={v => { const list = [...(draft.pacing_segments || [])]; list[i] = { ...s, avg_tension: v }; setDraft({ ...draft, pacing_segments: list }); }}
              />
            </div>
          ))}
          <button
            className="btn"
            style={{ fontSize: 12, padding: "4px 10px", alignSelf: "flex-start" }}
            onClick={() => setDraft({ ...draft, pacing_segments: [...(draft.pacing_segments || []), { start: 1, end: 1, pacing: "medium", avg_tension: 0.5 }] })}
          >+ 新增段</button>
        </div>

        <div className="label" style={{ marginBottom: 6 }}>张力曲线（每章 0-1，拖动调整）</div>
        <div style={{ maxHeight: 280, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 4, padding: 8, marginBottom: 12 }}>
          {(draft.tension_curve || []).length === 0 ? (
            <div className="text-xs text-muted" style={{ padding: 8 }}>暂无张力数据 — 请先提取特征。</div>
          ) : (draft.tension_curve || []).map((t, i) => (
            <div key={i} className="flex items-center gap-8" style={{ marginBottom: 4 }}>
              <span className="text-xs text-muted" style={{ width: 50, flexShrink: 0 }}>第{i + 1}章</span>
              <input
                type="range" min={0} max={1} step={0.01} value={t}
                onChange={e => {
                  const list = [...(draft.tension_curve || [])];
                  list[i] = parseFloat(e.target.value) || 0;
                  setDraft({ ...draft, tension_curve: list });
                }}
                style={{ flex: 1, accentColor: "var(--accent)" }}
              />
              <span style={{ fontFamily: "var(--font-mono)", fontSize: 11, fontWeight: 600, color: "var(--accent)", width: 36, textAlign: "right" }}>{t.toFixed(2)}</span>
            </div>
          ))}
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button className="btn" onClick={cancel} disabled={saving}>取消</button>
          <button className="btn-primary" onClick={save} disabled={saving}>{saving ? "保存中..." : "保存"}</button>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="text-xs text-muted" style={{ marginBottom: 4 }}>张力曲线（共 {curve.length} 章）</div>
      <TensionSparkline data={curve} />
      <div className="label" style={{ marginTop: 12, marginBottom: 6 }}>节奏分段</div>
      <div className="flex flex-col gap-4">
        {segs.length === 0 && <div className="text-xs text-muted">—</div>}
        {segs.map((s, i) => (
          <div key={i} className="flex items-center gap-8" style={{
            padding: "6px 10px",
            background: "var(--bg-surface)",
            borderRadius: 4,
            borderLeft: `3px solid ${PACING_COLOR[s.pacing] || "var(--border)"}`,
          }}>
            <span style={{ fontSize: 12, fontWeight: 600, color: PACING_COLOR[s.pacing] || "var(--text-primary)" }}>{PACING_LABEL[s.pacing] || s.pacing}</span>
            <span className="text-xs text-muted">第 {s.start} – {s.end} 章</span>
            <span className="text-xs text-muted" style={{ marginLeft: "auto" }}>平均张力 {s.avg_tension?.toFixed(2)}</span>
          </div>
        ))}
      </div>
      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 12 }}>
        <EditIconButton onClick={start} />
      </div>
    </div>
  );
}

/* ──────────────── Plot Outline (剧情大纲 · 编年史格式) ──────────────── */
/**
 * Chronicle format. Reference: 编年史是作者查阅工具,世界内史学家视角。
 *   epochs[] → periods[] (按时间) → events[]
 *   event = 【主体·分类·事件名】描述。颗粒度按章节弧标题级别（每章 1-3 条）。
 *   Each event also carries two time anchors:
 *     time_marker  = 故事中时间（in-fiction clock, e.g. 「1954 年」）
 *     first_chapter = 首次出现章节（real-text reference, e.g. 「第 12 章」）
 *
 * Backwards-compat: also accepts older arcs/key_events shape (and tolerates a
 * legacy `hidden` field on events / settings, but never displays or edits it).
 */

export interface ChronicleEvent {
  subject: string;     // 主体: 人物/组织/概念
  category: string;    // 分类
  name: string;        // 事件名（章节弧标题级别）
  description: string; // 1 句客观描述
  // Two time anchors — shown side-by-side as tags in the read view.
  // time_marker: 故事中时间 「1954 年 3 月」 (in-fiction clock)
  // first_chapter: 首次出现章节 「第 12 章」 (real-text reference)
  time_marker?: string;
  /** Multiple time references for the same event — used when the LLM
   *  provided more than one timestamp (e.g. an in-fiction date AND a
   *  countdown reading) and when the paste parser resolved a relative
   *  reference against an earlier absolute one. The read view renders
   *  each entry as a separate tag for cross-referencing. */
  time_markers?: string[];
  first_chapter?: string;
  /** @deprecated Legacy [隐] field from older chronicles. New events no
   *  longer carry it; the editor still tolerates the key on existing
   *  data but never displays or edits it. */
  hidden?: string;
}
export interface ChroniclePeriod {
  time: string;        // 时间标题: "2030 年 2 月上旬" / "第一卷开篇" 等
  events: ChronicleEvent[];
}
export interface ChronicleEpoch {
  title?: string;      // 可选大段标题: "前人类纪元" / "人类常态历史"
  periods: ChroniclePeriod[];
}
export interface PlotOutline {
  logline?: string;
  epochs?: ChronicleEpoch[];
  // legacy (older extractor output)
  themes?: string[];
  arcs?: any[];
  key_events?: any[];
}

function isLegacy(d: PlotOutline | null | undefined): boolean {
  if (!d) return false;
  if (d.epochs && d.epochs.length) return false;
  return Boolean((d.arcs && d.arcs.length) || (d.key_events && d.key_events.length));
}

function emptyEvent(): ChronicleEvent {
  return { subject: "", category: "", name: "", description: "", time_marker: "", first_chapter: "" };
}

/** Strip ```json fences and `<think>` blocks from raw LLM text so the
 * paste-back parser sees just the JSON payload. Mirrors the backend
 * `_strip_json` logic so paste behavior matches what the pipeline does. */
function stripJsonFences(s: string): string {
  let out = s.trim();
  // Drop <think>...</think> reasoning blocks.
  out = out.replace(/<think>[\s\S]*?<\/think>/gi, "").trim();
  out = out.replace(/<\/?think>/gi, "").trim();
  // Whole-response ```json fence.
  const fence = out.match(/^```(?:json)?\s*([\s\S]*?)\s*```$/i);
  if (fence) out = fence[1].trim();
  // Lock onto the earliest JSON delimiter so leading prose / chunk
  // separator lines don't break the parse.
  const earliest = (() => {
    const a = out.indexOf("["), b = out.indexOf("{");
    if (a < 0) return b;
    if (b < 0) return a;
    return Math.min(a, b);
  })();
  if (earliest > 0) out = out.slice(earliest);
  // Trim trailing prose.
  const lastClose = Math.max(out.lastIndexOf("}"), out.lastIndexOf("]"));
  if (lastClose >= 0) out = out.slice(0, lastClose + 1);
  return out;
}

const CHRONICLE_EVENT_KEYS = new Set([
  "subject", "category", "name", "description",
  "time_marker", "first_chapter",
]);
function looksLikeEvent(o: any): boolean {
  if (!o || typeof o !== "object") return false;
  // Treat anything with name OR description AND any other event key as event-shaped.
  const hasIdentity = typeof o.name === "string" || typeof o.description === "string";
  if (!hasIdentity) return false;
  for (const k of Object.keys(o)) if (CHRONICLE_EVENT_KEYS.has(k)) return true;
  return false;
}
function looksLikePeriod(o: any): boolean {
  return !!(o && typeof o === "object" && Array.isArray(o.events) && !Array.isArray(o.epochs));
}
function looksLikeEpoch(o: any): boolean {
  return !!(o && typeof o === "object" && Array.isArray(o.periods));
}
function looksLikeOutline(o: any): boolean {
  return !!(o && typeof o === "object" && Array.isArray(o.epochs));
}
function looksLikeEventsObject(o: any): boolean {
  // The per-chunk extraction prompt returns this shape:
  // {"events": [{first_chapter, time_marker, subject, ...}, ...]}
  // Distinct from a "period" which also has an events array — periods
  // typically carry a `time` field; an events-only wrapper does not.
  return !!(o && typeof o === "object" && !Array.isArray(o)
            && Array.isArray(o.events)
            && !Array.isArray(o.epochs)
            && !Array.isArray(o.periods)
            && typeof o.time !== "string");
}

/** Group a flat events array into a chronicle, bucketing by
 * `first_chapter` so each chapter becomes its own period. Preserves
 * the order in which chapters first appear in the input — that's the
 * "chapter order" the extraction step is supposed to produce. */
function eventsArrayToChronicle(
  events: ChronicleEvent[], logline = "", epochTitle = "(章节顺序)",
): PlotOutline {
  const filtered = events.filter(e => e && typeof e === "object" && (e.name || e.description));
  if (filtered.length === 0) return { logline, epochs: [] };
  const byChapter = new Map<string, ChronicleEvent[]>();
  for (const ev of filtered) {
    const key = (ev.first_chapter || "").trim() || "(未指定章节)";
    if (!byChapter.has(key)) byChapter.set(key, []);
    byChapter.get(key)!.push(ev);
  }
  const periods: ChroniclePeriod[] = Array.from(byChapter.entries()).map(
    ([time, evs]) => ({ time, events: evs }),
  );
  return { logline, epochs: [{ title: epochTitle, periods }] };
}

/** Coerce one of several user-pastable shapes into a `PlotOutline`.
 * The web-LLM workflow can produce: a full outline, a list of outlines
 * (from multi-chunk copy-all), a list of epochs, a list of periods,
 * a list of events, OR an object wrapping events under an `events`
 * key (the new per-chunk extraction format). This makes the paste
 * tolerant of all of them so the user doesn't have to hand-edit. */
function normalizePastedChronicle(data: any): PlotOutline | null {
  // Case 0: object with `events` array — the per-chunk extraction
  // output. Group by first_chapter into chapter-order periods.
  if (looksLikeEventsObject(data)) {
    return eventsArrayToChronicle(
      data.events as ChronicleEvent[],
      typeof data.logline === "string" ? data.logline : "",
    );
  }
  // Case 0b: array of those events-objects (multi-chunk paste-all).
  if (Array.isArray(data) && data.length > 0 && data.every(looksLikeEventsObject)) {
    const flat: ChronicleEvent[] = [];
    let logline = "";
    for (const obj of data) {
      if (!logline && typeof obj.logline === "string") logline = obj.logline;
      for (const ev of (obj.events || [])) flat.push(ev);
    }
    return eventsArrayToChronicle(flat, logline);
  }
  // Case 1: single PlotOutline
  if (looksLikeOutline(data)) return data as PlotOutline;
  // Case 2: array of PlotOutlines (multi-chunk copy-all of summary prompt)
  if (Array.isArray(data) && data.length > 0 && data.every(looksLikeOutline)) {
    const epochs: ChronicleEpoch[] = [];
    let logline = "";
    for (const po of data as PlotOutline[]) {
      if (!logline && po.logline) logline = po.logline;
      for (const ep of (po.epochs || [])) epochs.push(ep);
    }
    return { logline, epochs };
  }
  // Case 3: list of epochs
  if (Array.isArray(data) && data.length > 0 && data.every(looksLikeEpoch)) {
    return { logline: "", epochs: data as ChronicleEpoch[] };
  }
  // Case 4: list of periods → wrap in one epoch
  if (Array.isArray(data) && data.length > 0 && data.every(looksLikePeriod)) {
    return {
      logline: "",
      epochs: [{ title: "粘贴的事件", periods: data as ChroniclePeriod[] }],
    };
  }
  // Case 5: bare list of events → group by first_chapter
  if (Array.isArray(data) && data.length > 0 && data.every(looksLikeEvent)) {
    return eventsArrayToChronicle(data as ChronicleEvent[], "", "粘贴的事件");
  }
  return null;
}

const EVENT_CATEGORIES: { key: string; label: string }[] = [
  { key: "plot_main",   label: "主线情节 (plot_main)" },
  { key: "plot_side",   label: "支线情节 (plot_side)" },
  { key: "character",   label: "角色变化 (character)" },
  { key: "setting",     label: "世界观 (setting)" },
  { key: "conflict",    label: "冲突/对抗 (conflict)" },
  { key: "revelation",  label: "真相揭示 (revelation)" },
  { key: "foreshadow",  label: "伏笔铺垫 (foreshadow)" },
  { key: "other",       label: "其他 (other)" },
];

/** Short Chinese label for a category key, used in tag-style display
 * (read view, preview chips). The LLM emits English enum keys; the UI
 * uses the Chinese rendering exclusively per user request. Unknown
 * values fall through unchanged so legacy data still shows something. */
export const CATEGORY_LABELS_CN: Record<string, string> = {
  plot_main:  "主线",
  plot_side:  "支线",
  character:  "角色",
  setting:    "设定",
  conflict:   "冲突",
  revelation: "揭示",
  foreshadow: "伏笔",
  other:      "其他",
};
export function categoryLabel(cat: string | undefined | null): string {
  if (!cat) return "";
  return CATEGORY_LABELS_CN[cat] || cat;
}

/** Build a deduped list of time tags from an event. Supports both the
 * legacy single `time_marker` field and the newer `time_markers` array
 * (which can be populated by the paste parser when the LLM provided
 * multiple time references, or when a relative time was resolved
 * against an earlier absolute one). Preserves first-occurrence order. */
export function timeMarkers(ev: { time_marker?: string; time_markers?: string[] }): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  const push = (s: any) => {
    if (typeof s !== "string") return;
    const v = s.trim();
    if (!v || seen.has(v)) return;
    seen.add(v);
    out.push(v);
  };
  if (Array.isArray(ev.time_markers)) for (const t of ev.time_markers) push(t);
  push(ev.time_marker);
  return out;
}

/** Best-effort comparator for two story-time markers. Pure date math is
 * brittle on Chinese fiction-time strings ("天宝十年", "穿越后第一日"),
 * so we use a tiered heuristic: (1) numeric year if both have one;
 * (2) BCE/CE flag; (3) zh-locale string compare for stability. This
 * deliberately isn't perfect — the AI 时间线总结 step is the proper
 * tool for re-ordering; this comparator just gives the read view a
 * reasonable client-side guess. */
/** Parse a coarse numeric "date value" from a time marker so events
 *  can be ordered. Returns year*10000 + month*100 + day (BCE → negative
 *  year), or null when there's no year anchor. Seasons map to a month
 *  so "1954 年初春" still sorts. */
function parseDateNum(marker: string): number | null {
  if (!marker) return null;
  const iso = marker.match(/(\d{4})[-/](\d{1,2})(?:[-/](\d{1,2}))?/);
  if (iso) {
    return parseInt(iso[1], 10) * 10000 + parseInt(iso[2], 10) * 100
         + (iso[3] ? parseInt(iso[3], 10) : 0);
  }
  const ym = marker.match(/(\d{4})\s*年/);
  if (!ym) return null;
  let y = parseInt(ym[1], 10);
  if (/公元前|前\s*\d/.test(marker)) y = -y;
  const mo = marker.match(/年[^\d]{0,4}(\d{1,2})\s*月/);
  let moN = mo ? parseInt(mo[1], 10) : 0;
  if (!moN) {
    if (/年初|开年|岁首/.test(marker)) moN = 1;
    else if (/初春|早春|春/.test(marker)) moN = 3;
    else if (/初夏|盛夏|夏/.test(marker)) moN = 6;
    else if (/初秋|深秋|秋/.test(marker)) moN = 9;
    else if (/初冬|寒冬|冬|年末|年底/.test(marker)) moN = 12;
  }
  const d = marker.match(/月[^\d]{0,2}(\d{1,2})\s*日/);
  return y * 10000 + moN * 100 + (d ? parseInt(d[1], 10) : 0);
}

function chapterNumOf(s: string): number {
  const m = (s || "").match(/(\d+)/);
  return m ? parseInt(m[1], 10) : 0;
}

/** Build a single synthetic epoch grouped + sorted by story time, for
 *  the 全书时间线 view.
 *
 *  Sorting rule (per the user's spec): events with a concrete date sort
 *  by that date; events WITHOUT a concrete time fall back to chapter
 *  order (chapter number ≈ time progression). To interleave the two
 *  cleanly, an undated event inherits the date of the most recent dated
 *  event before it in chapter order — so it lands next to its chapter
 *  neighbours rather than being dumped at the end.
 *
 *  Heads-up: events shown here no longer correspond to a single
 *  (ei, pi, evi) location, so the caller suppresses per-event CRUD. */
function regroupChronicleByStoryTime(rawEpochs: ChronicleEpoch[]): ChronicleEpoch[] {
  type FlatEv = {
    ev: ChronicleEvent; marker: string; chapter: number;
    idx: number; dateNum: number | null; effDate: number;
  };
  const all: FlatEv[] = [];
  let i = 0;
  for (const ep of rawEpochs) {
    for (const per of (ep.periods || [])) {
      for (const ev of (per.events || [])) {
        const markers = timeMarkers(ev);
        const marker = markers.length > 0 ? markers[0] : "";
        const chapter = chapterNumOf((ev as any).first_chapter || per.time || "");
        all.push({
          ev, marker, chapter, idx: i++,
          dateNum: parseDateNum(marker), effDate: 0,
        });
      }
    }
  }
  // Walk chapter order, carrying the last seen concrete date forward so
  // undated events inherit a position.
  const byChapter = [...all].sort((a, b) => (a.chapter - b.chapter) || (a.idx - b.idx));
  let lastDate: number | null = null;
  for (const e of byChapter) {
    if (e.dateNum != null) lastDate = e.dateNum;
    e.effDate = e.dateNum != null ? e.dateNum : (lastDate != null ? lastDate : 0);
  }
  // Final order: effective date, then chapter, then original order.
  all.sort((a, b) =>
    (a.effDate - b.effDate) || (a.chapter - b.chapter) || (a.idx - b.idx));
  const periods: ChroniclePeriod[] = [];
  for (const { ev, marker, chapter } of all) {
    const key = marker || (chapter > 0 ? `第 ${chapter} 章` : "(未填时间)");
    const last = periods[periods.length - 1];
    if (last && last.time === key) last.events.push(ev);
    else periods.push({ time: key, events: [ev] });
  }
  return [{ title: "按故事时间排序", periods }];
}


const GRAN_LEVELS: { key: string; label: string }[] = [
  { key: "chapter", label: "章节级" },
  { key: "major_event", label: "大事件级" },
  { key: "volume", label: "卷级" },
  { key: "book", label: "全书级" },
];

/** 剧情大纲颗粒度调节 — condense the chapter-level outline to a more
 *  macro view (大事件 / 卷 / 全书) via built-in AI or the web-LLM
 *  copy-prompt / paste workflow. Applying overwrites plot_outline_json. */
function GranularityControl({ refId, onApply }: {
  refId: string;
  onApply: (plot: PlotOutline) => Promise<void> | void;
}) {
  const [level, setLevel] = useState("chapter");
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<PlotOutline | null>(null);
  const [pasteMode, setPasteMode] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [msg, setMsg] = useState("");

  const reset = () => { setResult(null); setPasteMode(false); setPasteText(""); setMsg(""); };
  const evCount = (p: PlotOutline | null): number => {
    let n = 0;
    for (const ep of (p?.epochs || [])) for (const per of (ep.periods || [])) n += (per.events || []).length;
    return n;
  };

  const genAI = async () => {
    setLoading(true); setMsg(""); setResult(null);
    try {
      const r = await apiPost<{ ok: boolean; plot_outline?: PlotOutline; error?: string }>(
        `/api/references/works/${refId}/plot_outline/summarize`, { level });
      if (r.ok && r.plot_outline) setResult(r.plot_outline);
      else setMsg(r.error || "概括失败");
    } catch (e: any) { setMsg(e?.message || "概括失败"); }
    finally { setLoading(false); }
  };

  const copyPrompt = async () => {
    try {
      const r = await apiPost<{ prompt?: string }>(
        `/api/references/works/${refId}/plot_outline/summarize`, { level, prompt_only: true });
      if (r.prompt) {
        try { await navigator.clipboard.writeText(r.prompt); }
        catch {
          const ta = document.createElement("textarea");
          ta.value = r.prompt; ta.style.position = "fixed"; ta.style.opacity = "0";
          document.body.appendChild(ta); ta.select(); document.execCommand("copy");
          document.body.removeChild(ta);
        }
        setMsg("已复制 prompt，可粘贴到网页 LLM");
      }
    } catch (e: any) { setMsg(e?.message || "复制失败"); }
  };

  const parsePaste = () => {
    try {
      const obj = JSON.parse(pasteText);
      if (obj && Array.isArray(obj.epochs)) { setResult(obj); setMsg(""); setPasteMode(false); }
      else setMsg("粘贴内容不是有效的大纲 JSON（需含 epochs 数组）");
    } catch { setMsg("JSON 解析失败，请检查粘贴内容"); }
  };

  const apply = async () => {
    if (!result) return;
    await onApply(result);
    reset();
    setMsg("已应用为剧情大纲");
  };

  return (
    <div style={{ marginBottom: 10, padding: 8, border: "1px solid var(--border)", borderRadius: 4, background: "var(--bg-surface)" }}>
      <div className="flex items-center" style={{ gap: 6, flexWrap: "wrap" }}>
        <span className="text-xs text-muted">颗粒度：</span>
        {GRAN_LEVELS.map(l => (
          <button key={l.key} className="btn-ghost"
            onClick={() => { setLevel(l.key); reset(); }}
            style={{
              padding: "3px 10px", fontSize: 11, borderRadius: 3, border: "1px solid var(--border)",
              fontWeight: level === l.key ? 600 : 400,
              color: level === l.key ? "var(--accent)" : "var(--text-secondary)",
              background: level === l.key ? "var(--accent-subtle)" : "transparent",
            }}>{l.label}</button>
        ))}
      </div>
      {level === "chapter" ? (
        <div className="text-xs text-muted" style={{ marginTop: 6, lineHeight: 1.5 }}>
          当前为最细颗粒度（来自预处理 + 特征提取）。选择更粗的颗粒度可生成更宏观的大纲。
        </div>
      ) : (
        <div style={{ marginTop: 6 }}>
          <div className="flex" style={{ gap: 6, flexWrap: "wrap" }}>
            <button className="btn-primary" style={{ fontSize: 11, padding: "3px 10px" }} onClick={genAI} disabled={loading}>
              {loading ? "概括中..." : "内置 AI 概括"}
            </button>
            <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }} onClick={copyPrompt}>复制 prompt</button>
            <button className="btn" style={{
              fontSize: 11, padding: "3px 10px",
              borderColor: pasteMode ? "var(--accent)" : "var(--border)",
              color: pasteMode ? "var(--accent)" : "var(--text-secondary)",
            }} onClick={() => setPasteMode(m => !m)}>{pasteMode ? "取消解析" : "解析网页结果"}</button>
          </div>
          {pasteMode && (
            <div style={{ marginTop: 6 }}>
              <textarea className="input" value={pasteText} onChange={e => setPasteText(e.target.value)}
                placeholder="把网页 LLM 返回的大纲 JSON 粘贴到这里" rows={4}
                style={{ width: "100%", fontSize: 11, padding: "4px 8px", resize: "vertical", lineHeight: 1.5 }} />
              <button className="btn-primary" style={{ fontSize: 11, padding: "3px 12px", marginTop: 4 }}
                onClick={parsePaste} disabled={!pasteText.trim()}>解析</button>
            </div>
          )}
          {result && (
            <div style={{ marginTop: 6, padding: "6px 8px", background: "var(--accent-subtle)", border: "1px solid var(--accent)", borderRadius: 4 }}>
              <span className="text-xs" style={{ color: "var(--accent)" }}>
                已生成「{GRAN_LEVELS.find(l => l.key === level)?.label}」大纲：{(result.epochs || []).length} 段 · {evCount(result)} 事件
              </span>
              <button className="btn-primary" style={{ fontSize: 11, padding: "3px 12px", marginLeft: 8 }}
                onClick={apply} title="替换当前剧情大纲（章节级可在「特征提取」中重新生成）">应用为剧情大纲</button>
            </div>
          )}
          {msg && (
            <div className="text-xs" style={{ marginTop: 4, color: msg.startsWith("已") ? "var(--jade)" : "var(--error)" }}>{msg}</div>
          )}
        </div>
      )}
    </div>
  );
}

export function PlotOutlineEditor({
  data,
  onSave,
  onExtract,
  extracting,
  refId,
  chunkList,
}: {
  data: PlotOutline | null;
  onSave: (d: PlotOutline) => Promise<void> | void;
  onExtract?: () => void;
  extracting?: boolean;
  /** Passed through so the embedded "全时间线总结" controls can hit
   *  the work-scoped summarize endpoint. Optional — when missing,
   *  the summary panel hides itself. */
  refId?: string;
  /** Extraction chunk list — when supplied, each period in 分段视图
   *  gets a「· 第 N 段」label so the user can locate the events. */
  chunkList?: { globalIndex: number; startChapter: number; endChapter: number }[];
}) {
  const { confirm } = useDialog();
  /** Find which 分段 a period (labelled "第 N 章") belongs to. */
  const chunkOfChapter = (periodTime: string): number | null => {
    if (!chunkList || chunkList.length === 0) return null;
    const m = (periodTime || "").match(/第\s*(\d+)\s*章/);
    if (!m) return null;
    const ch = parseInt(m[1], 10);
    const hit = chunkList.find(c => ch >= c.startChapter && ch <= c.endChapter);
    return hit ? hit.globalIndex : null;
  };
  const [openEpoch, setOpenEpoch] = useState<Record<number, boolean>>({});
  // Per-event inline edit state — "ei-pi-evi" key → ChronicleEvent draft.
  // Editing one row at a time keeps the rest of the chronicle in read view.
  const [eventEdit, setEventEdit] = useState<{ key: string; draft: ChronicleEvent } | null>(null);
  const [eventEditSaving, setEventEditSaving] = useState(false);

  const startEventEdit = (ei: number, pi: number, evi: number, ev: ChronicleEvent) => {
    setEventEdit({ key: `${ei}-${pi}-${evi}`, draft: { ...ev } });
  };
  const cancelEventEdit = () => setEventEdit(null);

  // Deep-clone the editor data so a CRUD operation in read view doesn't
  // mutate state objects that other components might be observing.
  const cloneData = (): PlotOutline => (
    data
      ? {
          ...data,
          epochs: (data.epochs || []).map(e => ({
            ...e,
            periods: (e.periods || []).map(p => ({
              ...p,
              events: [...(p.events || [])],
            })),
          })),
        }
      : { epochs: [] }
  );

  const saveEventEdit = async () => {
    if (!eventEdit) return;
    setEventEditSaving(true);
    try {
      const [ei, pi, evi] = eventEdit.key.split("-").map(n => parseInt(n, 10));
      const base = cloneData();
      const epochs = base.epochs || [];
      if (epochs[ei] && epochs[ei].periods[pi]) {
        epochs[ei].periods[pi].events[evi] = eventEdit.draft;
      }
      await onSave(base);
      setEventEdit(null);
    } finally { setEventEditSaving(false); }
  };

  // CRUD operations callable from the read view — no need to enter
  // global edit mode to delete a single bad event. We persist each
  // change immediately via onSave so the user sees the DB-backed
  // chronicle update right after pressing the button.
  const deleteEventInRead = async (ei: number, pi: number, evi: number) => {
    if (!(await confirm({ message: "确认删除这条事件？此操作会立即保存到数据库。", destructive: true }))) return;
    const base = cloneData();
    const epochs = base.epochs || [];
    if (!epochs[ei]?.periods[pi]?.events) return;
    epochs[ei].periods[pi].events.splice(evi, 1);
    await onSave(base);
  };
  const deletePeriodInRead = async (ei: number, pi: number) => {
    if (!(await confirm({ message: "确认删除这个时间段及其下所有事件？此操作会立即保存。", destructive: true }))) return;
    const base = cloneData();
    const epochs = base.epochs || [];
    if (!epochs[ei]?.periods) return;
    epochs[ei].periods.splice(pi, 1);
    await onSave(base);
  };
  const deleteEpochInRead = async (ei: number) => {
    if (!(await confirm({ message: "确认删除这个大段及其下所有时间段/事件？此操作会立即保存。", destructive: true }))) return;
    const base = cloneData();
    base.epochs = (base.epochs || []).filter((_, i) => i !== ei);
    await onSave(base);
  };
  // Instructions disclosure — collapsible because chronicle conventions
  // can be unfamiliar to first-time users, but veteran users won't want
  // to see the wall of text on every load.

  // View mode for the chronicle:
  //   "chapter" = 章节顺序 — events grouped by first_chapter, the
  //               natural order produced by the extraction step.
  //   "story"   = 时间顺序 — events grouped by their first (most-
  //               anchored) time_marker, sorted by that marker so the
  //               reader sees flashbacks/inserts pulled into the actual
  //               in-fiction chronology.
  // The toggle is display-only; nothing is rewritten to disk.
  const [viewMode, setViewMode] = useState<"chapter" | "story">("chapter");

  // Read-only chronicle view. With the full-edit / quick-add / paste
  // entry points removed (per user request), the only mutations from
  // this editor are per-event inline pencil-edit and the per-level
  // delete buttons — both write straight through onSave.
  const d = data || {};
  const legacy = isLegacy(d);

  const rawEpochs = d.epochs || [];
  // Apply the view-mode transformation. Chapter mode renders the stored
  // structure as-is. Story mode rebuilds a single synthetic epoch from
  // all events sorted by their first time_marker, with one period per
  // distinct marker.
  const epochs = (viewMode === "story" && rawEpochs.length > 0)
    ? regroupChronicleByStoryTime(rawEpochs)
    : rawEpochs;
  const hasContent = (epochs.length > 0 && epochs.some(e => (e.periods || []).length > 0)) || legacy || d.logline;

  return (
    <div>
      {/* View-mode toggle: 分段视图 (storage order) vs. 全书时间线
        * (flashbacks pulled into chronology). Storage is untouched —
        * story mode is a pure display transform. */}
      {hasContent && (
        <div className="flex items-center" style={{ marginBottom: 10, gap: 6 }}>
          <span className="text-xs text-muted">视图：</span>
          {([
            { key: "chapter" as const, label: "分段视图", hint: "按卷/分段分组，事件按首次出现的章节排列（提取的原始顺序）" },
            { key: "story"   as const, label: "全书时间线", hint: "全书按故事中时间排序，倒叙/插叙被拉直（仅显示重排，存储不变）" },
          ]).map(opt => (
            <button
              key={opt.key}
              className="btn-ghost"
              onClick={() => setViewMode(opt.key)}
              title={opt.hint}
              style={{
                padding: "3px 10px", fontSize: 11,
                fontWeight: viewMode === opt.key ? 600 : 400,
                color: viewMode === opt.key ? "var(--accent)" : "var(--text-secondary)",
                background: viewMode === opt.key ? "var(--accent-subtle)" : "transparent",
                border: "1px solid var(--border)",
                borderRadius: 3,
              }}>
              {opt.label}
            </button>
          ))}
          {viewMode === "story" && (
            <span className="text-xs text-muted" style={{ marginLeft: 6 }}>
              （此视图下隐藏编辑/删除按钮——切回分段视图后可改）
            </span>
          )}
        </div>
      )}

      {refId && hasContent && <GranularityControl refId={refId} onApply={onSave} />}

      {legacy && (
        <div style={{
          padding: "8px 10px",
          marginBottom: 10,
          background: "var(--bg-surface)",
          border: "1px dashed var(--border)",
          borderRadius: 4,
          fontSize: 11,
          color: "var(--text-tertiary)",
        }}>
          以下为旧版大纲数据。可在上方「分段提取大纲」逐段提取以替换为编年史格式，或点击「编辑」手动整理。
        </div>
      )}

      {!hasContent ? (
        <div className="text-xs text-muted text-center" style={{ padding: 12, lineHeight: 1.7 }}>
          暂无编年史。可以选择：
          <br />
          上方「分段提取大纲」让 AI 自动抽取；或下方手动添加 / 粘贴。
        </div>
      ) : (
        <>
          {d.logline && (
            <div style={{ marginBottom: 14 }}>
              <div className="text-xs text-muted" style={{ marginBottom: 4 }}>一句话梗概</div>
              <div style={{ fontSize: 13, lineHeight: 1.6, color: "var(--text-primary)" }}>{d.logline}</div>
            </div>
          )}

          <div className="flex flex-col gap-12">
            {epochs.map((ep, ei) => {
              const isOpen = openEpoch[ei] !== false; // default open
              return (
                <div key={ei}>
                  {ep.title && (
                    <div className="flex items-center" style={{ gap: 4 }}>
                      <button
                        className="btn-ghost"
                        style={{ flex: 1, justifyContent: "space-between", padding: "6px 0", fontWeight: 700, fontSize: 14, color: "var(--text-primary)", borderRadius: 0 }}
                        onClick={() => setOpenEpoch(prev => ({ ...prev, [ei]: !isOpen }))}
                      >
                        <span>{ep.title}</span>
                        <span className="text-xs text-muted" style={{ transition: "transform 0.15s", transform: isOpen ? "rotate(180deg)" : "none", display: "inline-block" }}>&#x25BC;</span>
                      </button>
                      {viewMode === "chapter" && <button
                        className="btn-icon"
                        onClick={() => deleteEpochInRead(ei)}
                        title="删除整个大段"
                        style={{ fontSize: 12, color: "var(--error)", width: 22, height: 22 }}
                      >&times;</button>}
                    </div>
                  )}
                  {isOpen && (ep.periods || []).map((per, pi) => {
                    const segNo = viewMode === "chapter"
                      ? chunkOfChapter(per.time || "") : null;
                    return (
                    <div key={pi} style={{ marginBottom: 12, paddingLeft: ep.title ? 8 : 0 }}>
                      <div className="flex items-center" style={{ marginBottom: 6, gap: 4 }}>
                        <div style={{ flex: 1, fontWeight: 600, fontSize: 13, color: "var(--accent)" }}>
                          {per.time || "(未填写时间)"}
                          {segNo != null && (
                            <span className="tag" style={{
                              marginLeft: 6, fontSize: 9, padding: "0 6px",
                              color: "var(--text-tertiary)", border: "1px solid var(--border)",
                              fontWeight: 400,
                            }}>第 {segNo} 段</span>
                          )}
                        </div>
                        {viewMode === "chapter" && (
                          <button
                            className="btn-icon"
                            onClick={() => deletePeriodInRead(ei, pi)}
                            title="删除这个时间段"
                            style={{ fontSize: 11, color: "var(--error)", width: 18, height: 18 }}
                          >&times;</button>
                        )}
                      </div>
                      <div className="flex flex-col gap-6" style={{ paddingLeft: 8, borderLeft: "2px solid var(--border)" }}>
                        {(per.events || []).map((ev, evi) => {
                          const key = `${ei}-${pi}-${evi}`;
                          const isInlineEditing = eventEdit?.key === key;
                          if (isInlineEditing) {
                            const ed = eventEdit!.draft;
                            const patch = (p: Partial<ChronicleEvent>) =>
                              setEventEdit({ key, draft: { ...ed, ...p } });
                            return (
                              <div key={evi} style={{
                                paddingLeft: 8,
                                padding: 8,
                                border: "1px solid var(--accent)",
                                borderRadius: 4,
                                background: "var(--bg-card)",
                              }}>
                                <div className="flex gap-4" style={{ marginBottom: 4 }}>
                                  <input className="input" placeholder="主体" value={ed.subject}
                                    onChange={e => patch({ subject: e.target.value })}
                                    style={{ flex: 1, fontSize: 12 }} />
                                  <input className="input" placeholder="分类" value={ed.category}
                                    onChange={e => patch({ category: e.target.value })}
                                    style={{ flex: 1, fontSize: 12 }} />
                                  <input className="input" placeholder="事件名" value={ed.name}
                                    onChange={e => patch({ name: e.target.value })}
                                    style={{ flex: 1, fontSize: 12 }} />
                                </div>
                                <textarea className="input" rows={3}
                                  placeholder="客观描述（2-5 句，不写对话/心理/场景细节）"
                                  value={ed.description}
                                  onChange={e => patch({ description: e.target.value })}
                                  style={{ marginBottom: 6, fontSize: 12 }} />
                                <div className="flex gap-4" style={{ marginBottom: 6 }}>
                                  <input className="input"
                                    placeholder='故事中时间（如「1954 年 3 月」）'
                                    value={ed.time_marker || ""}
                                    onChange={e => patch({ time_marker: e.target.value })}
                                    style={{ flex: 1, fontSize: 12 }} />
                                  <input className="input"
                                    placeholder='首次出现章节（如「第 12 章」）'
                                    value={ed.first_chapter || ""}
                                    onChange={e => patch({ first_chapter: e.target.value })}
                                    style={{ flex: 1, fontSize: 12 }} />
                                </div>
                                <div className="flex gap-6" style={{ justifyContent: "flex-end" }}>
                                  <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
                                          onClick={cancelEventEdit} disabled={eventEditSaving}>取消</button>
                                  <button className="btn-primary" style={{ fontSize: 11, padding: "3px 10px" }}
                                          onClick={saveEventEdit} disabled={eventEditSaving}>
                                    {eventEditSaving ? "保存中..." : "保存"}
                                  </button>
                                </div>
                              </div>
                            );
                          }
                          return (
                            <div key={evi} className="ref-event-row" style={{ paddingLeft: 8, position: "relative" }}>
                              <div style={{ fontSize: 12, lineHeight: 1.6, color: "var(--text-primary)" }}>
                                <span style={{ fontWeight: 600, color: "var(--accent)" }}>
                                  【{ev.subject}·{categoryLabel(ev.category)}·{ev.name}】
                                </span>
                                {/* Story-time tags: render each entry from
                                  * timeMarkers(ev) as its own chip so multi-
                                  * timestamp events ("1954 年春" 与 "倒计时
                                  * 6:00:00") cross-reference visually. */}
                                {timeMarkers(ev).map((t, ti) => (
                                  <span key={`t${ti}`} className="tag" style={{
                                    marginLeft: ti === 0 ? 6 : 3,
                                    fontSize: 10, padding: "1px 7px",
                                    color: "var(--gold)",
                                    background: "var(--bg-surface-2)",
                                    border: "1px solid var(--gold)",
                                  }} title="故事中时间">{t}</span>
                                ))}
                                {ev.first_chapter && (
                                  <span className="tag" style={{
                                    marginLeft: 4, fontSize: 10, padding: "1px 7px",
                                    color: "var(--jade)",
                                    background: "var(--bg-surface-2)",
                                    border: "1px solid var(--jade)",
                                  }} title="首次出现章节">{ev.first_chapter}</span>
                                )}
                                {" "}
                                <span style={{ color: "var(--text-secondary)" }}>{ev.description}</span>
                              </div>
                              {/* CRUD buttons — always visible so the
                                * per-event edit/delete affordance is
                                * discoverable. Hidden in 时间顺序 view
                                * because the displayed event no longer
                                * lives at the (ei, pi, evi) index — the
                                * read view is built from a synthetic
                                * regrouping. The user is told to switch
                                * back to 章节顺序 to edit/delete.
                                * position:static overrides the .ref-inline-edit
                                * absolute rule so the flex wrapper can lay
                                * the two buttons out side by side. */}
                              {viewMode === "chapter" && <div style={{
                                position: "absolute", top: 2, right: 0,
                                display: "flex", gap: 3,
                              }}>
                                <button
                                  className="ref-inline-edit"
                                  onClick={() => startEventEdit(ei, pi, evi, ev)}
                                  title="编辑这条事件"
                                  aria-label="编辑这条事件"
                                  style={{ position: "static" }}
                                >
                                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                                       stroke="currentColor" strokeWidth="2"
                                       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                                    <path d="M12 20h9" />
                                    <path d="M16.5 3.5a2.121 2.121 0 1 1 3 3L7 19l-4 1 1-4 12.5-12.5z" />
                                  </svg>
                                </button>
                                <button
                                  className="ref-inline-edit"
                                  onClick={() => deleteEventInRead(ei, pi, evi)}
                                  title="删除这条事件"
                                  aria-label="删除这条事件"
                                  style={{ position: "static", color: "var(--error)" }}
                                >
                                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                                       stroke="currentColor" strokeWidth="2"
                                       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                                    <path d="M3 6h18" />
                                    <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                                    <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                                  </svg>
                                </button>
                              </div>}
                            </div>
                          );
                        })}
                        {(per.events || []).length === 0 && (
                          <div className="text-xs text-muted" style={{ paddingLeft: 8 }}>（无事件）</div>
                        )}
                      </div>
                    </div>
                    );
                  })}
                </div>
              );
            })}
          </div>

          {legacy && (
            <details style={{ marginTop: 16 }}>
              <summary className="text-xs text-muted" style={{ cursor: "pointer" }}>查看旧版数据 (将被替换)</summary>
              <pre className="font-mono" style={{
                margin: "6px 0 0", padding: 8, fontSize: 11, lineHeight: 1.4,
                background: "var(--bg-surface)", borderRadius: 4,
                color: "var(--text-tertiary)", maxHeight: 220, overflow: "auto",
                whiteSpace: "pre-wrap", wordBreak: "break-all",
              }}>
                {JSON.stringify({ arcs: d.arcs, key_events: d.key_events, themes: d.themes }, null, 2)}
              </pre>
            </details>
          )}
        </>
      )}

    </div>
  );
}

/* ──────────────── Settings (设定) ──────────────── */

/** One chapter-tagged update on a setting (e.g. "第 7 章 · 穹顶嵌
 *  机枪、无人机"). The per-chunk extraction prompt fills this with
 *  every chapter that introduces, extends or revises the setting. */
export interface SettingUpdate {
  chapter: string;
  text: string;
}

export interface SettingItem {
  category: string;
  title: string;
  /** 简介 — a one-line summary of what the setting is. Populated from
   *  the extractor's `summary` field; falls back to the first update's
   *  text when the extractor omits it. */
  content: string;
  /** Per-chapter history of how the setting evolves in the text.
   *  Populated by the new per-chunk extractor. */
  updates?: SettingUpdate[];
  first_introduced_at?: string;
  first_chapter?: string;
  /** @deprecated Legacy [隐] field. New extractions don't write it; the
   *  editor no longer displays or edits the key. */
  hidden?: string;
}

export const SETTING_CATEGORIES: { key: string; label: string; color: string }[] = [
  { key: "power_system", label: "力量体系", color: "var(--accent)" },
  { key: "factions",     label: "势力组织", color: "var(--purple)" },
  { key: "geography",    label: "地理",     color: "var(--jade)" },
  { key: "social_rules", label: "社会规则", color: "var(--indigo)" },
  { key: "history",      label: "历史",     color: "var(--gold)" },
  { key: "worldview",    label: "世界观",   color: "var(--cyan)" },
  { key: "other",        label: "其他",     color: "var(--text-tertiary)" },
];

function settingLabel(cat: string): string {
  return SETTING_CATEGORIES.find(c => c.key === cat)?.label || cat;
}
function settingColor(cat: string): string {
  return SETTING_CATEGORIES.find(c => c.key === cat)?.color || "var(--text-tertiary)";
}

export function SettingsEditor({ data, onSave }: { data: SettingItem[] | null; onSave: (d: SettingItem[]) => Promise<void> | void }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<SettingItem[]>(data || []);
  const [saving, setSaving] = useState(false);
  const [filter, setFilter] = useState<string>("");

  const start = () => { setDraft(data || []); setEditing(true); };
  const cancel = () => { setDraft(data || []); setEditing(false); };
  const save = async () => {
    setSaving(true);
    try { await onSave(draft); setEditing(false); } finally { setSaving(false); }
  };

  const list = editing ? draft : (data || []);

  if (editing) {
    return (
      <div>
        <div className="text-xs text-muted" style={{ marginBottom: 10, lineHeight: 1.6 }}>
          编辑作品的世界观与设定。类别用于分组；尽量填上「首次出现的故事中时间」和「首次出现的章号」，方便定位。
        </div>
        <div className="flex flex-col gap-8" style={{ marginBottom: 12 }}>
          {draft.map((s, i) => (
            <div key={i} style={{ border: "1px solid var(--border)", borderRadius: 4, padding: 10 }}>
              <div className="flex gap-6 mb-6">
                <select
                  className="select"
                  value={s.category}
                  onChange={e => { const list = [...draft]; list[i] = { ...s, category: e.target.value }; setDraft(list); }}
                  style={{ width: 130 }}
                >
                  {SETTING_CATEGORIES.map(c => <option key={c.key} value={c.key}>{c.label}</option>)}
                </select>
                <input
                  className="input"
                  placeholder="设定名称（如「灵能力」）"
                  value={s.title}
                  onChange={e => { const list = [...draft]; list[i] = { ...s, title: e.target.value }; setDraft(list); }}
                  style={{ flex: 1 }}
                />
                <button
                  className="btn-icon"
                  onClick={() => { const list = [...draft]; list.splice(i, 1); setDraft(list); }}
                  style={{ fontSize: 14 }}
                >&times;</button>
              </div>
              <textarea
                className="input"
                rows={3}
                placeholder="客观描述（2-4 句）"
                value={s.content}
                onChange={e => { const list = [...draft]; list[i] = { ...s, content: e.target.value }; setDraft(list); }}
                style={{ marginBottom: 6 }}
              />
              <div className="flex gap-4">
                <input
                  className="input"
                  placeholder='首次出现的故事中时间（如「1954 年」）'
                  value={s.first_introduced_at || ""}
                  onChange={e => { const list = [...draft]; list[i] = { ...s, first_introduced_at: e.target.value }; setDraft(list); }}
                  style={{ flex: 1, fontSize: 12 }}
                />
                <input
                  className="input"
                  placeholder='首次出现的章号（如「第 3 章」）'
                  value={s.first_chapter || ""}
                  onChange={e => { const list = [...draft]; list[i] = { ...s, first_chapter: e.target.value }; setDraft(list); }}
                  style={{ flex: 1, fontSize: 12 }}
                />
              </div>
            </div>
          ))}
          <button
            className="btn"
            style={{ fontSize: 12, padding: "4px 10px", alignSelf: "flex-start" }}
            onClick={() => setDraft([...draft, { category: "worldview", title: "", content: "", first_introduced_at: "", first_chapter: "" }])}
          >+ 新增设定</button>
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button className="btn" onClick={cancel} disabled={saving}>取消</button>
          <button className="btn-primary" onClick={save} disabled={saving}>{saving ? "保存中..." : "保存"}</button>
        </div>
      </div>
    );
  }

  if (list.length === 0) {
    return (
      <div>
        <div className="text-xs text-muted text-center" style={{ padding: 16 }}>
          暂无设定。在分段提取大纲时勾选「使用 AI」会自动抽取设定，或点击「编辑」手动添加。
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <EditIconButton onClick={start} />
        </div>
      </div>
    );
  }

  // group by category, in canonical order
  const byCat: Record<string, { item: SettingItem; idx: number }[]> = {};
  list.forEach((it, idx) => {
    const cat = it.category || "other";
    (byCat[cat] ||= []).push({ item: it, idx });
  });
  const categories = SETTING_CATEGORIES.filter(c => byCat[c.key]?.length);
  const visible = filter ? categories.filter(c => c.key === filter) : categories;

  return (
    <div>
      <div className="flex gap-4" style={{ marginBottom: 10, flexWrap: "wrap" }}>
        <button
          className={!filter ? "btn-primary" : "btn"}
          style={{ fontSize: 11, padding: "3px 10px" }}
          onClick={() => setFilter("")}
        >全部 ({list.length})</button>
        {categories.map(c => (
          <button
            key={c.key}
            className={filter === c.key ? "btn-primary" : "btn"}
            style={{ fontSize: 11, padding: "3px 10px" }}
            onClick={() => setFilter(c.key === filter ? "" : c.key)}
          >{c.label} ({byCat[c.key].length})</button>
        ))}
      </div>

      <div className="flex flex-col gap-12">
        {visible.map(c => (
          <div key={c.key}>
            <div style={{
              fontSize: 12, fontWeight: 700,
              color: c.color, marginBottom: 6,
              paddingLeft: 6, borderLeft: `3px solid ${c.color}`,
            }}>
              {c.label}
            </div>
            <div className="flex flex-col gap-6" style={{ paddingLeft: 12 }}>
              {byCat[c.key].map(({ item, idx }) => (
                <div key={idx} style={{
                  padding: "8px 10px",
                  background: "var(--bg-surface)",
                  borderRadius: 4,
                  border: "1px solid var(--border)",
                }}>
                  <div className="flex items-center gap-6" style={{ marginBottom: 4, flexWrap: "wrap" }}>
                    <span style={{ fontWeight: 600, fontSize: 13, color: "var(--text-primary)" }}>
                      {item.title || "(未命名)"}
                    </span>
                    {item.first_introduced_at && (
                      <span className="tag" style={{
                        fontSize: 10, padding: "1px 6px",
                        color: "var(--gold)",
                        background: "var(--bg-surface-2)",
                        border: "1px solid var(--gold)",
                      }} title="故事中时间">{item.first_introduced_at}</span>
                    )}
                    {item.first_chapter && (
                      <span className="tag" style={{
                        fontSize: 10, padding: "1px 6px",
                        color: "var(--jade)",
                        background: "var(--bg-surface-2)",
                        border: "1px solid var(--jade)",
                      }} title="首次出现章节">{item.first_chapter}</span>
                    )}
                  </div>
                  {item.content && (
                    <div style={{ fontSize: 12, lineHeight: 1.55, color: "var(--text-secondary)" }}>
                      {item.content}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>

      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 12 }}>
        <EditIconButton onClick={start} />
      </div>
    </div>
  );
}

/* ──────────────── Rhythm v2 (合并节奏 + 叙事结构) ──────────────── */

export const CHAPTER_TYPE_VALUES = [
  "日常", "战斗", "高潮", "角色个人回",
  "主线事件", "支线事件", "伏笔铺垫", "收束",
  "转折", "其他",
] as const;
export type ChapterType = typeof CHAPTER_TYPE_VALUES[number];

const CHAPTER_TYPE_COLOR: Record<ChapterType, string> = {
  日常:       "var(--text-tertiary)",
  战斗:       "var(--accent)",
  高潮:       "var(--gold)",
  角色个人回: "var(--purple)",
  主线事件:   "var(--jade)",
  支线事件:   "var(--cyan)",
  伏笔铺垫:   "var(--indigo)",
  收束:       "#f472b6",
  转折:       "#e88c2e",
  其他:       "var(--text-tertiary)",
};

interface RhythmHook { position: "章首" | "段中" | "章末"; content: string }
interface RhythmChapterFeature {
  chapter: number;
  types: ChapterType[];
  info_density: number;
  summary: string;
  hooks: RhythmHook[];
}
export interface RhythmJson {
  coverage: { chapters: number; chars: number };
  opening_pattern: string;
  climax_positions: number[];
  shuangdian: { chapter: number; type: string; plot?: string }[];
  chapter_features: RhythmChapterFeature[];
  info_density_curve: number[];
  pacing_segments: { start: number; end: number; pacing: string; avg_info_density: number }[];
}

const _SHUANGDIAN_LABEL_RHYTHM: Record<string, string> = {
  face_slap: "打脸/反转",
  power_reveal: "实力展现",
  treasure_gain: "突破/晋级",
  mystery_reveal: "谜底揭开",
};

function _fmtChars(n: number): string {
  if (!n) return "0 字";
  if (n >= 10000) return `${(n / 10000).toFixed(1)} 万字`;
  return `${n.toLocaleString()} 字`;
}

function ChapterTypeChips({ types }: { types: ChapterType[] }) {
  return (
    <div className="flex gap-4" style={{ flexWrap: "wrap" }}>
      {types.map(t => (
        <span key={t} className="tag" style={{
          fontSize: 10, padding: "1px 7px",
          color: CHAPTER_TYPE_COLOR[t],
          background: "var(--bg-surface-2)",
          border: `1px solid ${CHAPTER_TYPE_COLOR[t]}`,
        }}>{t}</span>
      ))}
    </div>
  );
}

function ChapterTypePicker({ value, onChange }: { value: ChapterType[]; onChange: (next: ChapterType[]) => void }) {
  const toggle = (t: ChapterType) => {
    if (value.includes(t)) {
      const next = value.filter(x => x !== t);
      onChange(next.length ? next : ["其他"]);
    } else {
      onChange([...value, t]);
    }
  };
  return (
    <div className="flex gap-4" style={{ flexWrap: "wrap" }}>
      {CHAPTER_TYPE_VALUES.map(t => {
        const on = value.includes(t);
        return (
          <button
            key={t}
            className="btn-ghost"
            onClick={() => toggle(t)}
            style={{
              fontSize: 10, padding: "1px 7px", borderRadius: 3, cursor: "pointer",
              color: on ? "white" : CHAPTER_TYPE_COLOR[t],
              background: on ? CHAPTER_TYPE_COLOR[t] : "transparent",
              border: `1px solid ${CHAPTER_TYPE_COLOR[t]}`,
              fontWeight: on ? 600 : 400,
            }}
          >{t}</button>
        );
      })}
    </div>
  );
}

/**
 * Convert a legacy narrative_structure_json + rhythm_template_json pair
 * into the new RhythmJson shape so the read view can render them with
 * the same human-readable layout. The output is best-effort.
 */
function _legacyToRhythmJson(narr: any, rhythm: any): RhythmJson | null {
  if (!narr && !rhythm) return null;
  const beats: any[] = (narr?.chapter_beats) || [];
  const tensionCurve: number[] = (rhythm?.tension_curve) || [];
  const _BEAT_TO_TYPE: Record<string, ChapterType> = {
    intro: "其他",
    rising: "其他",
    climax: "高潮",
    falling: "其他",
    resolution: "收束",
  };
  const chapter_features: RhythmJson["chapter_features"] = beats.map((b, i) => {
    const idx = (typeof b?.chapter === "number") ? b.chapter : (i + 1);
    const fn = String(b?.function || "");
    return {
      chapter: idx,
      types: [_BEAT_TO_TYPE[fn] || "其他"],
      info_density: typeof b?.tension === "number" ? b.tension
                  : (typeof tensionCurve[idx - 1] === "number" ? tensionCurve[idx - 1] : 0),
      summary: "",
      hooks: [],
    };
  });
  if (chapter_features.length === 0 && tensionCurve.length > 0) {
    for (let i = 0; i < tensionCurve.length; i++) {
      chapter_features.push({
        chapter: i + 1, types: ["其他"],
        info_density: tensionCurve[i], summary: "", hooks: [],
      });
    }
  }
  return {
    coverage: { chapters: chapter_features.length || tensionCurve.length, chars: 0 },
    opening_pattern: String(narr?.opening_pattern || ""),
    climax_positions: Array.isArray(narr?.climax_positions)
      ? narr.climax_positions.filter((x: any) => typeof x === "number") : [],
    shuangdian: Array.isArray(narr?.shuangdian) ? narr.shuangdian : [],
    chapter_features,
    info_density_curve: tensionCurve.length > 0
      ? tensionCurve
      : chapter_features.map(cf => cf.info_density || 0),
    pacing_segments: (rhythm?.pacing_segments || []).map((p: any) => ({
      start: Number(p.start || 1),
      end: Number(p.end || 1),
      pacing: String(p.pacing || "medium"),
      avg_info_density: Number(p.avg_info_density ?? p.avg_tension ?? 0),
    })),
  };
}


/** Per-chapter rhythm chart: info-density bars coloured by chapter type,
 *  with 爽点 / 钩子 markers above each bar and 高潮章 columns highlighted.
 *  Hovering a bar reveals that chapter's summary / 爽点情节 / 钩子 in the
 *  detail strip below. */
function RhythmChart({ features, shuangdian, climax }: {
  features: RhythmChapterFeature[];
  shuangdian: { chapter: number; type: string; plot?: string }[];
  climax: number[];
}) {
  const [hover, setHover] = useState<number | null>(null);
  if (!features || features.length === 0) return null;

  const payoffsByCh = new Map<number, { type: string; plot?: string }[]>();
  for (const s of (shuangdian || [])) {
    const arr = payoffsByCh.get(s.chapter) || [];
    arr.push({ type: s.type, plot: s.plot });
    payoffsByCh.set(s.chapter, arr);
  }
  const climaxSet = new Set(climax || []);
  const n = features.length;
  const colW = n > 80 ? 8 : n > 40 ? 13 : n > 20 ? 20 : 30;
  const barAreaH = 92;
  const sel = hover != null ? features[hover] : null;
  const selPayoffs = sel ? (payoffsByCh.get(sel.chapter) || []) : [];

  return (
    <div style={{ marginBottom: 12 }}>
      <div className="flex items-center" style={{ gap: 14, flexWrap: "wrap", marginBottom: 6, fontSize: 10, color: "var(--text-tertiary)" }}>
        <span>柱高 = 信息密度</span>
        <span><span style={{ color: "var(--gold)" }}>●</span> 爽点</span>
        <span><span style={{ color: "var(--accent)" }}>●</span> 钩子</span>
        <span><span style={{
          display: "inline-block", width: 8, height: 8, borderRadius: 2,
          background: "var(--gold-subtle)", border: "1px solid var(--gold)",
          verticalAlign: "middle",
        }} /> 高潮章</span>
      </div>
      <div style={{
        overflowX: "auto", border: "1px solid var(--border)", borderRadius: 4,
        background: "var(--bg-surface-2)", padding: "8px 6px",
      }}>
        <div style={{ display: "flex", alignItems: "flex-end", gap: 1, minWidth: "100%" }}>
          {features.map((cf, i) => {
            const d = Math.max(0, Math.min(1, cf.info_density || 0));
            const hasPayoff = payoffsByCh.has(cf.chapter);
            const hasHook = (cf.hooks || []).length > 0;
            const isClimax = climaxSet.has(cf.chapter);
            const col = CHAPTER_TYPE_COLOR[cf.types?.[0] as ChapterType] || "var(--accent)";
            const active = hover === i;
            return (
              <div key={i}
                   onMouseEnter={() => setHover(i)}
                   style={{
                     width: colW, flexShrink: 0, cursor: "pointer",
                     display: "flex", flexDirection: "column", alignItems: "center",
                     background: active ? "var(--bg-surface-hover)"
                       : isClimax ? "var(--gold-subtle)" : "transparent",
                     borderRadius: 2,
                   }}>
                <div style={{ height: 9, display: "flex", gap: 1, alignItems: "center" }}>
                  {hasPayoff && <span style={{ width: 4, height: 4, borderRadius: "50%", background: "var(--gold)" }} />}
                  {hasHook && <span style={{ width: 4, height: 4, borderRadius: "50%", background: "var(--accent)" }} />}
                </div>
                <div style={{ height: barAreaH, display: "flex", alignItems: "flex-end", width: "100%", padding: "0 1px" }}>
                  <div style={{
                    width: "100%", height: `${Math.max(2, d * 100)}%`,
                    background: col, opacity: active ? 1 : 0.8,
                    borderRadius: "2px 2px 0 0",
                    outline: isClimax ? "1px solid var(--gold)" : "none",
                  }} />
                </div>
              </div>
            );
          })}
        </div>
      </div>
      <div className="flex justify-between" style={{ fontSize: 9, marginTop: 2, color: "var(--text-tertiary)" }}>
        <span>第 {features[0].chapter} 章</span>
        <span>第 {features[n - 1].chapter} 章</span>
      </div>
      {/* hover detail strip */}
      <div style={{
        marginTop: 6, padding: "6px 10px", minHeight: 50,
        border: "1px solid var(--border)", borderRadius: 4,
        background: "var(--bg-surface)", fontSize: 11, lineHeight: 1.6,
      }}>
        {sel ? (
          <>
            <div className="flex items-center" style={{ gap: 6, flexWrap: "wrap" }}>
              <span style={{ fontWeight: 700, color: "var(--text-primary)" }}>第 {sel.chapter} 章</span>
              <span style={{ color: "var(--accent)", fontFamily: "var(--font-mono)" }}>
                信息密度 {((sel.info_density || 0) * 100).toFixed(0)}%
              </span>
              {(sel.types || []).map((t, ti) => (
                <span key={ti} className="tag" style={{
                  fontSize: 9, padding: "0 5px",
                  color: CHAPTER_TYPE_COLOR[t] || "var(--text-tertiary)",
                  border: `1px solid ${CHAPTER_TYPE_COLOR[t] || "var(--border)"}`,
                }}>{t}</span>
              ))}
              {climaxSet.has(sel.chapter) && (
                <span className="tag" style={{
                  fontSize: 9, padding: "0 5px",
                  color: "var(--gold)", border: "1px solid var(--gold)",
                }}>高潮章</span>
              )}
            </div>
            {sel.summary && (
              <div className="text-muted" style={{ marginTop: 2 }}>{sel.summary}</div>
            )}
            {selPayoffs.map((p, pi) => (
              <div key={`p${pi}`} style={{ marginTop: 2 }}>
                <span className="tag" style={{
                  fontSize: 9, padding: "0 5px", marginRight: 5,
                  color: "var(--gold)", border: "1px solid var(--gold)",
                }}>爽点 · {_SHUANGDIAN_LABEL_RHYTHM[p.type] || p.type}</span>
                <span style={{ color: "var(--text-secondary)" }}>{p.plot || "（无具体情节）"}</span>
              </div>
            ))}
            {(sel.hooks || []).map((h, hi) => (
              <div key={`h${hi}`} style={{ marginTop: 2, color: "var(--text-secondary)" }}>
                <span style={{ color: "var(--accent)", marginRight: 5 }}>钩子 · {h.position}</span>
                {h.content}
              </div>
            ))}
          </>
        ) : (
          <span className="text-muted">悬停柱状图查看每章节奏详情（信息密度 / 爽点 / 钩子）</span>
        )}
      </div>
    </div>
  );
}

export function RhythmEditor({ data, legacyNarrative, legacyRhythm, onSave }: {
  data: RhythmJson | null;
  legacyNarrative?: any;            // narrative_structure_json (legacy)
  legacyRhythm?: any;               // rhythm_template_json (legacy)
  onSave: (d: RhythmJson) => Promise<void> | void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<RhythmJson | null>(data);
  const [saving, setSaving] = useState(false);

  const start = () => { setDraft(data); setEditing(true); };
  const cancel = () => { setDraft(data); setEditing(false); };
  const save = async () => {
    if (!draft) return;
    setSaving(true);
    try { await onSave(draft); setEditing(false); } finally { setSaving(false); }
  };

  // If the new rhythm_json is empty but legacy columns are populated,
  // upgrade them on the fly to the same shape so the user sees a
  // consistent human-readable view (no JSON, no "legacy" banner).
  const effective: RhythmJson | null = data || _legacyToRhythmJson(legacyNarrative, legacyRhythm);

  if (!effective) {
    return (
      <div className="text-xs text-muted text-center" style={{ padding: 14 }}>
        暂无节奏数据。请先在「剧情大纲」中分段提取。
      </div>
    );
  }

  if (editing && draft) {
    const updateCF = (i: number, patch: Partial<RhythmChapterFeature>) => {
      const list = [...draft.chapter_features];
      list[i] = { ...list[i], ...patch };
      setDraft({ ...draft, chapter_features: list });
    };
    return (
      <div>
        <div className="text-xs text-muted" style={{ marginBottom: 10, lineHeight: 1.6 }}>
          编辑每章的类型（多选）、信息密度（0-1）、摘要和钩子。高潮章节由系统计算，重新提取时刷新。
        </div>
        <div style={{ maxHeight: 460, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 4 }}>
          <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
            <thead style={{ position: "sticky", top: 0, background: "var(--bg-surface)", zIndex: 1 }}>
              <tr style={{ color: "var(--text-tertiary)" }}>
                <th style={{ textAlign: "left", padding: "6px 8px", width: 36 }}>章</th>
                <th style={{ textAlign: "left", padding: "6px 8px" }}>类型 (多选)</th>
                <th style={{ textAlign: "left", padding: "6px 8px", width: 90 }}>信息密度</th>
                <th style={{ textAlign: "left", padding: "6px 8px" }}>摘要</th>
              </tr>
            </thead>
            <tbody>
              {draft.chapter_features.map((cf, i) => (
                <tr key={cf.chapter} style={{ borderTop: "1px solid var(--border)" }}>
                  <td style={{ padding: "6px 8px", verticalAlign: "top" }}>{cf.chapter}</td>
                  <td style={{ padding: "6px 8px", verticalAlign: "top" }}>
                    <ChapterTypePicker value={cf.types} onChange={types => updateCF(i, { types })} />
                  </td>
                  <td style={{ padding: "6px 8px", verticalAlign: "top" }}>
                    <input type="range" min={0} max={1} step={0.01}
                      value={cf.info_density}
                      onChange={e => updateCF(i, { info_density: parseFloat(e.target.value) || 0 })}
                      style={{ width: 70, accentColor: "var(--accent)" }} />
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, marginLeft: 6, color: "var(--accent)" }}>
                      {cf.info_density.toFixed(2)}
                    </span>
                  </td>
                  <td style={{ padding: "6px 8px", verticalAlign: "top" }}>
                    <input className="input" value={cf.summary}
                      onChange={e => updateCF(i, { summary: e.target.value })}
                      style={{ fontSize: 11, padding: "2px 6px" }} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 8 }}>
          <button className="btn" onClick={cancel} disabled={saving}>取消</button>
          <button className="btn-primary" onClick={save} disabled={saving}>{saving ? "保存中..." : "保存"}</button>
        </div>
      </div>
    );
  }

  // ── Read view ──
  const cov = effective.coverage || { chapters: 0, chars: 0 };
  return (
    <div>
      <div style={{
        padding: "6px 10px", marginBottom: 10,
        background: "var(--accent-subtle)", border: "1px solid var(--accent)",
        borderRadius: 4, fontSize: 12,
        color: "var(--accent)", fontWeight: 600,
      }}>
        本数据截止到第 {cov.chapters} 章{cov.chars > 0 ? `（约 ${_fmtChars(cov.chars)}）` : ""}
      </div>

      <div className="flex gap-12" style={{ flexWrap: "wrap", fontSize: 12, marginBottom: 10 }}>
        <div>
          <span className="text-xs text-muted">高潮章：</span>
          {effective.climax_positions.length === 0 ? (
            <span className="text-xs text-muted">—</span>
          ) : (
            <span style={{ fontWeight: 600, color: "var(--gold)" }}>
              {effective.climax_positions.length} 章
            </span>
          )}
        </div>
        <div>
          <span className="text-xs text-muted">爽点 / 钩子：</span>
          <span style={{ fontWeight: 600 }}>
            {effective.shuangdian.length} / {effective.chapter_features.reduce((n, c) => n + (c.hooks?.length || 0), 0)}
          </span>
        </div>
      </div>

      {effective.chapter_features.length > 0 && (
        <>
          <div className="label" style={{ marginBottom: 6 }}>节奏图谱</div>
          <RhythmChart
            features={effective.chapter_features}
            shuangdian={effective.shuangdian}
            climax={effective.climax_positions}
          />
        </>
      )}

      <div className="label" style={{ marginBottom: 6 }}>章节特征</div>
      <div style={{ maxHeight: 340, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 4 }}>
        <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
          <thead style={{ position: "sticky", top: 0, background: "var(--bg-surface)", zIndex: 1 }}>
            <tr style={{ color: "var(--text-tertiary)" }}>
              <th style={{ textAlign: "left", padding: "6px 8px", width: 36 }}>章</th>
              <th style={{ textAlign: "left", padding: "6px 8px" }}>类型</th>
              <th style={{ textAlign: "left", padding: "6px 8px", width: 120 }}>信息密度</th>
              <th style={{ textAlign: "left", padding: "6px 8px" }}>摘要</th>
              <th style={{ textAlign: "left", padding: "6px 8px", width: 60 }}>钩子</th>
            </tr>
          </thead>
          <tbody>
            {effective.chapter_features.map(cf => (
              <RhythmChapterRow key={cf.chapter} cf={cf} />
            ))}
          </tbody>
        </table>
      </div>

      {effective.pacing_segments.length > 0 && (
        <>
          <div className="label" style={{ marginTop: 12, marginBottom: 6 }}>节奏分段</div>
          <div className="flex flex-col gap-4">
            {effective.pacing_segments.map((s, i) => (
              <div key={i} className="flex items-center gap-8" style={{
                padding: "5px 10px", background: "var(--bg-surface)", borderRadius: 4,
                borderLeft: `3px solid ${PACING_COLOR[s.pacing] || "var(--border)"}`,
              }}>
                <span style={{ fontSize: 11, fontWeight: 600, color: PACING_COLOR[s.pacing] || "var(--text-primary)" }}>
                  {PACING_LABEL[s.pacing] || s.pacing}
                </span>
                <span className="text-xs text-muted">第 {s.start} – {s.end} 章</span>
                <span className="text-xs text-muted" style={{ marginLeft: "auto" }}>
                  平均信息密度 {s.avg_info_density?.toFixed(2)}
                </span>
              </div>
            ))}
          </div>
        </>
      )}

      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 12 }}>
        <EditIconButton onClick={start} />
      </div>
    </div>
  );
}

function RhythmChapterRow({ cf }: { cf: RhythmChapterFeature }) {
  const [openHooks, setOpenHooks] = useState(false);
  const density = Math.max(0, Math.min(1, cf.info_density));
  return (
    <>
      <tr style={{ borderTop: "1px solid var(--border)" }}>
        <td style={{ padding: "6px 8px", verticalAlign: "top", fontWeight: 600 }}>{cf.chapter}</td>
        <td style={{ padding: "6px 8px", verticalAlign: "top" }}>
          <ChapterTypeChips types={cf.types} />
        </td>
        <td style={{ padding: "6px 8px", verticalAlign: "middle" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <div style={{ flex: 1, height: 6, background: "var(--bg-surface-2)", borderRadius: 3, overflow: "hidden" }}>
              <div style={{ height: "100%", width: `${density * 100}%`, background: "var(--accent)", borderRadius: 3 }} />
            </div>
            <span style={{ fontFamily: "var(--font-mono)", fontSize: 10, minWidth: 30, textAlign: "right", color: "var(--accent)" }}>
              {density.toFixed(2)}
            </span>
          </div>
        </td>
        <td style={{ padding: "6px 8px", verticalAlign: "top", color: "var(--text-secondary)" }}>
          {cf.summary || "—"}
        </td>
        <td style={{ padding: "6px 8px", verticalAlign: "top" }}>
          {(cf.hooks || []).length === 0 ? (
            <span className="text-xs text-muted">—</span>
          ) : (
            <button
              className="btn-ghost"
              onClick={() => setOpenHooks(!openHooks)}
              style={{ fontSize: 10, padding: "1px 6px", color: "var(--gold)" }}
            >
              {cf.hooks.length} {openHooks ? "▲" : "▼"}
            </button>
          )}
        </td>
      </tr>
      {openHooks && (cf.hooks || []).length > 0 && (
        <tr>
          <td colSpan={5} style={{ padding: "0 8px 8px 8px", background: "var(--bg-surface)" }}>
            <ul style={{ margin: 0, paddingLeft: 20, fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.6 }}>
              {cf.hooks.map((h, i) => (
                <li key={i}>
                  <span className="tag" style={{
                    fontSize: 9, padding: "0px 5px", marginRight: 6,
                    background: "var(--bg-surface-2)", color: "var(--gold)",
                    border: "1px solid var(--gold)",
                  }}>{h.position}</span>
                  {h.content}
                </li>
              ))}
            </ul>
          </td>
        </tr>
      )}
    </>
  );
}
