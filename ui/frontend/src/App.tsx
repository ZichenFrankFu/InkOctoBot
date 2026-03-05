import React, { useState, useRef, useCallback } from "react";
import DashboardPage from "./pages/DashboardPage";
import RankingsPage from "./pages/RankingsPage";
import ReferenceLibraryPage from "./pages/ReferenceLibraryPage";
import TrendAnalysisPage from "./pages/TrendAnalysisPage";
import EditorPage from "./pages/EditorPage";
import CharacterManagerPage from "./pages/CharacterManagerPage";
import WorldBookPage from "./pages/WorldBookPage";
import SettingsPage from "./pages/SettingsPage";

type Tab = "dashboard"|"rankings"|"references"|"trends"|"editor"|"characters"|"worldbook"|"settings";

export default function App() {
  const [tab, setTab] = useState<Tab>("dashboard");
  const [sidebarW, setSidebarW] = useState(240);
  const dragging = useRef(false);

  const onMouseDown = useCallback(() => { dragging.current = true; document.body.style.cursor = "col-resize"; document.body.style.userSelect = "none"; }, []);
  React.useEffect(() => {
    const onMove = (e: MouseEvent) => { if (dragging.current) setSidebarW(Math.max(180, Math.min(360, e.clientX))); };
    const onUp = () => { dragging.current = false; document.body.style.cursor = ""; document.body.style.userSelect = ""; };
    window.addEventListener("mousemove", onMove); window.addEventListener("mouseup", onUp);
    return () => { window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp); };
  }, []);

  return (
    <div className="app-layout">
      <aside className="sidebar" style={{ width: sidebarW }}>
        <div className="sidebar-brand"><h1>🐙 InkOctoBot</h1><p>小说AI智能体工作台</p></div>
        <nav className="sidebar-nav">
          <div className="sidebar-section-label">概览</div>
          <NB icon="📊" label="首页" active={tab==="dashboard"} onClick={()=>setTab("dashboard")} />
          <div className="sidebar-section-label">数据</div>
          <NB icon="📋" label="榜单浏览" active={tab==="rankings"} onClick={()=>setTab("rankings")} />
          <NB icon="📚" label="参考作品库" active={tab==="references"} onClick={()=>setTab("references")} />
          <NB icon="📈" label="趋势分析" active={tab==="trends"} onClick={()=>setTab("trends")} />
          <div className="sidebar-section-label">创作</div>
          <NB icon="✏️" label="编辑器" active={tab==="editor"} onClick={()=>setTab("editor")} />
          <NB icon="👤" label="角色管理" active={tab==="characters"} onClick={()=>setTab("characters")} />
          <NB icon="🌍" label="世界书" active={tab==="worldbook"} onClick={()=>setTab("worldbook")} />
          <div className="sidebar-section-label">系统</div>
          <NB icon="⚙️" label="设置" active={tab==="settings"} onClick={()=>setTab("settings")} />
        </nav>
        <div className="sidebar-footer">InkOctoBot v0.5</div>
      </aside>
      <div className="resize-handle" onMouseDown={onMouseDown} />
      <main className="main-content">
        {tab==="dashboard"&&<DashboardPage/>} {tab==="rankings"&&<RankingsPage/>}
        {tab==="references"&&<ReferenceLibraryPage/>} {tab==="trends"&&<TrendAnalysisPage/>}
        {tab==="editor"&&<EditorPage/>} {tab==="characters"&&<CharacterManagerPage/>}
        {tab==="worldbook"&&<WorldBookPage/>} {tab==="settings"&&<SettingsPage/>}
      </main>
    </div>
  );
}
function NB(p:{icon:string;label:string;active:boolean;onClick:()=>void}) {
  return <button className={`nav-btn${p.active?" active":""}`} onClick={p.onClick}><span className="nav-icon">{p.icon}</span><span>{p.label}</span></button>;
}
