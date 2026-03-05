import React, { useEffect, useState, useCallback, useMemo } from "react";
import { apiGet } from "../api/client";
const put=(u:string,b:any)=>fetch(u,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)}).then(r=>r.json());
const post=(u:string,b:any)=>fetch(u,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)}).then(r=>r.json());
const del=(u:string)=>fetch(u,{method:"DELETE"}).then(r=>r.json());

interface Entry { id:string; category:string; title:string; content:string; tags:string[]; project_id:string; [k:string]:any; }
interface Project { id:string; name:string; }
const CATS=[{n:"力量体系",i:"⚡"},{n:"社会结构",i:"🏛️"},{n:"地理",i:"🗺️"},{n:"历史",i:"📜"},{n:"硬规则",i:"📏"},{n:"其他",i:"📝"}];

export default function WorldBookPage({projectId,projects}:{projectId:string;projects:Project[]}) {
  const [items,setItems]=useState<Entry[]>([]); const [loading,setLoading]=useState(true);
  const [cat,setCat]=useState(""); const [editing,setEditing]=useState<Entry|null>(null); const [dirty,setDirty]=useState(false);

  const load=useCallback(()=>{setLoading(true);
    apiGet<{items:Entry[]}>(`/api/data/worldbook${projectId?`?project_id=${projectId}`:""}`).then(r=>setItems(r.items||[])).catch(console.error).finally(()=>setLoading(false));
  },[projectId]);
  useEffect(()=>{load();},[projectId]);

  const filtered=useMemo(()=>cat?items.filter(i=>i.category===cat):items,[items,cat]);
  const cc=useMemo(()=>{const m:Record<string,number>={};items.forEach(i=>{m[i.category]=(m[i.category]||0)+1;});return m;},[items]);
  const projName=projects.find(p=>p.id===projectId)?.name||"未选择项目";

  const create=async()=>{const e=await post("/api/data/worldbook",{category:cat||"力量体系",title:"新条目",project_id:projectId});setItems([...items,e]);setEditing(e);setDirty(false);};
  const save=async()=>{if(!editing)return;await put(`/api/data/worldbook/${editing.id}`,editing);setDirty(false);load();};
  const remove=async(id:string)=>{if(!confirm("确定删除？"))return;await del(`/api/data/worldbook/${id}`);if(editing?.id===id)setEditing(null);load();};
  const u=(k:string,v:any)=>{if(!editing)return;setEditing({...editing,[k]:v});setDirty(true);};

  return (
    <div style={{display:"flex",height:"100vh",overflow:"hidden"}}>
      <div style={{width:320,flexShrink:0,background:"var(--paper-card)",borderRight:"1px solid var(--ink-100)",display:"flex",flexDirection:"column"}}>
        <div style={{padding:"16px 16px 12px",borderBottom:"1px solid var(--ink-50)"}}>
          <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:6}}>
            <h3 style={{fontFamily:"var(--font-serif)",fontSize:16,fontWeight:700}}>🌍 世界书</h3>
            <button className="btn-primary" style={{padding:"6px 14px",fontSize:12}} onClick={create}>+ 新条目</button>
          </div>
          <div style={{fontSize:11,color:"var(--ink-400)"}}>📖 {projName}</div>
        </div>
        <div style={{padding:"10px 12px",borderBottom:"1px solid var(--ink-50)",display:"flex",gap:4,flexWrap:"wrap"}}>
          <button onClick={()=>setCat("")} style={{padding:"4px 10px",borderRadius:12,border:cat===""?"1px solid var(--accent)":"1px solid var(--ink-200)",background:cat===""?"var(--accent-muted)":"transparent",color:cat===""?"var(--accent)":"var(--ink-500)",fontSize:11,cursor:"pointer"}}>全部 ({items.length})</button>
          {CATS.map(c=><button key={c.n} onClick={()=>setCat(c.n)} style={{padding:"4px 10px",borderRadius:12,border:cat===c.n?"1px solid var(--accent)":"1px solid var(--ink-200)",background:cat===c.n?"var(--accent-muted)":"transparent",color:cat===c.n?"var(--accent)":"var(--ink-500)",fontSize:11,cursor:"pointer"}}>{c.i} {c.n} ({cc[c.n]||0})</button>)}
        </div>
        <div style={{flex:1,overflowY:"auto"}}>
          {loading?<div className="loading"><div className="loading-spinner"/></div>:filtered.length===0?
            <div className="empty-state" style={{padding:32}}><p>{cat?"该分类下暂无条目":"暂无条目"}</p></div>:
            filtered.map(e=><div key={e.id} onClick={()=>{setEditing(e);setDirty(false);}} style={{padding:"12px 16px",borderBottom:"1px solid var(--ink-50)",cursor:"pointer",background:editing?.id===e.id?"var(--accent-muted)":"transparent"}}>
              <div style={{display:"flex",alignItems:"center",gap:8,marginBottom:4}}>
                <span style={{fontSize:14}}>{CATS.find(c=>c.n===e.category)?.i||"📝"}</span>
                <span style={{fontSize:13,fontWeight:600,flex:1,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{e.title}</span>
                <button onClick={ev=>{ev.stopPropagation();remove(e.id);}} style={{border:"none",background:"transparent",color:"var(--ink-300)",cursor:"pointer"}}>×</button>
              </div>
              <div style={{fontSize:11,color:"var(--ink-400)"}}>{e.category} · {e.content.length>50?e.content.slice(0,50)+"…":e.content||"(空)"}</div>
            </div>)
          }
        </div>
      </div>
      <div style={{flex:1,overflowY:"auto",background:"var(--paper)"}}>
        {!editing?<div className="empty-state" style={{paddingTop:120}}><h4>选择或创建一个条目</h4></div>:
        <div style={{maxWidth:700,margin:"0 auto",padding:"28px 32px 48px"}}>
          <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:24}}>
            <h2 style={{fontFamily:"var(--font-serif)",fontSize:22,fontWeight:700}}>{editing.title}</h2>
            <button className="btn-primary" onClick={save} disabled={!dirty} style={{opacity:dirty?1:.5}}>{dirty?"保存":"已保存"}</button>
          </div>
          <div className="card" style={{marginBottom:20}}><div className="card-header"><h3>条目信息</h3></div><div className="card-body">
            <div style={{marginBottom:12}}><label style={{fontSize:12,fontWeight:600,color:"var(--ink-500)",display:"block",marginBottom:4}}>标题</label>
              <input value={editing.title} onChange={e=>u("title",e.target.value)} style={{width:"100%",padding:"8px 12px",border:"1px solid var(--ink-200)",borderRadius:6,fontSize:14,outline:"none",fontWeight:600}}/></div>
            <div style={{marginBottom:12}}><label style={{fontSize:12,fontWeight:600,color:"var(--ink-500)",display:"block",marginBottom:4}}>分类</label>
              <div style={{display:"flex",gap:6,flexWrap:"wrap"}}>{CATS.map(c=><button key={c.n} onClick={()=>u("category",c.n)} style={{padding:"6px 14px",borderRadius:20,border:editing.category===c.n?"2px solid var(--accent)":"1px solid var(--ink-200)",background:editing.category===c.n?"var(--accent-muted)":"transparent",color:editing.category===c.n?"var(--accent)":"var(--ink-600)",fontSize:12,cursor:"pointer"}}>{c.i} {c.n}</button>)}</div></div>
            <div style={{marginBottom:12}}><label style={{fontSize:12,fontWeight:600,color:"var(--ink-500)",display:"block",marginBottom:4}}>标签</label>
              <input value={(editing.tags||[]).join(", ")} onChange={e=>u("tags",e.target.value.split(",").map(s=>s.trim()).filter(Boolean))} style={{width:"100%",padding:"8px 12px",border:"1px solid var(--ink-200)",borderRadius:6,fontSize:13,outline:"none"}}/></div>
          </div></div>
          <div className="card"><div className="card-header"><h3>内容</h3></div><div className="card-body">
            <textarea value={editing.content} onChange={e=>u("content",e.target.value)} rows={16} style={{width:"100%",padding:"12px 16px",border:"1px solid var(--ink-200)",borderRadius:6,fontSize:14,fontFamily:"var(--font-serif)",outline:"none",resize:"vertical",lineHeight:1.8}} placeholder="在此输入世界观设定…"/>
          </div></div>
        </div>}
      </div>
    </div>
  );
}