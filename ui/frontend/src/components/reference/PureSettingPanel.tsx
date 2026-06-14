import React, { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost, apiPut } from "../../api/client";
import { useToast } from "../shared/Toast";

// 纯设定作品面板 (spec 2.2.2 / 6.2)：SCP、后室、战锤40K 等无完整正文的
// 众创/设定集作品。五个 tab：快捷输入 / 设定 / 角色 / 特征提取 / 设定特征。
// 提取为预览制 —— 用户逐项确认后才入库 (LLM交互·机制2)。

type SettingEntry = { category: string; title: string; content: string } & Record<string, string>;
type StaticCharacter = { name: string; role: string; description: string } & Record<string, string>;
type SettingFeature = { title: string; description: string } & Record<string, string>;

const CATEGORIES = ["力量体系", "势力组织", "地理", "社会规则", "历史背景", "世界观", "其他"];

type TabKey = "quick" | "settings" | "characters" | "extract" | "features";

const TABS: [TabKey, string][] = [
  ["quick", "快捷输入"], ["settings", "设定"], ["characters", "角色"],
  ["extract", "特征提取"], ["features", "设定特征"],
];

export default function PureSettingPanel({ refId }: { refId: string }) {
  const { toast } = useToast();
  const [tab, setTab] = useState<TabKey>("quick");
  const [quickText, setQuickText] = useState("");
  const [settings, setSettings] = useState<SettingEntry[]>([]);
  const [characters, setCharacters] = useState<StaticCharacter[]>([]);
  const [features, setFeatures] = useState<SettingFeature[]>([]);
  const [extracting, setExtracting] = useState(false);
  const [preview, setPreview] = useState<{
    settings: SettingEntry[]; characters: StaticCharacter[];
    setting_features: SettingFeature[];
  } | null>(null);
  const [saving, setSaving] = useState(false);

  const reload = useCallback(async () => {
    try {
      const r = await apiGet<any>(`/api/references/works/${refId}/pure-setting`);
      setQuickText(r.quick_input_text || "");
      setSettings(r.settings || []);
      setCharacters(r.static_characters || []);
      setFeatures(r.setting_features || []);
    } catch (e: any) { toast(e.message || "加载失败", "error"); }
  }, [refId]);

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

  const runExtract = async () => {
    setExtracting(true);
    setPreview(null);
    try {
      const r = await apiPost<any>(
        `/api/references/works/${refId}/pure-setting/extract`, {},
        { timeoutMs: 300000 },
      );
      setPreview({
        settings: r.settings || [],
        characters: r.characters || [],
        setting_features: r.setting_features || [],
      });
      setTab("extract");
    } catch (e: any) { toast(e.message || "提取失败", "error"); }
    finally { setExtracting(false); }
  };

  const commitSection = async (
    section: "settings" | "characters" | "setting_features",
  ) => {
    if (!preview) return;
    if (section === "settings") {
      await put({ settings: [...settings, ...preview.settings] }, "设定条目已入库");
      setPreview({ ...preview, settings: [] });
    } else if (section === "characters") {
      await put({ static_characters: [...characters, ...preview.characters] }, "角色条目已入库");
      setPreview({ ...preview, characters: [] });
    } else {
      await put({ setting_features: [...features, ...preview.setting_features] }, "设定特征已入库");
      setPreview({ ...preview, setting_features: [] });
    }
  };

  const inputStyle: React.CSSProperties = { fontSize: 11 };
  const rowStyle: React.CSSProperties = {
    display: "flex", gap: 6, alignItems: "flex-start", padding: "6px 0",
    borderBottom: "1px solid var(--border-subtle)", fontSize: 11,
  };

  return (
    <div>
      <div style={{ display: "flex", gap: 4, marginBottom: 12 }}>
        {TABS.map(([k, label]) => (
          <button key={k} className="btn" style={{
            fontSize: 11, padding: "3px 12px",
            color: tab === k ? "var(--accent)" : undefined,
            borderColor: tab === k ? "var(--accent)" : undefined,
          }} onClick={() => setTab(k)}>{label}</button>
        ))}
      </div>

      {tab === "quick" && (
        <div>
          <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginBottom: 8 }}>
            直接粘贴 wiki 条目原文（可多条拼接）。保存后可在「特征提取」tab 一键抽取设定条目、角色与设定特征。
          </div>
          <textarea className="input" value={quickText}
            onChange={e => setQuickText(e.target.value)} rows={14}
            placeholder="粘贴 SCP / 后室 / 战锤40K 等 wiki 条目原文..."
            style={{ width: "100%", boxSizing: "border-box", fontSize: 11, lineHeight: 1.6 }} />
          <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
            <button className="btn-primary" style={{ fontSize: 11, padding: "4px 14px" }}
              disabled={saving} onClick={() => put({ quick_input_text: quickText }, "快捷输入已保存")}>
              保存原文
            </button>
            <button className="btn" style={{ fontSize: 11, padding: "4px 14px" }}
              disabled={extracting || !quickText.trim()} onClick={runExtract}>
              {extracting ? "提取中..." : "保存并交给特征提取"}
            </button>
          </div>
        </div>
      )}

      {tab === "settings" && (
        <EditableList<SettingEntry>
          items={settings}
          columns={[
            { key: "category", label: "分类", width: 110, options: CATEGORIES },
            { key: "title", label: "条目名", width: 160 },
            { key: "content", label: "内容", flex: true },
          ]}
          blank={{ category: "其他", title: "", content: "" }}
          onSave={items => put({ settings: items }, "设定条目已保存")}
          saving={saving} rowStyle={rowStyle} inputStyle={inputStyle}
        />
      )}

      {tab === "characters" && (
        <EditableList<StaticCharacter>
          items={characters}
          columns={[
            { key: "name", label: "姓名", width: 140 },
            { key: "role", label: "定位", width: 120 },
            { key: "description", label: "描述（静态，不绑定章节）", flex: true },
          ]}
          blank={{ name: "", role: "", description: "" }}
          onSave={items => put({ static_characters: items }, "角色条目已保存")}
          saving={saving} rowStyle={rowStyle} inputStyle={inputStyle}
        />
      )}

      {tab === "features" && (
        <EditableList<SettingFeature>
          items={features}
          columns={[
            { key: "title", label: "高概念 / 母题", width: 200 },
            { key: "description", label: "一句话解释", flex: true },
          ]}
          blank={{ title: "", description: "" }}
          onSave={items => put({ setting_features: items }, "设定特征已保存")}
          saving={saving} rowStyle={rowStyle} inputStyle={inputStyle}
        />
      )}

      {tab === "extract" && (
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
              从快捷输入抽取设定条目、角色、设定特征；预览后逐板块确认入库。
            </span>
            <button className="btn-primary" style={{ fontSize: 11, padding: "3px 12px", marginLeft: "auto" }}
              disabled={extracting} onClick={runExtract}>
              {extracting ? "提取中..." : "重新提取"}
            </button>
          </div>
          {!preview && <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>暂无预览。点击「重新提取」开始。</div>}
          {preview && (
            <>
              <PreviewSection title={`设定条目（${preview.settings.length}）`}
                onCommit={() => commitSection("settings")} disabled={saving || preview.settings.length === 0}>
                {preview.settings.map((s, i) => (
                  <div key={i} style={rowStyle}>
                    <span style={{ width: 80, color: "var(--accent)" }}>{s.category}</span>
                    <span style={{ width: 140, fontWeight: 600 }}>{s.title}</span>
                    <span style={{ flex: 1, color: "var(--text-secondary)" }}>{s.content}</span>
                    <button className="btn" style={{ fontSize: 9, padding: "1px 8px", color: "var(--error)" }}
                      onClick={() => setPreview({ ...preview, settings: preview.settings.filter((_, j) => j !== i) })}>移除</button>
                  </div>
                ))}
              </PreviewSection>
              <PreviewSection title={`角色（${preview.characters.length}）`}
                onCommit={() => commitSection("characters")} disabled={saving || preview.characters.length === 0}>
                {preview.characters.map((c, i) => (
                  <div key={i} style={rowStyle}>
                    <span style={{ width: 120, fontWeight: 600 }}>{c.name}</span>
                    <span style={{ width: 100 }}>{c.role}</span>
                    <span style={{ flex: 1, color: "var(--text-secondary)" }}>{c.description}</span>
                    <button className="btn" style={{ fontSize: 9, padding: "1px 8px", color: "var(--error)" }}
                      onClick={() => setPreview({ ...preview, characters: preview.characters.filter((_, j) => j !== i) })}>移除</button>
                  </div>
                ))}
              </PreviewSection>
              <PreviewSection title={`设定特征（${preview.setting_features.length}）`}
                onCommit={() => commitSection("setting_features")} disabled={saving || preview.setting_features.length === 0}>
                {preview.setting_features.map((f, i) => (
                  <div key={i} style={rowStyle}>
                    <span style={{ width: 180, fontWeight: 600 }}>{f.title}</span>
                    <span style={{ flex: 1, color: "var(--text-secondary)" }}>{f.description}</span>
                    <button className="btn" style={{ fontSize: 9, padding: "1px 8px", color: "var(--error)" }}
                      onClick={() => setPreview({ ...preview, setting_features: preview.setting_features.filter((_, j) => j !== i) })}>移除</button>
                  </div>
                ))}
              </PreviewSection>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function PreviewSection({ title, onCommit, disabled, children }: {
  title: string; onCommit: () => void; disabled: boolean;
  children: React.ReactNode;
}) {
  return (
    <div style={{ marginBottom: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4 }}>
        <span style={{ fontSize: 12, fontWeight: 600 }}>{title}</span>
        <button className="btn-primary" style={{ fontSize: 10, padding: "2px 12px", marginLeft: "auto" }}
          disabled={disabled} onClick={onCommit}>确认入库</button>
      </div>
      {children}
    </div>
  );
}

function EditableList<T extends Record<string, string>>({ items, columns, blank, onSave, saving, rowStyle, inputStyle }: {
  items: T[];
  columns: { key: keyof T & string; label: string; width?: number; flex?: boolean; options?: string[] }[];
  blank: T;
  onSave: (items: T[]) => void;
  saving: boolean;
  rowStyle: React.CSSProperties;
  inputStyle: React.CSSProperties;
}) {
  const [draft, setDraft] = useState<T[]>(items);
  const [dirty, setDirty] = useState(false);
  useEffect(() => { if (!dirty) setDraft(items); }, [items, dirty]);

  const update = (i: number, key: string, v: string) => {
    setDraft(prev => prev.map((row, j) => j === i ? { ...row, [key]: v } : row));
    setDirty(true);
  };
  return (
    <div>
      <div style={{ display: "flex", gap: 6, fontSize: 10, color: "var(--text-tertiary)", padding: "0 0 4px" }}>
        {columns.map(c => (
          <span key={c.key} style={c.flex ? { flex: 1 } : { width: c.width }}>{c.label}</span>
        ))}
        <span style={{ width: 40 }} />
      </div>
      {draft.map((row, i) => (
        <div key={i} style={rowStyle}>
          {columns.map(c => c.options ? (
            <select key={c.key} className="select" value={row[c.key]}
              onChange={e => update(i, c.key, e.target.value)}
              style={{ ...inputStyle, width: c.width }}>
              {c.options.map(o => <option key={o} value={o}>{o}</option>)}
            </select>
          ) : (
            <input key={c.key} className="input" value={row[c.key]}
              onChange={e => update(i, c.key, e.target.value)}
              style={c.flex ? { ...inputStyle, flex: 1 } : { ...inputStyle, width: c.width }} />
          ))}
          <button className="btn" style={{ fontSize: 9, padding: "1px 8px", color: "var(--error)", width: 40 }}
            onClick={() => { setDraft(prev => prev.filter((_, j) => j !== i)); setDirty(true); }}>删</button>
        </div>
      ))}
      <div style={{ display: "flex", gap: 8, marginTop: 8 }}>
        <button className="btn" style={{ fontSize: 11, padding: "3px 12px" }}
          onClick={() => { setDraft(prev => [...prev, { ...blank }]); setDirty(true); }}>
          新增条目
        </button>
        <button className="btn-primary" style={{ fontSize: 11, padding: "3px 14px" }}
          disabled={saving || !dirty} onClick={() => { onSave(draft); setDirty(false); }}>
          保存
        </button>
      </div>
    </div>
  );
}
