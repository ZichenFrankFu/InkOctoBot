/**
 * MarketOverviewPage — title aligned to the standard page-container
 * convention (matches Rankings / ReferenceLibrary / etc.), then the
 * tab strip, then the body.
 */
import React, { useState } from "react";
import RankingsPage from "./RankingsPage";
import AnalysisDashboardPage from "./AnalysisDashboardPage";
import { t } from "../i18n";


type Tab = "rankings" | "analysis";


export default function MarketOverviewPage() {
  const [tab, setTab] = useState<Tab>("rankings");

  return (
    <div className="page-container" style={{ padding: "16px 20px", maxWidth: 1400, margin: "0 auto", display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Standard page header — title on the left, top-aligned like
          other pages (Rankings / Reference / Editor etc.). */}
      <div className="page-header" style={{ paddingBottom: 8 }}>
        <div className="page-header-row">
          <div>
            <h2>{t("市场总览")}</h2>
            <p>{t("榜单") + " · " + t("分析面板")}</p>
          </div>
        </div>
      </div>

      {/* Tab strip */}
      <div style={{
        display: "flex", gap: 0,
        borderBottom: "1px solid var(--border)",
        marginBottom: 0,
      }}>
        {([
          { key: "rankings" as const, label: t("榜单"),   desc: "逐级浏览各平台榜单" },
          { key: "analysis" as const, label: t("分析面板"), desc: "热度趋势 · 标签共现 · 双点分析" },
        ]).map(opt => (
          <button
            key={opt.key}
            onClick={() => setTab(opt.key)}
            title={opt.desc}
            style={{
              padding: "10px 20px",
              fontSize: 13,
              fontWeight: tab === opt.key ? 700 : 400,
              color: tab === opt.key ? "var(--accent)" : "var(--text-secondary)",
              background: "none",
              border: "none",
              borderBottom: tab === opt.key ? "2px solid var(--accent)" : "2px solid transparent",
              cursor: "pointer",
              marginBottom: -1,
            }}
          >{opt.label}</button>
        ))}
      </div>

      {/* Tab body */}
      <div style={{ flex: 1, minHeight: 0, overflow: "auto", marginTop: 12 }}>
        {tab === "rankings" && <RankingsPage hideOwnHeader={true} hideOpeningAi={true} />}
        {tab === "analysis" && <AnalysisDashboardPage />}
      </div>
    </div>
  );
}
