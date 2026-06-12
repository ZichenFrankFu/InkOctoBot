import React, { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { apiGet } from "./api/client";

import { ToastProvider, useToast } from "./components/shared/Toast";
import { DialogProvider } from "./components/shared/Dialog";
import ErrorBoundary from "./components/shared/ErrorBoundary";
import useKeyboardShortcuts from "./hooks/useKeyboardShortcuts";
import ShortcutHint from "./components/shared/ShortcutHint";

import GlobalSearch from "./components/shared/GlobalSearch";
import DashboardPage from "./pages/DashboardPage";
import RankingsPage from "./pages/RankingsPage";
import ReferenceLibraryPage from "./pages/ReferenceLibraryPage";
import ReferenceOverviewPage from "./pages/ReferenceOverviewPage";
import ReferenceSearchPage from "./pages/ReferenceSearchPage";
// TrendAnalysisPage merged into AnalysisDashboardPage
import EditorPage from "./pages/EditorPage";
import CharacterManagerPage from "./pages/CharacterManagerPage";
import WorldBookPage from "./pages/WorldBookPage";
import StorylinePage from "./pages/StorylinePage";
import StorylandPage from "./pages/StorylandPage";
import SettingsPage from "./pages/SettingsPage";
// AnalysisDashboardPage is now embedded inside MarketFeatureExtractionPage
import ProjectListPage from "./pages/ProjectListPage";
import ProjectSetupPage from "./pages/ProjectSetupPage";
import SkillsPage from "./pages/SkillsPage";
// New IA per latest UI overhaul: 4 top-level groups
// (市场 / 参考 / 灵感 / 创作), 学习反馈 absorbed into 智能体,
// Truth 审阅 moved into 创作.
import MarketOverviewPage from "./pages/MarketOverviewPage";
import MarketFeatureExtractionPage from "./pages/MarketFeatureExtractionPage";
import InspirationOverviewPage from "./pages/InspirationOverviewPage";
// 灵感搜索 merged into 灵感库 — InspirationSearchPage kept as a thin
// wrapper but no longer mounted directly from the nav.
import InspirationLibraryPage from "./pages/InspirationLibraryPage";

type Tab =
  // 市场数据库
  | "market-overview" | "references" | "market-features"
  // 参考数据库 (references = 参考作品特征提取, references-search = 工具)
  | "references-overview" | "references-search"
  // 灵感数据库
  | "inspiration-overview" | "inspiration-search" | "inspiration-library"
  // 创作
  | "projects" | "project-setup" | "editor" | "characters"
  | "worldbook" | "storyline" | "storyland"
  // 智能体与设置
  | "skills" | "settings"
  // legacy / dashboard
  | "dashboard" | "rankings" | "analysis"
  // legacy aliases kept for deep-links from the inspector drawer:
  | "preferences" | "domain-learning";

interface Project { id: string; name: string; genre?: string; word_count?: number; chapter_count?: number; }

const NAV: { section: string; items: { key: Tab; icon: string; label: string }[] }[] = [
  {
    section: "首页",
    items: [
      { key: "dashboard", icon: "▣", label: "首页" },
    ],
  },
  {
    section: "市场数据库",
    items: [
      // 市场总览 = 榜单 + 分析（合并）
      { key: "market-overview", icon: "≡", label: "市场总览" },
      // 市场特征提取 before 市场作品搜索 per latest UX call.
      { key: "market-features", icon: "△", label: "市场特征提取" },
    ],
  },
  {
    section: "参考数据库",
    items: [
      { key: "references-overview", icon: "▦", label: "参考总览" },
      // 参考作品详情 → 参考作品特征提取（renamed for clarity）
      { key: "references",          icon: "⊞", label: "参考作品特征提取" },
      // 工具页内含: 参考作品搜索 / 作品对比 / 索引管理
      { key: "references-search",   icon: "⚒", label: "参考数据库工具" },
    ],
  },
  {
    section: "灵感数据库",
    items: [
      { key: "inspiration-overview", icon: "▣", label: "灵感总览" },
      // 灵感搜索 merged INTO 灵感库 (default shows all + search box).
      // Cross-work semantic search is still available from any card.
      { key: "inspiration-library",  icon: "✦", label: "灵感库" },
    ],
  },
  {
    section: "创作",
    items: [
      { key: "projects",     icon: "□", label: "开书" },
      { key: "characters",   icon: "♢", label: "角色管理" },
      { key: "worldbook",    icon: "⊕", label: "世界书" },
      { key: "editor",       icon: "✎", label: "编辑器" },
      { key: "storyline",    icon: "─", label: "剧情线" },
      { key: "storyland",    icon: "◎", label: "故事中世界" },
    ],
  },
  {
    section: "智能体与设置",
    items: [
      // 智能体 page now also hosts 写作偏好 + 领域知识 as tabs
      { key: "skills",       icon: "⚙", label: "智能体" },
      { key: "settings",     icon: "☸", label: "设置" },
    ],
  },
];

function AppInner() {
  const [tab, setTab] = useState<Tab>("dashboard");
  const [sidebarW, setSidebarW] = useState(220);
  const [collapsed, setCollapsed] = useState(false);
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
      <aside className="sidebar" style={{ width: collapsed ? 56 : sidebarW }} role="navigation" aria-label="Main navigation">
        <div className="sidebar-brand" style={collapsed ? { padding: "14px 0", textAlign: "center" } : undefined}>
          <h1 style={collapsed ? { margin: 0 } : undefined}>
            <img src="/favicon.svg" alt="InkOctoBot" style={{ width: 24, height: 24, verticalAlign: "middle", marginRight: collapsed ? 0 : 6 }} />
            {!collapsed && "InkOctoBot"}
          </h1>
          {!collapsed && <p>AI 小说智能体工作台</p>}
        </div>

        {/* Search trigger */}
        <button
          className="nav-btn"
          onClick={() => setSearchOpen(true)}
          aria-label="Search (Ctrl+K)"
          title="搜索 (Ctrl+K)"
          style={{ margin: collapsed ? "10px auto 6px" : "10px 8px 6px", width: collapsed ? 38 : "auto", padding: collapsed ? "5px 0" : "5px 10px", display: "flex", alignItems: "center", justifyContent: "center", gap: 6, fontSize: 11, color: "var(--text-tertiary)", background: "var(--bg-surface-2)", border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", height: 30 }}
        >
          <span style={{ fontSize: 12, opacity: 0.6 }}>&#x2315;</span>
          {!collapsed && (
            <>
              <span style={{ flex: 1, textAlign: "left" }}>搜索...</span>
              <kbd style={{ fontSize: 9, color: "var(--text-disabled)", background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 3, padding: "1px 4px", fontFamily: "var(--font-mono)", lineHeight: 1.4 }}>
                {/Mac|iPod|iPhone|iPad/.test(navigator?.platform || "") ? "⌘K" : "Ctrl+K"}
              </kbd>
            </>
          )}
        </button>

        <nav className="sidebar-nav" aria-label="Page navigation">
          {NAV.map(group => (
            <React.Fragment key={group.section}>
              {collapsed || group.section === "首页" ? null : group.section === "创作" ? (
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
                  title={collapsed ? item.label : undefined}
                  style={collapsed ? { justifyContent: "center" } : undefined}
                >
                  <span className="nav-icon" aria-hidden="true">{item.icon}</span>
                  {!collapsed && <span>{item.label}</span>}
                </button>
              ))}
            </React.Fragment>
          ))}
        </nav>

        <button
          className="nav-btn"
          onClick={() => setCollapsed(c => !c)}
          title={collapsed ? "展开导航" : "收起导航"}
          style={{ margin: "4px 8px", justifyContent: collapsed ? "center" : undefined, color: "var(--text-tertiary)" }}
        >
          <span className="nav-icon" aria-hidden="true">{collapsed ? "»" : "«"}</span>
          {!collapsed && <span>收起导航</span>}
        </button>

        {!collapsed && <div className="sidebar-footer">InkOctoBot v2.1</div>}
      </aside>

      {!collapsed && <div className="resize-handle" onMouseDown={onMouseDown} />}

      <main id="main-content" className="main-content" role="main" aria-label="Page content">
        {tab === "dashboard" && <ErrorBoundary key="dashboard"><DashboardPage projects={projects} onNavigate={(t: string) => setTab(t as Tab)} onSelectProject={setActiveProject} /></ErrorBoundary>}

        {/* 市场数据库 */}
        {tab === "market-overview" && <ErrorBoundary key="market-overview"><MarketOverviewPage /></ErrorBoundary>}
        {tab === "market-features" && <ErrorBoundary key="market-features"><MarketFeatureExtractionPage /></ErrorBoundary>}
        {/* Legacy aliases — kept so deep-links / bookmarks still resolve */}
        {tab === "rankings" && <ErrorBoundary key="rankings"><MarketOverviewPage /></ErrorBoundary>}
        {tab === "analysis" && <ErrorBoundary key="analysis"><MarketFeatureExtractionPage /></ErrorBoundary>}

        {/* 参考数据库 */}
        {tab === "references-overview" && <ErrorBoundary key="references-overview"><ReferenceOverviewPage onNavigate={(t: string) => setTab(t as Tab)} /></ErrorBoundary>}
        {tab === "references"          && <ErrorBoundary key="references"><ReferenceLibraryPage /></ErrorBoundary>}
        {tab === "references-search"   && <ErrorBoundary key="references-search"><ReferenceSearchPage onNavigate={(t: string) => setTab(t as Tab)} /></ErrorBoundary>}

        {/* 灵感数据库 (NEW) */}
        {tab === "inspiration-overview" && <ErrorBoundary key="inspiration-overview"><InspirationOverviewPage /></ErrorBoundary>}
        {tab === "inspiration-library"  && <ErrorBoundary key="inspiration-library"><InspirationLibraryPage onNavigate={(t: string) => setTab(t as Tab)} /></ErrorBoundary>}
        {/* Legacy alias — old links to 灵感搜索 now land in the merged 灵感库 */}
        {tab === "inspiration-search"   && <ErrorBoundary key="inspiration-search"><InspirationLibraryPage onNavigate={(t: string) => setTab(t as Tab)} /></ErrorBoundary>}

        {/* 创作 */}
        {tab === "projects"      && <ErrorBoundary key="projects"><ProjectListPage activeProject={activeProject} onSelectProject={setActiveProject} onNavigate={(t: string) => setTab(t as Tab)} /></ErrorBoundary>}
        {tab === "project-setup" && <ErrorBoundary key="project-setup"><ProjectSetupPage projectId={activeProject} /></ErrorBoundary>}
        {tab === "editor"        && <ErrorBoundary key="editor"><EditorPage projectId={activeProject} onNavigate={(t: string) => setTab(t as Tab)} /></ErrorBoundary>}
        {tab === "characters"    && <ErrorBoundary key="characters"><CharacterManagerPage projectId={activeProject} projects={projects} /></ErrorBoundary>}
        {tab === "worldbook"     && <ErrorBoundary key="worldbook"><WorldBookPage projectId={activeProject} projects={projects} /></ErrorBoundary>}
        {tab === "storyline"     && <ErrorBoundary key="storyline"><StorylinePage projectId={activeProject} /></ErrorBoundary>}
        {tab === "storyland" && <ErrorBoundary key="storyland"><StorylandPage projectId={activeProject} /></ErrorBoundary>}

        {/* 智能体与设置 */}
        {tab === "skills"      && <ErrorBoundary key="skills"><SkillsPage projects={projects} activeProject={activeProject} /></ErrorBoundary>}
        {tab === "settings"    && <ErrorBoundary key="settings"><SettingsPage /></ErrorBoundary>}

        {/* Legacy deep-link tab keys — route into the new IA where preferences / domain
            live as tabs inside SkillsPage. */}
        {tab === "preferences"     && <ErrorBoundary key="alias-preferences"><SkillsPage projects={projects} activeProject={activeProject} /></ErrorBoundary>}
        {tab === "domain-learning" && <ErrorBoundary key="alias-domain"><SkillsPage projects={projects} activeProject={activeProject} /></ErrorBoundary>}
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
      <DialogProvider>
        <AppInner />
      </DialogProvider>
    </ToastProvider>
  );
}
