import React, { useEffect, useMemo, useState } from "react";
import { apiGet, apiPost } from "../api/client";
import type { ConfigRun, ConfigSchema } from "../api/types";

export default function ConfigPage(props: { onSaved: (runId: string) => void }) {
  const [schema, setSchema] = useState<ConfigSchema | null>(null);
  const [runs, setRuns] = useState<ConfigRun[]>([]);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState<any>({
    platform: "fanqie",
    rank_key: "",
    pages: null,
    qidian_pages: 2,
    chapter_count: 5,
    newbook_chapter_count: 2,
    no_detail: false,
    no_chapters: false,
    use_proxy: false,
    max_retries: 3,
    consecutive_threshold: 3,
  });

  const rankKeys = useMemo(() => {
    if (!schema) return [];
    return form.platform === "qidian" ? schema.rank_keys.qidian : schema.rank_keys.fanqie;
  }, [schema, form.platform]);

  async function loadRuns() {
    const res = await apiGet<{ runs: ConfigRun[] }>("/api/config/runs");
    setRuns(res.runs);
  }

  useEffect(() => {
    apiGet<ConfigSchema>("/api/config/schema").then(setSchema).catch((e) => alert(String(e)));
    loadRuns().catch((e) => alert(String(e)));
  }, []);

  function applyRunToForm(run: ConfigRun) {
    setForm((f: any) => ({ ...f, ...run.config }));
    props.onSaved(run.run_id);
  }

  async function save() {
    setSaving(true);
    try {
      const res = await apiPost<{ run_id: string; path: string }>("/api/config/runs", form);
      props.onSaved(res.run_id);
      await loadRuns();
      alert(`保存成功: ${res.run_id}`);
    } finally {
      setSaving(false);
    }
  }

  if (!schema) return <div style={{ color: "var(--text-secondary)" }}>Loading schema...</div>;

  return (
    <div>
      <h2 style={{ marginTop: 0 }}>爬虫配置</h2>

      <Row label="platform">
        <select value={form.platform ?? ""} onChange={(e) => setForm({ ...form, platform: e.target.value, rank_key: "" })}>
          <option value="qidian">qidian</option>
          <option value="fanqie">fanqie</option>
        </select>
      </Row>

      <Row label="rank_key（可选：留空=平台全榜）">
        <select value={form.rank_key ?? ""} onChange={(e) => setForm({ ...form, rank_key: e.target.value })}>
          <option value="">(ALL ranks)</option>
          {rankKeys.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
      </Row>

      {form.platform === "qidian" && (
        <Row label="pages（起点单榜/全榜页数）">
          <input
            type="number"
            value={form.pages ?? ""}
            placeholder="(empty = use qidian_pages)"
            onChange={(e) => setForm({ ...form, pages: e.target.value === "" ? null : Math.max(1, Number(e.target.value)) })}
          />
        </Row>
      )}

      <Row label="qidian_pages（legacy fallback）">
        <input
          type="number"
          min={1}
          value={form.qidian_pages}
          onChange={(e) => setForm({ ...form, qidian_pages: Math.max(1, Number(e.target.value)) })}
        />
      </Row>

      <Row label="chapter_count">
        <input
          type="number"
          min={1}
          value={form.chapter_count}
          onChange={(e) => setForm({ ...form, chapter_count: Math.max(1, Number(e.target.value)) })}
        />
      </Row>

      <Row label="newbook_chapter_count">
        <input
          type="number"
          min={1}
          value={form.newbook_chapter_count}
          onChange={(e) => setForm({ ...form, newbook_chapter_count: Math.max(1, Number(e.target.value)) })}
        />
      </Row>

      <Row label="no_detail">
        <input type="checkbox" checked={!!form.no_detail} onChange={(e) => setForm({ ...form, no_detail: e.target.checked })} />
      </Row>

      <Row label="no_chapters">
        <input type="checkbox" checked={!!form.no_chapters} onChange={(e) => setForm({ ...form, no_chapters: e.target.checked })} />
      </Row>

      <button
        disabled={saving}
        onClick={save}
        style={{ padding: "10px 12px", borderRadius: 8, border: "1px solid #ddd", cursor: "pointer" }}
      >
        {saving ? "保存中..." : "保存配置"}
      </button>

      <h3 style={{ marginTop: 22 }}>最近配置记录</h3>
      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
        <thead>
          <tr>
            <th align="left">run_id</th>
            <th align="left">platform</th>
            <th align="left">rank_key</th>
            <th align="left">created_at</th>
            <th align="left">操作</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.run_id}>
              <td style={{ borderTop: "1px solid #eee", padding: 6 }}>{r.run_id}</td>
              <td style={{ borderTop: "1px solid #eee", padding: 6 }}>{r.config.platform || "-"}</td>
              <td style={{ borderTop: "1px solid #eee", padding: 6 }}>{r.config.rank_key || "(ALL)"}</td>
              <td style={{ borderTop: "1px solid #eee", padding: 6 }}>{new Date(r.created_at * 1000).toLocaleString()}</td>
              <td style={{ borderTop: "1px solid #eee", padding: 6 }}>
                <button onClick={() => props.onSaved(r.run_id)}>设为当前运行配置</button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div style={{ marginTop: 16, fontSize: 12, color: "#666" }}>
        <div>Notes:</div>
        <pre style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify(schema.notes, null, 2)}</pre>
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
