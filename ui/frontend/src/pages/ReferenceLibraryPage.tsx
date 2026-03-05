import React from "react";

export default function ReferenceLibraryPage() {
  return (
    <div>
      <h2 style={{ marginTop: 0, marginBottom: 4 }}>
        📚 参考作品库
      </h2>
      <p style={{ color: "#888", marginBottom: 24 }}>管理参考作品、提取风格模板、对比分析</p>

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
        <div style={{ fontSize: 48 }}>📚</div>
        <div style={{ fontSize: 18, fontWeight: 600, color: "#999" }}>
          Phase 1
        </div>
        <div style={{ fontSize: 13, maxWidth: 480, lineHeight: 1.8, color: "#aaa" }}>
          <div>• 参考作品上传 & 管理</div>
          <div>• 自动风格提取 (PROSE 迭代收敛)</div>
          <div>• 叙事结构标注</div>
          <div>• 爽点模板识别</div>
          <div>• 作品间风格对比</div>
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
