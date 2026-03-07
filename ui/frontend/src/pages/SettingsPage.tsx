import React, { useEffect, useState } from "react";
import { apiGet, apiPut } from "../api/client";
import type { AppSettings } from "../api/types";

const PIPELINE_ROLES: { key: string; label: string; desc: string }[] = [
  { key: "scene_planner", label: "Scene Planner", desc: "场景规划 - 场景结构设计" },
  { key: "scene_director", label: "Scene Director", desc: "场景导演 - 生成导演指令" },
  { key: "actor_default", label: "Actor (Default)", desc: "默认角色 - 通用对话与行为" },
  { key: "actor_protagonist", label: "Actor (Protagonist)", desc: "主角扮演 - 主角专用生成" },
  { key: "editor_stylist", label: "Editor-Stylist", desc: "风格编辑 - 文学风格化" },
  { key: "editor_agent", label: "Editor Agent", desc: "编辑代理 - 自动修改与润色" },
  { key: "evaluator", label: "Evaluator", desc: "评估器 - 一致性与约束检测" },
];

const PROVIDER_META: Record<string, { label: string; hasKey: boolean; hasUrl: boolean }> = {
  openai: { label: "OpenAI", hasKey: true, hasUrl: false },
  anthropic: { label: "Anthropic", hasKey: true, hasUrl: false },
  deepseek: { label: "DeepSeek", hasKey: true, hasUrl: false },
  ollama: { label: "Ollama", hasKey: false, hasUrl: true },
  vllm: { label: "vLLM", hasKey: false, hasUrl: true },
  local: { label: "Local", hasKey: false, hasUrl: false },
};

