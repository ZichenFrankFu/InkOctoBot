import React from "react";

export default function EditorPage() {
  return (
    <div>
      <h2 style={{ marginTop: 0, marginBottom: 4 }}>
        📖 编辑器
      </h2>
      <p style={{ color: "#888", marginBottom: 24 }}>三栏编辑器: 目录树 | 文本编辑 | AI 面板</p>

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
        <div style={{ fontSize: 48 }}>📖</div>
        <div style={{ fontSize: 18, fontWeight: 600, color: "#999" }}>
          Phase 3
        </div>
        <div style={{ fontSize: 13, maxWidth: 480, lineHeight: 1.8, color: "#aaa" }}>
          <div>• TipTap 富文本编辑器</div>
          <div>• AI 章节生成 (Film Pipeline)</div>
          <div>• 多模型输出对比 (A/B Compare)</div>
          <div>• 版本历史 & Diff</div>
          <div>• AI 编辑建议 & 约束违规检测</div>
          <div>• 实时成本估算</div>
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
