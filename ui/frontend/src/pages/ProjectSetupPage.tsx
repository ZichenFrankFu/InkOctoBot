import React from "react";

export default function ProjectSetupPage() {
  return (
    <div>
      <h2 style={{ marginTop: 0, marginBottom: 4 }}>
        🛠️ 项目设置
      </h2>
      <p style={{ color: "#888", marginBottom: 24 }}>配置项目的四大创作维度 + 约束规则</p>

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
        <div style={{ fontSize: 48 }}>🛠️</div>
        <div style={{ fontSize: 18, fontWeight: 600, color: "#999" }}>
          Phase 2
        </div>
        <div style={{ fontSize: 13, maxWidth: 480, lineHeight: 1.8, color: "#aaa" }}>
          <div>• 世界观摘要</div>
          <div>• 人物卡列表 & 关系图</div>
          <div>• 分卷大纲编辑</div>
          <div>• 章节细纲编辑</div>
          <div>• 约束规则管理 (Constraint Wizard)</div>
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
          Coming in Phase 2
        </div>
      </div>
    </div>
  );
}
