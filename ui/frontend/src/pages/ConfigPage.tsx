import React, { useEffect, useMemo, useState } from "react";
import { apiDelete, apiGet, apiPost } from "../api/client";
import type { ConfigRun, ConfigSchema } from "../api/types";

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

const ALL_OPTION = "__ALL__";

export default function ConfigPage(props: {
  onSaved: (runId: string) => void;
  onDraftChange: (draft: any) => void;
}) {
  const [schema, setSchema] = useState<ConfigSchema | null>(null);
  const [runs, setRuns] = useState<ConfigRun[]>([]);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState<any>({});

  const rankKeys = useMemo(() => {
    if (!schema) return [];
    return form.platform === "qidian" ? schema.rank_keys.qidian : schema.rank_keys.fanqie;
  }, [schema, form.platform]);

  async function loadRuns() {
    const res = await apiGet<{ runs: ConfigRun[] }>("/api/config/runs");
    setRuns(res.runs);
  }

  useEffect(() => {
    apiGet<ConfigSchema>("/api/config/schema")
      .then((res) => {
        setSchema(res);
        const defaults = res.defaults || {};
        setForm(defaults);
        props.onDraftChange(defaults);
      })
      .catch((e) => alert(String(e)));
    loadRuns().catch((e) => alert(String(e)));
  }, []);

  function updateForm(patch: Record<string, any>) {
    setForm((prev: any) => {
      const next = { ...prev, ...patch };
      props.onDraftChange(next);
      return next;
    });
  }

  function applyRunToForm(run: ConfigRun) {
    const defaults = schema?.defaults || {};
    const next = { ...defaults, ...run.config };
    next.platforms = Array.isArray(next.platforms) ? next.platforms : next.platform ? [next.platform] : ["fanqie"];
    next.rank_keys = Array.isArray(next.rank_keys)
      ? next.rank_keys
      : next.rank_key && next.platform
        ? [`${next.platform}::${next.rank_key}`]
        : [];
    setForm(next);
    props.onSaved(run.run_id);
    props.onDraftChange(next);
  }

  async function save() {
    setSaving(true);
    try {
      const payload = toPayload(form);
      const res = await apiPost<{ run_id: string }>("/api/config/runs", payload);
      props.onSaved(res.run_id);
      await loadRuns();
      alert(`保存成功: ${res.run_id}`);
    } finally {
      setSaving(false);
    }
  }

  async function removeRun(runId: string) {
    if (!window.confirm(`确认删除配置 ${runId} ?`)) return;
    await apiDelete<{ ok: boolean }>(`/api/config/runs/${encodeURIComponent(runId)}`);
    await loadRuns();
  }

  if (!schema) return <div style={{ color: "var(--text-secondary)" }}>Loading schema...</div>;

  return (
    <div style={{ color: "var(--text-primary)" }}>
      <h2 style={{ marginTop: 0, marginBottom: 14 }}>爬虫配置</h2>
      <p style={{ marginBottom: 8, color: "var(--text-secondary)" }}>
        每次运行可以手动调整；不调整则使用默认值（输入框已展示默认值）。
      </p>
      <p style={{ marginBottom: 16, color: "var(--text-secondary)", fontSize: 12 }}>
        只有点击“保存配置”时，参数才会永久保存为可复用 run。
      </p>

      <div style={{ display: "grid", gridTemplateColumns: "1.3fr 1fr", gap: 16 }}>
        <section style={cardStyle}>
          <h3 style={{ marginTop: 0, marginBottom: 14 }}>运行参数</h3>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <Field label="platform（多选）">
              <select
                style={inputStyle}
                value={form.platform ?? ""}
                onChange={(e) => updateForm({ platform: e.target.value, rank_key: "" })}
              >
                {["fanqie", "qidian"].map((p) => (
                  <option key={p} value={p}>
                    {p}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="rank_key">
              <select style={inputStyle} value={form.rank_key ?? ""} onChange={(e) => updateForm({ rank_key: e.target.value })}>
                <option value="">(ALL)</option>
                {rankKeys.map((k: string) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="pages">
              <input
                style={inputStyle}
                type="number"
                min={1}
                value={form.pages ?? ""}
                onChange={(e) => updateForm({ pages: e.target.value === "" ? null : Math.max(1, Number(e.target.value)) })}
              />
            </Field>

            <Field label="qidian_pages">
              <input
                style={inputStyle}
                type="number"
                min={1}
                value={form.qidian_pages ?? ""}
                onChange={(e) => updateForm({ qidian_pages: e.target.value === "" ? null : Math.max(1, Number(e.target.value)) })}
              />
            </Field>

            <Field label="qidian_pages">
              <input
                style={inputStyle}
                type="number"
                min={1}
                value={form.chapter_count ?? ""}
                onChange={(e) => updateForm({ chapter_count: e.target.value === "" ? null : Math.max(1, Number(e.target.value)) })}
              />
            </Field>

            <Field label="chapter_count (1-5)">
              <input
                style={inputStyle}
                type="number"
                min={1}
                value={form.newbook_chapter_count ?? ""}
                onChange={(e) =>
                  updateForm({ newbook_chapter_count: e.target.value === "" ? null : Math.max(1, Number(e.target.value)) })
                }
              />
            </Field>

            <Field label="max_retries">
              <input
                style={inputStyle}
                type="number"
                min={0}
                value={form.max_retries ?? ""}
                onChange={(e) => updateForm({ max_retries: e.target.value === "" ? null : Math.max(0, Number(e.target.value)) })}
              />
            </Field>

            <Field label="newbook_chapter_count (1-5)">
              <input
                style={inputStyle}
                type="number"
                min={1}
                value={form.consecutive_threshold ?? ""}
                onChange={(e) =>
                  updateForm({ consecutive_threshold: e.target.value === "" ? null : Math.max(1, Number(e.target.value)) })
                }
              />
            </Field>
          </div>

          <div style={{ display: "flex", gap: 18, marginTop: 14 }}>
            <label style={{ display: "flex", gap: 8, alignItems: "center", color: "var(--text-secondary)" }}>
              <input type="checkbox" checked={!!form.use_proxy} onChange={(e) => updateForm({ use_proxy: e.target.checked })} />
              use_proxy
            </label>
            <label style={{ display: "flex", gap: 8, alignItems: "center", color: "var(--text-secondary)" }}>
              <input type="checkbox" checked={!!form.no_detail} onChange={(e) => updateForm({ no_detail: e.target.checked })} />
              no_detail
            </label>
            <label style={{ display: "flex", gap: 8, alignItems: "center", color: "var(--text-secondary)" }}>
              <input type="checkbox" checked={!!form.no_chapters} onChange={(e) => updateForm({ no_chapters: e.target.checked })} />
              no_chapters
            </label>
          </div>

          <div style={{ display: "flex", gap: 10, marginTop: 16 }}>
            <button disabled={saving} onClick={save} style={buttonStyle}>
              {saving ? "保存中..." : "保存配置"}
            </button>
          </div>
        </section>

        <section style={cardStyle}>
          <h3 style={{ marginTop: 0, marginBottom: 10 }}>最近配置记录</h3>
          <div style={{ maxHeight: 420, overflow: "auto", border: "1px solid var(--border)", borderRadius: 10 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead style={{ position: "sticky", top: 0, background: "var(--bg-surface)" }}>
                <tr>
                  <th align="left" style={{ padding: 8 }}>
                    run_id
                  </th>
                  <th align="left" style={{ padding: 8 }}>
                    platform
                  </th>
                  <th align="left" style={{ padding: 8 }}>
                    操作
                  </th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.run_id}>
                    <td style={{ borderTop: "1px solid var(--border)", padding: 8 }}>{r.run_id}</td>
                    <td style={{ borderTop: "1px solid var(--border)", padding: 8 }}>{r.config.platform || "-"}</td>
                    <td style={{ borderTop: "1px solid var(--border)", padding: 8 }}>
                      <button style={{ ...buttonStyle, padding: "6px 10px" }} onClick={() => applyRunToForm(r)}>
                        载入并设为当前
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </div>
  );
}

function Field(props: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <div style={{ marginBottom: 6, color: "var(--text-secondary)", fontSize: 12 }}>{props.label}</div>
      {props.children}
    </div>
  );
}
