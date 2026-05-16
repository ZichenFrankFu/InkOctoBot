import React, { useEffect, useState } from "react";
import { apiGet, apiPost, apiPut } from "../api/client";
import { useToast } from "../components/shared/Toast";
import type { AppSettings } from "../api/types";
import PromptPreview from "../components/reference/PromptPreview";

const PIPELINE_ROLE_GROUPS: { group: string; roles: { key: string; label: string; desc: string }[] }[] = [
  {
    group: "参考作品数据库",
    roles: [
      { key: "reference_extractor", label: "参考作品分段提取", desc: "角色 / 设定 / 叙事 / 节奏（无 AI 时回退 NLP）" },
      { key: "reference_web_search", label: "参考作品 AI 联网补全", desc: "元数据补全；需要支持 web search 的模型（如 Claude Sonnet 4 / GPT-4o）" },
    ],
  },
  {
    group: "开书（头脑风暴 & 校准）",
    roles: [
      { key: "scene_planner", label: "头脑风暴", desc: "大纲、角色、世界观综合构思" },
    ],
  },
  {
    group: "Creative Writing Pipeline（编辑器）",
    roles: [
      { key: "scene_director", label: "场景导演", desc: "拆分场景、生成导演指令" },
      { key: "actor_default", label: "默认角色", desc: "通用角色对话与行为" },
      { key: "actor_protagonist", label: "主角专属", desc: "主角视角的深度演绎" },
      { key: "editor_stylist", label: "风格编辑", desc: "文学风格化与章节组装" },
      { key: "editor_agent", label: "编辑代理", desc: "自动修改与质量提升" },
      { key: "evaluator", label: "评估器", desc: "一致性与约束检测" },
    ],
  },
  {
    group: "角色 & 世界书",
    roles: [
      { key: "character_profile_gen", label: "AI 生成人设", desc: "根据名字和定位生成角色档案" },
      { key: "worldbook_consistency", label: "一致性检查", desc: "检测世界观设定矛盾与冲突" },
    ],
  },
  {
    group: "分析 Skills",
    roles: [
      { key: "analyzer", label: "分析器", desc: "文本分析与特征提取" },
    ],
  },
];

// Flat list for backward compat
const PIPELINE_ROLES = PIPELINE_ROLE_GROUPS.flatMap(g => g.roles);

const PROVIDER_META: Record<string, { label: string; icon: string; hasKey: boolean; hasUrl: boolean; group: string }> = {
  // 国际 API
  openai: { label: "OpenAI", icon: "O", hasKey: true, hasUrl: false, group: "international" },
  anthropic: { label: "Anthropic", icon: "A", hasKey: true, hasUrl: false, group: "international" },
  gemini: { label: "Google Gemini", icon: "G", hasKey: true, hasUrl: false, group: "international" },
  deepseek: { label: "DeepSeek", icon: "D", hasKey: true, hasUrl: false, group: "international" },
  grok: { label: "Grok (xAI)", icon: "X", hasKey: true, hasUrl: false, group: "international" },
  // 国内 API
  volcengine: { label: "火山方舟 (豆包)", icon: "V", hasKey: true, hasUrl: true, group: "china" },
  baidu_qianfan: { label: "百度千帆 (文心)", icon: "B", hasKey: true, hasUrl: false, group: "china" },
  aliyun_bailian: { label: "阿里云百炼 (通义)", icon: "Q", hasKey: true, hasUrl: true, group: "china" },
  // 自部署
  ollama: { label: "Ollama", icon: "O", hasKey: false, hasUrl: true, group: "self_hosted" },
};

const PROVIDER_GROUPS: { key: string; label: string }[] = [
  { key: "international", label: "国际 API" },
  { key: "china", label: "国内 API" },
  { key: "self_hosted", label: "自部署" },
];

type Tab = "pipeline" | "providers" | "prompts" | "system";

