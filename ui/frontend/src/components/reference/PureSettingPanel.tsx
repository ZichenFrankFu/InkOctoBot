import React, { useCallback, useEffect, useRef, useState } from "react";
import { apiGet, apiPost, apiPut } from "../../api/client";
import { useToast } from "../shared/Toast";
import { useDialog } from "../shared/Dialog";
import { PromptCopyPanel } from "./AnalysisEditors";

// 纯设定作品面板 (spec 2.2.2 / 6.2)：SCP、后室、战锤40K 等无创作正文的
// 众创/设定集作品。tab 由父级 ReferenceLibraryPage 统一渲染（与叙事型一致），
// 这里只负责面板内容。
//
// 满足 LLM 交互机制 1/4：
//  - 特征提取与原文翻译都支持「大模型 API」和「大模型网页版」两种模式
//  - 原文按段落自动切分，每段一个独立 prompt
//  - 特征提取 prompt 同时包含「原始文本 / 设定 / 角色」三类用户输入，
//    三者只要任一非空即可提取
// 满足 LLM 交互机制 2：提取生成的内容先进入预览，逐项确认后才入库

export type PureSettingTab = "raw" | "settings" | "characters" | "extract" | "features";

export const PURE_SETTING_TABS: { key: PureSettingTab; label: string }[] = [
  { key: "raw", label: "原始文本" },
  { key: "settings", label: "设定" },
  { key: "characters", label: "角色" },
  { key: "extract", label: "特征提取" },
  { key: "features", label: "设定特征" },
];

type SettingEntry = { category: string; title: string; content: string };
type StaticCharacter = { name: string; role: string; description: string };
type SettingFeature = { category: string; title: string; description: string };
type RawEntry = { title: string; content: string };

const SETTING_CATEGORIES = ["力量体系", "势力组织", "地理", "社会规则", "历史背景", "世界观", "其他"];

const SETTING_CAT_COLORS: Record<string, string> = {
  "力量体系": "var(--accent)",
  "势力组织": "var(--indigo)",
  "地理": "var(--jade)",
  "社会规则": "var(--cyan)",
  "历史背景": "var(--gold)",
  "世界观": "var(--purple)",
  "其他": "var(--text-tertiary)",
};

const FEATURE_CATEGORIES = ["核心冲突", "高概念", "母题"] as const;
type FeatureCategory = typeof FEATURE_CATEGORIES[number];

