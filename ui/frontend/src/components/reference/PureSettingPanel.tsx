import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiGet, apiPost, apiPut } from "../../api/client";
import { useToast } from "../shared/Toast";
import { PromptCopyPanel } from "./AnalysisEditors";

// 纯设定作品面板 (spec 2.2.2 / 6.2)：SCP、后室、战锤40K 等无完整正文的
// 众创/设定集作品。tab 由父级 ReferenceLibraryPage 统一渲染（与叙事型一致），
// 这里只负责面板内容。
//
// 满足 LLM 交互机制 1/4：
//  - 特征提取支持「大模型 API」和「大模型网页版」两种模式
//  - 当原文超过单段上限时按段落自动切分，每段一个 prompt
// 满足 LLM 交互机制 2：提取生成的内容先进入预览，逐项确认后才入库
// 设定 / 角色 / 设定特征 改为可展开/收起的条目卡片，便于扫描和编辑

export type PureSettingTab = "quick" | "settings" | "characters" | "extract" | "features";

export const PURE_SETTING_TABS: { key: PureSettingTab; label: string }[] = [
  { key: "quick", label: "快捷输入" },
  { key: "settings", label: "设定" },
  { key: "characters", label: "角色" },
  { key: "extract", label: "特征提取" },
  { key: "features", label: "设定特征" },
];

type SettingEntry = { category: string; title: string; content: string };
type StaticCharacter = { name: string; role: string; description: string };
type SettingFeature = { title: string; description: string };

const CATEGORIES = ["力量体系", "势力组织", "地理", "社会规则", "历史背景", "世界观", "其他"];

interface ChunkMeta {
  chunk_index: number;
  total_chunks: number;
  n_chars: number;
  preview: string;
}

interface SegmentPlan {
  total_chars: number;
  total_chunks: number;
  max_chunk_chars: number;
  chunks: ChunkMeta[];
}

interface ChunkPreview {
  settings: SettingEntry[];
  characters: StaticCharacter[];
  setting_features: SettingFeature[];
}

type ChunkSource = "api" | "paste";
type ChunkPhase = "idle" | "running" | "ready" | "failed";

interface ChunkState {
  phase: ChunkPhase;
  source?: ChunkSource;
  preview?: ChunkPreview;
  error?: string;
  pasteRaw?: string;
  pasteError?: string;
}

