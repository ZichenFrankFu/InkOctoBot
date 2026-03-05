import React, { useState } from "react";
import DashboardPage from "./pages/DashboardPage";
import RankingsPage from "./pages/RankingsPage";
import ReferenceLibraryPage from "./pages/ReferenceLibraryPage";
import TrendAnalysisPage from "./pages/TrendAnalysisPage";
import EditorPage from "./pages/EditorPage";
import CharacterManagerPage from "./pages/CharacterManagerPage";
import WorldBookPage from "./pages/WorldBookPage";
import SettingsPage from "./pages/SettingsPage";

type Tab =
  | "dashboard"
  | "rankings"
  | "references"
  | "trends"
  | "editor"
  | "characters"
  | "worldbook"
  | "settings";

export default function App() {
  const [tab, setTab] = useState<Tab>("dashboard");

  return (
    <div className="app-layout">
      <aside className="sidebar">
        <div className="sidebar-brand">
          <h1>🐙 InkOctoBot</h1>
          <p>小说AI智能体工作台</p>
        </div>

        <nav className="sidebar-nav">
          <div className="sidebar-section-label">概览</div>
          <NavBtn icon="📊" label="首页" active={tab === "dashboard"} onClick={() => setTab("dashboard")} />

          <div className="sidebar-section-label">数据</div>
          <NavBtn icon="📋" label="榜单浏览" active={tab === "rankings"} onClick={() => setTab("rankings")} />
          <NavBtn icon="📚" label="参考作品库" active={tab === "references"} onClick={() => setTab("references")} />
          <NavBtn icon="📈" label="趋势分析" active={tab === "trends"} onClick={() => setTab("trends")} />

          <div className="sidebar-section-label">创作</div>
          <NavBtn icon="✏️" label="编辑器" active={tab === "editor"} onClick={() => setTab("editor")} />
          <NavBtn icon="👤" label="角色管理" active={tab === "characters"} onClick={() => setTab("characters")} />
          <NavBtn icon="🌍" label="世界书" active={tab === "worldbook"} onClick={() => setTab("worldbook")} />

          <div className="sidebar-section-label">系统</div>
          <NavBtn icon="⚙️" label="设置" active={tab === "settings"} onClick={() => setTab("settings")} />
        </nav>

        <div className="sidebar-footer">InkOctoBot v0.3 · 本地运行</div>
      </aside>

      <main className="main-content">
        {tab === "dashboard" && <DashboardPage />}
        {tab === "rankings" && <RankingsPage />}
        {tab === "references" && <ReferenceLibraryPage />}
        {tab === "trends" && <TrendAnalysisPage />}
        {tab === "editor" && <EditorPage />}
        {tab === "characters" && <CharacterManagerPage />}
        {tab === "worldbook" && <WorldBookPage />}
        {tab === "settings" && <SettingsPage />}
      </main>
    </div>
  );
}

function NavBtn(props: { icon: string; label: string; active: boolean; onClick: () => void }) {
  return (
    <button className={`nav-btn${props.active ? " active" : ""}`} onClick={props.onClick}>
      <span className="nav-icon">{props.icon}</span>
      <span>{props.label}</span>
    </button>
  );
}
