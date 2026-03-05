import React, { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { apiGet, apiPost } from "../api/client";

// Inline apiPut since client.ts may not have it
const apiPut = <T,>(url:string, body:any):Promise<T> =>
  fetch(url, {method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}).then(r=>{if(!r.ok)throw new Error(`HTTP ${r.status}`);return r.json()});

interface Chapter { id:string; title:string; content:string; }
interface Volume { id:string; title:string; chapters:Chapter[]; collapsed:boolean; }
const uid=()=>`${Date.now()}_${Math.random().toString(36).slice(2,8)}`;
const wc=(t:string)=>t?t.replace(/[\s\p{P}]/gu,"").length:0;

export default function EditorPage() {
  const [vols,setVols]=useState<Volume[]>([{id:uid(),title:"第一卷",collapsed:false,chapters:[{id:uid(),title:"第一章",content:""}]}]);
  const [activeId,setActiveId]=useState(vols[0].chapters[0].id);
  const [content,setContent]=useState("");
  const [loaded,setLoaded]=useState(false);
  const [leftW,setLeftW]=useState(220); const [rightW,setRightW]=useState(260);
  const dragRef=useRef<{which:"left"|"right";startX:number;startW:number}|null>(null);
  const saveTimer=useRef<ReturnType<typeof setTimeout>|null>(null);
  const [renaming,setRenaming]=useState<string|null>(null); const [renameVal,setRenameVal]=useState("");
  const [saved,setSaved]=useState(0);

  // Load from API on mount
  useEffect(()=>{apiGet<any>("/api/data/editor").then(d=>{if(d.volumes?.length){setVols(d.volumes);setActiveId(d.volumes[0]?.chapters?.[0]?.id||"");}setLoaded(true);}).catch(()=>setLoaded(true));},[]);

  const activeCh=useMemo(()=>{for(const v of vols){const c=v.chapters.find(c=>c.id===activeId);if(c)return c;}return null;},[vols,activeId]);
  const activeVol=useMemo(()=>vols.find(v=>v.chapters.some(c=>c.id===activeId))||null,[vols,activeId]);

  useEffect(()=>{if(activeCh)setContent(activeCh.content);},[activeId,loaded]);

  // Auto-save to state + API
  useEffect(()=>{if(!loaded)return;if(saveTimer.current)clearTimeout(saveTimer.current);
    saveTimer.current=setTimeout(()=>{
      const nv=vols.map(v=>({...v,chapters:v.chapters.map(c=>c.id===activeId?{...c,content}:c)}));
      setVols(nv);setSaved(Date.now());
      apiPut("/api/data/editor",{volumes:nv}).catch(console.error);
    },1200);
    return()=>{if(saveTimer.current)clearTimeout(saveTimer.current);};
  },[content,activeId,loaded]);

  // Resize handlers
  useEffect(()=>{
    const onMove=(e:MouseEvent)=>{if(!dragRef.current)return;const d=e.clientX-dragRef.current.startX;
      if(dragRef.current.which==="left")setLeftW(Math.max(160,Math.min(360,dragRef.current.startW+d)));
      else setRightW(Math.max(200,Math.min(400,dragRef.current.startW-d)));};
    const onUp=()=>{dragRef.current=null;document.body.style.cursor="";document.body.style.userSelect="";};
    window.addEventListener("mousemove",onMove);window.addEventListener("mouseup",onUp);
    return()=>{window.removeEventListener("mousemove",onMove);window.removeEventListener("mouseup",onUp);};
  },[]);
  const startDrag=(which:"left"|"right",e:React.MouseEvent)=>{dragRef.current={which,startX:e.clientX,startW:which==="left"?leftW:rightW};document.body.style.cursor="col-resize";document.body.style.userSelect="none";};

  const addVol=()=>{const v:Volume={id:uid(),title:`第${vols.length+1}卷`,collapsed:false,chapters:[]};setVols([...vols,v]);};
  const addCh=(vid:string)=>setVols(vols.map(v=>v.id===vid?{...v,chapters:[...v.chapters,{id:uid(),title:`第${v.chapters.length+1}章`,content:""}]}:v));
  const delCh=(cid:string)=>{const all=vols.flatMap(v=>v.chapters);if(all.length<=1)return;setVols(vols.map(v=>({...v,chapters:v.chapters.filter(c=>c.id!==cid)})));if(activeId===cid){const rem=all.filter(c=>c.id!==cid);if(rem.length)setActiveId(rem[0].id);}};
  const delVol=(vid:string)=>{if(vols.length<=1)return;const vol=vols.find(v=>v.id===vid);if(vol?.chapters.some(c=>c.id===activeId)){const o=vols.find(v=>v.id!==vid);if(o?.chapters.length)setActiveId(o.chapters[0].id);}setVols(vols.filter(v=>v.id!==vid));};
  const startRename=(id:string,t:string)=>{setRenaming(id);setRenameVal(t);};
  const commitRename=()=>{if(!renaming||!renameVal.trim()){setRenaming(null);return;}setVols(vols.map(v=>{if(v.id===renaming)return{...v,title:renameVal.trim()};return{...v,chapters:v.chapters.map(c=>c.id===renaming?{...c,title:renameVal.trim()}:c)};}));setRenaming(null);};

  const words=useMemo(()=>wc(content),[content]);
  const totalW=useMemo(()=>vols.reduce((s,v)=>s+v.chapters.reduce((s2,c)=>s2+wc(c.content),0),0),[vols]);
  const totalCh=useMemo(()=>vols.reduce((s,v)=>s+v.chapters.length,0),[vols]);

  const handleKey=useCallback((e:React.KeyboardEvent)=>{if((e.ctrlKey||e.metaKey)&&e.key==="s"){e.preventDefault();const nv=vols.map(v=>({...v,chapters:v.chapters.map(c=>c.id===activeId?{...c,content}:c)}));setVols(nv);setSaved(Date.now());apiPut("/api/data/editor",{volumes:nv}).catch(console.error);}},[content,activeId,vols]);

  if(!loaded)return<div className="loading" style={{height:"100vh"}}><div className="loading-spinner"/>加载中…</div>;

  return (
    <div style={{display:"flex",height:"100vh",overflow:"hidden"}} onKeyDown={handleKey}>
      {/* LEFT */}
      <div style={{width:leftW,flexShrink:0,background:"var(--paper-card)",borderRight:"1px solid var(--ink-100)",display:"flex",flexDirection:"column",overflow:"hidden"}}>
        <div style={{padding:"14px 14px 10px",borderBottom:"1px solid var(--ink-50)",display:"flex",alignItems:"center",justifyContent:"space-between"}}>
          <span style={{fontFamily:"var(--font-serif)",fontWeight:700,fontSize:14}}>📂 大纲</span>
          <button onClick={addVol} style={{width:26,height:26,border:"1px solid var(--ink-200)",borderRadius:4,background:"transparent",cursor:"pointer",fontSize:14,display:"flex",alignItems:"center",justifyContent:"center"}}>＋</button>
        </div>
        <div style={{flex:1,overflowY:"auto",padding:"8px 6px"}}>
          {vols.map(v=><div key={v.id}>
            <div style={{display:"flex",alignItems:"center",gap:4,padding:"6px 8px",borderRadius:4,fontSize:13,fontWeight:600,color:"var(--ink-700)",marginBottom:2,background:activeVol?.id===v.id?"var(--paper-warm)":"transparent"}}>
              <span style={{cursor:"pointer",fontSize:10,width:14,textAlign:"center"}} onClick={()=>setVols(vols.map(x=>x.id===v.id?{...x,collapsed:!x.collapsed}:x))}>{v.collapsed?"▶":"▼"}</span>
              {renaming===v.id?<input value={renameVal} onChange={e=>setRenameVal(e.target.value)} onBlur={commitRename} onKeyDown={e=>e.key==="Enter"&&commitRename()} autoFocus style={{flex:1,border:"1px solid var(--accent)",borderRadius:3,padding:"2px 6px",fontSize:12,outline:"none"}}/>
              :<span style={{flex:1,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} onDoubleClick={()=>startRename(v.id,v.title)}>{v.title}</span>}
              <button onClick={()=>addCh(v.id)} title="添加章节" style={{width:20,height:20,border:"none",borderRadius:3,background:"transparent",color:"var(--ink-400)",fontSize:14,cursor:"pointer"}}>+</button>
              {vols.length>1&&<button onClick={()=>delVol(v.id)} style={{width:20,height:20,border:"none",borderRadius:3,background:"transparent",color:"var(--ink-400)",fontSize:12,cursor:"pointer"}}>×</button>}
            </div>
            {!v.collapsed&&v.chapters.map(ch=><div key={ch.id} onClick={()=>setActiveId(ch.id)} style={{display:"flex",alignItems:"center",gap:6,padding:"5px 8px 5px 28px",borderRadius:4,fontSize:12.5,color:ch.id===activeId?"var(--accent)":"var(--ink-600)",background:ch.id===activeId?"var(--accent-muted)":"transparent",fontWeight:ch.id===activeId?600:400,cursor:"pointer",marginBottom:1}}>
              {renaming===ch.id?<input value={renameVal} onChange={e=>setRenameVal(e.target.value)} onBlur={commitRename} onKeyDown={e=>e.key==="Enter"&&commitRename()} autoFocus style={{flex:1,border:"1px solid var(--accent)",borderRadius:3,padding:"2px 6px",fontSize:12,outline:"none"}} onClick={e=>e.stopPropagation()}/>
              :<><span style={{flex:1,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} onDoubleClick={()=>startRename(ch.id,ch.title)}>{ch.title}</span><span style={{fontSize:10,color:"var(--ink-300)",fontFamily:"var(--font-mono)"}}>{wc(ch.content)}字</span></>}
              {totalCh>1&&<button onClick={e=>{e.stopPropagation();delCh(ch.id);}} style={{width:18,height:18,border:"none",background:"transparent",color:"var(--ink-400)",fontSize:12,cursor:"pointer"}}>×</button>}
            </div>)}
          </div>)}
        </div>
        <div style={{padding:"10px 14px",borderTop:"1px solid var(--ink-50)",fontSize:11,color:"var(--ink-400)"}}>{totalCh} 章 · {totalW.toLocaleString()} 字</div>
      </div>
      <div style={{width:4,cursor:"col-resize",background:"transparent",flexShrink:0}} onMouseDown={e=>startDrag("left",e)}>
        <div style={{width:2,height:"100%",margin:"0 auto",background:"var(--ink-100)",transition:"background 0.15s"}} onMouseEnter={e=>(e.currentTarget.style.background="var(--accent)")} onMouseLeave={e=>(e.currentTarget.style.background="var(--ink-100)")}/>
      </div>

      {/* CENTER */}
      <div style={{flex:1,display:"flex",flexDirection:"column",overflow:"hidden",background:"var(--paper)"}}>
        <div style={{padding:"16px 28px 12px",borderBottom:"1px solid var(--ink-50)",background:"var(--paper-card)"}}>
          <h3 style={{fontFamily:"var(--font-serif)",fontSize:18,fontWeight:700,marginBottom:2}}>{activeCh?.title||"选择章节"}</h3>
          <span style={{fontSize:11,color:"var(--ink-400)"}}>{activeVol?.title} · {words.toLocaleString()} 字{saved?` · 已保存`:""}</span>
        </div>
        <div style={{flex:1,overflow:"hidden"}}><textarea value={content} onChange={e=>setContent(e.target.value)} placeholder="在这里开始写作…" spellCheck={false}
          style={{width:"100%",height:"100%",border:"none",outline:"none",resize:"none",padding:"28px 48px 48px",fontFamily:"var(--font-serif)",fontSize:16,lineHeight:2,color:"var(--ink-800)",background:"var(--paper)",maxWidth:780,margin:"0 auto",display:"block"}}/></div>
      </div>

      <div style={{width:4,cursor:"col-resize",background:"transparent",flexShrink:0}} onMouseDown={e=>startDrag("right",e)}>
        <div style={{width:2,height:"100%",margin:"0 auto",background:"var(--ink-100)",transition:"background 0.15s"}} onMouseEnter={e=>(e.currentTarget.style.background="var(--accent)")} onMouseLeave={e=>(e.currentTarget.style.background="var(--ink-100)")}/>
      </div>

      {/* RIGHT */}
      <div style={{width:rightW,flexShrink:0,background:"var(--paper-card)",borderLeft:"1px solid var(--ink-100)",display:"flex",flexDirection:"column",overflowY:"auto"}}>
        <div style={{padding:"14px 16px 10px",borderBottom:"1px solid var(--ink-50)"}}><span style={{fontFamily:"var(--font-serif)",fontWeight:700,fontSize:14}}>🤖 AI 助手</span></div>
        <div style={{padding:"14px 16px",borderBottom:"1px solid var(--ink-50)"}}>
          <div style={{fontSize:11,fontWeight:600,textTransform:"uppercase",letterSpacing:.5,color:"var(--ink-400)",marginBottom:10}}>本章统计</div>
          <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:8}}>
            {[{v:words.toLocaleString(),l:"字数"},{v:content.split(/\n\n+/).filter(Boolean).length,l:"段落"},{v:content?(content.match(/[""「」『』]/g)||[]).length/2|0:0,l:"对话"},{v:Math.ceil(words/500),l:"阅读(分)"}].map(s=>
              <div key={s.l} style={{background:"var(--ink-50)",borderRadius:6,padding:10,textAlign:"center"}}><span style={{display:"block",fontFamily:"var(--font-mono)",fontSize:18,fontWeight:700,color:"var(--ink-800)"}}>{s.v}</span><span style={{display:"block",fontSize:10,color:"var(--ink-400)",marginTop:2}}>{s.l}</span></div>
            )}
          </div>
        </div>
        <div style={{padding:"14px 16px",borderBottom:"1px solid var(--ink-50)"}}>
          <div style={{fontSize:11,fontWeight:600,textTransform:"uppercase",letterSpacing:.5,color:"var(--ink-400)",marginBottom:10}}>AI 操作</div>
          {["✨ 生成本章","📝 续写下一段","🔄 改写选中","📊 风格分析"].map(t=><button key={t} disabled style={{display:"flex",alignItems:"center",gap:8,padding:"9px 12px",border:"1px solid var(--ink-200)",borderRadius:6,background:"transparent",color:"var(--ink-600)",fontSize:12.5,cursor:"not-allowed",opacity:.45,width:"100%",textAlign:"left",marginBottom:6,fontFamily:"var(--font-sans)"}}>{t}</button>)}
          <div style={{marginTop:8,padding:"10px 12px",background:"var(--paper-warm)",borderRadius:6,fontSize:11,color:"var(--ink-400)",lineHeight:1.5}}>🚧 AI 功能需配置模型后启用。</div>
        </div>
        <div style={{padding:"14px 16px",fontSize:12,color:"var(--ink-400)",lineHeight:1.8}}>
          共 {vols.length} 卷 · {totalCh} 章<br/>总字数: {totalW.toLocaleString()}<br/>数据自动保存至 /data/editor/
        </div>
      </div>
    </div>
  );
}