export default function PureSettingPanel({
  refId, tab, onTabChange,
}: {
  refId: string;
  tab: PureSettingTab;
  onTabChange: (t: PureSettingTab) => void;
}) {
  const { toast } = useToast();
  const [quickText, setQuickText] = useState("");
  const [savedText, setSavedText] = useState("");
  const [settings, setSettings] = useState<SettingEntry[]>([]);
  const [characters, setCharacters] = useState<StaticCharacter[]>([]);
  const [features, setFeatures] = useState<SettingFeature[]>([]);
  const [saving, setSaving] = useState(false);

  const reload = useCallback(async () => {
    try {
      const r = await apiGet<any>(`/api/references/works/${refId}/pure-setting`);
      const txt = r.quick_input_text || "";
      setQuickText(txt);
      setSavedText(txt);
      setSettings(r.settings || []);
      setCharacters(r.static_characters || []);
      setFeatures(r.setting_features || []);
    } catch (e: any) { toast(e.message || "加载失败", "error"); }
  }, [refId, toast]);

  useEffect(() => { reload(); }, [reload]);

  const put = async (body: any, msg: string) => {
    setSaving(true);
    try {
      await apiPut(`/api/references/works/${refId}/pure-setting`, body);
      toast(msg, "success");
      await reload();
    } catch (e: any) { toast(e.message || "保存失败", "error"); }
    finally { setSaving(false); }
  };

  return (
    <div>
      {tab === "quick" && (
        <QuickInputTab
          text={quickText} savedText={savedText}
          onChange={setQuickText} saving={saving}
          onSave={() => put({ quick_input_text: quickText }, "快捷输入已保存")}
          onSaveAndExtract={async () => {
            await put({ quick_input_text: quickText }, "快捷输入已保存");
            onTabChange("extract");
          }}
        />
      )}

      {tab === "settings" && (
        <CollapsibleList<SettingEntry>
          items={settings}
          identify={(s) => s.title || s.content}
          summary={(s) => (
            <>
              <span style={{
                fontSize: 10, padding: "1px 8px", borderRadius: 3,
                background: "var(--accent-subtle)", color: "var(--accent)",
                marginRight: 8, flexShrink: 0,
              }}>{s.category || "其他"}</span>
              <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                {s.title || "（未命名设定）"}
              </span>
              <span className="text-xs text-muted truncate" style={{ marginLeft: 10, flex: 1 }}>
                {s.content}
              </span>
            </>
          )}
          renderEditor={(item, set) => (
            <>
              <Field label="分类">
                <select className="select" value={item.category}
                        onChange={e => set({ ...item, category: e.target.value })}
                        style={{ fontSize: 12 }}>
                  {CATEGORIES.map(o => <option key={o} value={o}>{o}</option>)}
                </select>
              </Field>
              <Field label="条目名">
                <input className="input" value={item.title}
                       onChange={e => set({ ...item, title: e.target.value })}
                       style={{ fontSize: 12 }} />
              </Field>
              <Field label="内容">
                <textarea className="input" value={item.content} rows={3}
                          onChange={e => set({ ...item, content: e.target.value })}
                          style={{ fontSize: 12, lineHeight: 1.6 }} />
              </Field>
            </>
          )}
          blank={{ category: "其他", title: "", content: "" }}
          onSave={items => put({ settings: items }, "设定条目已保存")}
          saving={saving}
          addLabel="新增设定"
          emptyHint="暂无设定。点击「特征提取」或下方新增按钮开始。"
        />
      )}

      {tab === "characters" && (
        <CollapsibleList<StaticCharacter>
          items={characters}
          identify={(c) => c.name}
          summary={(c) => (
            <>
              <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                {c.name || "（未命名角色）"}
              </span>
              {c.role && (
                <span style={{
                  fontSize: 10, padding: "1px 8px", borderRadius: 3,
                  background: "var(--bg-surface-2)", color: "var(--text-secondary)",
                  marginLeft: 8, flexShrink: 0,
                }}>{c.role}</span>
              )}
              <span className="text-xs text-muted truncate" style={{ marginLeft: 10, flex: 1 }}>
                {c.description}
              </span>
            </>
          )}
          renderEditor={(item, set) => (
            <>
              <Field label="姓名">
                <input className="input" value={item.name}
                       onChange={e => set({ ...item, name: e.target.value })}
                       style={{ fontSize: 12 }} />
              </Field>
              <Field label="定位">
                <input className="input" value={item.role}
                       onChange={e => set({ ...item, role: e.target.value })}
                       placeholder="如 创始人 / 异常实体 / 守护者"
                       style={{ fontSize: 12 }} />
              </Field>
              <Field label="描述">
                <textarea className="input" value={item.description} rows={3}
                          onChange={e => set({ ...item, description: e.target.value })}
                          style={{ fontSize: 12, lineHeight: 1.6 }} />
              </Field>
            </>
          )}
          blank={{ name: "", role: "", description: "" }}
          onSave={items => put({ static_characters: items }, "角色条目已保存")}
          saving={saving}
          addLabel="新增角色"
          emptyHint="暂无角色。点击「特征提取」或下方新增按钮开始。"
        />
      )}

      {tab === "features" && (
        <CollapsibleList<SettingFeature>
          items={features}
          identify={(f) => f.title}
          summary={(f) => (
            <>
              <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                {f.title || "（未命名特征）"}
              </span>
              <span className="text-xs text-muted truncate" style={{ marginLeft: 10, flex: 1 }}>
                {f.description}
              </span>
            </>
          )}
          renderEditor={(item, set) => (
            <>
              <Field label="高概念 / 母题">
                <input className="input" value={item.title}
                       onChange={e => set({ ...item, title: e.target.value })}
                       style={{ fontSize: 12 }} />
              </Field>
              <Field label="一句话解释">
                <textarea className="input" value={item.description} rows={2}
                          onChange={e => set({ ...item, description: e.target.value })}
                          style={{ fontSize: 12, lineHeight: 1.6 }} />
              </Field>
            </>
          )}
          blank={{ title: "", description: "" }}
          onSave={items => put({ setting_features: items }, "设定特征已保存")}
          saving={saving}
          addLabel="新增特征"
          emptyHint="暂无特征。点击「特征提取」或下方新增按钮开始。"
        />
      )}

      {tab === "extract" && (
        <ExtractTab
          refId={refId}
          savedText={savedText}
          existing={{ settings, characters, features }}
          onCommitted={reload}
        />
      )}
    </div>
  );
}

