import React from "react";

export default function WorldBookPage() {
  return (
    <div className="page-container">
      <div className="page-header">
        <h2>世界书</h2>
        <p>构建和管理你的小说世界观 — 力量体系、社会结构、地理、历史</p>
      </div>

      <div className="placeholder-page">
        <div className="placeholder-hero">
          <span className="placeholder-hero-icon">🌍</span>
          <h3>世界书编辑器</h3>
          <p>世界观是创作的根基 — 从极简到极详细，你自由选择粒度</p>
        </div>

        {/* World book category preview */}
        <div className="card" style={{ maxWidth: 640, margin: "0 auto 32px" }}>
          <div className="card-body" style={{ padding: 0 }}>
            {[
              { icon: "⚡", name: "力量体系", desc: "修仙/武道/魔法/科技 — 等级划分、升级条件、战力对比", },
              { icon: "🏛️", name: "社会结构", desc: "门派/帝国/组织 — 权力架构、阵营关系、政治格局", },
              { icon: "🗺️", name: "地理", desc: "大陆/城市/秘境 — 空间布局、移动限制、资源分布", },
              { icon: "📜", name: "历史", desc: "重大事件/时间线 — 影响现在格局的过去", },
              { icon: "📏", name: "硬规则", desc: "不可违反的世界观约束 — 用于 AI 一致性检查", },
            ].map((cat) => (
              <div key={cat.name} style={{ padding: "14px 20px", borderBottom: "1px solid var(--ink-50)", display: "flex", alignItems: "center", gap: 14 }}>
                <span style={{ fontSize: 22, width: 36, textAlign: "center" }}>{cat.icon}</span>
                <div>
                  <div style={{ fontWeight: 600, fontSize: 14, marginBottom: 2 }}>{cat.name}</div>
                  <div className="text-xs text-muted">{cat.desc}</div>
                </div>
                <span style={{ marginLeft: "auto", color: "var(--ink-300)", fontSize: 14 }}>›</span>
              </div>
            ))}
          </div>
        </div>

        <div className="placeholder-features">
          <div className="placeholder-feature-card">
            <div className="placeholder-feature-icon">📝</div>
            <h4>结构化输入</h4>
            <p>支持 YAML/Markdown 混合格式，每个条目可附加参考作品</p>
          </div>
          <div className="placeholder-feature-card">
            <div className="placeholder-feature-icon">🔍</div>
            <h4>语义检索</h4>
            <p>通过 ChromaDB 向量化存储，创作时 AI 自动检索相关设定</p>
          </div>
          <div className="placeholder-feature-card">
            <div className="placeholder-feature-icon">🚫</div>
            <h4>一致性检查</h4>
            <p>硬规则条目生成约束，违规检测器在生成时实时校验</p>
          </div>
          <div className="placeholder-feature-card">
            <div className="placeholder-feature-icon">🤖</div>
            <h4>AI 补全</h4>
            <p>缺失的世界观条目由 AI 生成草案，你确认后入库</p>
          </div>
        </div>
      </div>
    </div>
  );
}
