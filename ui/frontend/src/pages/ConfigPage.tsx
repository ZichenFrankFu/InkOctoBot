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

  const selectedPlatforms: string[] = useMemo(() => {
    const platforms = Array.isArray(form.platforms) ? form.platforms : form.platform ? [form.platform] : ["fanqie"];
    return platforms.filter((p: string) => p === "fanqie" || p === "qidian");
  }, [form.platforms, form.platform]);

  const rankOptions = useMemo(() => {
    if (!schema) return [] as { value: string; label: string; platform: string }[];
    const options: { value: string; label: string; platform: string }[] = [];
    if (selectedPlatforms.includes("fanqie")) {
      options.push(...schema.rank_keys.fanqie.map((k) => ({ value: `fanqie::${k}`, label: `番茄 | ${k}`, platform: "fanqie" })));
    }
    if (selectedPlatforms.includes("qidian")) {
      options.push(...schema.rank_keys.qidian.map((k) => ({ value: `qidian::${k}`, label: `起点 | ${k}`, platform: "qidian" })));
    }
    return options;
  }, [schema, selectedPlatforms]);

  const selectedRankValues: string[] = useMemo(() => {
    const rankKeys = Array.isArray(form.rank_keys) ? form.rank_keys : form.rank_key ? [form.rank_key] : [];
    if (!rankKeys.length) return [ALL_OPTION];
    const set = new Set(rankOptions.map((o) => o.value));
    return rankKeys.filter((v: string) => set.has(v));
  }, [form.rank_keys, form.rank_key, rankOptions]);

  const isFanqieOnly = selectedPlatforms.length === 1 && selectedPlatforms[0] === "fanqie";

  async function loadRuns() {
    const res = await apiGet<{ runs: ConfigRun[] }>("/api/config/runs");
    setRuns(res.runs);
  }

  useEffect(() => {
    apiGet<ConfigSchema>("/api/config/schema")
      .then((res) => {
        setSchema(res);
        const defaults = { ...res.defaults };
        defaults.platforms = Array.isArray(defaults.platforms) ? defaults.platforms : [defaults.platform || "fanqie"];
        defaults.rank_keys = Array.isArray(defaults.rank_keys) ? defaults.rank_keys : [];
        if (defaults.platforms.length === 1 && defaults.platforms[0] === "qidian" && (defaults.pages == null || defaults.pages < 1)) {
          defaults.pages = 2;
        }
        if (defaults.platforms.length === 1 && defaults.platforms[0] === "fanqie") {
          defaults.pages = 1;
        }
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

  function onPlatformsChange(values: string[]) {
    const nextPlatforms = values.length ? values : ["fanqie"];
    const next: any = { ...form, platforms: nextPlatforms };
    if (nextPlatforms.length === 1 && nextPlatforms[0] === "fanqie") {
      next.pages = 1;
    }
    if (nextPlatforms.length === 1 && nextPlatforms[0] === "qidian" && (next.pages == null || next.pages < 1)) {
      next.pages = 2;
    }

    const allowed = new Set<string>();
    if (schema) {
      if (nextPlatforms.includes("fanqie")) schema.rank_keys.fanqie.forEach((k) => allowed.add(`fanqie::${k}`));
      if (nextPlatforms.includes("qidian")) schema.rank_keys.qidian.forEach((k) => allowed.add(`qidian::${k}`));
    }
    next.rank_keys = (Array.isArray(next.rank_keys) ? next.rank_keys : []).filter((v: string) => allowed.has(v));

    setForm(next);
    props.onDraftChange(next);
  }

  function onRankKeysChange(values: string[]) {
    if (values.includes(ALL_OPTION)) {
      updateForm({ rank_keys: [] });
      return;
    }
    updateForm({ rank_keys: values });
  }

  function toPayload(source: any) {
    const platforms = Array.isArray(source.platforms) ? source.platforms : source.platform ? [source.platform] : [];
    const rankValues: string[] = Array.isArray(source.rank_keys) ? source.rank_keys : [];

    const qidianRanks = rankValues.filter((x) => x.startsWith("qidian::")).map((x) => x.slice("qidian::".length));
    const fanqieRanks = rankValues.filter((x) => x.startsWith("fanqie::")).map((x) => x.slice("fanqie::".length));

    const payload: any = {
      platforms,
      platform: platforms.length === 1 ? platforms[0] : null,
      rank_keys: rankValues,
      rank_key: null,
      qidian_ranks: qidianRanks,
      fanqie_ranks: fanqieRanks,
      pages: platforms.length === 1 && platforms[0] === "fanqie" ? 1 : source.pages,
      qidian_pages: source.qidian_pages,
      chapter_count: Math.min(5, Math.max(1, Number(source.chapter_count ?? 5))),
      newbook_chapter_count: Math.min(5, Math.max(1, Number(source.newbook_chapter_count ?? 2))),
      use_proxy: !!source.use_proxy,
    };

    if (payload.platform && payload.rank_keys.length === 1) {
      payload.rank_key = (payload.rank_keys[0] as string).split("::")[1] || null;
    }

    return payload;
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

      <div style={{ display: "grid", gridTemplateColumns: "1.3fr 1fr", gap: 16 }}>
        <section style={cardStyle}>
          <h3 style={{ marginTop: 0, marginBottom: 14 }}>运行参数</h3>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <Field label="platform（多选）">
              <select
                multiple
                size={2}
                style={{ ...inputStyle, minHeight: 84 }}
                value={selectedPlatforms}
                onChange={(e) => onPlatformsChange(Array.from(e.target.selectedOptions).map((o) => o.value))}
              >
                <option value="fanqie">fanqie</option>
                <option value="qidian">qidian</option>
              </select>
            </Field>

            <Field label="rank_key（多选，含 ALL）">
              <select
                multiple
                size={Math.min(8, Math.max(4, rankOptions.length + 1))}
                style={{ ...inputStyle, minHeight: 160 }}
                value={selectedRankValues}
                onChange={(e) => onRankKeysChange(Array.from(e.target.selectedOptions).map((o) => o.value))}
              >
                <option value={ALL_OPTION}>(ALL)</option>
                {rankOptions.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </select>
            </Field>

            <Field label="pages">
              {isFanqieOnly ? (
                <input style={inputStyle} value="1 (fanqie fixed)" disabled />
              ) : (
                <input
                  style={inputStyle}
                  type="number"
                  min={1}
                  value={form.pages ?? 2}
                  onChange={(e) => updateForm({ pages: e.target.value === "" ? 2 : Math.max(1, Number(e.target.value)) })}
                />
              )}
            </Field>

            <Field label="qidian_pages">
              <input
                style={inputStyle}
                type="number"
                min={1}
                value={form.qidian_pages ?? 2}
                onChange={(e) => updateForm({ qidian_pages: e.target.value === "" ? 2 : Math.max(1, Number(e.target.value)) })}
              />
            </Field>

            <Field label="chapter_count (1-5)">
              <input
                style={inputStyle}
                type="number"
                min={1}
                max={5}
                value={form.chapter_count ?? 5}
                onChange={(e) => updateForm({ chapter_count: Math.min(5, Math.max(1, Number(e.target.value || 5))) })}
              />
            </Field>

            <Field label="newbook_chapter_count (1-5)">
              <input
                style={inputStyle}
                type="number"
                min={1}
                max={5}
                value={form.newbook_chapter_count ?? 2}
                onChange={(e) => updateForm({ newbook_chapter_count: Math.min(5, Math.max(1, Number(e.target.value || 2))) })}
              />
            </Field>
          </div>

          <div style={{ display: "flex", gap: 18, marginTop: 14 }}>
            <label style={{ display: "flex", gap: 8, alignItems: "center", color: "var(--text-secondary)" }}>
              <input type="checkbox" checked={!!form.use_proxy} onChange={(e) => updateForm({ use_proxy: e.target.checked })} />
              use_proxy
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
                  <th align="left" style={{ padding: 8 }}>run_id</th>
                  <th align="left" style={{ padding: 8 }}>platforms</th>
                  <th align="left" style={{ padding: 8 }}>操作</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => {
                  const p = Array.isArray(r.config.platforms)
                    ? r.config.platforms.join(",")
                    : (r.config.platform || "-");
                  return (
                    <tr key={r.run_id}>
                      <td style={{ borderTop: "1px solid var(--border)", padding: 8 }}>{r.run_id}</td>
                      <td style={{ borderTop: "1px solid var(--border)", padding: 8 }}>{p}</td>
                      <td style={{ borderTop: "1px solid var(--border)", padding: 8, display: "flex", gap: 8 }}>
                        <button style={{ ...buttonStyle, padding: "6px 10px" }} onClick={() => applyRunToForm(r)}>
                          载入
                        </button>
                        <button style={{ ...buttonStyle, padding: "6px 10px" }} onClick={() => removeRun(r.run_id)}>
                          删除
                        </button>
                      </td>
                    </tr>
                  );
                })}
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
