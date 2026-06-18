import React, { useCallback, useEffect, useState } from "react";
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
//  - 特征提取 prompt 同时包含「快捷输入 / 设定 / 角色」三类用户输入，
//    三者只要任一非空即可提取
// 满足 LLM 交互机制 2：提取生成的内容先进入预览，逐项确认后才入库

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

const CATEGORY_COLORS: Record<string, string> = {
  "力量体系": "var(--accent)",
  "势力组织": "var(--indigo)",
  "地理": "var(--jade)",
  "社会规则": "var(--cyan)",
  "历史背景": "var(--gold)",
  "世界观": "var(--purple)",
  "其他": "var(--text-tertiary)",
};

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
  existing_settings_count: number;
  existing_characters_count: number;
  can_extract: boolean;
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
  mode?: "api" | "web";
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
  const [loaded, setLoaded] = useState(false);

  const reload = useCallback(async () => {
    try {
      const r = await apiGet<any>(`/api/references/works/${refId}/pure-setting`);
      const txt = r.quick_input_text || "";
      setQuickText(txt);
      setSavedText(txt);
      setSettings(r.settings || []);
      setCharacters(r.static_characters || []);
      setFeatures(r.setting_features || []);
      setLoaded(true);
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
    <div style={{ paddingTop: 4 }}>
      {tab === "quick" && (
        <QuickInputTab
          text={quickText} savedText={savedText}
          onChange={setQuickText} saving={saving}
          onSave={() => put({ quick_input_text: quickText }, "快捷输入已保存")}
          onSaveAndExtract={async () => {
            if (quickText !== savedText) {
              await put({ quick_input_text: quickText }, "快捷输入已保存");
            }
            onTabChange("extract");
          }}
        />
      )}

      {tab === "settings" && (
        <TabCard
          title="设定条目"
          subtitle={`${settings.length} 条 · 折叠条目点击展开编辑`}
        >
          <CollapsibleList<SettingEntry>
            items={settings}
            identify={(s) => s.title || s.content}
            summary={(s) => (
              <>
                <CategoryChip category={s.category || "其他"} />
                <span style={summaryTitle}>
                  {s.title || "（未命名设定）"}
                </span>
                {s.content && (
                  <span style={summaryDesc} className="truncate">
                    {s.content}
                  </span>
                )}
              </>
            )}
            renderEditor={(item, set) => (
              <>
                <Field label="分类">
                  <select className="select" value={item.category}
                          onChange={e => set({ ...item, category: e.target.value })}>
                    {CATEGORIES.map(o => <option key={o} value={o}>{o}</option>)}
                  </select>
                </Field>
                <Field label="条目名">
                  <input className="input" value={item.title}
                         placeholder="如 18 号监狱、收容协议"
                         onChange={e => set({ ...item, title: e.target.value })} />
                </Field>
                <Field label="内容">
                  <textarea className="input" value={item.content} rows={4}
                            placeholder="一句话说明这是什么、有何作用"
                            onChange={e => set({ ...item, content: e.target.value })}
                            style={{ lineHeight: 1.65 }} />
                </Field>
              </>
            )}
            blank={{ category: "其他", title: "", content: "" }}
            onSave={items => put({ settings: items }, "设定条目已保存")}
            saving={saving}
            addLabel="新增设定"
            emptyHint="暂无设定条目"
            emptyAction={(
              <button className="btn" onClick={() => onTabChange("extract")}>
                去「特征提取」由 LLM 抽取
              </button>
            )}
          />
        </TabCard>
      )}

      {tab === "characters" && (
        <TabCard
          title="角色"
          subtitle={`${characters.length} 位 · 静态条目，不绑定章节`}
        >
          <CollapsibleList<StaticCharacter>
            items={characters}
            identify={(c) => c.name}
            summary={(c) => (
              <>
                <span style={summaryTitle}>
                  {c.name || "（未命名角色）"}
                </span>
                {c.role && (
                  <span style={{
                    fontSize: 11, padding: "2px 8px", borderRadius: 4,
                    background: "var(--bg-surface-2)", color: "var(--text-secondary)",
                    marginLeft: 6, flexShrink: 0,
                  }}>{c.role}</span>
                )}
                {c.description && (
                  <span style={summaryDesc} className="truncate">
                    {c.description}
                  </span>
                )}
              </>
            )}
            renderEditor={(item, set) => (
              <>
                <Field label="姓名">
                  <input className="input" value={item.name}
                         onChange={e => set({ ...item, name: e.target.value })} />
                </Field>
                <Field label="定位">
                  <input className="input" value={item.role}
                         onChange={e => set({ ...item, role: e.target.value })}
                         placeholder="如 创始人 / 异常实体 / 守护者" />
                </Field>
                <Field label="描述">
                  <textarea className="input" value={item.description} rows={4}
                            placeholder="静态描述（不与章节绑定）"
                            onChange={e => set({ ...item, description: e.target.value })}
                            style={{ lineHeight: 1.65 }} />
                </Field>
              </>
            )}
            blank={{ name: "", role: "", description: "" }}
            onSave={items => put({ static_characters: items }, "角色条目已保存")}
            saving={saving}
            addLabel="新增角色"
            emptyHint="暂无角色"
            emptyAction={(
              <button className="btn" onClick={() => onTabChange("extract")}>
                去「特征提取」由 LLM 抽取
              </button>
            )}
          />
        </TabCard>
      )}

      {tab === "features" && (
        <TabCard
          title="设定特征"
          subtitle={`${features.length} 条 · 作品级世界观高概念 / 母题`}
        >
          <CollapsibleList<SettingFeature>
            items={features}
            identify={(f) => f.title}
            summary={(f) => (
              <>
                <span style={summaryTitle}>
                  {f.title || "（未命名特征）"}
                </span>
                {f.description && (
                  <span style={summaryDesc} className="truncate">
                    {f.description}
                  </span>
                )}
              </>
            )}
            renderEditor={(item, set) => (
              <>
                <Field label="高概念 / 母题">
                  <input className="input" value={item.title}
                         placeholder="如「太空大航海」「唯心影响现实世界」"
                         onChange={e => set({ ...item, title: e.target.value })} />
                </Field>
                <Field label="一句话解释">
                  <textarea className="input" value={item.description} rows={3}
                            onChange={e => set({ ...item, description: e.target.value })}
                            style={{ lineHeight: 1.65 }} />
                </Field>
              </>
            )}
            blank={{ title: "", description: "" }}
            onSave={items => put({ setting_features: items }, "设定特征已保存")}
            saving={saving}
            addLabel="新增特征"
            emptyHint="暂无设定特征"
            emptyAction={(
              <button className="btn" onClick={() => onTabChange("extract")}>
                去「特征提取」由 LLM 综合分析
              </button>
            )}
          />
        </TabCard>
      )}

      {tab === "extract" && loaded && (
        <ExtractTab
          refId={refId}
          existing={{ settings, characters, features }}
          onCommitted={reload}
          onGoToTab={onTabChange}
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
    <TabCard
      title="快捷输入"
      subtitle="粘贴 wiki 条目原文，下一步由 LLM 抽取设定 / 角色 / 设定特征"
    >
      <div className="text-xs" style={{
        color: "var(--text-tertiary)", marginBottom: 12, lineHeight: 1.75,
        padding: "10px 12px", background: "var(--bg-surface)",
        borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)",
      }}>
        支持 SCP / 后室 / 战锤40K 等众创世界观；多个条目可直接拼接，原文超过
        <strong style={{ color: "var(--text-secondary)" }}> 12000 字</strong> 时自动按段落分段，
        每段在「特征提取」tab 独立处理。
      </div>
      <textarea className="input" value={text}
                onChange={e => onChange(e.target.value)} rows={20}
                placeholder="粘贴 wiki 原文..."
                style={{
                  width: "100%", boxSizing: "border-box",
                  fontSize: 13, lineHeight: 1.7,
                  fontFamily: "var(--font-mono)",
                }} />
      <div className="flex items-center" style={{ gap: 12, marginTop: 14 }}>
        <span className="text-xs" style={{
          color: dirty ? "var(--gold)" : "var(--text-tertiary)",
          fontFamily: "var(--font-mono)",
        }}>
          {text.length.toLocaleString()} 字{dirty ? " · 未保存" : ""}
        </span>
        <div style={{ flex: 1 }} />
        <button className="btn"
                disabled={saving || !dirty} onClick={onSave}>
          {saving ? "保存中..." : "保存原文"}
        </button>
        <button className="btn-primary"
                disabled={saving || !text.trim()} onClick={onSaveAndExtract}>
          保存并去特征提取 →
        </button>
      </div>
    </TabCard>
  );
}

/* ───────────────────────── Extract tab ────────────────────────────── */

function ExtractTab({
  refId, existing, onCommitted, onGoToTab,
}: {
  refId: string;
  existing: {
    settings: SettingEntry[];
    characters: StaticCharacter[];
    features: SettingFeature[];
  };
  onCommitted: () => void | Promise<void>;
  onGoToTab: (t: PureSettingTab) => void;
}) {
  const { toast } = useToast();
  const [plan, setPlan] = useState<SegmentPlan | null>(null);
  const [planLoading, setPlanLoading] = useState(false);
  const [chunkState, setChunkState] = useState<Record<number, ChunkState>>({});
  const [openChunks, setOpenChunks] = useState<Set<number>>(new Set());
  const [bulkRunning, setBulkRunning] = useState(false);
  const [bulkProgress, setBulkProgress] = useState<{ done: number; total: number } | null>(null);
  const [committing, setCommitting] = useState<{ idx: number; section: string } | null>(null);

  const loadPlan = useCallback(async () => {
    setPlanLoading(true);
    try {
      const r = await apiGet<SegmentPlan & { ref_id: string }>(
        `/api/references/works/${refId}/pure-setting/segments`,
      );
      setPlan(r);
      if (r.chunks.length > 0 && openChunks.size === 0) {
        setOpenChunks(new Set([r.chunks[0].chunk_index]));
      }
    } catch (e: any) {
      toast(e?.message || "加载分段失败", "error");
    } finally {
      setPlanLoading(false);
    }
    // openChunks intentionally excluded — we only want to default-open on first load
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refId, toast]);

  useEffect(() => { loadPlan(); }, [loadPlan, existing.settings.length, existing.characters.length]);

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
    setBulkProgress({ done: 0, total: plan.chunks.length });
    try {
      let done = 0;
      for (const c of plan.chunks) {
        if (chunkState[c.chunk_index]?.preview) {
          done++;
          setBulkProgress({ done, total: plan.chunks.length });
          continue;
        }
        await runApi(c.chunk_index);
        done++;
        setBulkProgress({ done, total: plan.chunks.length });
      }
      toast("批量 API 提取完成", "success");
    } finally {
      setBulkRunning(false);
      setBulkProgress(null);
    }
  };

  if (planLoading || !plan) {
    return (
      <TabCard title="特征提取">
        <div className="text-xs text-muted" style={{ padding: 16, textAlign: "center" }}>
          {planLoading ? "加载分段计划中…" : "加载中…"}
        </div>
      </TabCard>
    );
  }

  if (!plan.can_extract) {
    return (
      <TabCard title="特征提取" subtitle="所有三类输入都为空">
        <EmptyHero
          title="还没有可提取的内容"
          message="请到「快捷输入」粘贴 wiki 原文，或在「设定」「角色」tab 手动新增条目。三类输入只要任一非空即可提取。"
          actions={(
            <>
              <button className="btn" onClick={() => onGoToTab("quick")}>
                去「快捷输入」
              </button>
              <button className="btn" onClick={() => onGoToTab("settings")}>
                去「设定」
              </button>
              <button className="btn" onClick={() => onGoToTab("characters")}>
                去「角色」
              </button>
            </>
          )}
        />
      </TabCard>
    );
  }

  return (
    <TabCard
      title="特征提取"
      subtitle={`${plan.total_chunks} 段 · 每段独立 prompt · 含已有 ${plan.existing_settings_count} 条设定 + ${plan.existing_characters_count} 位角色作为去重上下文`}
      headerAction={
        plan.total_chunks > 1 ? (
          <button className="btn-primary"
                  disabled={bulkRunning}
                  onClick={runAll}
                  title="对每段调用大模型 API；已就绪的段跳过">
            {bulkRunning && bulkProgress
              ? `批量提取 ${bulkProgress.done}/${bulkProgress.total}`
              : "API 一键处理全部分段"}
          </button>
        ) : null
      }
    >
      {/* 输入材料汇总 */}
      <SourceSummary
        chars={plan.total_chars}
        chunks={plan.total_chunks}
        existingSettings={plan.existing_settings_count}
        existingCharacters={plan.existing_characters_count}
      />

      {/* 说明 */}
      <div className="text-xs" style={{
        color: "var(--text-tertiary)",
        margin: "12px 0", lineHeight: 1.75,
      }}>
        每段提供 <strong style={{ color: "var(--accent)" }}>大模型 API</strong>（设置中配置好模型后直接调用）
        与 <strong style={{ color: "var(--accent)" }}>大模型网页版</strong>
        （复制 prompt → 在网页 LLM 运行 → 粘贴 JSON 由系统解析）两种模式。
        每段 prompt 包含本段 wiki 原文 + 已有设定 + 已有角色，结果先入预览，
        确认后逐板块入库。
      </div>

      <div className="flex flex-col gap-8">
        {plan.chunks.map(c => (
          <ChunkRow
            key={c.chunk_index}
            refId={refId}
            chunk={c}
            state={chunkState[c.chunk_index] || { phase: "idle" }}
            open={openChunks.has(c.chunk_index)}
            onToggle={() => toggleChunk(c.chunk_index)}
            onModeChange={(mode) => patchChunk(c.chunk_index, { mode })}
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
    </TabCard>
  );
}

function SourceSummary({
  chars, chunks, existingSettings, existingCharacters,
}: {
  chars: number; chunks: number;
  existingSettings: number; existingCharacters: number;
}) {
  const items = [
    { label: "快捷输入", value: chars > 0 ? `${chars.toLocaleString()} 字` : "—",
      sub: chars > 0 ? `${chunks} 段` : "未填写",
      color: chars > 0 ? "var(--accent)" : "var(--text-tertiary)" },
    { label: "已有设定", value: existingSettings.toLocaleString(),
      sub: "去重上下文",
      color: existingSettings > 0 ? "var(--jade)" : "var(--text-tertiary)" },
    { label: "已有角色", value: existingCharacters.toLocaleString(),
      sub: "去重上下文",
      color: existingCharacters > 0 ? "var(--jade)" : "var(--text-tertiary)" },
  ];
  return (
    <div style={{
      display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10,
    }}>
      {items.map((it, i) => (
        <div key={i} style={{
          background: "var(--bg-surface)",
          border: "1px solid var(--border)",
          borderRadius: "var(--radius-sm)",
          padding: "10px 12px",
        }}>
          <div className="text-xs" style={{
            color: "var(--text-tertiary)", marginBottom: 4,
          }}>{it.label}</div>
          <div style={{
            fontFamily: "var(--font-mono)",
            fontSize: 18, fontWeight: 700, color: it.color, lineHeight: 1.2,
          }}>{it.value}</div>
          <div className="text-xs" style={{
            color: "var(--text-tertiary)", marginTop: 2,
          }}>{it.sub}</div>
        </div>
      ))}
    </div>
  );
}

function ChunkRow({
  refId, chunk, state, open,
  onToggle, onModeChange,
  onRunApi, onPasteChange, onParsePaste, onReset, onCommitSection,
  committing,
}: {
  refId: string;
  chunk: ChunkMeta;
  state: ChunkState;
  open: boolean;
  onToggle: () => void;
  onModeChange: (m: "api" | "web") => void;
  onRunApi: () => void;
  onPasteChange: (v: string) => void;
  onParsePaste: () => void;
  onReset: () => void;
  onCommitSection: (section: "settings" | "characters" | "setting_features") => void;
  committing?: string;
}) {
  const mode = state.mode || "api";
  const running = state.phase === "running";
  const ready = state.phase === "ready";
  const failed = state.phase === "failed";
  const p = state.preview;
  const accentColor = ready ? "var(--accent)"
    : failed ? "var(--error)"
    : "var(--border)";
  return (
    <div style={{
      border: `1px solid ${accentColor}`,
      borderRadius: "var(--radius-sm)",
      background: "var(--bg-card)",
      overflow: "hidden",
      transition: "border-color 0.15s",
    }}>
      <button
        className="btn-ghost w-full"
        onClick={onToggle}
        style={{
          display: "flex", alignItems: "center", gap: 10,
          padding: "10px 14px", textAlign: "left",
          justifyContent: "flex-start", borderRadius: 0,
          background: open ? "var(--bg-surface)" : "transparent",
        }}>
        <span style={{
          transition: "transform 0.15s",
          transform: open ? "rotate(90deg)" : "none",
          display: "inline-block", fontSize: 10, color: "var(--text-tertiary)",
          flexShrink: 0,
        }}>▶</span>
        <span style={{
          fontFamily: "var(--font-mono)",
          fontSize: 12, fontWeight: 700, color: "var(--text-primary)",
          padding: "2px 8px", borderRadius: 4,
          background: "var(--bg-surface-2)", flexShrink: 0,
        }}>
          {chunk.chunk_index + 1}/{chunk.total_chunks}
        </span>
        <span className="text-xs" style={{ color: "var(--text-secondary)", flexShrink: 0 }}>
          {chunk.n_chars.toLocaleString()} 字
        </span>
        <span className="text-xs truncate" style={{
          flex: 1, color: "var(--text-tertiary)", opacity: 0.85,
        }}>
          {chunk.preview}
        </span>
        {running && (
          <span className="tag" style={{
            fontSize: 11, padding: "2px 8px", flexShrink: 0,
            color: "var(--gold)", border: "1px solid var(--gold)",
          }}>处理中…</span>
        )}
        {ready && (
          <span className="tag" style={{
            fontSize: 11, padding: "2px 8px", flexShrink: 0,
            color: "var(--accent)", border: "1px solid var(--accent)",
          }}>{state.source === "paste" ? "网页版已解析" : "API 已生成"}</span>
        )}
        {failed && (
          <span className="tag" style={{
            fontSize: 11, padding: "2px 8px", flexShrink: 0,
            color: "var(--error)", border: "1px solid var(--error)",
          }}>失败</span>
        )}
      </button>

      {open && (
        <div style={{
          padding: "14px 16px",
          borderTop: "1px solid var(--border)",
        }}>
          {running && (
            <div style={{ marginBottom: 12 }}>
              <div className="text-xs" style={{
                color: "var(--accent)", marginBottom: 6, fontWeight: 600,
              }}>
                {state.source === "paste" ? "正在解析网页返回结果…" : "大模型 API 提取中…"}
              </div>
              <div className="ink-indeterminate" />
            </div>
          )}

          {p ? (
            <>
              <PreviewBlock
                title="设定条目"
                count={p.settings.length}
                accent="var(--accent)"
                onCommit={p.settings.length > 0 ? () => onCommitSection("settings") : undefined}
                committing={committing === "settings"}
              >
                {p.settings.length === 0
                  ? <EmptyLine>未提取到新设定</EmptyLine>
                  : p.settings.map((s, i) => (
                    <div key={i} style={previewRow}>
                      <CategoryChip category={s.category || "其他"} />
                      <span style={{
                        fontWeight: 600, color: "var(--text-primary)",
                        flexShrink: 0,
                      }}>{s.title}</span>
                      <span style={{
                        color: "var(--text-secondary)", flex: 1, minWidth: 0,
                      }}>{s.content}</span>
                    </div>
                  ))}
              </PreviewBlock>
              <PreviewBlock
                title="角色"
                count={p.characters.length}
                accent="var(--indigo)"
                onCommit={p.characters.length > 0 ? () => onCommitSection("characters") : undefined}
                committing={committing === "characters"}
              >
                {p.characters.length === 0
                  ? <EmptyLine>未提取到新角色</EmptyLine>
                  : p.characters.map((c, i) => (
                    <div key={i} style={previewRow}>
                      <span style={{
                        fontWeight: 700, color: "var(--text-primary)",
                        flexShrink: 0,
                      }}>{c.name}</span>
                      {c.role && (
                        <span style={{
                          fontSize: 11, padding: "2px 8px", borderRadius: 4,
                          background: "var(--bg-surface-2)",
                          color: "var(--text-secondary)", flexShrink: 0,
                        }}>{c.role}</span>
                      )}
                      <span style={{
                        color: "var(--text-secondary)", flex: 1, minWidth: 0,
                      }}>{c.description}</span>
                    </div>
                  ))}
              </PreviewBlock>
              <PreviewBlock
                title="设定特征"
                count={p.setting_features.length}
                accent="var(--purple)"
                onCommit={p.setting_features.length > 0 ? () => onCommitSection("setting_features") : undefined}
                committing={committing === "setting_features"}
              >
                {p.setting_features.length === 0
                  ? <EmptyLine>未提取到新特征</EmptyLine>
                  : p.setting_features.map((f, i) => (
                    <div key={i} style={previewRow}>
                      <span style={{
                        fontWeight: 700, color: "var(--text-primary)",
                        flexShrink: 0,
                      }}>{f.title}</span>
                      <span style={{
                        color: "var(--text-secondary)", flex: 1, minWidth: 0,
                      }}>{f.description}</span>
                    </div>
                  ))}
              </PreviewBlock>
              <div className="flex" style={{
                justifyContent: "flex-end", marginTop: 12,
                paddingTop: 12, borderTop: "1px dashed var(--border)",
              }}>
                <button className="btn" onClick={onReset}
                        disabled={!!committing}>
                  重新提取
                </button>
              </div>
            </>
          ) : (
            <>
              {/* 提取模式切换 */}
              <div style={{
                display: "inline-flex",
                background: "var(--bg-surface)",
                borderRadius: "var(--radius-sm)",
                border: "1px solid var(--border)",
                padding: 3,
                marginBottom: 12,
              }}>
                {(["api", "web"] as const).map(m => (
                  <button key={m}
                          onClick={() => onModeChange(m)}
                          style={{
                            padding: "6px 14px", fontSize: 12,
                            fontWeight: 600,
                            color: mode === m ? "white" : "var(--text-secondary)",
                            background: mode === m ? "var(--accent)" : "transparent",
                            border: "none",
                            borderRadius: 4,
                            cursor: "pointer",
                            transition: "all 0.15s",
                          }}>
                    {m === "api" ? "使用大模型 API" : "使用大模型网页版"}
                  </button>
                ))}
              </div>

              {mode === "api" && (
                <>
                  <div className="text-xs" style={{
                    color: "var(--text-tertiary)", marginBottom: 10, lineHeight: 1.7,
                  }}>
                    使用 UI 设置页面里配置好的模型 API，本段连同已有设定 / 角色
                    一同发送给 LLM。
                  </div>
                  <button className="btn-primary"
                          onClick={onRunApi}
                          disabled={running}>
                    {running ? "提取中…" : "调用 API 提取本段"}
                  </button>
                  {failed && state.error && (
                    <div style={errBox}>
                      <div style={{ fontWeight: 600, marginBottom: 4 }}>提取失败</div>
                      <div style={{ wordBreak: "break-word" }}>{state.error}</div>
                    </div>
                  )}
                </>
              )}

              {mode === "web" && (
                <>
                  <div className="text-xs" style={{
                    color: "var(--text-tertiary)", marginBottom: 10, lineHeight: 1.7,
                  }}>
                    复制下方 prompt 到大模型网页版（ChatGPT / Claude.ai 等），
                    把返回的 JSON 粘贴回下方输入框，系统自动解析。
                  </div>
                  <PromptCopyPanel
                    refId={refId}
                    promptKey="reference.pure_setting"
                    segmentIndex={chunk.chunk_index}
                    chunked={false}
                    defaultOpen
                    label={`第 ${chunk.chunk_index + 1} 段的 prompt（含已有设定/角色作为去重上下文）`}
                  />
                  <textarea className="input font-mono"
                            rows={6}
                            value={state.pasteRaw || ""}
                            placeholder='粘贴 LLM 返回的 {"settings":[...],"characters":[...],"setting_features":[...]}'
                            onChange={e => onPasteChange(e.target.value)}
                            style={{
                              fontSize: 12, lineHeight: 1.6, resize: "vertical",
                              background: "var(--bg-app)", marginBottom: 10,
                            }} />
                  <div className="flex items-center" style={{ gap: 12 }}>
                    <button className="btn-primary"
                            onClick={onParsePaste}
                            disabled={!(state.pasteRaw && state.pasteRaw.trim()) || running}>
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
  addLabel, emptyHint, emptyAction,
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
  emptyAction?: React.ReactNode;
}) {
  const [draft, setDraft] = useState<T[]>(items);
  const [openIdx, setOpenIdx] = useState<Set<number>>(new Set());
  const [dirty, setDirty] = useState(false);
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
      {draft.length === 0 ? (
        <EmptyHero
          title={emptyHint}
          message="点击下方「新增」手动录入，或先到「特征提取」让 LLM 抽取。"
          actions={(
            <>
              <button className="btn-primary" onClick={add}>+ {addLabel}</button>
              {emptyAction}
            </>
          )}
        />
      ) : (
        <div className="flex flex-col gap-6">
          {draft.map((row, i) => (
            <div key={i} style={{
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-sm)",
              background: "var(--bg-card)",
              transition: "border-color 0.15s",
            }}>
              <div className="flex items-center" style={{
                gap: 8, padding: "8px 12px",
              }}>
                <button className="btn-ghost"
                        onClick={() => toggle(i)}
                        style={{
                          display: "flex", alignItems: "center", gap: 8, flex: 1,
                          padding: "2px 0", justifyContent: "flex-start",
                          borderRadius: 0, minWidth: 0, textAlign: "left",
                        }}>
                  <span style={{
                    transition: "transform 0.15s",
                    transform: openIdx.has(i) ? "rotate(90deg)" : "none",
                    display: "inline-block", fontSize: 10, color: "var(--text-tertiary)",
                    flexShrink: 0,
                  }}>▶</span>
                  <div className="flex items-center" style={{
                    gap: 8, flex: 1, minWidth: 0, fontSize: 13,
                  }}>
                    {summary(row)}
                  </div>
                </button>
                <button className="btn-icon" title="删除"
                        onClick={() => remove(i)}
                        style={{
                          width: 28, height: 28, fontSize: 16,
                          color: "var(--text-tertiary)",
                        }}>×</button>
              </div>
              {openIdx.has(i) && (
                <div style={{
                  padding: "12px 14px 14px",
                  borderTop: "1px solid var(--border)",
                  background: "var(--bg-surface)",
                  display: "flex", flexDirection: "column", gap: 10,
                }}>
                  {renderEditor(row, (next) => update(i, next))}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="flex items-center" style={{
        gap: 10, marginTop: 16, paddingTop: 14,
        borderTop: "1px solid var(--border-subtle)",
      }}>
        <button className="btn" onClick={add}>
          + {addLabel}
        </button>
        <div style={{ flex: 1 }} />
        <span className="text-xs text-muted">
          {dirty ? "有未保存的修改" : "已保存"}
        </span>
        <button className="btn-primary"
                disabled={saving || !dirty}
                onClick={() => { onSave(draft); setDirty(false); }}>
          {saving ? "保存中…" : "保存修改"}
        </button>
      </div>
    </div>
  );
}

/* ───────────────────────── Reusable bits ──────────────────────────── */

function TabCard({
  title, subtitle, headerAction, children,
}: {
  title: string;
  subtitle?: string;
  headerAction?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="card">
      <div className="card-header">
        <div>
          <h3 style={{ margin: 0 }}>{title}</h3>
          {subtitle && <p style={{ margin: "2px 0 0" }}>{subtitle}</p>}
        </div>
        {headerAction}
      </div>
      <div className="card-body">
        {children}
      </div>
    </div>
  );
}

function EmptyHero({
  title, message, actions,
}: {
  title: string;
  message: string;
  actions?: React.ReactNode;
}) {
  return (
    <div style={{
      padding: "30px 20px",
      textAlign: "center",
      background: "var(--bg-surface)",
      border: "1px dashed var(--border)",
      borderRadius: "var(--radius-sm)",
    }}>
      <div style={{
        fontSize: 14, fontWeight: 600,
        color: "var(--text-secondary)", marginBottom: 6,
      }}>{title}</div>
      <div className="text-xs" style={{
        color: "var(--text-tertiary)", lineHeight: 1.7,
        maxWidth: 420, margin: "0 auto 16px",
      }}>{message}</div>
      {actions && (
        <div className="flex items-center" style={{
          gap: 8, justifyContent: "center", flexWrap: "wrap",
        }}>{actions}</div>
      )}
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="field" style={{ margin: 0 }}>
      <label className="label" style={{
        fontSize: 11, color: "var(--text-tertiary)",
        marginBottom: 4, fontWeight: 600, textTransform: "uppercase",
        letterSpacing: 0.5,
      }}>{label}</label>
      {children}
    </div>
  );
}

function PreviewBlock({
  title, count, accent, onCommit, committing, children,
}: {
  title: string;
  count: number;
  accent: string;
  onCommit?: () => void;
  committing: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(true);
  return (
    <div style={{
      marginBottom: 10, border: "1px solid var(--border)",
      borderRadius: "var(--radius-sm)", background: "var(--bg-surface)",
      overflow: "hidden",
    }}>
      <div className="flex items-center" style={{
        gap: 8, padding: "6px 10px",
        background: "var(--bg-surface-2)",
      }}>
        <button className="btn-ghost"
                onClick={() => setOpen(o => !o)}
                style={{
                  display: "flex", alignItems: "center", gap: 6, flex: 1,
                  padding: "2px 0", justifyContent: "flex-start",
                  borderRadius: 0, fontWeight: 600,
                }}>
          <span style={{
            transition: "transform 0.15s", transform: open ? "rotate(90deg)" : "none",
            display: "inline-block", fontSize: 9, color: "var(--text-tertiary)",
          }}>▶</span>
          <span style={{ fontSize: 12, color: "var(--text-primary)" }}>
            {title}
          </span>
          <span style={{
            fontSize: 11, padding: "1px 8px", borderRadius: 10,
            background: count > 0 ? accent : "var(--bg-surface)",
            color: count > 0 ? "white" : "var(--text-tertiary)",
            fontWeight: 600, fontFamily: "var(--font-mono)",
          }}>{count}</span>
        </button>
        {onCommit && (
          <button className="btn-primary"
                  style={{ fontSize: 11, padding: "4px 12px" }}
                  disabled={committing}
                  onClick={(e) => { e.stopPropagation(); onCommit(); }}>
            {committing ? "入库中…" : "确认入库"}
          </button>
        )}
      </div>
      {open && (
        <div style={{
          padding: "8px 12px",
          maxHeight: 240, overflowY: "auto",
          display: "flex", flexDirection: "column", gap: 6,
        }}>
          {children}
        </div>
      )}
    </div>
  );
}

function CategoryChip({ category }: { category: string }) {
  const color = CATEGORY_COLORS[category] || "var(--text-tertiary)";
  return (
    <span style={{
      fontSize: 11, padding: "2px 8px", borderRadius: 4,
      background: "var(--bg-surface-2)",
      color, border: `1px solid ${color}`,
      flexShrink: 0, fontWeight: 600,
    }}>{category}</span>
  );
}

function EmptyLine({ children }: { children: React.ReactNode }) {
  return (
    <span className="text-xs" style={{
      color: "var(--text-tertiary)", fontStyle: "italic",
      padding: "4px 0",
    }}>{children}</span>
  );
}

const summaryTitle: React.CSSProperties = {
  fontWeight: 600, color: "var(--text-primary)",
  fontSize: 13, flexShrink: 0,
};

const summaryDesc: React.CSSProperties = {
  color: "var(--text-tertiary)", fontSize: 12,
  flex: 1, minWidth: 0,
};

const previewRow: React.CSSProperties = {
  display: "flex", gap: 8, alignItems: "baseline",
  padding: "6px 0", borderBottom: "1px solid var(--border-subtle)",
  fontSize: 12, lineHeight: 1.6,
};

const errBox: React.CSSProperties = {
  padding: "8px 12px", marginTop: 10,
  background: "var(--bg-surface)", border: "1px solid var(--error)",
  borderRadius: "var(--radius-sm)",
  fontSize: 12, color: "var(--error)", lineHeight: 1.6,
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