const FEATURE_CAT_META: Record<FeatureCategory, {
  color: string; label: string; intro: string; example: string;
}> = {
  "核心冲突": {
    color: "var(--accent)",
    label: "核心冲突",
    intro: "世界观层面持续推动剧情的根本对立——可识别的张力对子。",
    example: "如「秩序与失序」「文明与异常」「神性与凡人」",
  },
  "高概念": {
    color: "var(--gold)",
    label: "高概念",
    intro: "作品被一句话能讲清的世界观底座，具备「钩子」属性。",
    example: "如「混凝土雕像未被注视时高速移动」「太空大航海」",
  },
  "母题": {
    color: "var(--purple)",
    label: "母题",
    intro: "贯穿作品的反复出现的意象 / 情绪 / 主题，不必是冲突。",
    example: "如「黑色幽默」「不可见者掌权」「记录癖」",
  },
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
  entries_count: number;
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
  const [rawEntries, setRawEntries] = useState<RawEntry[]>([]);
  const [settings, setSettings] = useState<SettingEntry[]>([]);
  const [characters, setCharacters] = useState<StaticCharacter[]>([]);
  const [features, setFeatures] = useState<SettingFeature[]>([]);
  const [saving, setSaving] = useState(false);
  const [loaded, setLoaded] = useState(false);

  const reload = useCallback(async () => {
    try {
      const r = await apiGet<any>(`/api/references/works/${refId}/pure-setting`);
      setRawEntries(r.raw_entries || []);
      setSettings(r.settings || []);
      setCharacters(r.static_characters || []);
      // Normalize features without category to 高概念
      setFeatures((r.setting_features || []).map((f: any) => ({
        category: (FEATURE_CATEGORIES as readonly string[]).includes(f.category)
          ? f.category : "高概念",
        title: f.title || "",
        description: f.description || "",
      })));
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
      {tab === "raw" && loaded && (
        <RawTextTab
          refId={refId}
          entries={rawEntries}
          saving={saving}
          onSave={items => put({ raw_entries: items }, "原始文本已保存")}
        />
      )}

      {tab === "settings" && (
        <TabCard
          title="设定条目"
          subtitle={`${settings.length} 条 · 折叠条目点击展开编辑`}
        >
          <CollapsibleList<SettingEntry>
            items={settings}
            summary={(s) => (
              <>
                <CategoryChip
                  category={s.category || "其他"}
                  colorMap={SETTING_CAT_COLORS}
                />
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
                    {SETTING_CATEGORIES.map(o => <option key={o} value={o}>{o}</option>)}
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
            emptyExtraAction={(
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
            emptyExtraAction={(
              <button className="btn" onClick={() => onTabChange("extract")}>
                去「特征提取」由 LLM 抽取
              </button>
            )}
          />
        </TabCard>
      )}

      {tab === "features" && (
        <FeaturesTab
          features={features}
          saving={saving}
          onSave={items => put({ setting_features: items }, "设定特征已保存")}
          onGoToExtract={() => onTabChange("extract")}
        />
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

/* ───────────────────────── Raw text tab ───────────────────────────── */

function RawTextTab({
  refId, entries, saving, onSave,
}: {
  refId: string;
  entries: RawEntry[];
  saving: boolean;
  onSave: (items: RawEntry[]) => void;
}) {
  const { toast } = useToast();
  const { confirm } = useDialog();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [translating, setTranslating] = useState<number | null>(null);
  const [translateModal, setTranslateModal] = useState<{
    entryIndex: number;
    mode: "api" | "web";
    raw?: string;
    parseError?: string;
  } | null>(null);
  const [importMode, setImportMode] = useState<"merge" | "replace">("merge");

  // Save handler for individual entry translate
  const applyTranslation = async (entryIndex: number, translated: string) => {
    const next = entries.slice();
    if (entryIndex < 0 || entryIndex >= next.length) return;
    next[entryIndex] = { ...next[entryIndex], content: translated };
    await onSaveImmediate(next);
  };

  // Save without going through the list's draft state (used by translate
  // / import flows where the result should land immediately)
  const onSaveImmediate = (items: RawEntry[]) =>
    new Promise<void>((resolve) => {
      onSave(items);
      // best-effort — onSave is fire-and-forget upstream
      setTimeout(resolve, 0);
    });

  const runTranslateApi = async (entryIndex: number) => {
    if (!entries[entryIndex]?.content?.trim()) {
      toast("条目内容为空，无法翻译", "info");
      return;
    }
    setTranslating(entryIndex);
    try {
      const r = await apiPost<{ translated: string }>(
        `/api/references/works/${refId}/pure-setting/translate`,
        { entry_index: entryIndex },
        { timeoutMs: 600_000 },
      );
      const ok = await confirm({
        title: "翻译已完成",
        message: "确认用译文替换原内容吗？此操作可通过编辑撤回。",
      });
      if (ok) {
        await applyTranslation(entryIndex, r.translated);
        toast("译文已应用", "success");
      }
    } catch (e: any) {
      toast(e?.message || "翻译失败", "error");
    } finally {
      setTranslating(null);
    }
  };

  const applyPastedTranslation = async () => {
    if (!translateModal) return;
    const raw = (translateModal.raw || "").trim();
    if (!raw) {
      setTranslateModal({ ...translateModal, parseError: "请先粘贴译文" });
      return;
    }
    await applyTranslation(translateModal.entryIndex, raw);
    setTranslateModal(null);
    toast("译文已应用", "success");
  };

  const exportTxt = () => {
    // Download the export TXT via a hidden link.
    const url = `/api/references/works/${refId}/pure-setting/export.txt`;
    const a = document.createElement("a");
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  };

  const onImportFile = async (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    try {
      const resp = await fetch(
        `/api/references/works/${refId}/pure-setting/import?mode=${importMode}`,
        { method: "POST", body: fd },
      );
      if (!resp.ok) throw new Error(await resp.text() || resp.statusText);
      const r = await resp.json();
      toast(
        `导入成功（模式：${importMode === "replace" ? "替换" : "合并"}）：` +
        `原文 ${r.imported_entries} · 设定 ${r.imported_settings} · ` +
        `角色 ${r.imported_characters} · 特征 ${r.imported_features}`,
        "success",
      );
      // refresh by no-op save → reload via parent
      window.location.reload();
    } catch (e: any) {
      toast(e?.message || "导入失败", "error");
    }
  };

  return (
    <TabCard
      title="原始文本"
      subtitle={`${entries.length} 个条目 · 多个条目可分开管理；保存后可展开/收起查看`}
      headerAction={(
        <div className="flex" style={{ gap: 8 }}>
          <button className="btn" onClick={exportTxt}
                  title="导出当前作品所有内容为 TXT 文件">
            导出 TXT
          </button>
          <label className="btn" style={{ cursor: "pointer", marginRight: 0 }}>
            导入 TXT
            <input
              ref={fileInputRef}
              type="file" accept=".txt,text/plain"
              style={{ display: "none" }}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) onImportFile(f);
                e.target.value = "";
              }} />
          </label>
          <select className="select" value={importMode}
                  onChange={e => setImportMode(e.target.value as any)}
                  style={{ fontSize: 12, width: "auto" }}
                  title="导入模式">
            <option value="merge">合并</option>
            <option value="replace">替换</option>
          </select>
        </div>
      )}
    >
      <div className="text-xs" style={{
        color: "var(--text-tertiary)", marginBottom: 12, lineHeight: 1.75,
        padding: "10px 12px", background: "var(--bg-surface)",
        borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)",
      }}>
        每个条目独立标题 + 正文，可以是一个 SCP 编号、一个房间、一个种族等。
        条目越独立，提取效果越好。每段正文超过
        <strong style={{ color: "var(--text-secondary)" }}> 12000 字</strong>
        时在「特征提取」自动按段落分段。条目正文支持
        <strong style={{ color: "var(--text-secondary)" }}>原文翻译</strong>，
        可使用 LLM API 或网页版完成。
      </div>

      <CollapsibleList<RawEntry>
        items={entries}
        summary={(e, i) => (
          <>
            <span style={{
              fontFamily: "var(--font-mono)",
              fontSize: 11, padding: "2px 8px", borderRadius: 4,
              background: "var(--bg-surface-2)",
              color: "var(--text-secondary)",
              flexShrink: 0, fontWeight: 600,
            }}>#{i + 1}</span>
            <span style={summaryTitle}>
              {e.title || "（未命名条目）"}
            </span>
            <span style={{
              ...summaryDesc, fontFamily: "var(--font-mono)",
              fontSize: 11,
            }}>
              {e.content.length.toLocaleString()} 字
            </span>
            {e.content && (
              <span style={summaryDesc} className="truncate">
                {e.content.slice(0, 60)}{e.content.length > 60 ? "…" : ""}
              </span>
            )}
          </>
        )}
        renderEditor={(item, set, idx) => (
          <>
            <Field label="标题">
              <input className="input" value={item.title}
                     placeholder="如 SCP-173 / 0号层级 / 阿斯塔特"
                     onChange={e => set({ ...item, title: e.target.value })} />
            </Field>
            <Field label={`正文（${item.content.length.toLocaleString()} 字）`}>
              <textarea className="input" value={item.content} rows={14}
                        placeholder="粘贴本条目的 wiki 原文..."
                        onChange={e => set({ ...item, content: e.target.value })}
                        style={{
                          fontFamily: "var(--font-mono)", fontSize: 12,
                          lineHeight: 1.65,
                        }} />
            </Field>
            <div className="flex items-center" style={{
              gap: 8, paddingTop: 4,
            }}>
              <button className="btn"
                      onClick={() => runTranslateApi(idx)}
                      disabled={translating === idx || !item.content.trim()}>
                {translating === idx ? "翻译中…" : "原文翻译 (API)"}
              </button>
              <button className="btn"
                      onClick={() => setTranslateModal({
                        entryIndex: idx, mode: "web",
                      })}
                      disabled={!item.content.trim()}>
                原文翻译 (网页版)
              </button>
              <span className="text-xs text-muted" style={{ marginLeft: 4 }}>
                翻译完成后会让你确认是否替换正文
              </span>
            </div>
          </>
        )}
        blank={{ title: "", content: "" }}
        onSave={onSave}
        saving={saving}
        addLabel="新增条目"
        emptyHint="暂无原始文本条目"
        defaultOpen
      />

      {translateModal && (
        <TranslateWebModal
          refId={refId}
          state={translateModal}
          onChange={setTranslateModal}
          onApply={applyPastedTranslation}
          onClose={() => setTranslateModal(null)}
        />
      )}
    </TabCard>
  );
}

function TranslateWebModal({
  refId, state, onChange, onApply, onClose,
}: {
  refId: string;
  state: { entryIndex: number; mode: "api" | "web"; raw?: string; parseError?: string };
  onChange: (s: typeof state) => void;
  onApply: () => void;
  onClose: () => void;
}) {
  return (
    <>
      <div className="side-panel-overlay" onClick={onClose} />
      <div style={{
        position: "fixed", inset: 0, zIndex: 100,
        display: "flex", alignItems: "center", justifyContent: "center",
        pointerEvents: "none",
      }}>
        <div className="card" style={{
          width: 720, maxHeight: "85vh", overflow: "auto",
          pointerEvents: "auto", boxShadow: "var(--shadow-lg)",
        }}>
          <div className="card-header">
            <h3>使用大模型网页版翻译 · 条目 #{state.entryIndex + 1}</h3>
            <button className="btn-icon" onClick={onClose}>×</button>
          </div>
          <div className="card-body">
            <div className="text-xs" style={{
              color: "var(--text-tertiary)", marginBottom: 12, lineHeight: 1.7,
            }}>
              复制下方 prompt 到大模型网页版（ChatGPT / Claude.ai 等），
              把返回的译文粘贴到下方输入框，点击「应用译文」会让你确认替换原内容。
            </div>
            <PromptCopyPanel
              refId={refId}
              promptKey="reference.pure_setting_translate"
              segmentIndex={state.entryIndex}
              chunked={false}
              defaultOpen
              label={`条目 #${state.entryIndex + 1} 的翻译 prompt`}
            />
            <Field label="粘贴 LLM 返回的译文">
              <textarea className="input" rows={10}
                        value={state.raw || ""}
                        placeholder="译文..."
                        onChange={e => onChange({ ...state, raw: e.target.value, parseError: undefined })}
                        style={{
                          fontFamily: "var(--font-mono)", fontSize: 12,
                          lineHeight: 1.65,
                        }} />
            </Field>
            {state.parseError && (
              <div className="text-xs" style={{ color: "var(--error)", marginTop: 6 }}>
                {state.parseError}
              </div>
            )}
            <div className="flex" style={{
              gap: 8, marginTop: 16, justifyContent: "flex-end",
            }}>
              <button className="btn" onClick={onClose}>取消</button>
              <button className="btn-primary" onClick={onApply}
                      disabled={!state.raw?.trim()}>
                应用译文
              </button>
            </div>
          </div>
        </div>
      </div>
    </>
  );
}

/* ───────────────────────── Features tab ───────────────────────────── */

function FeaturesTab({
  features, saving, onSave, onGoToExtract,
}: {
  features: SettingFeature[];
  saving: boolean;
  onSave: (items: SettingFeature[]) => void;
  onGoToExtract: () => void;
}) {
  const [draft, setDraft] = useState<SettingFeature[]>(features);
  const [dirty, setDirty] = useState(false);
  const [openIdx, setOpenIdx] = useState<Set<number>>(new Set());
  useEffect(() => { if (!dirty) setDraft(features); }, [features, dirty]);

  const grouped: Record<FeatureCategory, { item: SettingFeature; idx: number }[]> = {
    "核心冲突": [], "高概念": [], "母题": [],
  };
  draft.forEach((f, idx) => {
    const cat = (FEATURE_CATEGORIES as readonly string[]).includes(f.category)
      ? (f.category as FeatureCategory) : "高概念";
    grouped[cat].push({ item: f, idx });
  });

  const update = (i: number, next: SettingFeature) => {
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
  const add = (cat: FeatureCategory) => {
    const newItem: SettingFeature = { category: cat, title: "", description: "" };
    setDraft(prev => [...prev, newItem]);
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
    <TabCard
      title="设定特征"
      subtitle="作品级世界观特征 · 分为核心冲突 / 高概念 / 母题 三类"
    >
      <div className="text-xs" style={{
        color: "var(--text-tertiary)", marginBottom: 16, lineHeight: 1.8,
        padding: "12px 14px",
        background: "var(--bg-surface)",
        borderRadius: "var(--radius-sm)",
        border: "1px solid var(--border-subtle)",
      }}>
        本 tab 收录的是<strong style={{ color: "var(--text-primary)" }}>整部作品级</strong>的
        世界观特征。三类一起回答：这部作品在<strong style={{ color: "var(--text-primary)" }}>冲什么、
        亮点是什么、味道是什么</strong>。新增条目时请先选好分类；也可以到
        <strong style={{ color: "var(--accent)" }}>「特征提取」tab</strong>由 LLM 综合
        原始文本 + 设定 + 角色 一并抽取。
      </div>

      <div className="flex flex-col gap-12">
        {FEATURE_CATEGORIES.map(cat => {
          const meta = FEATURE_CAT_META[cat];
          const items = grouped[cat];
          return (
            <div key={cat} style={{
              border: `1px solid var(--border)`,
              borderLeft: `3px solid ${meta.color}`,
              borderRadius: "var(--radius-sm)",
              background: "var(--bg-card)",
              overflow: "hidden",
            }}>
              <div style={{
                padding: "10px 14px 8px",
                background: "var(--bg-surface)",
                borderBottom: "1px solid var(--border)",
              }}>
                <div className="flex items-center" style={{ gap: 8, marginBottom: 4 }}>
                  <span style={{
                    fontSize: 14, fontWeight: 700, color: meta.color,
                    fontFamily: "var(--font-serif)",
                  }}>{meta.label}</span>
                  <span className="tag" style={{
                    fontSize: 11, padding: "1px 8px",
                    color: items.length > 0 ? meta.color : "var(--text-tertiary)",
                    border: `1px solid ${items.length > 0 ? meta.color : "var(--border)"}`,
                    fontFamily: "var(--font-mono)",
                  }}>{items.length}</span>
                  <div style={{ flex: 1 }} />
                  <button className="btn"
                          onClick={() => add(cat)}
                          style={{ fontSize: 11, padding: "4px 10px" }}>
                    + 新增 {meta.label}
                  </button>
                </div>
                <div className="text-xs" style={{
                  color: "var(--text-tertiary)", lineHeight: 1.65,
                }}>
                  {meta.intro}
                  <span style={{
                    color: "var(--text-secondary)", marginLeft: 6, fontStyle: "italic",
                  }}>{meta.example}</span>
                </div>
              </div>
              <div style={{ padding: items.length === 0 ? "16px" : "8px 12px 12px" }}>
                {items.length === 0 ? (
                  <div className="text-xs" style={{
                    color: "var(--text-tertiary)", textAlign: "center",
                    fontStyle: "italic", padding: "8px 0",
                  }}>
                    暂无{meta.label}条目
                  </div>
                ) : (
                  <div className="flex flex-col gap-6">
                    {items.map(({ item, idx }) => (
                      <div key={idx} style={{
                        border: "1px solid var(--border)",
                        borderRadius: "var(--radius-sm)",
                        background: "var(--bg-surface)",
                      }}>
                        <div className="flex items-center" style={{
                          gap: 8, padding: "8px 12px",
                        }}>
                          <button className="btn-ghost"
                                  onClick={() => toggle(idx)}
                                  style={{
                                    display: "flex", alignItems: "center", gap: 8,
                                    flex: 1, padding: "2px 0", borderRadius: 0,
                                    minWidth: 0, textAlign: "left",
                                    justifyContent: "flex-start",
                                  }}>
                            <span style={{
                              transition: "transform 0.15s",
                              transform: openIdx.has(idx) ? "rotate(90deg)" : "none",
                              display: "inline-block", fontSize: 10,
                              color: "var(--text-tertiary)", flexShrink: 0,
                            }}>▶</span>
                            <span style={{
                              fontSize: 13, fontWeight: 600,
                              color: "var(--text-primary)", flexShrink: 0,
                            }}>{item.title || "（未命名）"}</span>
                            {item.description && (
                              <span style={summaryDesc} className="truncate">
                                {item.description}
                              </span>
                            )}
                          </button>
                          <button className="btn-icon" title="删除"
                                  onClick={() => remove(idx)}
                                  style={{ width: 28, height: 28, fontSize: 16 }}>×</button>
                        </div>
                        {openIdx.has(idx) && (
                          <div style={{
                            padding: "12px 14px 14px",
                            borderTop: "1px solid var(--border)",
                            display: "flex", flexDirection: "column", gap: 10,
                          }}>
                            <Field label="分类">
                              <select className="select" value={item.category}
                                      onChange={e => update(idx, { ...item, category: e.target.value })}>
                                {FEATURE_CATEGORIES.map(o => <option key={o} value={o}>{o}</option>)}
                              </select>
                            </Field>
                            <Field label="标题">
                              <input className="input" value={item.title}
                                     placeholder={meta.example.replace("如 ", "")}
                                     onChange={e => update(idx, { ...item, title: e.target.value })} />
                            </Field>
                            <Field label="一句话解释">
                              <textarea className="input" value={item.description} rows={3}
                                        onChange={e => update(idx, { ...item, description: e.target.value })}
                                        style={{ lineHeight: 1.65 }} />
                            </Field>
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>

      <div className="flex items-center" style={{
        gap: 10, marginTop: 18, paddingTop: 14,
        borderTop: "1px solid var(--border-subtle)",
      }}>
        <button className="btn" onClick={onGoToExtract}>
          去「特征提取」由 LLM 综合分析
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
          (f) => `${f.category}::${f.title}`,
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
          message="请到「原始文本」粘贴 wiki 原文，或在「设定」「角色」tab 手动新增条目。三类输入只要任一非空即可提取。"
          actions={(
            <>
              <button className="btn" onClick={() => onGoToTab("raw")}>
                去「原始文本」
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
      <SourceSummary
        chars={plan.total_chars}
        chunks={plan.total_chunks}
        entries={plan.entries_count}
        existingSettings={plan.existing_settings_count}
        existingCharacters={plan.existing_characters_count}
      />

      <div className="text-xs" style={{
        color: "var(--text-tertiary)",
        margin: "12px 0", lineHeight: 1.75,
      }}>
        每段提供 <strong style={{ color: "var(--accent)" }}>大模型 API</strong>（设置中配置好模型后直接调用）
        与 <strong style={{ color: "var(--accent)" }}>大模型网页版</strong>
        （复制 prompt → 在网页 LLM 运行 → 粘贴 JSON 由系统解析）两种模式。
        每段 prompt 包含本段原文 + 已有设定 + 已有角色，
        <strong style={{ color: "var(--text-secondary)" }}>设定特征会按核心冲突 / 高概念 / 母题三类输出</strong>。
        结果先入预览，确认后逐板块入库。
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
  chars, chunks, entries, existingSettings, existingCharacters,
}: {
  chars: number; chunks: number; entries: number;
  existingSettings: number; existingCharacters: number;
}) {
  const items = [
    { label: "原始文本",
      value: entries > 0 ? `${entries} 条 · ${chars.toLocaleString()} 字` : "—",
      sub: entries > 0 ? `${chunks} 段` : "未填写",
      color: entries > 0 ? "var(--accent)" : "var(--text-tertiary)" },
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
            fontSize: 16, fontWeight: 700, color: it.color, lineHeight: 1.3,
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
                      <CategoryChip category={s.category || "其他"} colorMap={SETTING_CAT_COLORS} />
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
                title="设定特征（核心冲突 / 高概念 / 母题）"
                count={p.setting_features.length}
                accent="var(--purple)"
                onCommit={p.setting_features.length > 0 ? () => onCommitSection("setting_features") : undefined}
                committing={committing === "setting_features"}
              >
                {p.setting_features.length === 0
                  ? <EmptyLine>未提取到新特征</EmptyLine>
                  : (() => {
                      const grouped: Record<string, SettingFeature[]> = {};
                      p.setting_features.forEach(f => {
                        const k = f.category || "高概念";
                        (grouped[k] = grouped[k] || []).push(f);
                      });
                      return FEATURE_CATEGORIES.flatMap(cat => {
                        const items = grouped[cat];
                        if (!items?.length) return [];
                        const meta = FEATURE_CAT_META[cat];
                        return items.map((f, i) => (
                          <div key={`${cat}-${i}`} style={previewRow}>
                            <span style={{
                              fontSize: 11, padding: "2px 8px", borderRadius: 4,
                              color: meta.color,
                              border: `1px solid ${meta.color}`,
                              background: "var(--bg-surface-2)",
                              flexShrink: 0, fontWeight: 600,
                            }}>{meta.label}</span>
                            <span style={{
                              fontWeight: 700, color: "var(--text-primary)",
                              flexShrink: 0,
                            }}>{f.title}</span>
                            <span style={{
                              color: "var(--text-secondary)", flex: 1, minWidth: 0,
                            }}>{f.description}</span>
                          </div>
                        ));
                      });
                    })()
                }
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
  items, summary, renderEditor, blank, onSave, saving,
  addLabel, emptyHint, emptyExtraAction, defaultOpen,
}: {
  items: T[];
  summary: (item: T, index: number) => React.ReactNode;
  renderEditor: (item: T, set: (next: T) => void, index: number) => React.ReactNode;
  blank: T;
  onSave: (items: T[]) => void;
  saving: boolean;
  addLabel: string;
  emptyHint: string;
  emptyExtraAction?: React.ReactNode;
  defaultOpen?: boolean;
}) {
  const [draft, setDraft] = useState<T[]>(items);
  const [openIdx, setOpenIdx] = useState<Set<number>>(
    defaultOpen && items.length > 0 ? new Set([0]) : new Set(),
  );
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
          message="点击下方「新增」手动录入条目。"
          actions={(
            <>
              <button className="btn-primary" onClick={add}>+ {addLabel}</button>
              {emptyExtraAction}
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
                    {summary(row, i)}
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
                  {renderEditor(row, (next) => update(i, next), i)}
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

function CategoryChip({ category, colorMap }: {
  category: string;
  colorMap: Record<string, string>;
}) {
  const color = colorMap[category] || "var(--text-tertiary)";
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
