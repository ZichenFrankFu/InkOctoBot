import React, { useEffect, useState } from "react";
import { apiGet, apiPost, apiPut } from "../api/client";
import type { AppSettings } from "../api/types";

const PIPELINE_ROLE_GROUPS: { group: string; roles: { key: string; label: string; desc: string }[] }[] = [
  {
    group: "创作工作室（Marketing）",
    roles: [
      { key: "marketing_outline", label: "大纲策划", desc: "全书大纲构思与讨论" },
      { key: "marketing_characters", label: "角色设计", desc: "角色人设创意与讨论" },
      { key: "marketing_world", label: "世界观构建", desc: "世界观设定与讨论" },
    ],
  },
  {
    group: "Film Pipeline（编辑器）",
    roles: [
      { key: "scene_planner", label: "场景规划器", desc: "设计场景结构与节奏" },
      { key: "scene_director", label: "场景导演", desc: "生成导演指令与镜头感" },
      { key: "actor_default", label: "默认角色", desc: "通用角色对话与行为" },
      { key: "actor_protagonist", label: "主角专属", desc: "主角视角的深度演绎" },
      { key: "editor_stylist", label: "风格编辑", desc: "文学风格化与润色" },
      { key: "editor_agent", label: "编辑代理", desc: "自动修改与质量提升" },
      { key: "evaluator", label: "评估器", desc: "一致性与约束检测" },
    ],
  },
  {
    group: "角色管理",
    roles: [
      { key: "character_profile_gen", label: "AI 生成人设", desc: "根据名字和定位生成角色档案" },
    ],
  },
  {
    group: "世界书",
    roles: [
      { key: "worldbook_consistency", label: "一致性检查", desc: "检测世界观设定矛盾与冲突" },
    ],
  },
];

