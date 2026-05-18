import React, { useCallback, useState } from "react";
import { apiGet, apiPost } from "../../api/client";

/* ════════════════════════════════════════════════════════════
 * Human-readable editors for reference-work analysis fields.
 * Each editor displays the data in a structured layout and lets
 * the user edit individual values. On save, calls onSave(data).
 * ════════════════════════════════════════════════════════════ */

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
  refId, promptKey, segmentIndex, label, chunked = true,
}: {
  refId: string;
  promptKey: "reference.characters" | "reference.settings" | "reference.outline";
  segmentIndex: number | null;
  label: string;
  /** Allow the multi-chunk endpoint. Defaults to true; pass false to
   * always render a single (possibly truncated) prompt — useful for
   * characters / settings, where each call needs the full segment text. */
  chunked?: boolean;
}) {
  const [open, setOpen] = useState(false);
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
        setChunks(r.chunks || []);
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
  }, [refId, promptKey, segmentIndex, chunked]);

  const toggle = async () => {
    const willOpen = !open;
    setOpen(willOpen);
    if (willOpen && chunks.length === 0 && !error) await loadPrompt();
  };

  const current = chunks[activeChunk];
  const total = chunks.length;

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
        {total > 1 && (
          <button className="btn-ghost" onClick={copyAll}
                  disabled={loading}
                  style={{ padding: "2px 8px", fontSize: 10 }}
                  title="复制全部 N 段（带分段分隔符）">
            复制全部 {total} 段
          </button>
        )}
      </div>
      {open && total > 1 && (
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
              {current && total > 1 && (
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
  avg_sentence_length?: number;
  dialogue_ratio?: number;
  description_density?: number;
  rhetoric_frequency?: number;
  vocab_complexity?: number;
  pacing_profile?: { fast?: number; medium?: number; slow?: number };
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
        </div>
        <Slider01 label="对话占比" hint="对话内容 / 全文" value={d.dialogue_ratio} onChange={v => setDraft({ ...draft, dialogue_ratio: v })} />
        <Slider01 label="描写密度" hint="形容/状语词比例" value={d.description_density} onChange={v => setDraft({ ...draft, description_density: v })} />
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

  return (
    <div>
      <MetricRow label="平均句长" value={d.avg_sentence_length} unit="字" max={60} hint="越长越书面化" />
      <MetricRow label="对话占比" value={d.dialogue_ratio} max={1} hint="对话内容 / 全文" />
      <MetricRow label="描写密度" value={d.description_density} max={1} hint="形容/状语词比例" />
      <MetricRow label="修辞频率" value={d.rhetoric_frequency} max={20} hint="每千字使用次数" />
      <MetricRow label="词汇丰富度" value={d.vocab_complexity} max={1} hint="归一化 TTR" />
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
      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 12 }}>
        <button className="btn-ghost" style={{ fontSize: 11, color: "var(--text-tertiary)" }} onClick={start}>编辑</button>
      </div>
    </div>
  );
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
        <button className="btn-ghost" style={{ fontSize: 11, color: "var(--text-tertiary)" }} onClick={start}>编辑</button>
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

interface CharacterItem {
  name: string;
  mentions?: number;
  intro?: string;
  speech_samples?: string[];
  appearance_chapters?: number;
  appearance_word_count?: number;
  first_seen_at?: string; // 首次出场的时间锚点
  role_tag?: CharacterRoleTag;
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
        <button className="btn-ghost" style={{ fontSize: 11, color: "var(--text-tertiary)" }} onClick={start}>编辑</button>
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

const CHRONICLE_INSTRUCTIONS = `编年史是「世界内史学家」的客观记录：
1. 视角：第三人称客观史学家，只记录「发生了什么」——不写心理、不写对话原文、不写场景细节。
2. 颗粒度：章节弧标题级别。「主角拜师」是一个事件；「主角拿茶杯/喝茶/放下」就太细，要合并成一个。每章 1-3 条为佳。
3. 时间标签有两个：
   • 故事中时间（time_marker）—— 故事世界的时钟，如「1954 年 3 月」「春末」。没有就留空。
   • 首次出现章节（first_chapter）—— 原文里事件首次描写的章号，如「第 12 章」。两个都尽量填。
4. 删除：阅读视图每条事件右上角 × 直接删除；时间段或大段的 × 在它们各自的标题行。
5. AI 提取分两步：先逐章抽事件（按文本顺序），再用「整理时间线」prompt 让 LLM 按故事时间重排（处理倒叙/插叙）。`;

/* ─── Chronicle 全时间线总结 panel ──────────────────────────────
 * Lives at the top of the chronicle display section. Two-phase UX:
 *
 *   Phase A (try internal AI):
 *     Click "运行全时间线总结" → POST /chronicle/summarize.
 *     On success: shows the AI-reorganized chronicle preview +
 *     "确认替换 / 取消" actions.
 *     On failure: response carries the rendered prompt — auto-flips
 *     to phase B with that prompt pre-loaded.
 *
 *   Phase B (manual via web LLM):
 *     "复制总结 prompt" + paste-response textarea + "解析并替换".
 *     The pasted JSON is parsed via the same normalizePastedChronicle
 *     used by the chronicle's 粘贴 JSON 大纲 button, so any of the
 *     supported shapes (full PlotOutline, {epochs:…}, {events:…}, …)
 *     work without hand-editing.
 *
 * 总结 only operates on the existing chronicle's events — it does NOT
 * extract new events from chapter text. That's the extraction step. */
function ChronicleSummarizePanel({ refId, data, onSave }: {
  refId: string;
  data: PlotOutline;
  onSave: (d: PlotOutline) => Promise<void> | void;
}) {
  type Phase = "idle" | "ai-running" | "ai-success" | "manual";
  const [phase, setPhase] = useState<Phase>("idle");
  const [open, setOpen] = useState(false);
  const [aiResult, setAiResult] = useState<PlotOutline | null>(null);
  const [aiError, setAiError] = useState("");
  const [manualPrompt, setManualPrompt] = useState("");
  const [pasteRaw, setPasteRaw] = useState("");
  const [pasteError, setPasteError] = useState("");
  const [pasteParsed, setPasteParsed] = useState<PlotOutline | null>(null);
  const [saving, setSaving] = useState(false);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "fail">("idle");

  // Quick event count so the user knows how much will be summarized.
  const eventCount = (data.epochs || []).reduce(
    (n, ep) => n + (ep.periods || []).reduce(
      (m, p) => m + (p.events?.length || 0), 0,
    ), 0,
  );

  const runInternalAI = async () => {
    setPhase("ai-running");
    setAiError("");
    setAiResult(null);
    try {
      const r = await apiPost<{
        ok: boolean;
        chronicle?: PlotOutline;
        rendered_prompt?: string;
        error?: string;
        event_count?: number;
      }>(`/api/references/works/${refId}/chronicle/summarize`, {},
        { timeoutMs: 600_000 });
      if (r.ok && r.chronicle) {
        setAiResult(r.chronicle);
        setPhase("ai-success");
      } else {
        setAiError(r.error || "AI 总结失败");
        if (r.rendered_prompt) setManualPrompt(r.rendered_prompt);
        setPhase("manual");
      }
    } catch (e: any) {
      setAiError(e?.message || "AI 总结失败");
      setPhase("manual");
      // Try to fetch the rendered prompt separately so the user can copy.
      try {
        const flat: any[] = [];
        for (const ep of (data.epochs || [])) {
          for (const per of (ep.periods || [])) {
            for (const ev of (per.events || [])) flat.push(ev);
          }
        }
        const r = await apiPost<{ rendered: string }>(
          `/api/references/prompts/reference.outline_summary/render`,
          { ref_id: refId, segment_index: 0, events: flat },
        );
        setManualPrompt(r.rendered || "");
      } catch { /* best-effort */ }
    }
  };

  const confirmAiResult = async () => {
    if (!aiResult) return;
    setSaving(true);
    try {
      await onSave(aiResult);
      setPhase("idle");
      setAiResult(null);
      setOpen(false);
    } finally { setSaving(false); }
  };

  const copyPrompt = async () => {
    if (!manualPrompt) {
      // Lazy-fetch the prompt the first time the user clicks copy.
      try {
        const flat: any[] = [];
        for (const ep of (data.epochs || [])) {
          for (const per of (ep.periods || [])) {
            for (const ev of (per.events || [])) flat.push(ev);
          }
        }
        const r = await apiPost<{ rendered: string }>(
          `/api/references/prompts/reference.outline_summary/render`,
          { ref_id: refId, segment_index: 0, events: flat },
        );
        setManualPrompt(r.rendered || "");
        await navigator.clipboard.writeText(r.rendered || "");
        setCopyState("copied");
        setTimeout(() => setCopyState("idle"), 1500);
        return;
      } catch {
        setCopyState("fail");
        setTimeout(() => setCopyState("idle"), 1500);
        return;
      }
    }
    try {
      await navigator.clipboard.writeText(manualPrompt);
      setCopyState("copied");
      setTimeout(() => setCopyState("idle"), 1500);
    } catch {
      setCopyState("fail");
      setTimeout(() => setCopyState("idle"), 1500);
    }
  };

  const parsePasted = () => {
    setPasteError("");
    setPasteParsed(null);
    const raw = pasteRaw.trim();
    if (!raw) { setPasteError("请先粘贴 LLM 返回的 JSON"); return; }
    const stripped = stripJsonFences(raw);
    let parsed: any;
    try { parsed = JSON.parse(stripped); }
    catch (e: any) { setPasteError(`JSON 解析失败：${e?.message || e}`); return; }
    const normalized = normalizePastedChronicle(parsed);
    if (!normalized || !normalized.epochs || normalized.epochs.length === 0) {
      setPasteError("内容不是合法的编年史 JSON。预期形如 {epochs:[…]} 或 {events:[…]}。");
      return;
    }
    setPasteParsed(normalized);
  };

  const confirmPasted = async () => {
    if (!pasteParsed) return;
    setSaving(true);
    try {
      await onSave(pasteParsed);
      setPhase("idle");
      setPasteRaw("");
      setPasteParsed(null);
      setOpen(false);
    } finally { setSaving(false); }
  };

  // Collapsed header — keeps the chronicle uncluttered until the user
  // explicitly invokes the summary.
  return (
    <div style={{
      marginBottom: 10, border: "1px solid var(--accent)", borderRadius: 4,
      background: "var(--bg-surface)",
    }}>
      <div className="flex items-center" style={{
        padding: "6px 10px", gap: 8, flexWrap: "wrap",
      }}>
        <button className="btn-ghost" onClick={() => setOpen(o => !o)}
                style={{
                  padding: "2px 4px", fontSize: 12, fontWeight: 600,
                  color: "var(--accent)", borderRadius: 0,
                }}>
          <span style={{
            transition: "transform 0.15s",
            transform: open ? "rotate(180deg)" : "none",
            display: "inline-block", marginRight: 4,
          }}>&#x25BC;</span>
          全时间线总结（按故事时间重排，处理倒叙/插叙）
        </button>
        <span className="text-xs text-muted">{eventCount} 个待整理事件</span>
        <div style={{ flex: 1 }} />
        <button className="btn-primary" onClick={runInternalAI}
                disabled={phase === "ai-running" || saving || eventCount === 0}
                style={{ fontSize: 11, padding: "3px 10px" }}>
          {phase === "ai-running" ? "运行中…" : "运行内置 AI 总结"}
        </button>
        <button className="btn" onClick={copyPrompt}
                disabled={saving || eventCount === 0}
                style={{ fontSize: 11, padding: "3px 10px" }}
                title="拷贝 prompt 到剪贴板，使用网页版 LLM 跑">
          {copyState === "copied" ? "已复制" : copyState === "fail" ? "复制失败" : "复制 prompt"}
        </button>
      </div>

      {open && (
        <div style={{ padding: "0 10px 10px" }}>
          {/* AI success → show preview + confirm */}
          {phase === "ai-success" && aiResult && (
            <div>
              <div className="text-xs text-muted" style={{ marginBottom: 6, lineHeight: 1.55 }}>
                内置 AI 已整理出 {(aiResult.epochs || []).length} 大段。确认后会**替换**当前编年史。
              </div>
              <div style={{
                maxHeight: 300, overflow: "auto", padding: 8,
                border: "1px solid var(--border)", borderRadius: 3,
                background: "var(--bg-card)", marginBottom: 8,
              }}>
                {(aiResult.epochs || []).map((ep, ei) => (
                  <div key={ei} style={{ marginBottom: 8 }}>
                    <div style={{ fontWeight: 600, fontSize: 12, color: "var(--accent)" }}>{ep.title}</div>
                    {(ep.periods || []).map((per, pi) => (
                      <div key={pi} style={{ marginLeft: 10, marginTop: 4 }}>
                        <div className="text-xs" style={{ color: "var(--gold)" }}>{per.time || "(未填时间)"}</div>
                        <div style={{ marginLeft: 8, fontSize: 11, color: "var(--text-secondary)" }}>
                          {(per.events || []).length} 个事件
                        </div>
                      </div>
                    ))}
                  </div>
                ))}
              </div>
              <div className="flex gap-6" style={{ justifyContent: "flex-end" }}>
                <button className="btn" onClick={() => { setPhase("idle"); setAiResult(null); }}
                        disabled={saving} style={{ fontSize: 11 }}>取消</button>
                <button className="btn-primary" onClick={confirmAiResult}
                        disabled={saving} style={{ fontSize: 11 }}>
                  {saving ? "替换中…" : "确认替换编年史"}
                </button>
              </div>
            </div>
          )}

          {/* AI failed OR user wants to paste manually */}
          {(phase === "manual" || phase === "idle") && (
            <div>
              {aiError && (
                <div className="text-xs" style={{
                  color: "var(--error)", marginBottom: 6, lineHeight: 1.55,
                  padding: "4px 8px", background: "var(--bg-card)",
                  border: "1px solid var(--error)", borderRadius: 3,
                }}>
                  ⚠ 内置 AI 失败：{aiError}。请点上方「复制 prompt」到 ChatGPT / Claude.ai 跑，再把结果粘到下方。
                </div>
              )}
              <div className="text-xs text-muted" style={{ marginBottom: 6, lineHeight: 1.55 }}>
                在网页版 LLM 跑完总结 prompt 后，把它返回的 JSON 粘到下方：
              </div>
              <textarea className="input font-mono" rows={5}
                        value={pasteRaw}
                        onChange={e => setPasteRaw(e.target.value)}
                        placeholder='{"logline":"…","epochs":[{"title":"…","periods":[…]}]}'
                        style={{
                          fontSize: 11, lineHeight: 1.5, resize: "vertical",
                          background: "var(--bg-card)", marginBottom: 6,
                        }} />
              <div className="flex items-center gap-6" style={{ flexWrap: "wrap" }}>
                <button className="btn" onClick={parsePasted}
                        disabled={saving || !pasteRaw.trim()}
                        style={{ fontSize: 11, padding: "3px 10px" }}>解析</button>
                <button className="btn-primary" onClick={confirmPasted}
                        disabled={saving || !pasteParsed}
                        style={{ fontSize: 11, padding: "3px 10px" }}
                        title="解析并替换当前编年史">
                  {saving ? "替换中…" : "确认替换编年史"}
                </button>
                {pasteError && (
                  <span className="text-xs" style={{ color: "var(--error)" }}>{pasteError}</span>
                )}
                {pasteParsed && (
                  <span className="text-xs" style={{ color: "var(--jade)" }}>
                    解析成功：{(pasteParsed.epochs || []).length} 大段 ·
                    {" "}{(pasteParsed.epochs || []).reduce((n, e) =>
                      n + (e.periods || []).reduce((m, p) => m + (p.events?.length || 0), 0), 0)} 事件
                  </span>
                )}
              </div>
            </div>
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
}: {
  data: PlotOutline | null;
  onSave: (d: PlotOutline) => Promise<void> | void;
  onExtract?: () => void;
  extracting?: boolean;
  /** Passed through so the embedded "全时间线总结" controls can hit
   *  the work-scoped summarize endpoint. Optional — when missing,
   *  the summary panel hides itself. */
  refId?: string;
}) {
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
    if (typeof window !== "undefined" &&
        !window.confirm("确认删除这条事件？此操作会立即保存到数据库。")) return;
    const base = cloneData();
    const epochs = base.epochs || [];
    if (!epochs[ei]?.periods[pi]?.events) return;
    epochs[ei].periods[pi].events.splice(evi, 1);
    await onSave(base);
  };
  const deletePeriodInRead = async (ei: number, pi: number) => {
    if (typeof window !== "undefined" &&
        !window.confirm("确认删除这个时间段及其下所有事件？此操作会立即保存。")) return;
    const base = cloneData();
    const epochs = base.epochs || [];
    if (!epochs[ei]?.periods) return;
    epochs[ei].periods.splice(pi, 1);
    await onSave(base);
  };
  const deleteEpochInRead = async (ei: number) => {
    if (typeof window !== "undefined" &&
        !window.confirm("确认删除这个大段及其下所有时间段/事件？此操作会立即保存。")) return;
    const base = cloneData();
    base.epochs = (base.epochs || []).filter((_, i) => i !== ei);
    await onSave(base);
  };
  // Instructions disclosure — collapsible because chronicle conventions
  // can be unfamiliar to first-time users, but veteran users won't want
  // to see the wall of text on every load.
  const [showInstructions, setShowInstructions] = useState(false);


  // Read-only chronicle view. With the full-edit / quick-add / paste
  // entry points removed (per user request), the only mutations from
  // this editor are per-event inline pencil-edit and the per-level
  // delete buttons — both write straight through onSave.
  const d = data || {};
  const legacy = isLegacy(d);

  const epochs = d.epochs || [];
  const hasContent = (epochs.length > 0 && epochs.some(e => (e.periods || []).length > 0)) || legacy || d.logline;

  return (
    <div>
      {/* 全时间线总结 controls — show only when refId is supplied AND
        * the chronicle has events (i.e. extraction step produced
        * something). On internal-AI failure, the panel reveals the
        * outline_summary prompt for the user to copy to a web LLM,
        * and accepts the parsed-back chronicle JSON. */}
      {refId && hasContent && (
        <ChronicleSummarizePanel refId={refId} data={d} onSave={onSave} />
      )}

      {/* Chronicle usage instructions — collapsed by default to keep
        * the read view dense, but always one click away. */}
      <div style={{
        marginBottom: 10, border: "1px dashed var(--border)", borderRadius: 4,
      }}>
        <button className="btn-ghost w-full" onClick={() => setShowInstructions(s => !s)}
                style={{
                  justifyContent: "space-between", padding: "6px 10px",
                  fontSize: 11, fontWeight: 600, color: "var(--text-secondary)",
                  borderRadius: 0,
                }}>
          <span>编年史格式使用说明</span>
          <span className="text-xs text-muted" style={{
            transition: "transform 0.15s",
            transform: showInstructions ? "rotate(180deg)" : "none",
            display: "inline-block",
          }}>&#x25BC;</span>
        </button>
        {showInstructions && (
          <pre style={{
            margin: 0, padding: "8px 12px",
            background: "var(--bg-surface)",
            fontSize: 11, lineHeight: 1.65,
            color: "var(--text-secondary)",
            whiteSpace: "pre-wrap", wordBreak: "break-word",
            fontFamily: "inherit",
          }}>{CHRONICLE_INSTRUCTIONS}</pre>
        )}
      </div>

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
                      <button
                        className="btn-icon"
                        onClick={() => deleteEpochInRead(ei)}
                        title="删除整个大段"
                        style={{ fontSize: 12, color: "var(--error)", width: 22, height: 22 }}
                      >&times;</button>
                    </div>
                  )}
                  {isOpen && (ep.periods || []).map((per, pi) => (
                    <div key={pi} style={{ marginBottom: 12, paddingLeft: ep.title ? 8 : 0 }}>
                      <div className="flex items-center" style={{ marginBottom: 6, gap: 4 }}>
                        <div style={{ flex: 1, fontWeight: 600, fontSize: 13, color: "var(--accent)" }}>
                          {per.time || "(未填写时间)"}
                        </div>
                        <button
                          className="btn-icon"
                          onClick={() => deletePeriodInRead(ei, pi)}
                          title="删除这个时间段"
                          style={{ fontSize: 11, color: "var(--error)", width: 18, height: 18 }}
                        >&times;</button>
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
                                  【{ev.subject}·{ev.category}·{ev.name}】
                                </span>
                                {/* Two time tags rendered side-by-side: story-time
                                  * (in-fiction clock) and first_chapter (where in
                                  * the real text this event is first described).
                                  * Both are visually distinct so users can tell
                                  * them apart at a glance. */}
                                {ev.time_marker && (
                                  <span className="tag" style={{
                                    marginLeft: 6, fontSize: 10, padding: "1px 7px",
                                    color: "var(--gold)",
                                    background: "var(--bg-surface-2)",
                                    border: "1px solid var(--gold)",
                                  }} title="故事中时间">⏱ {ev.time_marker}</span>
                                )}
                                {ev.first_chapter && (
                                  <span className="tag" style={{
                                    marginLeft: 4, fontSize: 10, padding: "1px 7px",
                                    color: "var(--jade)",
                                    background: "var(--bg-surface-2)",
                                    border: "1px solid var(--jade)",
                                  }} title="首次出现章节">📖 {ev.first_chapter}</span>
                                )}
                                {" "}
                                <span style={{ color: "var(--text-secondary)" }}>{ev.description}</span>
                              </div>
                              <div style={{
                                position: "absolute", top: 0, right: 0,
                                display: "flex", gap: 2,
                              }}>
                                <button
                                  className="ref-inline-edit"
                                  onClick={() => startEventEdit(ei, pi, evi, ev)}
                                  title="编辑这条事件"
                                  aria-label="编辑这条事件"
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
                                  style={{ color: "var(--error)" }}
                                >
                                  <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
                                       stroke="currentColor" strokeWidth="2"
                                       strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                                    <path d="M3 6h18" />
                                    <path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2" />
                                    <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
                                  </svg>
                                </button>
                              </div>
                            </div>
                          );
                        })}
                        {(per.events || []).length === 0 && (
                          <div className="text-xs text-muted" style={{ paddingLeft: 8 }}>（无事件）</div>
                        )}
                      </div>
                    </div>
                  ))}
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

export interface SettingItem {
  category: string;
  title: string;
  content: string;
  first_introduced_at?: string; // 首次出现的故事中时间
  first_chapter?: string;       // 首次出现的章号
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
  { key: "hard_rules",   label: "硬规则",   color: "#f472b6" },
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
          <button className="btn-ghost" style={{ fontSize: 11, color: "var(--text-tertiary)" }} onClick={start}>编辑</button>
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
                      }} title="故事中时间">⏱ {item.first_introduced_at}</span>
                    )}
                    {item.first_chapter && (
                      <span className="tag" style={{
                        fontSize: 10, padding: "1px 6px",
                        color: "var(--jade)",
                        background: "var(--bg-surface-2)",
                        border: "1px solid var(--jade)",
                      }} title="首次出现章节">📖 {item.first_chapter}</span>
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
        <button className="btn-ghost" style={{ fontSize: 11, color: "var(--text-tertiary)" }} onClick={start}>编辑</button>
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
  shuangdian: { chapter: number; type: string }[];
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


function InfoDensitySparkline({ data }: { data: number[] }) {
  if (!data || data.length === 0) return null;
  const w = 600, h = 50;
  const step = data.length > 1 ? w / (data.length - 1) : w;
  const pts = data.map((v, i) => `${i * step},${h - Math.max(0, Math.min(1, v)) * h}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" style={{ width: "100%", height: 50, background: "var(--bg-surface-2)", borderRadius: 4 }}>
      <polyline points={pts} fill="none" stroke="var(--accent)" strokeWidth={1.5} />
    </svg>
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
          编辑每章的类型（多选）、信息密度（0-1）、摘要和钩子。开篇模式、高潮章节由系统计算，重新提取时刷新。
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
        本数据截止到第 {cov.chapters} 章（约 {_fmtChars(cov.chars)}）
      </div>

      <div className="flex gap-12" style={{ flexWrap: "wrap", fontSize: 12, marginBottom: 10 }}>
        <div>
          <span className="text-xs text-muted">开篇模式：</span>
          <span style={{ fontWeight: 600 }}>{OPENING_LABELS[effective.opening_pattern] || effective.opening_pattern || "—"}</span>
        </div>
        <div>
          <span className="text-xs text-muted">高潮章：</span>
          {effective.climax_positions.length === 0 ? (
            <span className="text-xs text-muted">—</span>
          ) : (
            <div className="flex gap-4" style={{ display: "inline-flex", flexWrap: "wrap" }}>
              {effective.climax_positions.map(c => (
                <span key={c} className="tag accent" style={{ fontSize: 10, padding: "1px 6px" }}>第{c}章</span>
              ))}
            </div>
          )}
        </div>
        {effective.shuangdian.length > 0 && (
          <div>
            <span className="text-xs text-muted">爽点：</span>
            <div className="flex gap-4" style={{ display: "inline-flex", flexWrap: "wrap" }}>
              {effective.shuangdian.map((s, i) => (
                <span key={i} className="tag" style={{ fontSize: 10, padding: "1px 6px" }}>
                  第{s.chapter}章 · {_SHUANGDIAN_LABEL_RHYTHM[s.type] || s.type}
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      {effective.info_density_curve.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <div className="text-xs text-muted" style={{ marginBottom: 4 }}>
            信息密度曲线（共 {effective.info_density_curve.length} 章）
          </div>
          <InfoDensitySparkline data={effective.info_density_curve} />
        </div>
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
        <button className="btn-ghost" style={{ fontSize: 11, color: "var(--text-tertiary)" }} onClick={start}>编辑</button>
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
