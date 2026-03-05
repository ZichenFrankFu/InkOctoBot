import React from "react";

export default function SettingsPage() {
  return (
    <div className="page-container">
      <div className="page-header">
        <h2>设置</h2>
        <p>模型配置、API Key 管理、约束预设、系统参数</p>
      </div>

      <div className="placeholder-page">
        <div className="placeholder-hero">
          <span className="placeholder-hero-icon">⚙️</span>
          <h3>系统设置</h3>
          <p>所有配置均可在 UI 中完成，无需编辑配置文件</p>
        </div>

        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20, maxWidth: 800, margin: "0 auto" }}>
          {/* Model Config */}
          <div className="card">
            <div className="card-header"><h3>🤖 模型配置</h3></div>
            <div className="card-body">
              {[
                { label: "OpenAI", desc: "GPT-4o / GPT-4o-mini" },
                { label: "Anthropic", desc: "Claude Sonnet / Haiku" },
                { label: "DeepSeek", desc: "DeepSeek-V3" },
                { label: "Ollama 本地", desc: "Qwen / Llama" },
                { label: "vLLM 本地", desc: "自定义模型服务" },
              ].map((p) => (
                <div key={p.label} style={{ padding: "8px 0", borderBottom: "1px solid var(--ink-50)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 500 }}>{p.label}</div>
                    <div className="text-xs text-muted">{p.desc}</div>
                  </div>
                  <div className="placeholder-toggle" />
                </div>
              ))}
            </div>
          </div>

          {/* API Keys */}
          <div className="card">
            <div className="card-header"><h3>🔑 API Key 管理</h3></div>
            <div className="card-body">
              <p className="text-sm text-muted" style={{ marginBottom: 12 }}>API Key 使用 Fernet 加密存储在本地 keyring 中，不上传到任何服务器。</p>
              {["OpenAI", "Anthropic", "DeepSeek"].map((name) => (
                <div key={name} style={{ padding: "10px 0", borderBottom: "1px solid var(--ink-50)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <span style={{ fontSize: 13 }}>{name}</span>
                  <span className="tag category">未配置</span>
                </div>
              ))}
            </div>
          </div>

          {/* Preset */}
          <div className="card">
            <div className="card-header"><h3>📋 模型预设方案</h3></div>
            <div className="card-body">
              {[
                { name: "成本优先", desc: "本地模型为主，仅关键步骤用云端", active: false },
                { name: "均衡", desc: "本地+云端混合", active: true },
                { name: "质量优先", desc: "全部使用最强云端模型", active: false },
              ].map((p) => (
                <div key={p.name} style={{ padding: "10px 12px", borderRadius: "var(--radius-sm)", marginBottom: 6, background: p.active ? "var(--accent-muted)" : "transparent", border: p.active ? "1px solid var(--accent)" : "1px solid var(--ink-100)", cursor: "pointer" }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: p.active ? "var(--accent)" : "var(--ink-700)" }}>{p.name}</div>
                  <div className="text-xs text-muted">{p.desc}</div>
                </div>
              ))}
            </div>
          </div>

          {/* System */}
          <div className="card">
            <div className="card-header"><h3>🔧 系统设置</h3></div>
            <div className="card-body">
              {[
                { label: "暗色主题", desc: "长时间写作护眼" },
                { label: "自动保存", desc: "每 30 秒自动保存草稿" },
                { label: "成本确认", desc: "商业 API 调用前显示预估" },
                { label: "导出格式", desc: "TXT / DOCX / EPUB" },
              ].map((s) => (
                <div key={s.label} style={{ padding: "8px 0", borderBottom: "1px solid var(--ink-50)", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <div>
                    <div style={{ fontSize: 13, fontWeight: 500 }}>{s.label}</div>
                    <div className="text-xs text-muted">{s.desc}</div>
                  </div>
                  <div className="placeholder-toggle" />
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
