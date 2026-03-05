import React, { useState, useRef, useEffect, useCallback } from "react";
import { apiGet } from "./api/client";
import DashboardPage from "./pages/DashboardPage";
import RankingsPage from "./pages/RankingsPage";
import ReferenceLibraryPage from "./pages/ReferenceLibraryPage";
import TrendAnalysisPage from "./pages/TrendAnalysisPage";
import EditorPage from "./pages/EditorPage";
import CharacterManagerPage from "./pages/CharacterManagerPage";
import WorldBookPage from "./pages/WorldBookPage";
import StorylinePage from "./pages/StorylinePage";
import SettingsPage from "./pages/SettingsPage";

type Tab = "dashboard"|"rankings"|"references"|"trends"|"editor"|"characters"|"worldbook"|"storyline"|"settings";
interface Project { id: string; name: string; genre?: string; }

export default function App() {
  const [tab, setTab] = useState<Tab>("dashboard");
  const [sidebarW, setSidebarW] = useState(240);
  const [projects, setProjects] = useState<Project[]>([]);
  const [activeProject, setActiveProject] = useState<string>("");
  const [showProjMenu, setShowProjMenu] = useState(false);
  const dragging = useRef(false);

  // Load projects
  useEffect(() => {
    apiGet<{items: Project[]}>("/api/data/projects").then(r => {
      setProjects(r.items || []);
      if (r.items?.length && !activeProject) setActiveProject(r.items[0].id);
    }).catch(() => {});
  }, []);

  const createProject = async () => {
    const name = prompt("新项目名称：");
    if (!name) return;
    const res = await fetch("/api/data/projects", {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name})}).then(r=>r.json());
    setProjects(prev => [...prev, res]);
    setActiveProject(res.id);
    setShowProjMenu(false);
  };

  const activeProjectName = projects.find(p => p.id === activeProject)?.name || "未选择项目";

  // Sidebar resize
  const onMouseDown = useCallback(() => { dragging.current = true; document.body.style.cursor = "col-resize"; document.body.style.userSelect = "none"; }, []);
  useEffect(() => {
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

          <div className="sidebar-section-label" style={{display:"flex",alignItems:"center",justifyContent:"space-between",paddingRight:10}}>
            <span>创作</span>
            <div style={{position:"relative"}}>
              <button onClick={()=>setShowProjMenu(!showProjMenu)} style={{background:"none",border:"1px solid var(--ink-500)",borderRadius:4,color:"var(--ink-300)",fontSize:9,padding:"2px 6px",cursor:"pointer",letterSpacing:0}}>
                {activeProjectName.length > 8 ? activeProjectName.slice(0,8)+"…" : activeProjectName} ▾
              </button>
              {showProjMenu && <div style={{position:"absolute",top:"100%",right:0,marginTop:4,background:"var(--ink-800)",border:"1px solid var(--ink-600)",borderRadius:6,padding:4,zIndex:100,minWidth:160,boxShadow:"var(--shadow-lg)"}}>
                {projects.map(p => <div key={p.id} onClick={()=>{setActiveProject(p.id);setShowProjMenu(false);}} style={{padding:"6px 10px",fontSize:12,color:p.id===activeProject?"var(--accent-light)":"var(--ink-200)",cursor:"pointer",borderRadius:4,background:p.id===activeProject?"rgba(192,57,43,0.15)":"transparent"}}>{p.name}</div>)}
                <div style={{borderTop:"1px solid var(--ink-600)",marginTop:4,paddingTop:4}}>
                  <div onClick={createProject} style={{padding:"6px 10px",fontSize:12,color:"var(--jade)",cursor:"pointer",borderRadius:4}}>+ 新建项目</div>
                </div>
              </div>}
            </div>
          </div>
          <NB icon="✏️" label="编辑器" active={tab==="editor"} onClick={()=>setTab("editor")} />
          <NB icon="👤" label="角色管理" active={tab==="characters"} onClick={()=>setTab("characters")} />
          <NB icon="🌍" label="世界书" active={tab==="worldbook"} onClick={()=>setTab("worldbook")} />
          <NB icon="🗺️" label="剧情线" active={tab==="storyline"} onClick={()=>setTab("storyline")} />

          <div className="sidebar-section-label">系统</div>
          <NB icon="⚙️" label="设置" active={tab==="settings"} onClick={()=>setTab("settings")} />
        </nav>
        <div className="sidebar-footer">InkOctoBot v0.6</div>
      </aside>
      <div className="resize-handle" onMouseDown={onMouseDown} />
      <main className="main-content">
        {tab==="dashboard"&&<DashboardPage/>}
        {tab==="rankings"&&<RankingsPage/>}
        {tab==="references"&&<ReferenceLibraryPage/>}
        {tab==="trends"&&<TrendAnalysisPage/>}
        {tab==="editor"&&<EditorPage projectId={activeProject}/>}
        {tab==="characters"&&<CharacterManagerPage projectId={activeProject} projects={projects}/>}
        {tab==="worldbook"&&<WorldBookPage projectId={activeProject} projects={projects}/>}
        {tab==="storyline"&&<StorylinePage projectId={activeProject}/>}
        {tab==="settings"&&<SettingsPage/>}
      </main>
    </div>
  );
}

function NB(p:{icon:string;label:string;active:boolean;onClick:()=>void}) {
  return <button className={`nav-btn${p.active?" active":""}`} onClick={p.onClick}><span className="nav-icon">{p.icon}</span><span>{p.label}</span></button>;
}