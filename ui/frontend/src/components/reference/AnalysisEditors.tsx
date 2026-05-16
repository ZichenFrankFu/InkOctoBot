import React, { useState } from "react";

/* ════════════════════════════════════════════════════════════
 * Human-readable editors for reference-work analysis fields.
 * Each editor displays the data in a structured layout and lets
 * the user edit individual values. On save, calls onSave(data).
 * ════════════════════════════════════════════════════════════ */

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

interface CharacterItem {
  name: string;
  mentions?: number;
  intro?: string;
  speech_samples?: string[];
  appearance_chapters?: number;
  appearance_word_count?: number;
  first_seen_at?: string; // 首次出场的时间锚点
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
                style={{ marginBottom: 6 }}
              />
              <input
                className="input"
                placeholder='首次出场时间锚点 (如 "1954 年" / "第 3 章" / "约 5 万字处")'
                value={c.first_seen_at || ""}
                onChange={e => {
                  const list = [...draft]; list[i] = { ...c, first_seen_at: e.target.value }; setDraft(list);
                }}
                style={{ fontSize: 12 }}
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
    <CharactersReadOnlyList list={list} onEdit={start} />
  );
}

function CharactersReadOnlyList({ list, onEdit }: { list: CharacterItem[]; onEdit: () => void }) {
  const [showAll, setShowAll] = useState(false);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});
  const INITIAL = 5;
  const visible = showAll ? list : list.slice(0, INITIAL);

  return (
    <div>
      <div className="flex flex-col gap-6" style={{ marginBottom: 12 }}>
        {list.length === 0 && <div className="text-xs text-muted text-center" style={{ padding: 8 }}>暂无角色</div>}
        {visible.map((c, i) => {
          const isOpen = !!expanded[i];
          const hasSpeech = (c.speech_samples || []).length > 0;
          return (
            <div key={i} style={{ border: "1px solid var(--border)", borderRadius: 4 }}>
              <button
                className="btn-ghost w-full"
                style={{ justifyContent: "space-between", padding: "8px 10px", borderRadius: 0, fontWeight: 500, textAlign: "left" }}
                onClick={() => setExpanded(prev => ({ ...prev, [i]: !prev[i] }))}
                disabled={!hasSpeech && !c.intro}
              >
                <span style={{ flex: 1, minWidth: 0 }}>
                  <span className="flex items-center gap-8" style={{ marginBottom: c.intro ? 4 : 0, flexWrap: "wrap" }}>
                    <span style={{ fontWeight: 600 }}>{c.name || "(未命名)"}</span>
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
 *   event = 【主体·分类·事件名】描述。  [隐] (可选)
 *
 * Backwards-compat: also accepts older arcs/key_events shape and renders a hint
 * to re-extract in chronicle format.
 */

export interface ChronicleEvent {
  subject: string;     // 主体: 人物/组织/概念
  category: string;    // 分类
  name: string;        // 事件名
  description: string; // 客观描述,2-5 句
  hidden?: string;     // [隐] 当时无人知晓但作为史学家知道的真相
  time_marker?: string; // 事件在文本中的时间锚点 (date | "第 N 章" | "约 M 万字处")
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
  return { subject: "", category: "", name: "", description: "", hidden: "", time_marker: "" };
}

export function PlotOutlineEditor({
  data,
  onSave,
  onExtract,
  extracting,
}: {
  data: PlotOutline | null;
  onSave: (d: PlotOutline) => Promise<void> | void;
  onExtract?: () => void;
  extracting?: boolean;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<PlotOutline>(data || { epochs: [] });
  const [saving, setSaving] = useState(false);
  const [openEpoch, setOpenEpoch] = useState<Record<number, boolean>>({});
  // Read-view mode: "full" = 全时间线 (god view, all info inline);
  //                 "iceberg" = 冰山理论 (reader POV, [隐] hidden behind click).
  const [viewMode, setViewMode] = useState<"full" | "iceberg">("iceberg");
  const [revealed, setRevealed] = useState<Record<string, boolean>>({});

  const start = () => { setDraft(data ? { ...data, epochs: data.epochs || [] } : { epochs: [] }); setEditing(true); };
  const cancel = () => { setDraft(data ? { ...data, epochs: data.epochs || [] } : { epochs: [] }); setEditing(false); };
  const save = async () => {
    setSaving(true);
    try {
      // strip legacy fields when saving chronicle format
      const { themes, arcs, key_events, ...rest } = draft;
      await onSave({ ...rest, epochs: draft.epochs || [] });
      setEditing(false);
    } finally { setSaving(false); }
  };

  const d = editing ? draft : (data || {});
  const legacy = isLegacy(d);

  // ── helpers for editing ──
  const updateEpoch = (ei: number, patch: Partial<ChronicleEpoch>) => {
    const epochs = [...(draft.epochs || [])];
    epochs[ei] = { ...epochs[ei], ...patch };
    setDraft({ ...draft, epochs });
  };
  const updatePeriod = (ei: number, pi: number, patch: Partial<ChroniclePeriod>) => {
    const epochs = [...(draft.epochs || [])];
    const periods = [...(epochs[ei]?.periods || [])];
    periods[pi] = { ...periods[pi], ...patch };
    epochs[ei] = { ...epochs[ei], periods };
    setDraft({ ...draft, epochs });
  };
  const updateEvent = (ei: number, pi: number, evi: number, patch: Partial<ChronicleEvent>) => {
    const epochs = [...(draft.epochs || [])];
    const periods = [...(epochs[ei]?.periods || [])];
    const events = [...(periods[pi]?.events || [])];
    events[evi] = { ...events[evi], ...patch };
    periods[pi] = { ...periods[pi], events };
    epochs[ei] = { ...epochs[ei], periods };
    setDraft({ ...draft, epochs });
  };

  if (editing) {
    return (
      <div>
        <div style={{
          padding: "8px 12px",
          marginBottom: 12,
          background: "var(--bg-surface)",
          border: "1px solid var(--border)",
          borderRadius: 4,
          fontSize: 11,
          color: "var(--text-secondary)",
          lineHeight: 1.6,
        }}>
          <strong>编年史格式提示：</strong>世界内史学家第三人称客观视角，只记录"发生了什么"。
          条目格式 <code>【主体·分类·事件名】描述。</code> 隐藏动机/真相用 <code>[隐]</code> 独立标注。
          不写对话原文、心理活动、场景细节、章节结构、伏笔预告。
        </div>
        <div className="field" style={{ marginBottom: 14 }}>
          <label className="label">一句话梗概（可选）</label>
          <textarea
            className="input"
            rows={2}
            value={draft.logline || ""}
            onChange={e => setDraft({ ...draft, logline: e.target.value })}
            placeholder="此项不属于编年史本身，可选填,用于快速识别作品。"
          />
        </div>

        <div className="flex flex-col gap-12" style={{ marginBottom: 12 }}>
          {(draft.epochs || []).map((ep, ei) => (
            <div key={ei} style={{ border: "1px solid var(--border)", borderRadius: 6, padding: 10, background: "var(--bg-surface)" }}>
              <div className="flex gap-6 items-center" style={{ marginBottom: 8 }}>
                <input
                  className="input"
                  placeholder="大段标题（可选，如「人类常态历史」）"
                  value={ep.title || ""}
                  onChange={e => updateEpoch(ei, { title: e.target.value })}
                  style={{ flex: 1, fontWeight: 600 }}
                />
                <button
                  className="btn-icon"
                  onClick={() => {
                    const epochs = [...(draft.epochs || [])];
                    epochs.splice(ei, 1);
                    setDraft({ ...draft, epochs });
                  }}
                  title="删除整个大段"
                  style={{ fontSize: 14 }}
                >&times;</button>
              </div>

              <div className="flex flex-col gap-10" style={{ paddingLeft: 4 }}>
                {(ep.periods || []).map((per, pi) => (
                  <div key={pi} style={{ borderLeft: "3px solid var(--accent)", paddingLeft: 10 }}>
                    <div className="flex gap-6 items-center" style={{ marginBottom: 6 }}>
                      <input
                        className="input"
                        placeholder="时间标题（如「1954 年」/「2030 年 2 月上旬」/「第一卷开篇」）"
                        value={per.time}
                        onChange={e => updatePeriod(ei, pi, { time: e.target.value })}
                        style={{ flex: 1 }}
                      />
                      <button
                        className="btn-icon"
                        onClick={() => {
                          const periods = [...(ep.periods || [])];
                          periods.splice(pi, 1);
                          updateEpoch(ei, { periods });
                        }}
                        style={{ fontSize: 14 }}
                      >&times;</button>
                    </div>

                    <div className="flex flex-col gap-6">
                      {(per.events || []).map((ev, evi) => (
                        <div key={evi} style={{ padding: 8, background: "var(--bg-card)", borderRadius: 4, border: "1px solid var(--border)" }}>
                          <div className="flex gap-4" style={{ marginBottom: 4 }}>
                            <input
                              className="input"
                              placeholder="主体"
                              value={ev.subject}
                              onChange={e => updateEvent(ei, pi, evi, { subject: e.target.value })}
                              style={{ flex: 1, fontSize: 12 }}
                            />
                            <input
                              className="input"
                              placeholder="分类"
                              value={ev.category}
                              onChange={e => updateEvent(ei, pi, evi, { category: e.target.value })}
                              style={{ flex: 1, fontSize: 12 }}
                            />
                            <input
                              className="input"
                              placeholder="事件名"
                              value={ev.name}
                              onChange={e => updateEvent(ei, pi, evi, { name: e.target.value })}
                              style={{ flex: 1, fontSize: 12 }}
                            />
                            <button
                              className="btn-icon"
                              onClick={() => {
                                const events = [...(per.events || [])];
                                events.splice(evi, 1);
                                updatePeriod(ei, pi, { events });
                              }}
                              style={{ fontSize: 14 }}
                            >&times;</button>
                          </div>
                          <textarea
                            className="input"
                            rows={2}
                            placeholder="客观描述（2-5 句，不写对话/心理/场景细节）"
                            value={ev.description}
                            onChange={e => updateEvent(ei, pi, evi, { description: e.target.value })}
                            style={{ marginBottom: 4, fontSize: 12 }}
                          />
                          <textarea
                            className="input"
                            rows={1}
                            placeholder="[隐] 隐藏真相/动机（可选，留空则不显示）"
                            value={ev.hidden || ""}
                            onChange={e => updateEvent(ei, pi, evi, { hidden: e.target.value })}
                            style={{ fontSize: 12, color: "var(--gold)", marginBottom: 4 }}
                          />
                          <input
                            className="input"
                            placeholder='事件时间锚点 (留空则用 period 的时间)'
                            value={ev.time_marker || ""}
                            onChange={e => updateEvent(ei, pi, evi, { time_marker: e.target.value })}
                            style={{ fontSize: 12 }}
                          />
                        </div>
                      ))}
                      <button
                        className="btn"
                        style={{ fontSize: 11, padding: "3px 8px", alignSelf: "flex-start" }}
                        onClick={() => updatePeriod(ei, pi, { events: [...(per.events || []), emptyEvent()] })}
                      >+ 新增事件</button>
                    </div>
                  </div>
                ))}
                <button
                  className="btn"
                  style={{ fontSize: 12, padding: "4px 10px", alignSelf: "flex-start" }}
                  onClick={() => updateEpoch(ei, { periods: [...(ep.periods || []), { time: "", events: [] }] })}
                >+ 新增时间段</button>
              </div>
            </div>
          ))}
          <button
            className="btn-primary"
            style={{ fontSize: 12, padding: "5px 12px", alignSelf: "flex-start" }}
            onClick={() => setDraft({ ...draft, epochs: [...(draft.epochs || []), { title: "", periods: [{ time: "", events: [emptyEvent()] }] }] })}
          >+ 新增大段（纪元/章节阶段）</button>
        </div>

        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 12 }}>
          <button className="btn" onClick={cancel} disabled={saving}>取消</button>
          <button className="btn-primary" onClick={save} disabled={saving}>{saving ? "保存中..." : "保存"}</button>
        </div>
      </div>
    );
  }

  const epochs = d.epochs || [];
  const hasContent = (epochs.length > 0 && epochs.some(e => (e.periods || []).length > 0)) || legacy || d.logline;

  return (
    <div>
      {onExtract && (
        <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 10 }}>
          <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }} onClick={onExtract} disabled={extracting}>
            {extracting ? "提取中..." : "从正文重新提取"}
          </button>
        </div>
      )}

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
          检测到旧版大纲数据。点击「从正文重新提取」迁移到编年史格式，或点击「编辑」手动整理。
        </div>
      )}

      {!hasContent ? (
        <div className="text-xs text-muted text-center" style={{ padding: 12 }}>
          暂无编年史。点击「从正文重新提取」自动生成骨架，或点击「编辑」手动添加。
        </div>
      ) : (
        <>
          {d.logline && (
            <div style={{ marginBottom: 14 }}>
              <div className="text-xs text-muted" style={{ marginBottom: 4 }}>一句话梗概</div>
              <div style={{ fontSize: 13, lineHeight: 1.6, color: "var(--text-primary)" }}>{d.logline}</div>
            </div>
          )}

          {/* View-mode toggle: 全时间线 vs 冰山理论 */}
          <div style={{
            display: "flex",
            gap: 0,
            marginBottom: 12,
            border: "1px solid var(--border)",
            borderRadius: "var(--radius-sm)",
            overflow: "hidden",
            width: "fit-content",
          }}>
            {([
              { key: "full", label: "全时间线剧情", hint: "按作品中的时间顺序，包含所有信息（含隐藏真相）" },
              { key: "iceberg", label: "冰山理论视角", hint: "与读者视角一致，隐藏信息点击展开" },
            ] as const).map(opt => (
              <button
                key={opt.key}
                className="btn-ghost"
                title={opt.hint}
                onClick={() => setViewMode(opt.key)}
                style={{
                  padding: "5px 14px",
                  fontSize: 12,
                  fontWeight: viewMode === opt.key ? 600 : 400,
                  color: viewMode === opt.key ? "var(--accent)" : "var(--text-secondary)",
                  background: viewMode === opt.key ? "var(--accent-subtle)" : "transparent",
                  borderRadius: 0,
                }}
              >{opt.label}</button>
            ))}
          </div>
          <div className="text-xs text-muted" style={{ marginBottom: 10, marginTop: -6 }}>
            {viewMode === "full"
              ? "按作品内时间顺序排列，所有信息（含 [隐] 隐藏真相）直接展示。"
              : "采用冰山理论 · 主视角阅读 · 隐藏信息默认折叠，点击 [隐] 标签展开。"}
          </div>

          <div className="flex flex-col gap-12">
            {epochs.map((ep, ei) => {
              const isOpen = openEpoch[ei] !== false; // default open
              return (
                <div key={ei}>
                  {ep.title && (
                    <button
                      className="btn-ghost w-full"
                      style={{ justifyContent: "space-between", padding: "6px 0", fontWeight: 700, fontSize: 14, color: "var(--text-primary)", borderRadius: 0 }}
                      onClick={() => setOpenEpoch(prev => ({ ...prev, [ei]: !isOpen }))}
                    >
                      <span>{ep.title}</span>
                      <span className="text-xs text-muted" style={{ transition: "transform 0.15s", transform: isOpen ? "rotate(180deg)" : "none", display: "inline-block" }}>&#x25BC;</span>
                    </button>
                  )}
                  {isOpen && (ep.periods || []).map((per, pi) => (
                    <div key={pi} style={{ marginBottom: 12, paddingLeft: ep.title ? 8 : 0 }}>
                      <div style={{ fontWeight: 600, fontSize: 13, color: "var(--accent)", marginBottom: 6 }}>
                        {per.time || "(未填写时间)"}
                      </div>
                      <div className="flex flex-col gap-6" style={{ paddingLeft: 8, borderLeft: "2px solid var(--border)" }}>
                        {(per.events || []).map((ev, evi) => {
                          const key = `${ei}-${pi}-${evi}`;
                          const showHidden = viewMode === "full" || revealed[key];
                          return (
                            <div key={evi} style={{ paddingLeft: 8 }}>
                              <div style={{ fontSize: 12, lineHeight: 1.6, color: "var(--text-primary)" }}>
                                <span style={{ fontWeight: 600, color: "var(--accent)" }}>
                                  【{ev.subject}·{ev.category}·{ev.name}{ev.time_marker ? ` · ${ev.time_marker}` : ""}】
                                </span>
                                <span style={{ color: "var(--text-secondary)" }}>{ev.description}</span>
                                {ev.hidden && viewMode === "iceberg" && !showHidden && (
                                  <button
                                    className="btn-ghost"
                                    onClick={() => setRevealed(prev => ({ ...prev, [key]: true }))}
                                    style={{
                                      marginLeft: 6,
                                      padding: "1px 8px",
                                      fontSize: 10,
                                      borderRadius: 3,
                                      background: "var(--bg-surface-2)",
                                      border: "1px dashed var(--gold)",
                                      color: "var(--gold)",
                                      fontWeight: 600,
                                      cursor: "pointer",
                                      lineHeight: 1.4,
                                    }}
                                    title="点击展开隐藏的真相"
                                  >[隐] 展开</button>
                                )}
                              </div>
                              {ev.hidden && showHidden && (
                                <div style={{ fontSize: 12, lineHeight: 1.6, marginTop: 2, color: "var(--gold)" }}>
                                  <span
                                    style={{ fontWeight: 600, cursor: viewMode === "iceberg" ? "pointer" : "default" }}
                                    onClick={() => viewMode === "iceberg" && setRevealed(prev => ({ ...prev, [key]: false }))}
                                    title={viewMode === "iceberg" ? "点击重新隐藏" : undefined}
                                  >[隐]</span> {ev.hidden}
                                </div>
                              )}
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

      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 12 }}>
        <button className="btn-ghost" style={{ fontSize: 11, color: "var(--text-tertiary)" }} onClick={start}>编辑</button>
      </div>
    </div>
  );
}

/* ──────────────── Settings (设定) ──────────────── */

export interface SettingItem {
  category: string;
  title: string;
  content: string;
  hidden?: string;
  first_introduced_at?: string; // 首次出现的时间锚点
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
  const [revealed, setRevealed] = useState<Record<number, boolean>>({});

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
          编辑作品的世界观与设定。类别用于分组；如设定背后有读者尚未知道的隐藏真相，写在「[隐]」字段中。
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
              <textarea
                className="input"
                rows={2}
                placeholder="[隐] 该设定背后的真相 / 读者尚未知道的部分（可选）"
                value={s.hidden || ""}
                onChange={e => { const list = [...draft]; list[i] = { ...s, hidden: e.target.value }; setDraft(list); }}
                style={{ color: "var(--gold)", marginBottom: 6 }}
              />
              <input
                className="input"
                placeholder='首次出现的时间锚点 (如 "1954 年" / "第 3 章")'
                value={s.first_introduced_at || ""}
                onChange={e => { const list = [...draft]; list[i] = { ...s, first_introduced_at: e.target.value }; setDraft(list); }}
                style={{ fontSize: 12 }}
              />
            </div>
          ))}
          <button
            className="btn"
            style={{ fontSize: 12, padding: "4px 10px", alignSelf: "flex-start" }}
            onClick={() => setDraft([...draft, { category: "worldview", title: "", content: "", hidden: "", first_introduced_at: "" }])}
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
                        color: "var(--accent)",
                        background: "var(--accent-subtle)",
                        border: "1px solid var(--accent)",
                      }} title="首次出现时间锚点">{item.first_introduced_at}</span>
                    )}
                  </div>
                  {item.content && (
                    <div style={{ fontSize: 12, lineHeight: 1.55, color: "var(--text-secondary)" }}>
                      {item.content}
                    </div>
                  )}
                  {item.hidden && (
                    revealed[idx] ? (
                      <div style={{ fontSize: 12, lineHeight: 1.55, marginTop: 6, color: "var(--gold)" }}>
                        <span
                          style={{ fontWeight: 600, cursor: "pointer" }}
                          onClick={() => setRevealed(prev => ({ ...prev, [idx]: false }))}
                          title="点击重新隐藏"
                        >[隐]</span> {item.hidden}
                      </div>
                    ) : (
                      <button
                        className="btn-ghost"
                        onClick={() => setRevealed(prev => ({ ...prev, [idx]: true }))}
                        style={{
                          marginTop: 6,
                          padding: "1px 8px",
                          fontSize: 10,
                          borderRadius: 3,
                          background: "var(--bg-surface-2)",
                          border: "1px dashed var(--gold)",
                          color: "var(--gold)",
                          fontWeight: 600,
                          lineHeight: 1.4,
                        }}
                      >[隐] 展开</button>
                    )
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
