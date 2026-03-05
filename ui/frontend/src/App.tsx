import React, { useState } from "react";
import DashboardPage from "./pages/DashboardPage";
import RankingsPage from "./pages/RankingsPage";
import ReportsPage from "./pages/ReportsPage";

type Tab = "dashboard" | "rankings" | "reports";

export default function App() {
  const [tab, setTab] = useState<Tab>("dashboard");

  return (
    <div className="app-layout">
      {/* ── Sidebar ── */}
      <aside className="sidebar">
        <div className="sidebar-brand">
          <h1>🐙 InkOctoBot</h1>
          <p>网文趋势分析系统</p>
        </div>

        <nav className="sidebar-nav">
          <div className="sidebar-section-label">分析</div>
          <NavBtn
            icon="📊"
            label="数据概览"
            active={tab === "dashboard"}
            onClick={() => setTab("dashboard")}
          />
          <NavBtn
            icon="📋"
            label="榜单浏览"
            active={tab === "rankings"}
            onClick={() => setTab("rankings")}
          />

          <div className="sidebar-section-label">输出</div>
          <NavBtn
            icon="📝"
            label="趋势报告"
            active={tab === "reports"}
            onClick={() => setTab("reports")}
          />
        </nav>

        <div className="sidebar-footer">
          InkOctoBot v0.2 · 本地运行
        </div>
      </aside>

      {/* ── Main Content ── */}
      <main className="main-content">
        {tab === "dashboard" && <DashboardPage />}
        {tab === "rankings" && <RankingsPage />}
        {tab === "reports" && <ReportsPage />}
      </main>
    </div>
  );
}

function NavBtn(props: {
  icon: string;
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      className={`nav-btn${props.active ? " active" : ""}`}
      onClick={props.onClick}
    >
      <span className="nav-icon">{props.icon}</span>
      <span>{props.label}</span>
    </button>
  );
}