/* ───────────────────────── Quick input tab ────────────────────────── */

function QuickInputTab({
  text, savedText, onChange, saving, onSave, onSaveAndExtract,
}: {
  text: string; savedText: string;
  onChange: (s: string) => void;
  saving: boolean;
  onSave: () => void;
  onSaveAndExtract: () => void;
}) {
  const dirty = text !== savedText;
  return (
    <div>
      <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginBottom: 8, lineHeight: 1.7 }}>
        直接粘贴 wiki 条目原文（可多条拼接）。保存后到「特征提取」tab 可使用大模型 API 或网页版抽取设定条目、角色与设定特征。
        <br />原文超过 12000 字时会自动分段，每段独立生成 prompt。
      </div>
      <textarea className="input" value={text}
                onChange={e => onChange(e.target.value)} rows={18}
                placeholder="粘贴 SCP / 后室 / 战锤40K 等 wiki 条目原文..."
                style={{ width: "100%", boxSizing: "border-box", fontSize: 11, lineHeight: 1.6 }} />
      <div className="flex items-center" style={{ gap: 8, marginTop: 8 }}>
        <span className="text-xs text-muted">{text.length.toLocaleString()} 字</span>
        <div style={{ flex: 1 }} />
        <button className="btn" style={{ fontSize: 11, padding: "4px 14px" }}
                disabled={saving || !dirty} onClick={onSave}>
          {saving ? "保存中..." : "保存原文"}
        </button>
        <button className="btn-primary" style={{ fontSize: 11, padding: "4px 14px" }}
                disabled={saving || !text.trim()} onClick={onSaveAndExtract}>
          保存并交给特征提取
        </button>
      </div>
    </div>
  );
}

/* ───────────────────────── Extract tab ────────────────────────────── */

