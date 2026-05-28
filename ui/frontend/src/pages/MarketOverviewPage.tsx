/**
 * MarketOverviewPage — title first, then tab strip, then body.
 * Wraps Rankings + AnalysisDashboard so the user can swap between
 * them inside the single "市场总览" nav entry.
 */
import React, { useState } from "react";
import RankingsPage from "./RankingsPage";
import AnalysisDashboardPage from "./AnalysisDashboardPage";


type Tab = "rankings" | "analysis";


export default function MarketOverviewPage() {
  const [tab, setTab] = useState<Tab>("rankings");

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Page header (title first) */}
      <div className="page-header" style={{ padding: "16px 20px 6px" }}>
        <div className="page-header-row">
          <div>
            <h2>市场总览</h2>
            <p>逐级浏览各平台榜单 · 热度趋势 · 标签共现 · 双点分析</p>
          </div>
        </div>
      </div>

      {/* Tab strip (after the title) */}
      <div style={{
        display: "flex", gap: 0,
        borderBottom: "1px solid var(--border)",
        padding: "0 20px",
        background: "var(--bg-surface)",
      }}>
        {([
          { key: "rankings" as const, label: "榜单",   desc: "逐级浏览各平台榜单" },
          { key: "analysis" as const, label: "分析面板", desc: "热度趋势 · 标签共现 · 双点分析" },
        ]).map(t => (
          <button
            key={t.key}
            onClick={() => setTab(t.key)}
            title={t.desc}
            style={{
              padding: "10px 20px",
              fontSize: 13,
              fontWeight: tab === t.key ? 700 : 400,
              color: tab === t.key ? "var(--accent)" : "var(--text-secondary)",
              background: "none",
              border: "none",
              borderBottom: tab === t.key ? "2px solid var(--accent)" : "2px solid transparent",
              cursor: "pointer",
              marginBottom: -1,
            }}
          >{t.label}</button>
        ))}
      </div>

      {/* Tab body */}
      <div style={{ flex: 1, minHeight: 0, overflow: "auto" }}>
        {tab === "rankings" && <RankingsPage hideOwnHeader={true} hideOpeningAi={true} />}
        {tab === "analysis" && <AnalysisDashboardPage />}
      </div>
    </div>
  );
}
