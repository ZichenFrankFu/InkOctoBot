import React from "react";

export default function WorldBookEditorPage() {
  return (
    <div>
      <h2 style={{ marginTop: 0, marginBottom: 4 }}>
        🌍 世界书
      </h2>
      <p style={{ color: "#888", marginBottom: 24 }}>编辑世界观设定、力量体系、社会结构</p>

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
        <div style={{ fontSize: 48 }}>🌍</div>
        <div style={{ fontSize: 18, fontWeight: 600, color: "#999" }}>
          Phase 2
        </div>
        <div style={{ fontSize: 13, maxWidth: 480, lineHeight: 1.8, color: "#aaa" }}>
          <div>• 结构化世界书编辑器</div>
          <div>• 力量体系 & 等级定义</div>
          <div>• 地理 / 历史 / 社会规则</div>
          <div>• 知识隔离标记 (角色可知范围)</div>
          <div>• 世界书版本历史</div>
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
