import React, { useEffect, useMemo, useState } from "react";
import { apiGet, apiPost } from "../api/client";
import type { ConfigSchema } from "../api/types";

const cardStyle: React.CSSProperties = {
  background: "var(--bg-card)",
  border: "1px solid var(--border)",
  borderRadius: 12,
  padding: 16,
  boxShadow: "var(--shadow-sm)",
};

const inputStyle: React.CSSProperties = {
  width: "100%",
  padding: "9px 10px",
  borderRadius: 8,
  border: "1px solid var(--border)",
  background: "var(--bg-input)",
  color: "var(--text-primary)",
  fontFamily: "inherit",
};

const buttonStyle: React.CSSProperties = {
  padding: "10px 14px",
  borderRadius: 10,
  border: "1px solid var(--border)",
  background: "var(--bg-surface)",
  color: "var(--text-primary)",
  cursor: "pointer",
  fontFamily: "inherit",
};

export default function ConfigPage(props: {
  onSaved: (runId: string) => void;
  onDraftChange: (draft: any) => void;
}) {
  const [schema, setSchema] = useState<ConfigSchema | null>(null);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState<any>({});

  const rankKeys = useMemo(() => {
    if (!schema) return [];
    return form.platform === "qidian" ? schema.rank_keys.qidian : schema.rank_keys.fanqie;
  }, [schema, form.platform]);

  useEffect(() => {
    apiGet<ConfigSchema>("/api/config/schema")
      .then((res) => {
        setSchema(res);
        setForm(res.defaults || {});
        props.onDraftChange(res.defaults || {});
      })
      .catch((e) => alert(String(e)));
  }, []);

  function updateForm(patch: Record<string, any>) {
    setForm((prev: any) => {
      const next = { ...prev, ...patch };
      props.onDraftChange(next);
      return next;
    });
  }

  function resetToDefaults() {
    if (!schema) return;
    const d = { ...schema.defaults };
    setForm(d);
    props.onDraftChange(d);
  }

  async function save() {
    setSaving(true);
    try {
      const res = await apiPost<{ run_id: string }>("/api/config/runs", form);
      props.onSaved(res.run_id);
      alert(`保存成功: ${res.run_id}`);
    } finally {
      setSaving(false);
    }
  }

  if (!schema) return <div style={{ color: "var(--text-secondary)" }}>Loading schema...</div>;

  return (
    <div style={{ color: "var(--text-primary)" }}>
      <h2 style={{ marginTop: 0, marginBottom: 14 }}>爬虫配置</h2>
      <p style={{ marginBottom: 8, color: "var(--text-secondary)" }}>每次运行可以手动调整；不调整则使用默认值（输入框已展示默认值）。</p>
      <p style={{ marginBottom: 16, color: "var(--text-secondary)", fontSize: 12 }}>只有点击“保存配置”时，参数才会永久保存为可复用 run。</p>

      <section style={cardStyle}>
        <h3 style={{ marginTop: 0, marginBottom: 12 }}>基础采集参数</h3>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <Field label="platform">
            <select style={inputStyle} value={form.platform ?? ""} onChange={(e) => updateForm({ platform: e.target.value, rank_key: "" })}>
              <option value="qidian">qidian</option>
              <option value="fanqie">fanqie</option>
            </select>
          </Field>

          <Field label="rank_key（空=全榜）">
            <select style={inputStyle} value={form.rank_key ?? ""} onChange={(e) => updateForm({ rank_key: e.target.value })}>
              <option value="">(ALL ranks)</option>
              {rankKeys.map((k) => <option key={k} value={k}>{k}</option>)}
            </select>
          </Field>

          {form.platform === "qidian" && (
            <Field label="pages（起点专用）">
              <input style={inputStyle} type="number" min={1} value={form.pages ?? ""} onChange={(e) => updateForm({ pages: e.target.value === "" ? null : Math.max(1, Number(e.target.value)) })} />
            </Field>
          )}

          <Field label="qidian_pages"><input style={inputStyle} type="number" min={1} value={form.qidian_pages ?? ""} onChange={(e) => updateForm({ qidian_pages: Math.max(1, Number(e.target.value)) })} /></Field>
          <Field label="chapter_count（上限5）"><input style={inputStyle} type="number" min={1} max={5} value={form.chapter_count ?? ""} onChange={(e) => updateForm({ chapter_count: Math.min(5, Math.max(1, Number(e.target.value))) })} /></Field>
          <Field label="newbook_chapter_count（上限5）"><input style={inputStyle} type="number" min={1} max={5} value={form.newbook_chapter_count ?? ""} onChange={(e) => updateForm({ newbook_chapter_count: Math.min(5, Math.max(1, Number(e.target.value))) })} /></Field>
        </div>

        <h3 style={{ marginTop: 18, marginBottom: 10 }}>高级运行参数</h3>
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
          <Field label="max_retries"><input style={inputStyle} type="number" min={0} value={form.max_retries ?? ""} onChange={(e) => updateForm({ max_retries: e.target.value === "" ? null : Math.max(0, Number(e.target.value)) })} /></Field>
          <Field label="retry_delay（秒）"><input style={inputStyle} type="number" min={0} step="0.1" value={form.retry_delay ?? ""} onChange={(e) => updateForm({ retry_delay: e.target.value === "" ? null : Math.max(0, Number(e.target.value)) })} /></Field>
          <Field label="page_max_retries"><input style={inputStyle} type="number" min={0} value={form.page_max_retries ?? ""} onChange={(e) => updateForm({ page_max_retries: e.target.value === "" ? null : Math.max(0, Number(e.target.value)) })} /></Field>
          <Field label="page_retry_delay（秒）"><input style={inputStyle} type="number" min={0} step="0.1" value={form.page_retry_delay ?? ""} onChange={(e) => updateForm({ page_retry_delay: e.target.value === "" ? null : Math.max(0, Number(e.target.value)) })} /></Field>
          <Field label="page_default_wait_sec"><input style={inputStyle} type="number" min={1} value={form.page_default_wait_sec ?? ""} onChange={(e) => updateForm({ page_default_wait_sec: e.target.value === "" ? null : Math.max(1, Number(e.target.value)) })} /></Field>
          <Field label="consecutive_threshold"><input style={inputStyle} type="number" min={1} value={form.consecutive_threshold ?? ""} onChange={(e) => updateForm({ consecutive_threshold: e.target.value === "" ? null : Math.max(1, Number(e.target.value)) })} /></Field>
        </div>

        <div style={{ display: "flex", gap: 18, marginTop: 14, flexWrap: "wrap" }}>
          <label style={{ display: "flex", gap: 8, alignItems: "center", color: "var(--text-secondary)" }}><input type="checkbox" checked={!!form.use_proxy} onChange={(e) => updateForm({ use_proxy: e.target.checked })} />use_proxy</label>
          <label style={{ display: "flex", gap: 8, alignItems: "center", color: "var(--text-secondary)" }}><input type="checkbox" checked={!!form.no_detail} onChange={(e) => updateForm({ no_detail: e.target.checked })} />no_detail</label>
          <label style={{ display: "flex", gap: 8, alignItems: "center", color: "var(--text-secondary)" }}><input type="checkbox" checked={!!form.no_chapters} onChange={(e) => updateForm({ no_chapters: e.target.checked })} />no_chapters</label>
        </div>

        <div style={{ display: "flex", gap: 10, marginTop: 16 }}>
          <button onClick={resetToDefaults} style={buttonStyle}>恢复默认值</button>
          <button disabled={saving} onClick={save} style={buttonStyle}>{saving ? "保存中..." : "保存配置"}</button>
        </div>
      </section>
    </div>
  );
}

function Field(props: { label: string; children: React.ReactNode }) {
  return <div><div style={{ marginBottom: 6, color: "var(--text-secondary)", fontSize: 12 }}>{props.label}</div>{props.children}</div>;
}