// Flat list for backward compat
const PIPELINE_ROLES = PIPELINE_ROLE_GROUPS.flatMap(g => g.roles);

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
            为所有 AI Agent 分配模型供应商和具体模型。请先在「模型供应商」中启用并配置供应商。
          </div>
          {PIPELINE_ROLE_GROUPS.map((group) => (
            <div key={group.group} className="card" style={{ marginBottom: 16 }}>
              <div className="card-header">
                <h3 style={{ fontSize: 14, margin: 0 }}>{group.group}</h3>
              </div>
              <div className="card-body" style={{ padding: 0 }}>
                {group.roles.map((role, idx) => {
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
                        borderBottom: idx < group.roles.length - 1 ? "1px solid var(--border-subtle)" : "none",
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
                            const newProvider = e.target.value;
                            const newModels = modelsForProvider(newProvider);
                            if (!settings) return;
                            const prev = settings.pipeline[role.key] || { provider: "", model: "", compare_models: [] };
                            setSettings({
                              ...settings,
                              pipeline: {
                                ...settings.pipeline,
                                [role.key]: { ...prev, provider: newProvider, model: newModels[0] || "" },
                              },
                            });
                            setDirty(true);
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
          ))}
        </div>
      )}

      {/* ===== Providers Tab ===== */}
      {tab === "providers" && (
        <ProviderGrid
          settings={settings}
          onUpdateProvider={updateProvider}
          onSettingsChange={(s) => { setSettings(s); setDirty(true); }}
        />
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

/* ---- Provider Grid with test connection ---- */
function ProviderGrid({
  settings,
  onUpdateProvider,
  onSettingsChange,
}: {
  settings: AppSettings;
  onUpdateProvider: (name: string, field: string, value: unknown) => void;
  onSettingsChange: (s: AppSettings) => void;
}) {
  const [testing, setTesting] = useState<Record<string, boolean>>({});
  const [testResults, setTestResults] = useState<Record<string, { ok: boolean; message: string; models?: string[] }>>({});

  const testConnection = async (name: string) => {
    setTesting(prev => ({ ...prev, [name]: true }));
    setTestResults(prev => ({ ...prev, [name]: undefined as any }));
    try {
      const prov = settings.providers[name];
      const resp = await apiPost<{ connected: boolean; message: string; models?: string[] }>("/api/models/test", {
        provider: name,
        base_url: prov?.base_url || "",
        api_key: prov?.api_key || "",
        model: (prov?.models || [])[0] || "",
      });
      setTestResults(prev => ({
        ...prev,
        [name]: { ok: resp.connected, message: resp.message, models: resp.models },
      }));
      // Auto-fill detected models
      if (resp.connected && resp.models && resp.models.length > 0) {
        onUpdateProvider(name, "models", resp.models);
      }
    } catch (e: any) {
      setTestResults(prev => ({
        ...prev,
        [name]: { ok: false, message: e?.message || "连接失败" },
      }));
    }
    setTesting(prev => ({ ...prev, [name]: false }));
  };

  const autoDetectOllama = async () => {
    setTesting(prev => ({ ...prev, ollama: true }));
    try {
      const resp = await apiPost<{ connected: boolean; models: string[]; message: string }>("/api/settings/detect-ollama", {});
      setTestResults(prev => ({
        ...prev,
        ollama: { ok: resp.connected, message: resp.message, models: resp.models },
      }));
      if (resp.connected && resp.models?.length > 0) {
        // Reload settings to pick up auto-assigned models
        const newSettings = await apiGet<AppSettings>("/api/data/settings");
        onSettingsChange(newSettings);
      }
    } catch (e: any) {
      setTestResults(prev => ({
        ...prev,
        ollama: { ok: false, message: e?.message || "检测失败" },
      }));
    }
    setTesting(prev => ({ ...prev, ollama: false }));
  };

  return (
    <div>
      {/* Auto-detect banner */}
      <div style={{
        display: "flex", alignItems: "center", justifyContent: "space-between",
        padding: "12px 16px", marginBottom: 16, background: "var(--bg-surface-2)",
        borderRadius: 8, border: "1px solid var(--border)",
      }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
            Ollama 自动检测
          </div>
          <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 2 }}>
            自动检测本地 Ollama 服务和已安装的模型
          </div>
        </div>
        <button
          className="btn-primary"
          onClick={autoDetectOllama}
          disabled={testing.ollama}
          style={{ padding: "6px 16px", fontSize: 12 }}
        >
          {testing.ollama ? "检测中..." : "自动检测"}
        </button>
      </div>
      {testResults.ollama && (
        <div style={{
          padding: "8px 16px", marginBottom: 16, borderRadius: 6,
          background: testResults.ollama.ok ? "var(--jade-subtle)" : "var(--accent-subtle)",
          color: testResults.ollama.ok ? "var(--jade)" : "var(--accent)",
          fontSize: 12,
        }}>
          {testResults.ollama.message}
          {testResults.ollama.models && testResults.ollama.models.length > 0 && (
            <div style={{ marginTop: 4, color: "var(--text-secondary)" }}>
              模型: {testResults.ollama.models.join(", ")}
            </div>
          )}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
        {Object.entries(settings.providers).map(([name, prov]) => {
          const meta = PROVIDER_META[name] || { label: name, icon: "?", hasKey: false, hasUrl: false };
          const testResult = testResults[name];

          return (
            <div key={name} className="card" style={{
              borderColor: prov.enabled ? "var(--accent)" : "var(--border)",
              opacity: prov.enabled ? 1 : 0.7,
              transition: "all 0.2s",
            }}>
              <div className="card-header" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <div style={{
                    width: 32, height: 32, borderRadius: 8,
                    background: prov.enabled ? "var(--accent-subtle)" : "var(--bg-surface-2)",
                    color: prov.enabled ? "var(--accent)" : "var(--text-disabled)",
                    display: "flex", alignItems: "center", justifyContent: "center",
                    fontSize: 14, fontWeight: 700, fontFamily: "var(--font-mono)",
                  }}>
                    {meta.icon}
                  </div>
                  <h3 style={{ margin: 0, fontSize: 14 }}>{meta.label}</h3>
                </div>
                <button
                  onClick={() => onUpdateProvider(name, "enabled", !prov.enabled)}
                  style={{
                    padding: "4px 12px", borderRadius: 12, border: "1px solid",
                    borderColor: prov.enabled ? "var(--jade)" : "var(--border)",
                    background: prov.enabled ? "var(--jade-subtle)" : "transparent",
                    color: prov.enabled ? "var(--jade)" : "var(--text-secondary)",
                    fontSize: 11, cursor: "pointer", fontWeight: 600,
                  }}
                >
                  {prov.enabled ? "已启用" : "未启用"}
                </button>
              </div>
              <div className="card-body">
                {meta.hasKey && (
                  <div style={{ marginBottom: 12 }}>
                    <label className="label">API Key</label>
                    <input className="input" type="password"
                      value={prov.api_key || ""} onChange={(e) => onUpdateProvider(name, "api_key", e.target.value)}
                      placeholder="sk-..." style={{ fontFamily: "var(--font-mono)", fontSize: 12 }} />
                  </div>
                )}
                {meta.hasUrl && (
                  <div style={{ marginBottom: 12 }}>
                    <label className="label">Base URL</label>
                    <input className="input"
                      value={prov.base_url || ""} onChange={(e) => onUpdateProvider(name, "base_url", e.target.value)}
                      placeholder="http://localhost:11434" style={{ fontFamily: "var(--font-mono)", fontSize: 12 }} />
                  </div>
                )}
                <div style={{ marginBottom: 12 }}>
                  <label className="label">模型列表（逗号分隔）</label>
                  <input className="input"
                    value={(prov.models || []).join(", ")}
                    onChange={(e) => onUpdateProvider(name, "models", e.target.value.split(",").map((s) => s.trim()).filter(Boolean))}
                    placeholder="model-name, ..." style={{ fontFamily: "var(--font-mono)", fontSize: 12 }} />
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                  <button
                    className="btn"
                    onClick={() => testConnection(name)}
                    disabled={testing[name]}
                    style={{ fontSize: 11, padding: "4px 12px" }}
                  >
                    {testing[name] ? "测试中..." : "测试连接"}
                  </button>
                  {testResult && (
                    <span style={{
                      fontSize: 11,
                      color: testResult.ok ? "var(--jade)" : "var(--error)",
                    }}>
                      {testResult.ok ? "连接成功" : "连接失败"}
                    </span>
                  )}
                </div>
                {testResult && !testResult.ok && (
                  <div style={{ marginTop: 6, fontSize: 11, color: "var(--error)", lineHeight: 1.5 }}>
                    {testResult.message}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
