import React, { useState, useRef, useEffect, useCallback } from "react";
import { apiGet } from "./api/client";

import DashboardPage from "./pages/DashboardPage";
import RankingsPage from "./pages/RankingsPage";
import ReferenceLibraryPage from "./pages/ReferenceLibraryPage";
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
import DevToolsPage from "./pages/DevToolsPage";

type Tab =
  | "dashboard" | "rankings" | "references" | "analysis"
  | "projects" | "project-setup" | "editor" | "characters" | "worldbook" | "storyline"
  | "skills" | "settings" | "devtools";

interface Project { id: string; name: string; genre?: string; }

const NAV: { section: string; items: { key: Tab; icon: string; label: string }[] }[] = [
  {
    section: "概览",
    items: [
      { key: "dashboard", icon: "📊", label: "首页" },
    ],
  },
  {
    section: "数据",
    items: [
      { key: "rankings", icon: "📋", label: "市场数据库" },
      { key: "references", icon: "📚", label: "参考作品库" },
      { key: "analysis", icon: "📈", label: "分析面板" },
    ],
  },
  {
    section: "创作",
    items: [
      { key: "projects", icon: "📁", label: "开书" },
      { key: "characters", icon: "👤", label: "角色管理" },
      { key: "worldbook", icon: "🌍", label: "世界书" },
      { key: "editor", icon: "✏️", label: "编辑器" },
      { key: "storyline", icon: "🗺️", label: "剧情线" },
    ],
  },
  {
    section: "系统",
    items: [
      { key: "skills", icon: "🧩", label: "Skills" },
      { key: "settings", icon: "⚙️", label: "设置" },
      { key: "devtools", icon: "🔧", label: "开发者工具" },
    ],
  },
];

export default function App() {
  const [tab, setTab] = useState<Tab>("dashboard");
  const [sidebarW, setSidebarW] = useState(220);
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeProject, setActiveProject] = useState<string>("");
  const dragging = useRef(false);

  useEffect(() => {
    apiGet<{ items: Project[] }>("/api/data/projects")
      .then(r => {
        const items = r.items || [];
        setProjects(items);
        if (items.length && !activeProject) setActiveProject(items[0].id);
      })
      .catch(() => {});
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
      <aside className="sidebar" style={{ width: sidebarW }}>
        <div className="sidebar-brand">
          <h1>
            <span style={{ fontSize: 22 }}>🐙</span>
            InkOctoBot
          </h1>
          <p>AI 小说智能体工作台</p>
        </div>

        <nav className="sidebar-nav">
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
                >
                  <span className="nav-icon">{item.icon}</span>
                  <span>{item.label}</span>
                </button>
              ))}
            </React.Fragment>
          ))}
        </nav>

        <div className="sidebar-footer">InkOctoBot v2.1</div>
      </aside>

      <div className="resize-handle" onMouseDown={onMouseDown} />

      <main className="main-content">
        {tab === "dashboard" && <DashboardPage projects={projects} onNavigate={(t: string) => setTab(t as Tab)} />}
        {tab === "rankings" && <RankingsPage />}
        {tab === "references" && <ReferenceLibraryPage />}
        {tab === "analysis" && <AnalysisDashboardPage />}
        {tab === "projects" && <ProjectListPage activeProject={activeProject} onSelectProject={setActiveProject} onNavigate={(t: string) => setTab(t as Tab)} />}
        {tab === "project-setup" && <ProjectSetupPage projectId={activeProject} />}
        {tab === "editor" && <EditorPage projectId={activeProject} onNavigate={(t: string) => setTab(t as Tab)} />}
        {tab === "characters" && <CharacterManagerPage projectId={activeProject} projects={projects} />}
        {tab === "worldbook" && <WorldBookPage projectId={activeProject} projects={projects} />}
        {tab === "storyline" && <StorylinePage projectId={activeProject} />}
        {tab === "skills" && <SkillsPage />}
        {tab === "settings" && <SettingsPage />}
        {tab === "devtools" && <DevToolsPage projectId={activeProject} />}
      </main>
    </div>
  );
}
