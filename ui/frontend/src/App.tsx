import React, { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { apiGet } from "./api/client";

import { ToastProvider, useToast } from "./components/shared/Toast";
import ErrorBoundary from "./components/shared/ErrorBoundary";
import useKeyboardShortcuts from "./hooks/useKeyboardShortcuts";
import ShortcutHint from "./components/shared/ShortcutHint";

import GlobalSearch from "./components/shared/GlobalSearch";
import DashboardPage from "./pages/DashboardPage";
import RankingsPage from "./pages/RankingsPage";
import ReferenceLibraryPage from "./pages/ReferenceLibraryPage";
import ReferenceOverviewPage from "./pages/ReferenceOverviewPage";
// TrendAnalysisPage merged into AnalysisDashboardPage
import EditorPage from "./pages/EditorPage";
import CharacterManagerPage from "./pages/CharacterManagerPage";
import WorldBookPage from "./pages/WorldBookPage";
import StorylinePage from "./pages/StorylinePage";
import SettingsPage from "./pages/SettingsPage";
import AnalysisDashboardPage from "./pages/AnalysisDashboardPage";
import ProjectListPage from "./pages/ProjectListPage";
import ProjectSetupPage from "./pages/ProjectSetupPage";
import SkillsPage from "./pages/SkillsPage";
// DevToolsPage removed

type Tab =
  | "dashboard" | "rankings" | "references" | "references-overview" | "analysis"
  | "projects" | "project-setup" | "editor" | "characters" | "worldbook" | "storyline"
  | "skills" | "settings";

interface Project { id: string; name: string; genre?: string; }

const NAV: { section: string; items: { key: Tab; icon: string; label: string }[] }[] = [
  {
    section: "市场信息",
    items: [
      { key: "dashboard", icon: "\u25A3", label: "首页" },
      { key: "rankings", icon: "\u2261", label: "市场数据库" },
      { key: "analysis", icon: "\u2197", label: "分析面板" },
    ],
  },
  {
    section: "参考作品数据库",
    items: [
      { key: "references-overview", icon: "\u25A6", label: "数据库概览" },
      { key: "references", icon: "\u229E", label: "参考作品库" },
    ],
  },
  {
    section: "创作",
    items: [
      { key: "projects", icon: "\u25A1", label: "开书" },
      { key: "characters", icon: "\u2662", label: "角色管理" },
      { key: "worldbook", icon: "\u2295", label: "世界书" },
      { key: "editor", icon: "\u270E", label: "编辑器" },
      { key: "storyline", icon: "\u2500", label: "剧情线" },
      { key: "skills", icon: "\u2699", label: "智能体" },
      { key: "settings", icon: "\u2638", label: "设置" },
    ],
  },
];

function AppInner() {
  const [tab, setTab] = useState<Tab>("dashboard");
  const [sidebarW, setSidebarW] = useState(220);
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeProject, setActiveProject] = useState<string>("");
  const [searchOpen, setSearchOpen] = useState(false);
  const dragging = useRef(false);
  const { toast } = useToast();

  const shortcutHandlers = useMemo(() => ({
    onSearch: () => setSearchOpen(true),
    onSave: () => { /* Ctrl+S handled by EditorPage directly */ },
    onEscape: () => { setSearchOpen(false); },
  }), [toast]);

  useKeyboardShortcuts(shortcutHandlers);

  useEffect(() => {
    apiGet<{ items: Project[] }>("/api/data/projects")
      .then(r => {
        const items = r.items || [];
        setProjects(items);
        if (items.length && !activeProject) setActiveProject(items[0].id);
      })
      .catch((err) => { console.warn("Failed to load projects:", err.message); });
  }, []);

  const activeProjectName = projects.find(p => p.id === activeProject)?.name || "未选择";

  // Sidebar resize
  const onMouseDown = useCallback(() => {
    dragging.current = true;
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
  }, []);

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (dragging.current) setSidebarW(Math.max(180, Math.min(340, e.clientX)));
    };
    const onUp = () => {
      dragging.current = false;
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  return (
    <div className="app-layout">
      <a href="#main-content" className="sr-only">Skip to main content</a>
      <aside className="sidebar" style={{ width: sidebarW }} role="navigation" aria-label="Main navigation">
        <div className="sidebar-brand">
          <h1>
            <img src="/favicon.svg" alt="InkOctoBot" style={{ width: 24, height: 24, verticalAlign: "middle", marginRight: 6 }} />
            InkOctoBot
          </h1>
          <p>AI 小说智能体工作台</p>
        </div>

        {/* Search trigger */}
        <button
          className="nav-btn"
          onClick={() => setSearchOpen(true)}
          aria-label="Search (Ctrl+K)"
          style={{ margin: "10px 8px 6px", padding: "5px 10px", display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: "var(--text-tertiary)", background: "var(--bg-surface-2)", border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", height: 30 }}
        >
          <span style={{ fontSize: 12, opacity: 0.6 }}>&#x2315;</span>
          <span style={{ flex: 1, textAlign: "left" }}>搜索...</span>
          <kbd style={{ fontSize: 9, color: "var(--text-disabled)", background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 3, padding: "1px 4px", fontFamily: "var(--font-mono)", lineHeight: 1.4 }}>
            {/Mac|iPod|iPhone|iPad/.test(navigator?.platform || "") ? "\u2318K" : "Ctrl+K"}
          </kbd>
        </button>

        <nav className="sidebar-nav" aria-label="Page navigation">
          {NAV.map(group => (
            <React.Fragment key={group.section}>
              {group.section === "创作" ? (
                <div className="sidebar-section-label" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", paddingRight: 8 }}>
                  <span>{group.section}</span>
                  <span
                    style={{ padding: "2px 8px", fontSize: 10, letterSpacing: 0, color: "var(--text-secondary)" }}
                    title={`当前项目：${activeProjectName}`}
                  >
                    当前：{activeProjectName.length > 6 ? activeProjectName.slice(0, 6) + "…" : activeProjectName}
                  </span>
                </div>
              ) : (
                <div className="sidebar-section-label">{group.section}</div>
              )}
              {group.items.map(item => (
                <button
                  key={item.key}
                  className={`nav-btn${tab === item.key ? " active" : ""}`}
                  onClick={() => setTab(item.key)}
                  aria-current={tab === item.key ? "page" : undefined}
                  aria-label={item.label}
                >
                  <span className="nav-icon" aria-hidden="true">{item.icon}</span>
                  <span>{item.label}</span>
                </button>
              ))}
            </React.Fragment>
          ))}
        </nav>

        <div className="sidebar-footer">InkOctoBot v2.1</div>
      </aside>

      <div className="resize-handle" onMouseDown={onMouseDown} />

      <main id="main-content" className="main-content" role="main" aria-label="Page content">
        {tab === "dashboard" && <ErrorBoundary key="dashboard"><DashboardPage projects={projects} onNavigate={(t: string) => setTab(t as Tab)} onSelectProject={setActiveProject} /></ErrorBoundary>}
        {tab === "rankings" && <ErrorBoundary key="rankings"><RankingsPage /></ErrorBoundary>}
        {tab === "references-overview" && <ErrorBoundary key="references-overview"><ReferenceOverviewPage onNavigate={(t: string) => setTab(t as Tab)} /></ErrorBoundary>}
        {tab === "references" && <ErrorBoundary key="references"><ReferenceLibraryPage /></ErrorBoundary>}
        {tab === "analysis" && <ErrorBoundary key="analysis"><AnalysisDashboardPage /></ErrorBoundary>}
        {tab === "projects" && <ErrorBoundary key="projects"><ProjectListPage activeProject={activeProject} onSelectProject={setActiveProject} onNavigate={(t: string) => setTab(t as Tab)} /></ErrorBoundary>}
        {tab === "project-setup" && <ErrorBoundary key="project-setup"><ProjectSetupPage projectId={activeProject} /></ErrorBoundary>}
        {tab === "editor" && <ErrorBoundary key="editor"><EditorPage projectId={activeProject} onNavigate={(t: string) => setTab(t as Tab)} /></ErrorBoundary>}
        {tab === "characters" && <ErrorBoundary key="characters"><CharacterManagerPage projectId={activeProject} projects={projects} /></ErrorBoundary>}
        {tab === "worldbook" && <ErrorBoundary key="worldbook"><WorldBookPage projectId={activeProject} projects={projects} /></ErrorBoundary>}
        {tab === "storyline" && <ErrorBoundary key="storyline"><StorylinePage projectId={activeProject} /></ErrorBoundary>}
        {tab === "skills" && <ErrorBoundary key="skills"><SkillsPage projects={projects} activeProject={activeProject} /></ErrorBoundary>}
        {tab === "settings" && <ErrorBoundary key="settings"><SettingsPage /></ErrorBoundary>}
      </main>

      <GlobalSearch
        open={searchOpen}
        onClose={() => setSearchOpen(false)}
        onNavigate={(t: string) => setTab(t as Tab)}
        projects={projects}
        activeProject={activeProject}
      />

      <ShortcutHint />
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <AppInner />
    </ToastProvider>
  );
}