function ExtractTab({
  refId, savedText, existing, onCommitted,
}: {
  refId: string;
  savedText: string;
  existing: {
    settings: SettingEntry[];
    characters: StaticCharacter[];
    features: SettingFeature[];
  };
  onCommitted: () => void | Promise<void>;
}) {
  const { toast } = useToast();
  const [plan, setPlan] = useState<SegmentPlan | null>(null);
  const [planLoading, setPlanLoading] = useState(false);
  const [chunkState, setChunkState] = useState<Record<number, ChunkState>>({});
  const [openChunks, setOpenChunks] = useState<Set<number>>(new Set());
  const [bulkRunning, setBulkRunning] = useState(false);
  const [committing, setCommitting] = useState<{ idx: number; section: string } | null>(null);

  const loadPlan = useCallback(async () => {
    if (!savedText.trim()) {
      setPlan(null);
      return;
    }
    setPlanLoading(true);
    try {
      const r = await apiGet<SegmentPlan & { ref_id: string }>(
        `/api/references/works/${refId}/pure-setting/segments`,
      );
      setPlan(r);
      // 默认展开第一段
      if (r.chunks.length > 0) setOpenChunks(new Set([r.chunks[0].chunk_index]));
    } catch (e: any) {
      toast(e?.message || "加载分段失败", "error");
    } finally {
      setPlanLoading(false);
    }
  }, [refId, savedText, toast]);

  useEffect(() => { loadPlan(); }, [loadPlan]);

  const patchChunk = (idx: number, p: Partial<ChunkState>) =>
    setChunkState(prev => ({
      ...prev,
      [idx]: { ...(prev[idx] || { phase: "idle" }), ...p },
    }));

  const toggleChunk = (idx: number) =>
    setOpenChunks(prev => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx); else next.add(idx);
      return next;
    });

  const runApi = async (idx: number) => {
    patchChunk(idx, { phase: "running", source: "api", error: undefined, preview: undefined });
    try {
      const r = await apiPost<{
        settings: SettingEntry[]; characters: StaticCharacter[];
        setting_features: SettingFeature[];
      }>(
        `/api/references/works/${refId}/pure-setting/extract`,
        { chunk_index: idx },
        { timeoutMs: 300_000 },
      );
      patchChunk(idx, {
        phase: "ready", source: "api",
        preview: {
          settings: r.settings || [],
          characters: r.characters || [],
          setting_features: r.setting_features || [],
        },
      });
    } catch (e: any) {
      patchChunk(idx, { phase: "failed", error: e?.message || "API 提取失败" });
    }
  };

  const parsePaste = async (idx: number) => {
    const raw = chunkState[idx]?.pasteRaw || "";
    if (!raw.trim()) {
      patchChunk(idx, { pasteError: "请先粘贴 LLM 返回的 JSON" });
      return;
    }
    patchChunk(idx, { phase: "running", source: "paste", pasteError: undefined });
    try {
      const r = await apiPost<{
        settings: SettingEntry[]; characters: StaticCharacter[];
        setting_features: SettingFeature[];
      }>(
        `/api/references/works/${refId}/pure-setting/parse-paste`,
        { chunk_index: idx, raw },
      );
      patchChunk(idx, {
        phase: "ready", source: "paste",
        preview: {
          settings: r.settings || [],
          characters: r.characters || [],
          setting_features: r.setting_features || [],
        },
      });
    } catch (e: any) {
      patchChunk(idx, {
        phase: "failed", source: "paste",
        pasteError: e?.message || "解析失败",
      });
    }
  };

  const reset = (idx: number) =>
    patchChunk(idx, {
      phase: "idle", source: undefined, preview: undefined,
      error: undefined, pasteRaw: undefined, pasteError: undefined,
    });

  const commitSection = async (
    idx: number, section: "settings" | "characters" | "setting_features",
  ) => {
    const preview = chunkState[idx]?.preview;
    if (!preview) return;
    setCommitting({ idx, section });
    try {
      const body: any = {};
      if (section === "settings") {
        body.settings = dedupeBy(
          [...existing.settings, ...preview.settings],
          (s) => `${s.category}::${s.title}`,
        );
      } else if (section === "characters") {
        body.static_characters = dedupeBy(
          [...existing.characters, ...preview.characters],
          (c) => c.name,
        );
      } else {
        body.setting_features = dedupeBy(
          [...existing.features, ...preview.setting_features],
          (f) => f.title,
        );
      }
      await apiPut(`/api/references/works/${refId}/pure-setting`, body);
      toast(
        section === "settings" ? "设定已入库"
        : section === "characters" ? "角色已入库" : "设定特征已入库",
        "success",
      );
      // 清掉本段对应板块，避免重复入库
      const next = { ...preview };
      if (section === "settings") next.settings = [];
      else if (section === "characters") next.characters = [];
      else next.setting_features = [];
      patchChunk(idx, { preview: next });
      await onCommitted();
    } catch (e: any) {
      toast(e?.message || "入库失败", "error");
    } finally {
      setCommitting(null);
    }
  };

  const runAll = async () => {
    if (!plan || bulkRunning) return;
    setBulkRunning(true);
    try {
      for (const c of plan.chunks) {
        if (chunkState[c.chunk_index]?.preview) continue; // 已就绪的跳过
        await runApi(c.chunk_index);
      }
      toast("批量 API 提取完成", "success");
    } finally {
      setBulkRunning(false);
    }
  };

  if (!savedText.trim()) {
    return (
      <div className="text-xs text-muted" style={{ padding: 12, lineHeight: 1.8 }}>
        快捷输入为空。请到「快捷输入」tab 粘贴 wiki 原文后保存。
      </div>
    );
  }
  if (planLoading) {
    return <div className="text-xs text-muted" style={{ padding: 12 }}>加载分段计划中…</div>;
  }
  if (!plan || plan.chunks.length === 0) {
    return (
      <div className="text-xs text-muted" style={{ padding: 12 }}>
        暂无可处理的内容。
      </div>
    );
  }

  return (
    <div style={{
      border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
      padding: 12, background: "var(--bg-surface)",
    }}>
      <div className="flex items-center" style={{
        gap: 8, marginBottom: 8, flexWrap: "wrap",
      }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)" }}>
          特征提取
        </div>
        <span className="text-xs text-muted">
          共 {plan.total_chars.toLocaleString()} 字 · {plan.total_chunks} 段
          {plan.total_chunks > 1 && ` · 每段最长 ${plan.max_chunk_chars.toLocaleString()} 字`}
        </span>
        <div style={{ flex: 1 }} />
        {plan.total_chunks > 1 && (
          <button className="btn-primary"
                  style={{ fontSize: 11, padding: "4px 12px" }}
                  disabled={bulkRunning}
                  onClick={runAll}
                  title="对每段调用大模型 API；已就绪的段跳过">
            {bulkRunning ? "批量提取中…" : "使用 API 一键处理全部分段"}
          </button>
        )}
      </div>

      <div className="text-xs text-muted" style={{ marginBottom: 10, lineHeight: 1.7 }}>
        每段提供两种提取方式：
        <strong style={{ color: "var(--accent)" }}>大模型 API</strong>（在设置中配置好模型后直接调用）
        与 <strong style={{ color: "var(--accent)" }}>大模型网页版</strong>
        （复制 prompt → 在网页 LLM 中运行 → 粘贴返回的 JSON 由系统解析）。
        提取结果先进入预览，确认后逐板块入库。
      </div>

      <div className="flex flex-col gap-6">
        {plan.chunks.map(c => (
          <ChunkRow
            key={c.chunk_index}
            refId={refId}
            chunk={c}
            state={chunkState[c.chunk_index] || { phase: "idle" }}
            open={openChunks.has(c.chunk_index)}
            onToggle={() => toggleChunk(c.chunk_index)}
            onRunApi={() => runApi(c.chunk_index)}
            onPasteChange={(v) => patchChunk(c.chunk_index, { pasteRaw: v, pasteError: undefined })}
            onParsePaste={() => parsePaste(c.chunk_index)}
            onReset={() => reset(c.chunk_index)}
            onCommitSection={(section) => commitSection(c.chunk_index, section)}
            committing={
              committing?.idx === c.chunk_index ? committing.section : undefined
            }
          />
        ))}
      </div>
    </div>
  );
}

