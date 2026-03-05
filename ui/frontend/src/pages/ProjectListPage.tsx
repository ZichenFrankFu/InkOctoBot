import React from "react";

export default function ProjectListPage() {
  return (
    <div>
      <h2 style={{ marginTop: 0, marginBottom: 4 }}>
        📂 项目列表
      </h2>
      <p style={{ color: "#888", marginBottom: 24 }}>创建和管理你的网文创作项目</p>

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
        <div style={{ fontSize: 48 }}>📂</div>
        <div style={{ fontSize: 18, fontWeight: 600, color: "#999" }}>
          Phase 2
        </div>
        <div style={{ fontSize: 13, maxWidth: 480, lineHeight: 1.8, color: "#aaa" }}>
          <div>• 创建 / 打开 / 归档项目</div>
          <div>• 项目进度概览</div>
          <div>• 最近编辑的章节快速跳转</div>
          <div>• 项目数据 & 成本统计</div>
          <div>• 导出项目 (TXT / DOCX / EPUB)</div>
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
