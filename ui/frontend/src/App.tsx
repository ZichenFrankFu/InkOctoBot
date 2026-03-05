import React, { useState } from "react";

// -- Phase 1: existing pages --
import ConfigPage from "./pages/ConfigPage";
import RunnerPage from "./pages/RunnerPage";
import ReportsPage from "./pages/ReportsPage";
import DatabasePage from "./pages/DatabasePage";

// -- Phase 1: new placeholder --
import AnalysisDashboardPage from "./pages/AnalysisDashboardPage";
import ReferenceLibraryPage from "./pages/ReferenceLibraryPage";

// -- Phase 2: placeholder --
import ProjectListPage from "./pages/ProjectListPage";
import ProjectSetupPage from "./pages/ProjectSetupPage";
import CharacterManagerPage from "./pages/CharacterManagerPage";
import WorldBookEditorPage from "./pages/WorldBookEditorPage";

// -- Phase 3: placeholder --
import EditorPage from "./pages/EditorPage";
import SettingsPage from "./pages/SettingsPage";

type Tab =
  | "spider-config"
  | "spider-runner"
  | "reports"
  | "analysis"
  | "references"
  | "database"
  | "projects"
  | "project-setup"
  | "characters"
  | "worldbook"
  | "editor"
  | "settings";

interface NavItem { id: Tab; icon: string; label: string; }
interface NavGroup { title: string; items: NavItem[]; }

const NAV: NavGroup[] = [
  { title: "数据采集", items: [
    { id: "spider-config", icon: "📡", label: "爬虫配置" },
    { id: "spider-runner", icon: "▶️", label: "爬虫运行" },
  ]},
  { title: "市场分析", items: [
    { id: "reports",    icon: "📊", label: "趋势报告" },
    { id: "analysis",   icon: "📈", label: "分析面板" },
    { id: "references", icon: "📚", label: "参考作品库" },
    { id: "database",   icon: "🗄️", label: "数据库" },
  ]},
  { title: "创作项目", items: [
    { id: "projects",      icon: "📂", label: "项目列表" },
    { id: "project-setup", icon: "🛠️", label: "项目设置" },
    { id: "worldbook",     icon: "🌍", label: "世界书" },
    { id: "characters",    icon: "👤", label: "人物卡" },
    { id: "editor",        icon: "📖", label: "编辑器" },
  ]},
  { title: "系统", items: [
    { id: "settings", icon: "⚙️", label: "设置" },
  ]},
];

export default function App() {
  const [tab, setTab] = useState<Tab>("spider-config");
  const [lastRunId, setLastRunId] = useState<string | null>(null);
  const [collapsed, setCollapsed] = useState(false);

  const page = (() => {
    switch (tab) {
      case "spider-config":  return <ConfigPage onSaved={(id) => setLastRunId(id)} />;
      case "spider-runner":  return <RunnerPage lastRunId={lastRunId} />;
      case "reports":        return <ReportsPage />;
      case "database":       return <DatabasePage />;
      case "analysis":       return <AnalysisDashboardPage />;
      case "references":     return <ReferenceLibraryPage />;
      case "projects":       return <ProjectListPage />;
      case "project-setup":  return <ProjectSetupPage />;
      case "characters":     return <CharacterManagerPage />;
      case "worldbook":      return <WorldBookEditorPage />;
      case "editor":         return <EditorPage />;
      case "settings":       return <SettingsPage />;
    }
  })();

  return (
    <div style={{ display: "flex", height: "100vh", fontFamily: "system-ui, -apple-system, sans-serif", background: "#fafafa" }}>
      {/* Sidebar */}
      <aside style={{
        width: collapsed ? 56 : 220,
        minWidth: collapsed ? 56 : 220,
        background: "#1a1a2e",
        color: "#ccc",
        display: "flex",
        flexDirection: "column",
        transition: "width 0.2s, min-width 0.2s",
        overflow: "hidden",
      }}>
        {/* Brand */}
        <div
          style={{
            padding: collapsed ? "16px 8px" : "16px 16px",
            borderBottom: "1px solid #2a2a4a",
            display: "flex", alignItems: "center", gap: 8, cursor: "pointer",
          }}
          onClick={() => setCollapsed(!collapsed)}
          title={collapsed ? "展开" : "收起"}
        >
          <span style={{ fontSize: 22 }}>🐙</span>
          {!collapsed && <span style={{ fontWeight: 700, fontSize: 14, color: "#e0e0ff", whiteSpace: "nowrap" }}>InkOctoBot</span>}
        </div>

        {/* Nav */}
        <nav style={{ flex: 1, overflowY: "auto", padding: "8px 0" }}>
          {NAV.map((g) => (
            <div key={g.title} style={{ marginBottom: 8 }}>
              {!collapsed && (
                <div style={{ padding: "6px 16px", fontSize: 10, fontWeight: 600, textTransform: "uppercase", letterSpacing: 1.2, color: "#666" }}>
                  {g.title}
                </div>
              )}
              {g.items.map((item) => {
                const active = tab === item.id;
                return (
                  <button
                    key={item.id}
                    onClick={() => setTab(item.id)}
                    title={collapsed ? item.label : undefined}
                    style={{
                      display: "flex", alignItems: "center", gap: 10, width: "100%",
                      padding: collapsed ? "9px 0" : "9px 16px",
                      justifyContent: collapsed ? "center" : "flex-start",
                      border: "none",
                      background: active ? "#16213e" : "transparent",
                      borderLeft: active ? "3px solid #4a7dff" : "3px solid transparent",
                      color: active ? "#fff" : "#aaa",
                      cursor: "pointer", fontSize: 13, textAlign: "left",
                    }}
                  >
                    <span style={{ fontSize: 15, width: 20, textAlign: "center" }}>{item.icon}</span>
                    {!collapsed && <span style={{ whiteSpace: "nowrap" }}>{item.label}</span>}
                  </button>
                );
              })}
            </div>
          ))}
        </nav>

        {!collapsed && (
          <div style={{ padding: "12px 16px", borderTop: "1px solid #2a2a4a", fontSize: 11, color: "#555" }}>
            run: {lastRunId ?? "—"}
          </div>
        )}
      </aside>

      {/* Main */}
      <main style={{ flex: 1, overflow: "auto" }}>
        <div style={{ maxWidth: 1400, margin: "0 auto", padding: "24px 32px" }}>{page}</div>
      </main>
    </div>
  );
}
