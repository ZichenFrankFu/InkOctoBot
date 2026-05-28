/**
 * MarketOverviewPage — wrapper that fulfills the user's request to
 * "merge 分析面板 into 市场总览" without rewriting either page.
 *
 * Tabs:
 *   - 榜单 (Rankings)
 *   - 分析 (AnalysisDashboard)
 */
import React, { useState } from "react";
import RankingsPage from "./RankingsPage";
import AnalysisDashboardPage from "./AnalysisDashboardPage";


type Tab = "rankings" | "analysis";


export default function MarketOverviewPage() {
  const [tab, setTab] = useState<Tab>("rankings");

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Tab bar */}
      <div style={{
        display: "flex", gap: 0,
        borderBottom: "1px solid var(--border)",
        padding: "0 20px", paddingTop: 10,
        background: "var(--bg-surface)",
      }}>
        {([
          { key: "rankings" as const, label: "📊 榜单", desc: "逐级浏览各平台榜单" },
          { key: "analysis" as const, label: "📈 分析", desc: "热度趋势 · 标签共现 · 双点分析" },
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
        {tab === "rankings" && <RankingsPage />}
        {tab === "analysis" && <AnalysisDashboardPage />}
      </div>
    </div>
  );
}
