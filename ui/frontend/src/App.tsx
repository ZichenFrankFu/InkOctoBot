import React, { useState } from "react";
import { useResizable } from "./hooks/useResizable";
import { useTheme } from "./hooks/useTheme";
import ResizeHandle from "./components/ResizeHandle";

// -- Phase 1: existing pages --
import ConfigPage from "./pages/ConfigPage";
import RunnerPage from "./pages/RunnerPage";
import ReportsPage from "./pages/ReportsPage";
import DatabasePage from "./pages/DatabasePage";

// -- Phase 1: new --
import AnalysisDashboardPage from "./pages/AnalysisDashboardPage";
import ReferenceLibraryPage from "./pages/ReferenceLibraryPage";

// -- Phase 2-3: placeholder --
import ProjectListPage from "./pages/ProjectListPage";
import ProjectSetupPage from "./pages/ProjectSetupPage";
import CharacterManagerPage from "./pages/CharacterManagerPage";
import WorldBookEditorPage from "./pages/WorldBookEditorPage";
import EditorPage from "./pages/EditorPage";
import SettingsPage from "./pages/SettingsPage";

/* ── Types ── */
type Tab =
  | "spider-config" | "spider-runner"
  | "reports" | "analysis" | "references" | "database"
  | "projects" | "project-setup" | "characters" | "worldbook"
  | "editor" | "settings";

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

/* ── App ── */
export default function App() {
  const [tab, setTab] = useState<Tab>("spider-config");
  const [lastRunId, setLastRunId] = useState<string | null>(null);
  const [currentConfig, setCurrentConfig] = useState<any>({});
  const [configVersion, setConfigVersion] = useState(0);
  const { theme, toggleTheme } = useTheme();

  const sidebar = useResizable({
    direction: "horizontal",
    initialSize: 220,
    minSize: 180,
    maxSize: 340,
  });

  const [collapsed, setCollapsed] = useState(false);

  const effectiveWidth = collapsed ? 56 : sidebar.size;

  const page = (() => {
    switch (tab) {
      case "spider-config":  return <ConfigPage onSaved={(id) => setLastRunId(id)} onDraftChange={(cfg) => { setCurrentConfig(cfg); setConfigVersion((v) => v + 1); }} />;
      case "spider-runner":  return <RunnerPage lastRunId={lastRunId} currentConfig={currentConfig} configVersion={configVersion} />;
      case "spider-config":  return <ConfigPage onSaved={(id) => setLastRunId(id)} />;
      case "spider-runner":  return <RunnerPage lastRunId={lastRunId} onRunSelected={(id) => setLastRunId(id || null)} />;
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
    <div style={{ display: "flex", height: "100vh", width: "100vw", overflow: "hidden" }}>

      {/* ══════ Sidebar ══════ */}
      <aside
        style={{
          width: effectiveWidth,
          minWidth: effectiveWidth,
          background: "var(--bg-sidebar)",
          display: "flex",
          flexDirection: "column",
          transition: collapsed ? "width 0.25s ease, min-width 0.25s ease" : (sidebar.isDragging ? "none" : "width 0.25s ease, min-width 0.25s ease"),
          overflow: "hidden",
          borderRight: "1px solid var(--border)",
          zIndex: 40,
        }}
      >
        {/* Brand header */}
        <div
          onClick={() => { if (!sidebar.isDragging) setCollapsed(!collapsed); }}
          style={{
            padding: collapsed ? "16px 8px" : "16px",
            borderBottom: "1px solid var(--border)",
            display: "flex",
            alignItems: "center",
            gap: 10,
            cursor: "pointer",
            userSelect: "none",
            flexShrink: 0,
          }}
        >
          <span style={{ fontSize: 24, filter: "drop-shadow(0 0 6px var(--accent-glow))" }}>🐙</span>
          {!collapsed && (
            <span style={{
              fontWeight: 700,
              fontSize: 15,
              color: "var(--text-primary)",
              whiteSpace: "nowrap",
              letterSpacing: 0.3,
            }}>
              InkOctoBot
            </span>
          )}
        </div>

        {/* Navigation */}
        <nav style={{ flex: 1, overflowY: "auto", overflowX: "hidden", padding: "8px 0" }}>
          {NAV.map((group) => (
            <div key={group.title} style={{ marginBottom: 4 }}>
              {!collapsed && (
                <div style={{
                  padding: "8px 16px 4px",
                  fontSize: 10,
                  fontWeight: 700,
                  textTransform: "uppercase",
                  letterSpacing: 1.5,
                  color: "var(--text-tertiary)",
                }}>
                  {group.title}
                </div>
              )}
              {group.items.map((item) => {
                const active = tab === item.id;
                return (
                  <button
                    key={item.id}
                    onClick={() => setTab(item.id)}
                    title={collapsed ? item.label : undefined}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 10,
                      width: "100%",
                      padding: collapsed ? "10px 0" : "8px 16px",
                      justifyContent: collapsed ? "center" : "flex-start",
                      border: "none",
                      background: active ? "var(--accent-subtle)" : "transparent",
                      borderLeft: active ? "3px solid var(--accent)" : "3px solid transparent",
                      color: active ? "var(--accent)" : "var(--text-secondary)",
                      cursor: "pointer",
                      fontSize: 13,
                      fontWeight: active ? 600 : 400,
                      fontFamily: "inherit",
                      textAlign: "left",
                      transition: "background var(--transition-fast), color var(--transition-fast)",
                      borderRadius: 0,
                    }}
                  >
                    <span style={{ fontSize: 15, width: 22, textAlign: "center", flexShrink: 0 }}>{item.icon}</span>
                    {!collapsed && <span className="truncate">{item.label}</span>}
                  </button>
                );
              })}
            </div>
          ))}
        </nav>

        {/* Footer */}
        {!collapsed && (
          <div style={{
            padding: "10px 16px",
            borderTop: "1px solid var(--border)",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            flexShrink: 0,
          }}>
            <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
              {lastRunId ? `run: ${lastRunId.slice(-8)}` : "no run"}
            </span>
            <button
              onClick={(e) => { e.stopPropagation(); toggleTheme(); }}
              title={theme === "dark" ? "切换亮色" : "切换暗色"}
              style={{
                background: "none",
                border: "none",
                cursor: "pointer",
                fontSize: 16,
                padding: "2px 4px",
                borderRadius: 4,
                color: "var(--text-secondary)",
              }}
            >
              {theme === "dark" ? "☀️" : "🌙"}
            </button>
          </div>
        )}
      </aside>

      {/* ══════ Resize Handle ══════ */}
      {!collapsed && (
        <ResizeHandle
          direction="horizontal"
          isDragging={sidebar.isDragging}
          onMouseDown={sidebar.handleProps.onMouseDown}
          onTouchStart={sidebar.handleProps.onTouchStart}
        />
      )}

      {/* ══════ Main Content ══════ */}
      <main style={{
        flex: 1,
        overflow: "auto",
        background: "var(--bg-app)",
        minWidth: 0,
      }}>
        <div style={{
          maxWidth: 1440,
          margin: "0 auto",
          padding: "28px 36px",
        }}>
          {page}
        </div>
      </main>
    </div>
  );
}