function ChunkRow({
  refId, chunk, state, open,
  onToggle, onRunApi, onPasteChange, onParsePaste, onReset, onCommitSection,
  committing,
}: {
  refId: string;
  chunk: ChunkMeta;
  state: ChunkState;
  open: boolean;
  onToggle: () => void;
  onRunApi: () => void;
  onPasteChange: (v: string) => void;
  onParsePaste: () => void;
  onReset: () => void;
  onCommitSection: (section: "settings" | "characters" | "setting_features") => void;
  committing?: string;
}) {
  const [mode, setMode] = useState<"api" | "web">("api");
  const running = state.phase === "running";
  const ready = state.phase === "ready";
  const failed = state.phase === "failed";
  const p = state.preview;
  return (
    <div style={{
      border: `1px solid ${ready ? "var(--accent)" : failed ? "var(--error)" : "var(--border)"}`,
      borderRadius: 4,
      background: "var(--bg-card)",
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
          第 {chunk.chunk_index + 1} / {chunk.total_chunks} 段
        </span>
        <span className="text-xs text-muted">{chunk.n_chars.toLocaleString()} 字</span>
        <span className="text-xs text-muted truncate" style={{ flex: 1, opacity: 0.7 }}>
          {chunk.preview}
        </span>
        {running && <span className="text-xs" style={{ color: "var(--gold)" }}>处理中…</span>}
        {ready && (
          <span className="tag" style={{
            fontSize: 10, padding: "1px 6px",
            color: "var(--accent)", border: "1px solid var(--accent)",
          }}>{state.source === "paste" ? "网页版 已解析" : "API 已生成"}</span>
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
          {running && (
            <div style={{ marginBottom: 8 }}>
              <div className="text-xs" style={{ color: "var(--accent)", marginBottom: 4 }}>
                {state.source === "paste" ? "正在解析网页返回结果…" : "大模型 API 提取中…"}
              </div>
              <div className="ink-indeterminate" />
            </div>
          )}

          {p ? (
            <>
              <PreviewBlock
                title={`设定条目（${p.settings.length}）`}
                onCommit={p.settings.length > 0 ? () => onCommitSection("settings") : undefined}
                committing={committing === "settings"}
              >
                {p.settings.length === 0
                  ? <span className="text-xs text-muted">（无）</span>
                  : p.settings.map((s, i) => (
                    <div key={i} style={previewRow}>
                      <span style={{ width: 80, color: "var(--accent)" }}>{s.category}</span>
                      <span style={{ width: 160, fontWeight: 600 }}>{s.title}</span>
                      <span style={{ flex: 1, color: "var(--text-secondary)" }}>{s.content}</span>
                    </div>
                  ))}
              </PreviewBlock>
              <PreviewBlock
                title={`角色（${p.characters.length}）`}
                onCommit={p.characters.length > 0 ? () => onCommitSection("characters") : undefined}
                committing={committing === "characters"}
              >
                {p.characters.length === 0
                  ? <span className="text-xs text-muted">（无）</span>
                  : p.characters.map((c, i) => (
                    <div key={i} style={previewRow}>
                      <span style={{ width: 140, fontWeight: 600 }}>{c.name}</span>
                      <span style={{ width: 120 }}>{c.role}</span>
                      <span style={{ flex: 1, color: "var(--text-secondary)" }}>{c.description}</span>
                    </div>
                  ))}
              </PreviewBlock>
              <PreviewBlock
                title={`设定特征（${p.setting_features.length}）`}
                onCommit={p.setting_features.length > 0 ? () => onCommitSection("setting_features") : undefined}
                committing={committing === "setting_features"}
              >
                {p.setting_features.length === 0
                  ? <span className="text-xs text-muted">（无）</span>
                  : p.setting_features.map((f, i) => (
                    <div key={i} style={previewRow}>
                      <span style={{ width: 180, fontWeight: 600 }}>{f.title}</span>
                      <span style={{ flex: 1, color: "var(--text-secondary)" }}>{f.description}</span>
                    </div>
                  ))}
              </PreviewBlock>
              <div className="flex" style={{
                justifyContent: "flex-end", marginTop: 8,
                paddingTop: 8, borderTop: "1px dashed var(--border)",
              }}>
                <button className="btn" onClick={onReset}
                        disabled={!!committing}
                        style={{ fontSize: 11, padding: "3px 10px" }}>
                  重新提取
                </button>
              </div>
            </>
          ) : (
            <>
              <div className="flex items-center" style={{
                gap: 4, marginBottom: 8,
                borderBottom: "1px solid var(--border)",
              }}>
                {(["api", "web"] as const).map(m => (
                  <button key={m} className="btn-ghost"
                          onClick={() => setMode(m)}
                          style={{
                            padding: "4px 12px", fontSize: 11,
                            fontWeight: mode === m ? 600 : 400,
                            color: mode === m ? "var(--accent)" : "var(--text-secondary)",
                            borderBottom: mode === m ? "2px solid var(--accent)" : "2px solid transparent",
                            marginBottom: -1, borderRadius: 0,
                          }}>
                    {m === "api" ? "使用大模型 API" : "使用大模型网页版"}
                  </button>
                ))}
              </div>

              {mode === "api" && (
                <>
                  <div className="text-xs text-muted" style={{ marginBottom: 6, lineHeight: 1.7 }}>
                    使用 UI 设置页面里配置的模型 API，直接调用并解析结果。
                  </div>
                  <button className="btn-primary"
                          onClick={onRunApi}
                          disabled={running}
                          style={{ fontSize: 11, padding: "4px 12px" }}>
                    {running ? "提取中…" : "调用 API 提取本段"}
                  </button>
                  {failed && state.error && (
                    <div style={errBox}>
                      <div style={{ fontWeight: 600, marginBottom: 2 }}>提取失败</div>
                      <div style={{ wordBreak: "break-word" }}>{state.error}</div>
                    </div>
                  )}
                </>
              )}

              {mode === "web" && (
                <>
                  <div className="text-xs text-muted" style={{ marginBottom: 6, lineHeight: 1.7 }}>
                    复制下方 prompt 到你的大模型网页版（如 ChatGPT / Claude.ai），
                    把返回的 JSON 粘贴回下面的输入框，系统会自动解析。
                  </div>
                  <PromptCopyPanel
                    refId={refId}
                    promptKey="reference.pure_setting"
                    segmentIndex={chunk.chunk_index}
                    chunked={false}
                    defaultOpen
                    label={`第 ${chunk.chunk_index + 1} 段的 prompt（设定 / 角色 / 设定特征）`}
                  />
                  <textarea className="input font-mono"
                            rows={5}
                            value={state.pasteRaw || ""}
                            placeholder='粘贴网页 LLM 返回的 {"settings":[...],"characters":[...],"setting_features":[...]}'
                            onChange={e => onPasteChange(e.target.value)}
                            style={{
                              fontSize: 11, lineHeight: 1.5, resize: "vertical",
                              background: "var(--bg-app)", marginBottom: 6,
                            }} />
                  <div className="flex items-center" style={{ gap: 8 }}>
                    <button className="btn-primary"
                            onClick={onParsePaste}
                            disabled={!(state.pasteRaw && state.pasteRaw.trim()) || running}
                            style={{ fontSize: 11, padding: "4px 12px" }}>
                      解析并预览
                    </button>
                    {state.pasteError && (
                      <span className="text-xs" style={{ color: "var(--error)", flex: 1 }}>
                        {state.pasteError}
                      </span>
                    )}
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

/* ───────────────────────── Collapsible list ───────────────────────── */

function CollapsibleList<T extends Record<string, any>>({
  items, identify, summary, renderEditor, blank, onSave, saving,
  addLabel, emptyHint,
}: {
  items: T[];
  identify: (item: T) => string;
  summary: (item: T) => React.ReactNode;
  renderEditor: (item: T, set: (next: T) => void) => React.ReactNode;
  blank: T;
  onSave: (items: T[]) => void;
  saving: boolean;
  addLabel: string;
  emptyHint: string;
}) {
  const [draft, setDraft] = useState<T[]>(items);
  const [openIdx, setOpenIdx] = useState<Set<number>>(new Set());
  const [dirty, setDirty] = useState(false);
  // re-sync when items change from outside (e.g. after extract commit)
  useEffect(() => { if (!dirty) setDraft(items); }, [items, dirty]);

  const update = (i: number, next: T) => {
    setDraft(prev => prev.map((row, j) => j === i ? next : row));
    setDirty(true);
  };
  const remove = (i: number) => {
    setDraft(prev => prev.filter((_, j) => j !== i));
    setOpenIdx(prev => {
      const next = new Set<number>();
      prev.forEach(idx => { if (idx < i) next.add(idx); else if (idx > i) next.add(idx - 1); });
      return next;
    });
    setDirty(true);
  };
  const add = () => {
    setDraft(prev => [...prev, { ...blank }]);
    setOpenIdx(prev => new Set([...prev, draft.length]));
    setDirty(true);
  };
  const toggle = (i: number) =>
    setOpenIdx(prev => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i); else next.add(i);
      return next;
    });

  return (
    <div>
      {draft.length === 0 && (
        <div className="text-xs text-muted" style={{
          padding: 14, textAlign: "center", fontStyle: "italic",
        }}>{emptyHint}</div>
      )}
      <div className="flex flex-col gap-4">
        {draft.map((row, i) => (
          <div key={i} style={{
            border: "1px solid var(--border)",
            borderRadius: 4,
            background: "var(--bg-card)",
          }}>
            <div className="flex items-center" style={{ gap: 6, padding: "5px 8px" }}>
              <button className="btn-ghost"
                      onClick={() => toggle(i)}
                      style={{
                        display: "flex", alignItems: "center", gap: 6, flex: 1,
                        padding: "2px 0", justifyContent: "flex-start",
                        borderRadius: 0, minWidth: 0, textAlign: "left",
                      }}>
                <span style={{
                  transition: "transform 0.15s",
                  transform: openIdx.has(i) ? "rotate(90deg)" : "none",
                  display: "inline-block", fontSize: 9, color: "var(--text-tertiary)",
                  flexShrink: 0,
                }}>▶</span>
                <div className="flex items-center" style={{
                  gap: 0, flex: 1, minWidth: 0, fontSize: 11,
                }}>
                  {summary(row)}
                </div>
              </button>
              <button className="btn" title="删除"
                      onClick={() => remove(i)}
                      style={{
                        fontSize: 10, padding: "1px 8px",
                        color: "var(--error)", flexShrink: 0,
                      }}>删</button>
            </div>
            {openIdx.has(i) && (
              <div style={{
                padding: "8px 10px", borderTop: "1px dashed var(--border)",
                display: "flex", flexDirection: "column", gap: 8,
              }}>
                {renderEditor(row, (next) => update(i, next))}
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="flex items-center" style={{ gap: 8, marginTop: 10 }}>
        <button className="btn"
                onClick={add}
                style={{ fontSize: 11, padding: "4px 12px" }}>
          + {addLabel}
        </button>
        <div style={{ flex: 1 }} />
        <button className="btn-primary"
                disabled={saving || !dirty}
                onClick={() => { onSave(draft); setDirty(false); }}
                style={{ fontSize: 11, padding: "4px 14px" }}>
          {saving ? "保存中…" : "保存"}
        </button>
      </div>
    </div>
  );
}

/* ───────────────────────── Misc helpers ───────────────────────────── */

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="label" style={{
        fontSize: 10, color: "var(--text-tertiary)", marginBottom: 3,
      }}>{label}</label>
      {children}
    </div>
  );
}

function PreviewBlock({
  title, onCommit, committing, children,
}: {
  title: string;
  onCommit?: () => void;
  committing: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div style={{
      marginBottom: 8, border: "1px solid var(--border)",
      borderRadius: 4, background: "var(--bg-surface)",
    }}>
      <div className="flex items-center" style={{
        gap: 6, padding: "3px 8px",
      }}>
        <button className="btn-ghost"
                onClick={() => setOpen(o => !o)}
                style={{
                  display: "flex", alignItems: "center", gap: 6, flex: 1,
                  padding: "2px 0", justifyContent: "flex-start", borderRadius: 0,
                }}>
          <span style={{
            transition: "transform 0.15s", transform: open ? "rotate(90deg)" : "none",
            display: "inline-block", fontSize: 9, color: "var(--text-tertiary)",
          }}>▶</span>
          <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-primary)" }}>{title}</span>
        </button>
        {onCommit && (
          <button className="btn-primary"
                  style={{ fontSize: 10, padding: "2px 10px" }}
                  disabled={committing}
                  onClick={(e) => { e.stopPropagation(); onCommit(); }}>
            {committing ? "入库中…" : "确认入库"}
          </button>
        )}
      </div>
      {open && (
        <div style={{
          padding: "4px 8px 8px", borderTop: "1px dashed var(--border)",
          maxHeight: 220, overflowY: "auto",
        }}>
          {children}
        </div>
      )}
    </div>
  );
}

const previewRow: React.CSSProperties = {
  display: "flex", gap: 6, alignItems: "flex-start",
  padding: "4px 0", borderBottom: "1px solid var(--border-subtle)",
  fontSize: 11, lineHeight: 1.55,
};

const errBox: React.CSSProperties = {
  padding: "6px 10px", marginTop: 8,
  background: "var(--bg-surface)", border: "1px solid var(--error)",
  borderRadius: 3, fontSize: 11, color: "var(--error)", lineHeight: 1.55,
};

function dedupeBy<T>(items: T[], key: (x: T) => string): T[] {
  const seen = new Set<string>();
  const out: T[] = [];
  for (const item of items) {
    const k = (key(item) || "").trim();
    if (!k) {
      out.push(item);
      continue;
    }
    if (seen.has(k)) continue;
    seen.add(k);
    out.push(item);
  }
  return out;
}
