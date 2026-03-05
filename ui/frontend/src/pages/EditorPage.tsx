import React from "react";

export default function EditorPage() {
  return (
    <div className="page-container">
      <div className="page-header">
        <h2>编辑器</h2>
        <p>AI 辅助创作的核心工作台 — 三栏布局：目录树 / 文本编辑 / AI 面板</p>
      </div>

      <div className="placeholder-page">
        <div className="placeholder-hero">
          <span className="placeholder-hero-icon">✏️</span>
          <h3>智能编辑器</h3>
          <p>纯文字版电影制作 — 你是编剧+导演，AI 是剧组</p>
        </div>

        {/* Three-column layout preview */}
        <div className="editor-preview">
          <div className="editor-col editor-col-left">
            <div className="editor-col-header">📂 目录树</div>
            <div className="editor-col-body">
              <div className="editor-tree-item active">▼ 第一卷</div>
              <div className="editor-tree-item indent">第1章 起始</div>
              <div className="editor-tree-item indent active">● 第2章 初入宗门</div>
              <div className="editor-tree-item indent">第3章 试炼</div>
              <div className="editor-tree-item">▶ 第二卷</div>
            </div>
          </div>
          <div className="editor-col editor-col-center">
            <div className="editor-col-header">📝 文本编辑器</div>
            <div className="editor-col-body" style={{ fontFamily: "var(--font-serif)", lineHeight: 2, color: "var(--ink-400)" }}>
              <p>清晨的阳光穿过竹林，洒在蜿蜒的石阶上。张远背着包袱，跟在师兄身后，沿着山路缓缓向上…</p>
              <p style={{ opacity: 0.4 }}>（正文编辑区域，支持 TipTap 富文本编辑）</p>
            </div>
          </div>
          <div className="editor-col editor-col-right">
            <div className="editor-col-header">🤖 AI 面板</div>
            <div className="editor-col-body">
              <div style={{ marginBottom: 12 }}>
                <div className="text-xs text-muted" style={{ marginBottom: 4 }}>Pipeline 状态</div>
                <div style={{ display: "flex", gap: 4 }}>
                  <span className="pipeline-dot done" />
                  <span className="pipeline-dot done" />
                  <span className="pipeline-dot active" />
                  <span className="pipeline-dot" />
                </div>
              </div>
              <div className="placeholder-btn">生成本章</div>
              <div className="placeholder-btn" style={{ opacity: 0.5 }}>模型对比</div>
              <div className="placeholder-btn" style={{ opacity: 0.5 }}>版本历史</div>
            </div>
          </div>
        </div>

        <div className="placeholder-features">
          <div className="placeholder-feature-card">
            <div className="placeholder-feature-icon">🎬</div>
            <h4>Film Pipeline</h4>
            <p>Scene Planner → Scene Director → Actor Agents → Editor-Stylist 四层创作流水线</p>
          </div>
          <div className="placeholder-feature-card">
            <div className="placeholder-feature-icon">🔄</div>
            <h4>版本管理</h4>
            <p>每次生成自动版本化，支持 diff 对比、回溯、定向重新生成</p>
          </div>
          <div className="placeholder-feature-card">
            <div className="placeholder-feature-icon">⚖️</div>
            <h4>多模型对比</h4>
            <p>同一输入多模型并行生成，对比选择最优输出</p>
          </div>
          <div className="placeholder-feature-card">
            <div className="placeholder-feature-icon">💰</div>
            <h4>成本控制</h4>
            <p>每次 API 调用前显示成本预估，等待确认后执行</p>
          </div>
        </div>
      </div>
    </div>
  );
}
