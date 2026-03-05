import React from "react";

export default function SettingsPage() {
  return (
    <div>
      <h2 style={{ marginTop: 0, marginBottom: 4 }}>
        ⚙️ 设置
      </h2>
      <p style={{ color: "#888", marginBottom: 24 }}>模型配置、成本追踪、系统偏好</p>

      <div
        style={{
          border: "2px dashed #ddd",
          borderRadius: 12,
          padding: 40,
          textAlign: "center",
          color: "#bbb",
          minHeight: 360,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: 16,
        }}
      >
        <div style={{ fontSize: 48 }}>⚙️</div>
        <div style={{ fontSize: 18, fontWeight: 600, color: "#999" }}>
          Phase 3
        </div>
        <div style={{ fontSize: 13, maxWidth: 480, lineHeight: 1.8, color: "#aaa" }}>
          <div>• 模型 Provider 管理 (OpenAI / Anthropic / DeepSeek / Ollama / vLLM)</div>
          <div>• API Key 加密存储 (keyring)</div>
          <div>• Per-Agent 模型路由配置</div>
          <div>• 成本预算 & 用量追踪</div>
          <div>• 暗色 / 亮色主题切换</div>
          <div>• LoRA 模型管理 (Phase 4)</div>
        </div>
        <div
          style={{
            marginTop: 8,
            padding: "6px 20px",
            borderRadius: 20,
            background: "#f0f0f0",
            fontSize: 12,
            color: "#999",
          }}
        >
          Coming in Phase 3
        </div>
      </div>
    </div>
  );
}
