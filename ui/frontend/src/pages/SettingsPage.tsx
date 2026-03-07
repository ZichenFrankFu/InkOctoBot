import React, { useEffect, useState } from "react";
import { apiGet, apiPut } from "../api/client";
import type { AppSettings } from "../api/types";

const PIPELINE_ROLES: { key: string; label: string; desc: string }[] = [
  { key: "scene_planner", label: "场景规划器", desc: "设计场景结构与节奏" },
  { key: "scene_director", label: "场景导演", desc: "生成导演指令与镜头感" },
  { key: "actor_default", label: "默认角色", desc: "通用角色对话与行为" },
  { key: "actor_protagonist", label: "主角专属", desc: "主角视角的深度演绎" },
  { key: "editor_stylist", label: "风格编辑", desc: "文学风格化与润色" },
  { key: "editor_agent", label: "编辑代理", desc: "自动修改与质量提升" },
  { key: "evaluator", label: "评估器", desc: "一致性与约束检测" },
];

const PROVIDER_META: Record<string, { label: string; icon: string; hasKey: boolean; hasUrl: boolean }> = {
  openai: { label: "OpenAI", icon: "O", hasKey: true, hasUrl: false },
  anthropic: { label: "Anthropic", icon: "A", hasKey: true, hasUrl: false },
  deepseek: { label: "DeepSeek", icon: "D", hasKey: true, hasUrl: false },
  ollama: { label: "Ollama", icon: "L", hasKey: false, hasUrl: true },
  vllm: { label: "vLLM", icon: "V", hasKey: false, hasUrl: true },
  local: { label: "本地模型", icon: "M", hasKey: false, hasUrl: false },
};

