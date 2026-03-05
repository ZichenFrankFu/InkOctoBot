import React from "react";

export default function AnalysisDashboardPage() {
  return (
    <div>
      <h2 style={{ marginTop: 0, marginBottom: 4 }}>
        📈 分析面板
      </h2>
      <p style={{ color: "#888", marginBottom: 24 }}>可视化市场趋势、题材热度分布、竞争格局</p>

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
        <div style={{ fontSize: 48 }}>📈</div>
        <div style={{ fontSize: 18, fontWeight: 600, color: "#999" }}>
          Phase 1
        </div>
        <div style={{ fontSize: 13, maxWidth: 480, lineHeight: 1.8, color: "#aaa" }}>
          <div>• 题材份额 & 热度趋势图 (Recharts)</div>
          <div>• 跨平台差异对比</div>
          <div>• 集中度 HHI 指标可视化</div>
          <div>• 新书入榜率追踪</div>
          <div>• 潜在风口自动识别</div>
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
          Coming in Phase 1
        </div>
      </div>
    </div>
  );
}