export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>("pipeline");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [settings, setSettings] = useState<AppSettings | null>(null);
  const { toast } = useToast();

  useEffect(() => {
    apiGet<AppSettings>("/api/data/settings")
      .then((s) => {
        // Auto-fill empty pipeline roles with the first enabled provider that has models
        let needsSave = false;
        const providers = s.providers || {};
        // Find the first enabled provider with models
        let autoProvider = "";
        let autoModel = "";
        for (const [pname, pcfg] of Object.entries(providers)) {
          if (pcfg.enabled && pcfg.models && pcfg.models.length > 0) {
            autoProvider = pname;
            autoModel = pcfg.models[0];
            break;
          }
        }
        if (autoProvider && autoModel && s.pipeline) {
          const newPipeline = { ...s.pipeline };
          for (const [role, cfg] of Object.entries(newPipeline)) {
            if (!cfg.model) {
              newPipeline[role] = { ...cfg, provider: autoProvider, model: autoModel };
              needsSave = true;
            }
          }
          if (needsSave) {
            s = { ...s, pipeline: newPipeline };
          }
        }
        setSettings(s);
        if (needsSave) setDirty(true);
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
    if (!settings) return;

    // Validate: warn if any pipeline role has a provider selected but the provider has no API key
    const providers = settings.providers || {};
    for (const [role, cfg] of Object.entries(settings.pipeline || {})) {
      if (cfg.provider) {
        const provMeta = PROVIDER_META[cfg.provider];
        const provCfg = providers[cfg.provider];
        if (provMeta?.hasKey && provCfg && !provCfg.api_key?.trim()) {
          toast(`供应商「${provMeta.label}」的 API Key 为空，请先在「模型供应商」中配置`, "error");
          return;
        }
      }
    }

    setSaving(true);
    try {
      await apiPut("/api/data/settings", settings);
      setDirty(false);
      toast("已保存", "success");
    } catch (e: any) {
      toast(e?.message || "保存失败", "error");
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
    { key: "pipeline", label: "Pipeline 配置", icon: "\u2699" },
    { key: "providers", label: "模型供应商", icon: "\u2261" },
    { key: "prompts", label: "LLM Prompt", icon: "\u270E" },
    { key: "system", label: "系统设置", icon: "\u2638" },
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

      {/* ===== Prompts Tab ===== */}
      {tab === "prompts" && <PromptsTab />}

      {/* ===== System Tab ===== */}
      {tab === "system" && (
        <SystemTab settings={settings} onUpdate={update} onSettingsChange={(s) => { setSettings(s); setDirty(true); }} />
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

      {PROVIDER_GROUPS.map(group => {
        const groupProviders = Object.entries(settings.providers).filter(
          ([name]) => (PROVIDER_META[name]?.group || "international") === group.key
        );
        if (groupProviders.length === 0) return null;
        return (
          <div key={group.key} style={{ marginBottom: 20 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 10, paddingLeft: 4 }}>
              {group.label}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
              {groupProviders.map(([name, prov]) => {
                const meta = PROVIDER_META[name] || { label: name, icon: "?", hasKey: false, hasUrl: false, group: "international" };
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
      })}
    </div>
  );
}

/* ---- Usage Stats Types ---- */
interface UsageData {
  total_input_tokens: number;
  total_output_tokens: number;
  total_calls: number;
  by_provider: Record<string, { input_tokens: number; output_tokens: number; calls: number }>;
  by_model: Record<string, { input_tokens: number; output_tokens: number; calls: number }>;
  by_role: Record<string, { input_tokens: number; output_tokens: number; calls: number }>;
}

const ROLE_LABELS: Record<string, string> = {
  scene_director: "场景导演",
  scene_planner: "头脑风暴",
  actor_default: "默认角色",
  actor_protagonist: "主角专属",
  editor_stylist: "风格编辑",
  editor_agent: "编辑代理",
  evaluator: "评估器",
  character_profile_gen: "AI 生成人设",
  worldbook_consistency: "一致性检查",
  analyzer: "分析器",
};

function formatTokens(n: number): string {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + "M";
  if (n >= 1_000) return (n / 1_000).toFixed(1) + "K";
  return String(n);
}

/* ---- System Tab with crawler DB, GGUF, usage, etc. ---- */
function SystemTab({
  settings,
  onUpdate,
  onSettingsChange,
}: {
  settings: AppSettings;
  onUpdate: (patch: Partial<AppSettings>) => void;
  onSettingsChange: (s: AppSettings) => void;
}) {
  const [crawlerPath, setCrawlerPath] = useState((settings as any).crawler_db_path || "");
  const [crawlerStatus, setCrawlerStatus] = useState<{ ok: boolean; message: string } | null>(null);
  const [ggufModels, setGgufModels] = useState<{ name: string; path: string; size_mb: number }[]>([]);
  const [detectingGguf, setDetectingGguf] = useState(false);
  const [usage, setUsage] = useState<UsageData | null>(null);
  const [usageLoading, setUsageLoading] = useState(false);

  const { toast } = useToast();

  // Auto-refresh usage every 10s
  useEffect(() => {
    let cancelled = false;
    const fetchUsage = () => {
      apiGet<UsageData>("/api/generation/usage")
        .then(d => { if (!cancelled) setUsage(d); })
        .catch((e) => toast(e.message || "加载用量数据失败", "error"));
    };
    fetchUsage();
    const iv = setInterval(fetchUsage, 10_000);
    return () => { cancelled = true; clearInterval(iv); };
  }, []);

  const resetUsage = async () => {
    setUsageLoading(true);
    try {
      await apiPost("/api/generation/usage/reset", {});
      setUsage({ total_input_tokens: 0, total_output_tokens: 0, total_calls: 0, by_provider: {}, by_model: {}, by_role: {} });
      toast("用量统计已重置", "success");
    } catch (e: any) { toast(e?.message || "重置失败", "error"); }
    setUsageLoading(false);
  };

  const setCrawlerDbPath = async () => {
    if (!crawlerPath.trim()) return;
    try {
      const resp = await apiPost<{ status: string; message: string; path?: string }>("/api/settings/crawler-db-path", { path: crawlerPath.trim() });
      setCrawlerStatus({ ok: resp.status === "ok", message: resp.message });
      if (resp.status === "ok") {
        onSettingsChange({ ...settings, crawler_db_path: resp.path } as any);
      }
    } catch (e: any) {
      setCrawlerStatus({ ok: false, message: e?.message || "设置失败" });
    }
  };

  const detectGguf = async () => {
    setDetectingGguf(true);
    try {
      const resp = await apiPost<{ models: typeof ggufModels; message: string }>("/api/settings/detect-gguf", {});
      setGgufModels(resp.models || []);
    } catch (e: any) { toast(e?.message || "检测 GGUF 模型失败", "error"); }
    setDetectingGguf(false);
  };

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, maxWidth: 800 }}>
      {/* Crawler DB Path */}
      <div className="card" style={{ gridColumn: "1 / -1" }}>
        <div className="card-header"><h3>爬虫数据库路径</h3></div>
        <div className="card-body">
          <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginBottom: 12, lineHeight: 1.6 }}>
            指定 InkOctoBot_Crawler.db 文件的路径，用于市场热门题材和排行榜数据。
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <input
              className="input"
              value={crawlerPath}
              onChange={(e) => setCrawlerPath(e.target.value)}
              placeholder="/path/to/InkOctoBot_Crawler.db"
              style={{ flex: 1, fontFamily: "var(--font-mono)", fontSize: 12 }}
            />
            <button className="btn-primary" onClick={setCrawlerDbPath} style={{ padding: "8px 16px", fontSize: 12, whiteSpace: "nowrap" }}>
              设置路径
            </button>
          </div>
          {crawlerStatus && (
            <div style={{
              marginTop: 8, fontSize: 11, padding: "6px 12px", borderRadius: 6,
              background: crawlerStatus.ok ? "var(--jade-subtle)" : "var(--accent-subtle)",
              color: crawlerStatus.ok ? "var(--jade)" : "var(--error)",
            }}>
              {crawlerStatus.message}
            </div>
          )}
        </div>
      </div>

      {/* GGUF Model Detection */}
      <div className="card" style={{ gridColumn: "1 / -1" }}>
        <div className="card-header" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <h3>本地 GGUF 模型</h3>
          <button className="btn" onClick={detectGguf} disabled={detectingGguf} style={{ fontSize: 11, padding: "4px 12px" }}>
            {detectingGguf ? "检测中..." : "扫描 models/ 目录"}
          </button>
        </div>
        <div className="card-body">
          <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginBottom: 8 }}>
            自动检测 models/ 目录下的 GGUF 格式模型文件。
          </div>
          {ggufModels.length > 0 ? (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {ggufModels.map((m) => (
                <div key={m.path} style={{
                  display: "flex", alignItems: "center", justifyContent: "space-between",
                  padding: "8px 12px", background: "var(--bg-secondary)", borderRadius: 6, fontSize: 12,
                }}>
                  <span style={{ fontWeight: 600, color: "var(--text-primary)" }}>{m.name}</span>
                  <span style={{ color: "var(--text-tertiary)", fontFamily: "var(--font-mono)", fontSize: 11 }}>{m.size_mb} MB</span>
                </div>
              ))}
            </div>
          ) : (
            <div style={{ fontSize: 12, color: "var(--text-disabled)" }}>
              点击「扫描」按钮检测本地模型
            </div>
          )}
        </div>
      </div>

      {/* Auto-save */}
      <div className="card">
        <div className="card-header"><h3>自动保存</h3></div>
        <div className="card-body">
          <label style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16, cursor: "pointer", fontSize: 13 }}>
            <input type="checkbox" checked={settings.auto_save} onChange={(e) => onUpdate({ auto_save: e.target.checked })} style={{ accentColor: "var(--accent)" }} />
            <span style={{ color: "var(--text-primary)" }}>启用自动保存</span>
          </label>
          <div style={{ marginBottom: 8 }}>
            <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>保存间隔：</span>
            <span style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)" }}>{settings.auto_save_interval}秒</span>
          </div>
          <input type="range" min={5} max={300} step={5} value={settings.auto_save_interval} onChange={(e) => onUpdate({ auto_save_interval: Number(e.target.value) })} style={{ width: "100%", accentColor: "var(--accent)" }} />
          <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--text-tertiary)" }}>
            <span>5秒</span><span>300秒</span>
          </div>
          <div style={{ marginTop: 16, paddingTop: 16, borderTop: "1px solid var(--border-subtle)" }}>
            <div style={{ marginBottom: 8 }}>
              <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>最大保存备份数量：</span>
              <span style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)" }}>{settings.max_backup_versions || 10} 个版本</span>
            </div>
            <input type="range" min={1} max={20} step={1} value={settings.max_backup_versions || 10} onChange={(e) => onUpdate({ max_backup_versions: Number(e.target.value) })} style={{ width: "100%", accentColor: "var(--accent)" }} />
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 10, color: "var(--text-tertiary)" }}>
              <span>1</span><span>20</span>
            </div>
            <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 4, lineHeight: 1.5 }}>
              每章保留的自动备份版本数。超出时自动删除最早的备份。
            </div>
          </div>
        </div>
      </div>

      {/* Cost confirm */}
      <div className="card">
        <div className="card-header"><h3>费用确认</h3></div>
        <div className="card-body">
          <label style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer", fontSize: 13, marginBottom: 8 }}>
            <input type="checkbox" checked={settings.cost_confirm} onChange={(e) => onUpdate({ cost_confirm: e.target.checked })} style={{ accentColor: "var(--accent)" }} />
            <span style={{ color: "var(--text-primary)" }}>产生费用前先确认</span>
          </label>
          <div style={{ fontSize: 11, color: "var(--text-tertiary)", lineHeight: 1.6 }}>启用后，在执行可能产生 API 调用费用的操作前系统会弹窗确认。</div>
        </div>
      </div>

      {/* Export format */}
      <div className="card">
        <div className="card-header"><h3>导出格式</h3></div>
        <div className="card-body">
          <div style={{ display: "flex", gap: 8 }}>
            {(["txt", "docx", "epub"] as const).map((fmt) => (
              <button key={fmt} onClick={() => onUpdate({ export_format: fmt })} style={{
                flex: 1, padding: "10px 0", borderRadius: 8,
                border: settings.export_format === fmt ? "2px solid var(--accent)" : "1px solid var(--border)",
                background: settings.export_format === fmt ? "var(--accent-subtle)" : "transparent",
                color: settings.export_format === fmt ? "var(--accent)" : "var(--text-secondary)",
                fontSize: 13, cursor: "pointer", fontFamily: "var(--font-mono)",
                fontWeight: settings.export_format === fmt ? 700 : 400, textTransform: "uppercase", transition: "all 0.15s",
              }}>
                .{fmt}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* API Usage */}
      <div className="card" style={{ gridColumn: "1 / -1" }}>
        <div className="card-header" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <h3>API 用量统计</h3>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>持久化 · 每 10 秒自动刷新</span>
            <button className="btn" onClick={resetUsage} disabled={usageLoading}
              style={{ fontSize: 11, padding: "4px 12px", color: "var(--error)", borderColor: "var(--error)" }}>
              {usageLoading ? "..." : "重置"}
            </button>
          </div>
        </div>
        <div className="card-body">
          {usage ? (
            <div>
              {/* Summary row */}
              <div style={{ display: "flex", gap: 24, marginBottom: 16 }}>
                {[
                  { label: "总调用", value: String(usage.total_calls), color: "var(--accent)" },
                  { label: "输入 Tokens", value: formatTokens(usage.total_input_tokens), color: "var(--indigo)" },
                  { label: "输出 Tokens", value: formatTokens(usage.total_output_tokens), color: "var(--jade)" },
                  { label: "总 Tokens", value: formatTokens(usage.total_input_tokens + usage.total_output_tokens), color: "var(--gold)" },
                ].map(s => (
                  <div key={s.label} style={{
                    flex: 1, padding: "12px 16px", background: "var(--bg-secondary)", borderRadius: 8,
                    borderLeft: `3px solid ${s.color}`,
                  }}>
                    <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginBottom: 4 }}>{s.label}</div>
                    <div style={{ fontSize: 20, fontWeight: 700, color: "var(--text-primary)", fontFamily: "var(--font-mono)" }}>{s.value}</div>
                  </div>
                ))}
              </div>

              {/* By provider */}
              {Object.keys(usage.by_provider).length > 0 && (
                <div style={{ marginBottom: 16 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 8 }}>按供应商</div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    {Object.entries(usage.by_provider).map(([name, d]) => (
                      <div key={name} style={{
                        display: "flex", alignItems: "center", justifyContent: "space-between",
                        padding: "8px 12px", background: "var(--bg-secondary)", borderRadius: 6, fontSize: 12,
                      }}>
                        <span style={{ fontWeight: 600 }}>{name}</span>
                        <span style={{ color: "var(--text-tertiary)", fontFamily: "var(--font-mono)", fontSize: 11 }}>
                          {d.calls} 次 · 输入 {formatTokens(d.input_tokens)} · 输出 {formatTokens(d.output_tokens)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* By model */}
              {usage.by_model && Object.keys(usage.by_model).length > 0 && (
                <div style={{ marginBottom: 16 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 8 }}>按模型</div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    {Object.entries(usage.by_model).map(([name, d]) => (
                      <div key={name} style={{
                        display: "flex", alignItems: "center", justifyContent: "space-between",
                        padding: "8px 12px", background: "var(--bg-secondary)", borderRadius: 6, fontSize: 12,
                      }}>
                        <span style={{ fontWeight: 600, fontFamily: "var(--font-mono)", fontSize: 11 }}>{name}</span>
                        <span style={{ color: "var(--text-tertiary)", fontFamily: "var(--font-mono)", fontSize: 11 }}>
                          {d.calls} 次 · 输入 {formatTokens(d.input_tokens)} · 输出 {formatTokens(d.output_tokens)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* By role */}
              {Object.keys(usage.by_role).length > 0 && (
                <div>
                  <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 8 }}>按 Agent 角色</div>
                  <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    {Object.entries(usage.by_role).map(([role, d]) => (
                      <div key={role} style={{
                        display: "flex", alignItems: "center", justifyContent: "space-between",
                        padding: "8px 12px", background: "var(--bg-secondary)", borderRadius: 6, fontSize: 12,
                      }}>
                        <span style={{ fontWeight: 600 }}>{ROLE_LABELS[role] || role}</span>
                        <span style={{ color: "var(--text-tertiary)", fontFamily: "var(--font-mono)", fontSize: 11 }}>
                          {d.calls} 次 · 输入 {formatTokens(d.input_tokens)} · 输出 {formatTokens(d.output_tokens)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {usage.total_calls === 0 && (
                <div style={{ fontSize: 12, color: "var(--text-disabled)", textAlign: "center", padding: 16 }}>
                  暂无 API 调用记录。开始使用 Pipeline 或头脑风暴后将自动统计。
                </div>
              )}
            </div>
          ) : (
            <div style={{ fontSize: 12, color: "var(--text-disabled)", textAlign: "center", padding: 16 }}>
              加载用量数据中...
            </div>
          )}
        </div>
      </div>

      {/* Data storage info */}
      <div className="card">
        <div className="card-header"><h3>数据存储</h3></div>
        <div className="card-body">
          <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 2 }}>
            {[
              ["data/projects/", "项目文件"],
              ["data/characters/", "角色卡片"],
              ["data/worldbook/", "世界书"],
              ["data/editor/", "编辑器内容"],
            ].map(([path, label]) => (
              <div key={path} style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <code style={{ background: "var(--bg-secondary)", padding: "2px 8px", borderRadius: 4, fontSize: 11 }}>{path}</code>
                <span>{label}</span>
              </div>
            ))}
          </div>
          <div style={{ marginTop: 16 }}>
            <button className="btn" onClick={() => { if (window.confirm("确定要清除所有缓存数据吗？此操作不可撤销。")) { localStorage.clear(); window.location.reload(); } }}
              style={{ fontSize: 12, padding: "6px 16px", color: "var(--error)", borderColor: "var(--error)" }}>
              清除缓存
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

/* ───────────────── LLM Prompt 模板管理 ───────────────── */

interface PromptItem {
  key: string;
  description: string;
  vars: string[];
  has_override: boolean;
}

function PromptsTab() {
  const [items, setItems] = useState<PromptItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const r = await apiGet<{ items: PromptItem[] }>("/api/references/prompts");
      setItems(r.items || []);
    } catch (e) {
      console.error("prompts list failed:", e);
    } finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  return (
    <div>
      <div style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 16, padding: "12px 16px", background: "var(--bg-secondary)", borderRadius: 8, borderLeft: "3px solid var(--accent)" }}>
        参考作品 LLM 调用使用的 prompt 模板。点击「编辑」可查看出厂默认 + 当前内容、修改、并保存为新默认。
        每次提取/对话时可单独覆盖（不影响保存的默认）。
      </div>

      {loading ? (
        <div className="text-xs text-muted" style={{ padding: 20, textAlign: "center" }}>加载中...</div>
      ) : (
        <div className="card" style={{ marginBottom: 16 }}>
          <div className="card-body" style={{ padding: 0 }}>
            {items.map((it, idx) => (
              <div key={it.key} style={{
                padding: "14px 18px",
                borderBottom: idx < items.length - 1 ? "1px solid var(--border-subtle)" : "none",
                display: "flex", alignItems: "center", gap: 12,
              }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="flex items-center gap-8" style={{ marginBottom: 3 }}>
                    <span style={{ fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>
                      {it.key}
                    </span>
                    {it.has_override && (
                      <span className="tag" style={{
                        fontSize: 10, padding: "1px 6px",
                        color: "var(--gold)", background: "var(--bg-surface-2)",
                        border: "1px solid var(--gold)",
                      }}>已覆盖</span>
                    )}
                  </div>
                  <div className="text-xs text-muted">{it.description || "—"}</div>
                  {it.vars.length > 0 && (
                    <div className="text-xs text-muted" style={{ marginTop: 2, fontFamily: "var(--font-mono)" }}>
                      vars: {it.vars.join(", ")}
                    </div>
                  )}
                </div>
                <button
                  className={selected === it.key ? "btn-primary" : "btn"}
                  style={{ fontSize: 12, padding: "4px 12px" }}
                  onClick={() => setSelected(selected === it.key ? null : it.key)}
                >{selected === it.key ? "关闭" : "编辑"}</button>
              </div>
            ))}
          </div>
        </div>
      )}

      {selected && (
        <PromptPreview
          promptKey={selected}
          onSaved={load}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}