type Tab = "pipeline" | "providers" | "system";

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>("pipeline");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const [toast, setToast] = useState("");

  useEffect(() => {
    apiGet<AppSettings>("/api/data/settings")
      .then(setSettings)
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
      setToast("已保存");
      setTimeout(() => setToast(""), 2000);
    } catch (e) {
      console.error("Save failed:", e);
      setToast("保存失败");
      setTimeout(() => setToast(""), 3000);
    } finally {
      setSaving(false);
    }
  };

  const modelsForProvider = (providerName: string): string[] => {
    if (!settings) return [];
    return settings.providers[providerName]?.models ?? [];
  };

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
        加载设置中...
      </div>
    );
  }

  const TABS: { key: Tab; label: string; icon: string }[] = [
    { key: "pipeline", label: "Pipeline 配置", icon: "⚙" },
    { key: "providers", label: "模型供应商", icon: "🔌" },
    { key: "system", label: "系统设置", icon: "🛠" },
  ];

  return (
    <div className="page-container" style={{ maxWidth: 1000 }}>
      {/* Header */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 24 }}>
        <div>
          <h2 style={{ fontFamily: "var(--font-serif)", fontSize: 22, fontWeight: 700, color: "var(--text-primary)", margin: 0 }}>
            设置
          </h2>
          <p style={{ fontSize: 13, color: "var(--text-secondary)", marginTop: 4 }}>
            Pipeline 模型分配、供应商管理、系统参数
          </p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          {toast && (
            <span style={{
              fontSize: 12,
              color: toast === "已保存" ? "var(--jade)" : "var(--error)",
              animation: "fadeIn 0.2s",
            }}>
              {toast}
            </span>
          )}
          <button
            className="btn-primary"
            onClick={save}
            disabled={!dirty || saving}
            style={{ opacity: dirty ? 1 : 0.5, padding: "8px 24px" }}
          >
            {saving ? "保存中..." : dirty ? "保存更改" : "已保存"}
          </button>
        </div>
      </div>

      {/* Tab bar */}
      <div style={{ display: "flex", gap: 4, marginBottom: 24, background: "var(--bg-secondary)", padding: 4, borderRadius: 10 }}>
        {TABS.map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            style={{
              flex: 1,
              padding: "10px 16px",
              border: "none",
              borderRadius: 8,
              cursor: "pointer",
              fontSize: 13,
              fontWeight: tab === t.key ? 600 : 400,
              background: tab === t.key ? "var(--bg-surface)" : "transparent",
              color: tab === t.key ? "var(--text-primary)" : "var(--text-secondary)",
              boxShadow: tab === t.key ? "0 1px 3px rgba(0,0,0,0.2)" : "none",
              transition: "all 0.15s",
            }}
          >
            {t.icon} {t.label}
          </button>
        ))}
      </div>

      {/* ===== Pipeline Tab ===== */}
      {tab === "pipeline" && (
        <div>
          <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 16, padding: "12px 16px", background: "var(--bg-secondary)", borderRadius: 8, borderLeft: "3px solid var(--accent)" }}>
            为 Film Pipeline 中的每个 Agent 角色分配模型供应商和具体模型。请先在「模型供应商」中启用并配置供应商。
          </div>
          <div className="card">
            <div className="card-body" style={{ padding: 0 }}>
              {PIPELINE_ROLES.map((role, idx) => {
                const assignment = settings.pipeline[role.key] || { provider: "", model: "", compare_models: [] };
                const models = modelsForProvider(assignment.provider);
                const providers = enabledProviders();

                return (
                  <div
                    key={role.key}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 16,
                      padding: "16px 20px",
                      borderBottom: idx < PIPELINE_ROLES.length - 1 ? "1px solid var(--border-subtle)" : "none",
                    }}
                  >
                    <div style={{ flex: "0 0 180px" }}>
                      <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>{role.label}</div>
                      <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 2 }}>{role.desc}</div>
                    </div>
                    <div style={{ flex: 1, display: "flex", gap: 12 }}>
                      <select
                        className="select"
                        value={assignment.provider}
                        onChange={(e) => {
                          updatePipeline(role.key, "provider", e.target.value);
                          const newModels = modelsForProvider(e.target.value);
                          updatePipeline(role.key, "model", newModels[0] || "");
                        }}
                        style={{ flex: 1 }}
                      >
                        <option value="">-- 选择供应商 --</option>
                        {providers.map((p) => (
                          <option key={p} value={p}>
                            {PROVIDER_META[p]?.label || p}
                          </option>
                        ))}
                      </select>
                      <select
                        className="select"
                        value={assignment.model}
                        onChange={(e) => updatePipeline(role.key, "model", e.target.value)}
                        disabled={!assignment.provider}
                        style={{ flex: 1, opacity: assignment.provider ? 1 : 0.4 }}
                      >
                        <option value="">-- 选择模型 --</option>
                        {models.map((m) => (
                          <option key={m} value={m}>{m}</option>
                        ))}
                      </select>
                    </div>
                    <div style={{
                      width: 8, height: 8, borderRadius: 4, flexShrink: 0,
                      background: assignment.provider && assignment.model ? "var(--jade)" : "var(--text-disabled)",
                    }} />
                  </div>
                );
              })}
            </div>
          </div>
        </div>
      )}

      {/* ===== Providers Tab ===== */}
      {tab === "providers" && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
          {Object.entries(settings.providers).map(([name, prov]) => {
            const meta = PROVIDER_META[name] || { label: name, icon: "?", hasKey: false, hasUrl: false };

            return (
              <div key={name} className="card" style={{
                borderColor: prov.enabled ? "var(--accent)" : "var(--border)",
                borderWidth: prov.enabled ? 1 : 1,
                opacity: prov.enabled ? 1 : 0.7,
                transition: "all 0.2s",
              }}>
                <div className="card-header" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <div style={{
                      width: 32, height: 32, borderRadius: 8,
                      background: prov.enabled ? "var(--accent-subtle)" : "var(--bg-secondary)",
                      color: prov.enabled ? "var(--accent)" : "var(--text-disabled)",
                      display: "flex", alignItems: "center", justifyContent: "center",
                      fontSize: 14, fontWeight: 700, fontFamily: "var(--font-mono)",
                    }}>
                      {meta.icon}
                    </div>
                    <div>
                      <h3 style={{ margin: 0, fontSize: 14 }}>{meta.label}</h3>
                    </div>
                  </div>
                  <button
                    onClick={() => updateProvider(name, "enabled", !prov.enabled)}
                    style={{
                      padding: "4px 12px",
                      borderRadius: 12,
                      border: "1px solid",
                      borderColor: prov.enabled ? "var(--jade)" : "var(--border)",
                      background: prov.enabled ? "var(--jade-subtle)" : "transparent",
                      color: prov.enabled ? "var(--jade)" : "var(--text-secondary)",
                      fontSize: 11,
                      cursor: "pointer",
                      fontWeight: 600,
                    }}
                  >
                    {prov.enabled ? "已启用" : "未启用"}
                  </button>
                </div>
                <div className="card-body">
                  {meta.hasKey && (
                    <div style={{ marginBottom: 12 }}>
                      <label className="label">API Key</label>
                      <input
                        className="input"
                        type="password"
                        value={prov.api_key || ""}
                        onChange={(e) => updateProvider(name, "api_key", e.target.value)}
                        placeholder="sk-..."
                        style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
                      />
                    </div>
                  )}
                  {meta.hasUrl && (
                    <div style={{ marginBottom: 12 }}>
                      <label className="label">Base URL</label>
                      <input
                        className="input"
                        value={prov.base_url || ""}
                        onChange={(e) => updateProvider(name, "base_url", e.target.value)}
                        placeholder="http://localhost:11434"
                        style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
                      />
                    </div>
                  )}
                  <div>
                    <label className="label">模型列表（逗号分隔）</label>
                    <input
                      className="input"
                      value={(prov.models || []).join(", ")}
                      onChange={(e) =>
                        updateProvider(name, "models", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))
                      }
                      placeholder="gpt-4o, gpt-4o-mini, ..."
                      style={{ fontFamily: "var(--font-mono)", fontSize: 12 }}
                    />
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* ===== System Tab ===== */}
      {tab === "system" && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, maxWidth: 800 }}>
          <div className="card">
            <div className="card-header"><h3>自动保存</h3></div>
            <div className="card-body">
              <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16, cursor: "pointer", fontSize: 13 }}>
                <input
                  type="checkbox"
                  checked={settings.auto_save}
                  onChange={(e) => update({ auto_save: e.target.checked })}
                  style={{ accentColor: "var(--accent)" }}
                />
                <span style={{ color: "var(--text-primary)" }}>启用自动保存</span>
              </label>
              <div style={{ marginBottom: 8 }}>
                <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                  保存间隔：
                </span>
                <span style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)" }}>
                  {settings.auto_save_interval}秒
                </span>
              </div>
              <input
                type="range"
                min={5} max={300} step={5}
                value={settings.auto_save_interval}
                onChange={(e) => update({ auto_save_interval: Number(e.target.value) })}
                style={{ width: "100%", accentColor: "var(--accent)" }}
              />
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--text-tertiary)" }}>
                <span>5秒</span><span>300秒</span>
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-header"><h3>费用确认</h3></div>
            <div className="card-body">
              <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", fontSize: 13, marginBottom: 8 }}>
                <input
                  type="checkbox"
                  checked={settings.cost_confirm}
                  onChange={(e) => update({ cost_confirm: e.target.checked })}
                  style={{ accentColor: "var(--accent)" }}
                />
                <span style={{ color: "var(--text-primary)" }}>产生费用前先确认</span>
              </label>
              <div style={{ fontSize: 11, color: "var(--text-tertiary)", lineHeight: 1.6 }}>
                启用后，在执行可能产生 API 调用费用的操作前系统会弹窗确认。
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-header"><h3>导出格式</h3></div>
            <div className="card-body">
              <div style={{ display: "flex", gap: 8 }}>
                {(["txt", "docx", "epub"] as const).map((fmt) => (
                  <button
                    key={fmt}
                    onClick={() => update({ export_format: fmt })}
                    style={{
                      flex: 1,
                      padding: "10px 0",
                      borderRadius: 8,
                      border: settings.export_format === fmt ? "2px solid var(--accent)" : "1px solid var(--border)",
                      background: settings.export_format === fmt ? "var(--accent-subtle)" : "transparent",
                      color: settings.export_format === fmt ? "var(--accent)" : "var(--text-secondary)",
                      fontSize: 13,
                      cursor: "pointer",
                      fontFamily: "var(--font-mono)",
                      fontWeight: settings.export_format === fmt ? 700 : 400,
                      textTransform: "uppercase",
                      transition: "all 0.15s",
                    }}
                  >
                    .{fmt}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="card">
            <div className="card-header"><h3>数据存储</h3></div>
            <div className="card-body">
              <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 2 }}>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <code style={{ background: "var(--bg-secondary)", padding: "2px 8px", borderRadius: 4, fontSize: 11 }}>data/projects/</code>
                  <span>项目文件</span>
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <code style={{ background: "var(--bg-secondary)", padding: "2px 8px", borderRadius: 4, fontSize: 11 }}>data/characters/</code>
                  <span>角色卡片</span>
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <code style={{ background: "var(--bg-secondary)", padding: "2px 8px", borderRadius: 4, fontSize: 11 }}>data/worldbook/</code>
                  <span>世界书</span>
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <code style={{ background: "var(--bg-secondary)", padding: "2px 8px", borderRadius: 4, fontSize: 11 }}>data/editor/</code>
                  <span>编辑器内容</span>
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <code style={{ background: "var(--bg-secondary)", padding: "2px 8px", borderRadius: 4, fontSize: 11 }}>data/novels.db</code>
                  <span>市场数据</span>
                </div>
              </div>
              <div style={{ marginTop: 16 }}>
                <button
                  className="btn"
                  onClick={() => {
                    if (window.confirm("确定要清除所有缓存数据吗？此操作不可撤销。")) {
                      localStorage.clear();
                      window.location.reload();
                    }
                  }}
                  style={{ fontSize: 12, padding: "6px 16px", color: "var(--error)", borderColor: "var(--error)" }}
                >
                  清除缓存
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