type Tab = "pipeline" | "providers" | "system";

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>("pipeline");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [settings, setSettings] = useState<AppSettings | null>(null);

  useEffect(() => {
    apiGet<AppSettings>("/api/data/settings")
      .then((data) => {
        setSettings(data);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  const update = (patch: Partial<AppSettings>) => {
    if (!settings) return;
    setSettings({ ...settings, ...patch });
    setDirty(true);
  };

  const updateProvider = (name: string, field: string, value: unknown) => {
    if (!settings) return;
    const prev = settings.providers[name];
    setSettings({
      ...settings,
      providers: {
        ...settings.providers,
        [name]: { ...prev, [field]: value },
      },
    });
    setDirty(true);
  };

  const updatePipeline = (role: string, field: string, value: string) => {
    if (!settings) return;
    const prev = settings.pipeline[role] || { provider: "", model: "", compare_models: [] };
    setSettings({
      ...settings,
      pipeline: {
        ...settings.pipeline,
        [role]: { ...prev, [field]: value },
      },
    });
    setDirty(true);
  };

  const save = async () => {
    setSaving(true);
    try {
      await apiPut("/api/data/settings", settings);
      setDirty(false);
    } catch (e) {
      console.error("Save failed:", e);
    } finally {
      setSaving(false);
    }
  };

  // Collect all available models for a given provider name
  const modelsForProvider = (providerName: string): string[] => {
    if (!settings) return [];
    const prov = settings.providers[providerName];
    return prov?.models ?? [];
  };

  // Enabled provider names for pipeline dropdowns
  const enabledProviders = (): string[] => {
    if (!settings) return [];
    return Object.entries(settings.providers)
      .filter(([, cfg]) => cfg.enabled)
      .map(([name]) => name);
  };

  if (loading || !settings) {
    return (
      <div className="loading" style={{ paddingTop: 120 }}>
        <div className="loading-spinner" />
        Loading settings...
      </div>
    );
  }

  const inputStyle: React.CSSProperties = {
    width: "100%",
    padding: "6px 10px",
    border: "1px solid var(--border)",
    borderRadius: 4,
    fontSize: 12,
    fontFamily: "var(--font-mono)",
    background: "var(--bg-secondary)",
    color: "var(--text-primary)",
    outline: "none",
    boxSizing: "border-box",
  };

  const selectStyle: React.CSSProperties = {
    padding: "6px 10px",
    border: "1px solid var(--border)",
    borderRadius: 4,
    fontSize: 12,
    background: "var(--bg-secondary)",
    color: "var(--text-primary)",
    outline: "none",
    minWidth: 140,
  };

  return (
    <div className="page-container" style={{ maxWidth: 1000 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
        <div className="page-header" style={{ marginBottom: 0 }}>
          <h2>Settings</h2>
          <p>Pipeline model configuration, provider management, system parameters</p>
        </div>
        <button
          className="btn-primary"
          onClick={save}
          disabled={!dirty || saving}
          style={{ opacity: dirty ? 1 : 0.5 }}
        >
          {saving ? "Saving..." : dirty ? "Save" : "Saved"}
        </button>
      </div>

      {/* Tab bar */}
      <div className="platform-tabs" style={{ marginBottom: 24 }}>
        <button className={`platform-tab${tab === "pipeline" ? " active" : ""}`} onClick={() => setTab("pipeline")}>
          Pipeline
        </button>
        <button className={`platform-tab${tab === "providers" ? " active" : ""}`} onClick={() => setTab("providers")}>
          Providers
        </button>
        <button className={`platform-tab${tab === "system" ? " active" : ""}`} onClick={() => setTab("system")}>
          System
        </button>
      </div>

      {/* ===== Pipeline Tab ===== */}
      {tab === "pipeline" && (
        <div>
          <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 16 }}>
            Assign a model provider and model to each agent role in the Film Pipeline.
          </div>
          <div className="card">
            <div className="card-body" style={{ padding: 0 }}>
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ borderBottom: "1px solid var(--border)" }}>
                    <th style={{ textAlign: "left", padding: "12px 20px", fontSize: 12, color: "var(--text-secondary)", fontWeight: 600 }}>
                      Agent Role
                    </th>
                    <th style={{ textAlign: "left", padding: "12px 20px", fontSize: 12, color: "var(--text-secondary)", fontWeight: 600 }}>
                      Provider
                    </th>
                    <th style={{ textAlign: "left", padding: "12px 20px", fontSize: 12, color: "var(--text-secondary)", fontWeight: 600 }}>
                      Model
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {PIPELINE_ROLES.map((role) => {
                    const assignment = settings.pipeline[role.key] || { provider: "", model: "", compare_models: [] };
                    const models = modelsForProvider(assignment.provider);
                    const providers = enabledProviders();

                    return (
                      <tr key={role.key} style={{ borderBottom: "1px solid var(--border)" }}>
                        <td style={{ padding: "14px 20px", verticalAlign: "middle" }}>
                          <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>{role.label}</div>
                          <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 2 }}>{role.desc}</div>
                        </td>
                        <td style={{ padding: "14px 20px", verticalAlign: "middle" }}>
                          <select
                            value={assignment.provider}
                            onChange={(e) => {
                              updatePipeline(role.key, "provider", e.target.value);
                              // Reset model when provider changes
                              const newModels = modelsForProvider(e.target.value);
                              updatePipeline(role.key, "model", newModels[0] || "");
                            }}
                            style={selectStyle}
                          >
                            <option value="">-- select --</option>
                            {providers.map((p) => (
                              <option key={p} value={p}>
                                {PROVIDER_META[p]?.label || p}
                              </option>
                            ))}
                          </select>
                        </td>
                        <td style={{ padding: "14px 20px", verticalAlign: "middle" }}>
                          <select
                            value={assignment.model}
                            onChange={(e) => updatePipeline(role.key, "model", e.target.value)}
                            style={selectStyle}
                            disabled={!assignment.provider}
                          >
                            <option value="">-- select --</option>
                            {models.map((m) => (
                              <option key={m} value={m}>
                                {m}
                              </option>
                            ))}
                          </select>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* ===== Providers Tab ===== */}
      {tab === "providers" && (
        <div>
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
            {Object.entries(settings.providers).map(([name, prov]) => {
              const meta = PROVIDER_META[name] || { label: name, hasKey: false, hasUrl: false };

              return (
                <div key={name} className="card">
                  <div className="card-header" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                    <div>
                      <h3 style={{ margin: 0 }}>{meta.label}</h3>
                      <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>{name}</span>
                    </div>
                    <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer", fontSize: 12 }}>
                      <input
                        type="checkbox"
                        checked={prov.enabled}
                        onChange={(e) => updateProvider(name, "enabled", e.target.checked)}
                        style={{ accentColor: "var(--accent)" }}
                      />
                      <span style={{ color: prov.enabled ? "var(--accent)" : "var(--text-secondary)" }}>
                        {prov.enabled ? "Enabled" : "Disabled"}
                      </span>
                    </label>
                  </div>
                  <div className="card-body">
                    {meta.hasKey && (
                      <div style={{ marginBottom: 12 }}>
                        <label style={{ fontSize: 11, color: "var(--text-secondary)", display: "block", marginBottom: 4 }}>
                          API Key
                        </label>
                        <input
                          type="password"
                          value={prov.api_key || ""}
                          onChange={(e) => updateProvider(name, "api_key", e.target.value)}
                          placeholder="sk-..."
                          style={inputStyle}
                        />
                      </div>
                    )}

                    {meta.hasUrl && (
                      <div style={{ marginBottom: 12 }}>
                        <label style={{ fontSize: 11, color: "var(--text-secondary)", display: "block", marginBottom: 4 }}>
                          Base URL
                        </label>
                        <input
                          value={prov.base_url || ""}
                          onChange={(e) => updateProvider(name, "base_url", e.target.value)}
                          placeholder="http://localhost:11434"
                          style={inputStyle}
                        />
                      </div>
                    )}

                    <div style={{ marginBottom: 12 }}>
                      <label style={{ fontSize: 11, color: "var(--text-secondary)", display: "block", marginBottom: 4 }}>
                        Models (comma-separated)
                      </label>
                      <input
                        value={(prov.models || []).join(", ")}
                        onChange={(e) =>
                          updateProvider(
                            name,
                            "models",
                            e.target.value.split(",").map((s) => s.trim()).filter(Boolean)
                          )
                        }
                        style={inputStyle}
                      />
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* ===== System Tab ===== */}
      {tab === "system" && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20, maxWidth: 800 }}>
          <div className="card">
            <div className="card-header">
              <h3>Auto-Save</h3>
            </div>
            <div className="card-body">
              <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12, cursor: "pointer", fontSize: 13 }}>
                <input
                  type="checkbox"
                  checked={settings.auto_save}
                  onChange={(e) => update({ auto_save: e.target.checked })}
                  style={{ accentColor: "var(--accent)" }}
                />
                <span style={{ color: "var(--text-primary)" }}>Enable auto-save</span>
              </label>
              <label style={{ fontSize: 12, color: "var(--text-secondary)", display: "block", marginBottom: 8 }}>
                Interval (seconds): <strong style={{ color: "var(--text-primary)" }}>{settings.auto_save_interval}s</strong>
              </label>
              <input
                type="range"
                min={5}
                max={300}
                step={5}
                value={settings.auto_save_interval}
                onChange={(e) => update({ auto_save_interval: Number(e.target.value) })}
                style={{ width: "100%", accentColor: "var(--accent)" }}
              />
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--text-secondary)" }}>
                <span>5s</span>
                <span>300s</span>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-header">
              <h3>Cost Confirmation</h3>
            </div>
            <div className="card-body">
              <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", fontSize: 13 }}>
                <input
                  type="checkbox"
                  checked={settings.cost_confirm}
                  onChange={(e) => update({ cost_confirm: e.target.checked })}
                  style={{ accentColor: "var(--accent)" }}
                />
                <span style={{ color: "var(--text-primary)" }}>
                  Confirm before incurring costs
                </span>
              </label>
              <div style={{ marginTop: 8, fontSize: 11, color: "var(--text-secondary)" }}>
                When enabled, the system will ask for confirmation before running operations that incur API costs.
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-header">
              <h3>Export Format</h3>
            </div>
            <div className="card-body">
              <div style={{ display: "flex", gap: 8 }}>
                {(["txt", "docx", "epub"] as const).map((fmt) => (
                  <button
                    key={fmt}
                    onClick={() => update({ export_format: fmt })}
                    style={{
                      padding: "6px 20px",
                      borderRadius: 16,
                      border: settings.export_format === fmt ? "2px solid var(--accent)" : "1px solid var(--border)",
                      background: settings.export_format === fmt ? "var(--accent-muted)" : "transparent",
                      color: settings.export_format === fmt ? "var(--accent)" : "var(--text-secondary)",
                      fontSize: 12,
                      cursor: "pointer",
                      fontFamily: "var(--font-mono)",
                      textTransform: "uppercase",
                    }}
                  >
                    {fmt}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-header">
              <h3>Data Storage</h3>
            </div>
            <div className="card-body">
              <div style={{ fontSize: 13, color: "var(--text-primary)", lineHeight: 1.8 }}>
                All data is stored in{" "}
                <code
                  style={{
                    background: "var(--bg-tertiary)",
                    padding: "2px 6px",
                    borderRadius: 3,
                    fontFamily: "var(--font-mono)",
                    fontSize: 12,
                  }}
                >
                  /data/
                </code>
              </div>
              <div style={{ marginTop: 12, fontSize: 12, color: "var(--text-secondary)", lineHeight: 2 }}>
                data/projects/ - Project files
                <br />
                data/characters/ - Character cards
                <br />
                data/worldbook/ - World book
                <br />
                data/editor/ - Editor content
                <br />
                data/settings.json - Settings
              </div>
              <div style={{ marginTop: 16 }}>
                <button
                  className="btn-secondary"
                  onClick={() => {
                    if (window.confirm("Clear all cached data? This cannot be undone.")) {
                      localStorage.clear();
                      window.location.reload();
                    }
                  }}
                  style={{ fontSize: 12, padding: "6px 16px", color: "var(--danger)", borderColor: "var(--danger)" }}
                >
                  Clear Cache
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
