import React from "react";

export default function CharacterManagerPage() {
  return (
    <div className="page-container">
      <div className="page-header">
        <h2>角色管理</h2>
        <p>创建和管理人物卡 — 自然语言描述 + 量化决策模型</p>
      </div>

      <div className="placeholder-page">
        <div className="placeholder-hero">
          <span className="placeholder-hero-icon">👤</span>
          <h3>人物卡系统</h3>
          <p>双层设计：自然语言描述（LLM prompt 用）+ 量化决策模型（Python 引擎用）</p>
        </div>

        {/* Character card preview */}
        <div className="card" style={{ maxWidth: 560, margin: "0 auto 32px" }}>
          <div className="card-header">
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <div style={{ width: 48, height: 48, borderRadius: "50%", background: "var(--accent-muted)", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 24 }}>👨</div>
              <div>
                <h3 style={{ marginBottom: 0 }}>张远</h3>
                <span className="text-xs text-muted">主角 · 连载中 · 12 章出场</span>
              </div>
            </div>
          </div>
          <div className="card-body">
            <div className="detail-section" style={{ marginBottom: 16 }}>
              <h4 style={{ fontSize: 13 }}>Layer A: 自然语言</h4>
              <p className="text-sm text-muted" style={{ fontStyle: "italic" }}>性格坚毅但偶尔冲动，对师门充满忠诚。说话简洁，遇到不平事会挺身而出…</p>
            </div>
            <div className="detail-section" style={{ marginBottom: 0 }}>
              <h4 style={{ fontSize: 13 }}>Layer B: 量化模型</h4>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8 }}>
                <div className="quant-param"><span>loss_aversion</span><span className="font-mono">2.1</span></div>
                <div className="quant-param"><span>risk_aversion</span><span className="font-mono">0.6</span></div>
                <div className="quant-param"><span>impulsive_p</span><span className="font-mono">0.35</span></div>
                <div className="quant-param"><span>trust(师门)</span><span className="font-mono">β(12,2)</span></div>
              </div>
            </div>
          </div>
        </div>

        <div className="placeholder-features">
          <div className="placeholder-feature-card">
            <div className="placeholder-feature-icon">🧠</div>
            <h4>效用函数</h4>
            <p>价值维度权重（survival/power/love/loyalty/justice），时间折扣因子</p>
          </div>
          <div className="placeholder-feature-card">
            <div className="placeholder-feature-icon">📊</div>
            <h4>前景理论</h4>
            <p>loss_aversion（创伤后上升）、risk_aversion_gain/loss 参数化</p>
          </div>
          <div className="placeholder-feature-card">
            <div className="placeholder-feature-icon">🎲</div>
            <h4>随机行为</h4>
            <p>Poisson（搭话频率 λ）、Bernoulli（冲动动手 p）+ 情境修正因子</p>
          </div>
          <div className="placeholder-feature-card">
            <div className="placeholder-feature-icon">🤝</div>
            <h4>贝叶斯信任</h4>
            <p>Beta(α,β) 分布，观察行为后更新，α/(α+β) = 信任度期望</p>
          </div>
        </div>
      </div>
    </div>
  );
}
