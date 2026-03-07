import React, { useEffect, useState, useMemo } from "react";
import { apiGet, apiPut } from "../api/client";
import type { ModelProvider, ModelAssignment, AppSettings } from "../api/types";
import ModelSelector from "../components/shared/ModelSelector";

const AGENT_ROLES = [
  { key: "scene_director", label: "Scene Director", desc: "场景导演 - 生成导演指令" },
  { key: "actor", label: "Actor", desc: "角色扮演 - 对话与行为生成" },
  { key: "editor_writer", label: "Editor-Writer", desc: "剪辑写手 - 文学风格化" },
  { key: "evaluator", label: "Evaluator", desc: "评估器 - 一致性与约束检测" },
  { key: "marketing", label: "Marketing", desc: "市场分析 - 爽点与数据优化" },
  { key: "story_architect", label: "Story Architect", desc: "故事建筑师 - 大纲与结构设计" },
];

const PROVIDER_TEMPLATES: {
  key: string;
  name: string;
  type: ModelProvider["type"];
  hasKey: boolean;
  hasUrl: boolean;
  defaultModels: string[];
}[] = [
  { key: "openai", name: "OpenAI", type: "openai", hasKey: true, hasUrl: false, defaultModels: ["gpt-4o", "gpt-4o-mini", "o4-mini"] },
  { key: "anthropic", name: "Anthropic", type: "anthropic", hasKey: true, hasUrl: false, defaultModels: ["claude-sonnet-4-5-20250929", "claude-haiku-4-5-20251001"] },
  { key: "deepseek", name: "DeepSeek", type: "deepseek", hasKey: true, hasUrl: false, defaultModels: ["deepseek-chat", "deepseek-reasoner"] },
  { key: "ollama", name: "Ollama", type: "ollama", hasKey: false, hasUrl: true, defaultModels: ["qwen2.5:7b", "qwen2.5:14b", "llama3:8b"] },
  { key: "vllm", name: "vLLM", type: "vllm", hasKey: false, hasUrl: true, defaultModels: [] },
  { key: "local", name: "Local", type: "local", hasKey: false, hasUrl: false, defaultModels: [] },
];

