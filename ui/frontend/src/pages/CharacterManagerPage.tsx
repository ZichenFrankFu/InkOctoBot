import React from "react";

export default function CharacterManagerPage() {
  return (
    <div>
      <h2 style={{ marginTop: 0, marginBottom: 4 }}>
        👤 人物卡管理
      </h2>
      <p style={{ color: "#888", marginBottom: 24 }}>创建人物、配置决策模型、可视化关系网</p>

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
        <div style={{ fontSize: 48 }}>👤</div>
        <div style={{ fontSize: 18, fontWeight: 600, color: "#999" }}>
          Phase 2
        </div>
        <div style={{ fontSize: 13, maxWidth: 480, lineHeight: 1.8, color: "#aaa" }}>
          <div>• 人物卡 CRUD (姓名 / 性格 / 背景 / 行为模式)</div>
          <div>• 量化决策参数 (效用 + 前景 + 贝叶斯)</div>
          <div>• 人物关系图 (D3 force graph)</div>
          <div>• 角色 LoRA 绑定 (Phase 4)</div>
          <div>• 人物成长轨迹时间线</div>
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