type Tab = "pipeline" | "providers" | "system";

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>("pipeline");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);

  const [providers, setProviders] = useState<ModelProvider[]>([]);
  const [assignments, setAssignments] = useState<ModelAssignment[]>([]);
  const [settings, setSettings] = useState<AppSettings | null>(null);

  // Provider edit state (local copy with api_key input)
  const [providerEdits, setProviderEdits] = useState<
    Record<string, { api_key: string; base_url: string; models: string[] }>
  >({});
  const [testResults, setTestResults] = useState<Record<string, "ok" | "fail" | "testing">>({});

  useEffect(() => {
    Promise.all([
      apiGet<ModelProvider[]>("/api/data/model-providers"),
      apiGet<ModelAssignment[]>("/api/data/model-assignments"),
      apiGet<AppSettings>("/api/data/settings"),
    ])
      .then(([prov, assign, sett]) => {
        setProviders(prov);
        setAssignments(assign);
        setSettings(sett);

        const edits: typeof providerEdits = {};
        for (const p of prov) {
          edits[p.id] = {
            api_key: "",
            base_url: p.base_url || "",
            models: p.models || [],
          };
        }
        setProviderEdits(edits);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  const getAssignment = (role: string): { provider: string; model: string } => {
    const found = assignments.find((a) => a.role === role);
    return found ? { provider: found.provider, model: found.model } : { provider: "", model: "" };
  };

  const updateAssignment = (role: string, value: { provider: string; model: string }) => {
    setAssignments((prev) => {
      const filtered = prev.filter((a) => a.role !== role);
      return [...filtered, { role, provider: value.provider, model: value.model }];
    });
    setDirty(true);
  };

  const updateProviderEdit = (id: string, field: string, value: string | string[]) => {
    setProviderEdits((prev) => ({
      ...prev,
      [id]: { ...prev[id], [field]: value },
    }));
    setDirty(true);
  };

  const addProvider = () => {
    const id = `provider_${Date.now()}`;
    const newProv: ModelProvider = {
      id,
      name: "New Provider",
      type: "openai",
      api_key_set: false,
      base_url: "",
      models: [],
    };
    setProviders((prev) => [...prev, newProv]);
    setProviderEdits((prev) => ({
      ...prev,
      [id]: { api_key: "", base_url: "", models: [] },
    }));
    setDirty(true);
  };

  const testConnection = async (providerId: string) => {
    setTestResults((prev) => ({ ...prev, [providerId]: "testing" }));
    try {
      await apiGet(`/api/data/model-providers/${providerId}/test`);
      setTestResults((prev) => ({ ...prev, [providerId]: "ok" }));
    } catch {
      setTestResults((prev) => ({ ...prev, [providerId]: "fail" }));
    }
  };

  const updateSettings = (key: keyof AppSettings, value: unknown) => {
    if (!settings) return;
    setSettings({ ...settings, [key]: value } as AppSettings);
    setDirty(true);
  };

  const save = async () => {
    setSaving(true);
    try {
      await apiPut("/api/data/model-assignments", assignments);
      await apiPut("/api/data/settings", settings);
      setDirty(false);
    } catch (e) {
      console.error("Save failed:", e);
    } finally {
      setSaving(false);
    }
  };

  if (loading || !settings) {
    return (
      <div className="loading" style={{ paddingTop: 120 }}>
        <div className="loading-spinner" />
        Loading settings...
      </div>
    );
  }

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
                      Provider / Model
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {AGENT_ROLES.map((role) => (
                    <tr key={role.key} style={{ borderBottom: "1px solid var(--border)" }}>
                      <td style={{ padding: "14px 20px", verticalAlign: "middle" }}>
                        <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>{role.label}</div>
                        <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 2 }}>{role.desc}</div>
                      </td>
                      <td style={{ padding: "14px 20px", verticalAlign: "middle" }}>
                        <ModelSelector
                          value={getAssignment(role.key)}
                          onChange={(v) => updateAssignment(role.key, v)}
                          providers={providers}
                        />
                      </td>
                    </tr>
                  ))}
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
            {providers.map((prov) => {
              const tmpl = PROVIDER_TEMPLATES.find((t) => t.type === prov.type);
              const edit = providerEdits[prov.id] || { api_key: "", base_url: "", models: [] };
              const hasKey = tmpl?.hasKey ?? true;
              const hasUrl = tmpl?.hasUrl ?? false;
              const testStatus = testResults[prov.id];

              return (
                <div key={prov.id} className="card">
                  <div className="card-header" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                    <div>
                      <h3 style={{ margin: 0 }}>{prov.name}</h3>
                      <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>{prov.type}</span>
                    </div>
                    <span
                      style={{
                        fontSize: 10,
                        padding: "2px 8px",
                        borderRadius: 8,
                        background: prov.api_key_set ? "var(--accent-muted)" : "var(--bg-tertiary)",
                        color: prov.api_key_set ? "var(--accent)" : "var(--text-secondary)",
                      }}
                    >
                      {prov.api_key_set ? "Key Set" : "No Key"}
                    </span>
                  </div>
                  <div className="card-body">
                    {hasKey && (
                      <div style={{ marginBottom: 12 }}>
                        <label style={{ fontSize: 11, color: "var(--text-secondary)", display: "block", marginBottom: 4 }}>
                          API Key
                        </label>
                        <input
                          type="password"
                          value={edit.api_key}
                          onChange={(e) => updateProviderEdit(prov.id, "api_key", e.target.value)}
                          placeholder={prov.api_key_set ? "********" : "sk-..."}
                          style={{
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
                          }}
                        />
                      </div>
                    )}

                    {hasUrl && (
                      <div style={{ marginBottom: 12 }}>
                        <label style={{ fontSize: 11, color: "var(--text-secondary)", display: "block", marginBottom: 4 }}>
                          Base URL
                        </label>
                        <input
                          value={edit.base_url}
                          onChange={(e) => updateProviderEdit(prov.id, "base_url", e.target.value)}
                          placeholder="http://localhost:11434"
                          style={{
                            width: "100%",
                            padding: "6px 10px",
                            border: "1px solid var(--border)",
                            borderRadius: 4,
                            fontSize: 12,
                            background: "var(--bg-secondary)",
                            color: "var(--text-primary)",
                            outline: "none",
                            boxSizing: "border-box",
                          }}
                        />
                      </div>
                    )}

                    <div style={{ marginBottom: 12 }}>
                      <label style={{ fontSize: 11, color: "var(--text-secondary)", display: "block", marginBottom: 4 }}>
                        Models (comma-separated)
                      </label>
                      <input
                        value={(edit.models || []).join(", ")}
                        onChange={(e) =>
                          updateProviderEdit(
                            prov.id,
                            "models",
                            e.target.value.split(",").map((s) => s.trim()).filter(Boolean)
                          )
                        }
                        style={{
                          width: "100%",
                          padding: "6px 10px",
                          border: "1px solid var(--border)",
                          borderRadius: 4,
                          fontSize: 12,
                          background: "var(--bg-secondary)",
                          color: "var(--text-primary)",
                          outline: "none",
                          boxSizing: "border-box",
                        }}
                      />
                      {tmpl && tmpl.defaultModels.length > 0 && (
                        <div style={{ marginTop: 4, fontSize: 10, color: "var(--text-secondary)" }}>
                          Defaults: {tmpl.defaultModels.join(", ")}
                        </div>
                      )}
                    </div>

                    <button
                      className="btn-secondary"
                      onClick={() => testConnection(prov.id)}
                      disabled={testStatus === "testing"}
                      style={{ fontSize: 12, padding: "5px 14px" }}
                    >
                      {testStatus === "testing"
                        ? "Testing..."
                        : testStatus === "ok"
                        ? "Connected"
                        : testStatus === "fail"
                        ? "Failed - Retry"
                        : "Test Connection"}
                    </button>
                    {testStatus === "ok" && (
                      <span style={{ marginLeft: 8, fontSize: 11, color: "var(--accent)" }}>OK</span>
                    )}
                    {testStatus === "fail" && (
                      <span style={{ marginLeft: 8, fontSize: 11, color: "var(--danger)" }}>Connection failed</span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          <div style={{ marginTop: 20, textAlign: "center" }}>
            <button className="btn-secondary" onClick={addProvider} style={{ padding: "8px 24px" }}>
              + Add Provider
            </button>
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
              <label style={{ fontSize: 12, color: "var(--text-secondary)", display: "block", marginBottom: 8 }}>
                Interval (seconds): <strong style={{ color: "var(--text-primary)" }}>{settings.auto_save_interval}s</strong>
              </label>
              <input
                type="range"
                min={5}
                max={300}
                step={5}
                value={settings.auto_save_interval}
                onChange={(e) => updateSettings("auto_save_interval", Number(e.target.value))}
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
              <label style={{ fontSize: 12, color: "var(--text-secondary)", display: "block", marginBottom: 8 }}>
                Threshold (USD): <strong style={{ color: "var(--text-primary)" }}>${settings.cost_confirmation_threshold.toFixed(2)}</strong>
              </label>
              <input
                type="range"
                min={0}
                max={10}
                step={0.1}
                value={settings.cost_confirmation_threshold}
                onChange={(e) => updateSettings("cost_confirmation_threshold", Number(e.target.value))}
                style={{ width: "100%", accentColor: "var(--accent)" }}
              />
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--text-secondary)" }}>
                <span>$0.00</span>
                <span>$10.00</span>
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
                    onClick={() => updateSettings("export_format", fmt)}
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
