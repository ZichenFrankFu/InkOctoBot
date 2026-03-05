import React, { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { apiGet } from "../api/client";
const apiPut = (u:string,b:any) => fetch(u,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)}).then(r=>r.json());

interface Ch { id:string; title:string; content:string; outline:string; }
interface Vol { id:string; title:string; chapters:Ch[]; collapsed:boolean; }
const uid=()=>`${Date.now()}_${Math.random().toString(36).slice(2,8)}`;
const wc=(t:string)=>t?t.replace(/[\s\p{P}]/gu,"").length:0;

// Placeholder pipeline data
const PIPELINE_DEMO = [
  {step:"Scene Planner",status:"done",desc:"将章节拆为3个场景"},
  {step:"Scene Director",status:"done",desc:"为每个场景注入导演指令"},
  {step:"Actor Agents",status:"active",desc:"角色扮演生成原始对话",
    actors:[
      {name:"张远",line:"*抽出剑* \"你到底是谁？\"",inner:"这个人的气势太强了"},
      {name:"李四",line:"*缓缓拔剑* \"你猜？\"",inner:"有点意思，居然没跑"},
    ]},
  {step:"Editor-Stylist",status:"idle",desc:"剪辑+文学风格化 → 分段输出（~600字/段）"},
];

export default function EditorPage({projectId}:{projectId:string}) {
  const [vols,setVols]=useState<Vol[]>([{id:uid(),title:"第一卷",collapsed:false,chapters:[{id:uid(),title:"第一章",content:"",outline:""}]}]);
  const [activeId,setActiveId]=useState(vols[0].chapters[0].id);
  const [content,setContent]=useState(""); const [loaded,setLoaded]=useState(false);
  const [leftW,setLeftW]=useState(220); const [rightW,setRightW]=useState(300);
  const dragRef=useRef<{which:"left"|"right";startX:number;startW:number}|null>(null);
  const saveTimer=useRef<ReturnType<typeof setTimeout>|null>(null);
  const [saved,setSaved]=useState(0); const [renaming,setRenaming]=useState<string|null>(null); const [renameVal,setRenameVal]=useState("");
  const [editingTitle,setEditingTitle]=useState(false); const [titleVal,setTitleVal]=useState("");
  const [aiTab,setAiTab]=useState<"outline"|"inspire"|"rewrite">("outline");
  const textRef=useRef<HTMLTextAreaElement>(null);
  const [selection,setSelection]=useState<{start:number;end:number;text:string}|null>(null);
  const [rewritePos,setRewritePos]=useState<{top:number;left:number}|null>(null);
  const [startTime]=useState(Date.now());

  useEffect(()=>{const pid=projectId||"default";
    apiGet<any>(`/api/data/editor?project_id=${pid}`).then(d=>{if(d.volumes?.length){setVols(d.volumes);setActiveId(d.volumes[0]?.chapters?.[0]?.id||"");}setLoaded(true);}).catch(()=>setLoaded(true));
  },[projectId]);

  const ch=useMemo(()=>{for(const v of vols){const c=v.chapters.find(c=>c.id===activeId);if(c)return c;}return null;},[vols,activeId]);
  const vol=useMemo(()=>vols.find(v=>v.chapters.some(c=>c.id===activeId))||null,[vols,activeId]);
  useEffect(()=>{if(ch&&loaded){setContent(ch.content);setTitleVal(ch.title);}setEditingTitle(false);},[activeId,loaded]);

  // Auto-save
  useEffect(()=>{if(!loaded)return;if(saveTimer.current)clearTimeout(saveTimer.current);
    saveTimer.current=setTimeout(()=>{
      const nv=vols.map(v=>({...v,chapters:v.chapters.map(c=>c.id===activeId?{...c,content,title:titleVal||c.title}:c)}));
      setVols(nv);setSaved(Date.now());
      apiPut("/api/data/editor",{project_id:projectId||"default",volumes:nv}).catch(console.error);
    },1200);
    return()=>{if(saveTimer.current)clearTimeout(saveTimer.current);};
  },[content,titleVal,activeId,loaded]);

  // Resize
  useEffect(()=>{
    const m=(e:MouseEvent)=>{if(!dragRef.current)return;const d=e.clientX-dragRef.current.startX;
      if(dragRef.current.which==="left")setLeftW(Math.max(160,Math.min(360,dragRef.current.startW+d)));
      else setRightW(Math.max(200,Math.min(440,dragRef.current.startW-d)));};
    const u=()=>{dragRef.current=null;document.body.style.cursor="";document.body.style.userSelect="";};
    window.addEventListener("mousemove",m);window.addEventListener("mouseup",u);
    return()=>{window.removeEventListener("mousemove",m);window.removeEventListener("mouseup",u);};
  },[]);
  const startDrag=(w:"left"|"right",e:React.MouseEvent)=>{dragRef.current={which:w,startX:e.clientX,startW:w==="left"?leftW:rightW};document.body.style.cursor="col-resize";document.body.style.userSelect="none";};

  // Text selection for rewrite
  const handleSelect=()=>{
    const el=textRef.current;if(!el)return;
    const s=el.selectionStart,e=el.selectionEnd;
    if(e>s&&e-s>2){
      const txt=content.substring(s,e);
      setSelection({start:s,end:e,text:txt});
      // Position the rewrite button near selection
      const lineH=32; const lines=content.substring(0,s).split("\n").length;
      setRewritePos({top:Math.min(lines*lineH+60,400),left:200});
    }else{setSelection(null);setRewritePos(null);}
  };

  // Outline operations
  const addVol=()=>{setVols([...vols,{id:uid(),title:`第${vols.length+1}卷`,collapsed:false,chapters:[]}]);};
  const addCh=(vid:string)=>setVols(vols.map(v=>v.id===vid?{...v,chapters:[...v.chapters,{id:uid(),title:`第${v.chapters.length+1}章`,content:"",outline:""}]}:v));
  const delCh=(cid:string)=>{const all=vols.flatMap(v=>v.chapters);if(all.length<=1)return;setVols(vols.map(v=>({...v,chapters:v.chapters.filter(c=>c.id!==cid)})));if(activeId===cid){const r=all.filter(c=>c.id!==cid);if(r.length)setActiveId(r[0].id);}};
  const startRename=(id:string,t:string)=>{setRenaming(id);setRenameVal(t);};
  const commitRename=()=>{if(!renaming||!renameVal.trim()){setRenaming(null);return;}setVols(vols.map(v=>{if(v.id===renaming)return{...v,title:renameVal.trim()};return{...v,chapters:v.chapters.map(c=>c.id===renaming?{...c,title:renameVal.trim()}:c)};}));setRenaming(null);};
  const updateOutline=(v:string)=>{setVols(vols.map(vol=>({...vol,chapters:vol.chapters.map(c=>c.id===activeId?{...c,outline:v}:c)})));};

  const words=useMemo(()=>wc(content),[content]);
  const totalW=useMemo(()=>vols.reduce((s,v)=>s+v.chapters.reduce((s2,c)=>s2+wc(c.content),0),0),[vols]);
  const totalCh=useMemo(()=>vols.reduce((s,v)=>s+v.chapters.length,0),[vols]);
  const elapsed=Math.floor((Date.now()-startTime)/60000);

  if(!loaded)return<div className="loading" style={{height:"100vh"}}><div className="loading-spinner"/>加载中…</div>;

  return (
    <div style={{display:"flex",height:"100vh",overflow:"hidden"}}>
      {/* ═══ LEFT: Outline ═══ */}
      <div style={{width:leftW,flexShrink:0,background:"var(--paper-card)",borderRight:"1px solid var(--ink-100)",display:"flex",flexDirection:"column"}}>
        <div style={{padding:"14px 14px 10px",borderBottom:"1px solid var(--ink-50)",display:"flex",alignItems:"center",justifyContent:"space-between"}}>
          <span style={{fontFamily:"var(--font-serif)",fontWeight:700,fontSize:14}}>📂 大纲</span>
          <button onClick={addVol} style={{width:26,height:26,border:"1px solid var(--ink-200)",borderRadius:4,background:"transparent",cursor:"pointer",fontSize:14,display:"flex",alignItems:"center",justifyContent:"center"}}>＋</button>
        </div>
        <div style={{flex:1,overflowY:"auto",padding:"8px 6px"}}>
          {vols.map(v=><div key={v.id}>
            <div style={{display:"flex",alignItems:"center",gap:4,padding:"6px 8px",borderRadius:4,fontSize:13,fontWeight:600,color:"var(--ink-700)",marginBottom:2}}>
              <span style={{cursor:"pointer",fontSize:10,width:14}} onClick={()=>setVols(vols.map(x=>x.id===v.id?{...x,collapsed:!x.collapsed}:x))}>{v.collapsed?"▶":"▼"}</span>
              {renaming===v.id?<input value={renameVal} onChange={e=>setRenameVal(e.target.value)} onBlur={commitRename} onKeyDown={e=>e.key==="Enter"&&commitRename()} autoFocus style={{flex:1,border:"1px solid var(--accent)",borderRadius:3,padding:"2px 6px",fontSize:12,outline:"none"}}/>
              :<span style={{flex:1,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} onDoubleClick={()=>startRename(v.id,v.title)}>{v.title}</span>}
              <button onClick={()=>addCh(v.id)} style={{width:20,height:20,border:"none",background:"transparent",color:"var(--ink-400)",fontSize:14,cursor:"pointer"}}>+</button>
            </div>
            {!v.collapsed&&v.chapters.map(c=><div key={c.id} onClick={()=>setActiveId(c.id)} style={{display:"flex",alignItems:"center",gap:6,padding:"5px 8px 5px 28px",borderRadius:4,fontSize:12.5,color:c.id===activeId?"var(--accent)":"var(--ink-600)",background:c.id===activeId?"var(--accent-muted)":"transparent",fontWeight:c.id===activeId?600:400,cursor:"pointer",marginBottom:1}}>
              {renaming===c.id?<input value={renameVal} onChange={e=>setRenameVal(e.target.value)} onBlur={commitRename} onKeyDown={e=>e.key==="Enter"&&commitRename()} autoFocus style={{flex:1,border:"1px solid var(--accent)",borderRadius:3,padding:"2px 6px",fontSize:12,outline:"none"}} onClick={e=>e.stopPropagation()}/>
              :<><span style={{flex:1,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}} onDoubleClick={()=>startRename(c.id,c.title)}>{c.title}</span><span style={{fontSize:10,color:"var(--ink-300)",fontFamily:"var(--font-mono)"}}>{wc(c.content)}字</span></>}
              {totalCh>1&&<button onClick={e=>{e.stopPropagation();delCh(c.id);}} style={{width:18,height:18,border:"none",background:"transparent",color:"var(--ink-400)",fontSize:12,cursor:"pointer"}}>×</button>}
            </div>)}
          </div>)}
        </div>
        <div style={{padding:"10px 14px",borderTop:"1px solid var(--ink-50)",fontSize:11,color:"var(--ink-400)"}}>{totalCh} 章 · {totalW.toLocaleString()} 字</div>
      </div>
      <div style={{width:4,cursor:"col-resize",flexShrink:0}} onMouseDown={e=>startDrag("left",e)}><div style={{width:2,height:"100%",margin:"0 auto",background:"var(--ink-100)"}} onMouseEnter={e=>(e.currentTarget.style.background="var(--accent)")} onMouseLeave={e=>(e.currentTarget.style.background="var(--ink-100)")}/></div>

      {/* ═══ CENTER: Editor ═══ */}
      <div style={{flex:1,display:"flex",flexDirection:"column",overflow:"hidden",background:"var(--paper)",position:"relative"}}>
        <div style={{padding:"16px 28px 12px",borderBottom:"1px solid var(--ink-50)",background:"var(--paper-card)"}}>
          {editingTitle?<input value={titleVal} onChange={e=>setTitleVal(e.target.value)} onBlur={()=>setEditingTitle(false)} onKeyDown={e=>{if(e.key==="Enter")setEditingTitle(false);}} autoFocus style={{fontFamily:"var(--font-serif)",fontSize:18,fontWeight:700,border:"none",borderBottom:"2px solid var(--accent)",outline:"none",background:"transparent",width:"100%",padding:"2px 0"}}/>
          :<h3 onClick={()=>{setEditingTitle(true);setTitleVal(ch?.title||"");}} style={{fontFamily:"var(--font-serif)",fontSize:18,fontWeight:700,marginBottom:2,cursor:"text"}} title="点击编辑章节名">{ch?.title||"选择章节"} <span style={{fontSize:12,color:"var(--ink-300)",fontWeight:400}}>✎</span></h3>}
          <span style={{fontSize:11,color:"var(--ink-400)"}}>{vol?.title} · {words.toLocaleString()} 字 · 写作 {elapsed} 分钟{saved?" · ✓ 已保存":""}</span>
        </div>
        <div style={{flex:1,overflow:"hidden",position:"relative"}}>
          <textarea ref={textRef} value={content} onChange={e=>{setContent(e.target.value);setSelection(null);setRewritePos(null);}} onSelect={handleSelect} placeholder={"在这里开始写作…\n\n提示：\n· 点击上方章节名可编辑\n· 选中文本可触发「重写」\n· Ctrl+S 手动保存"} spellCheck={false}
            style={{width:"100%",height:"100%",border:"none",outline:"none",resize:"none",padding:"28px 48px 48px",fontFamily:"var(--font-serif)",fontSize:16,lineHeight:2,color:"var(--ink-800)",background:"var(--paper)",maxWidth:780,margin:"0 auto",display:"block"}}/>
          {/* Floating rewrite button */}
          {rewritePos&&selection&&<div style={{position:"absolute",top:rewritePos.top,left:"50%",transform:"translateX(-50%)",zIndex:50}}>
            <button style={{padding:"4px 12px",background:"var(--accent)",color:"white",border:"none",borderRadius:16,fontSize:11,fontWeight:600,cursor:"pointer",boxShadow:"var(--shadow-md)",fontFamily:"var(--font-sans)"}} onClick={()=>{setAiTab("rewrite");setRewritePos(null);}}>
              ✏️ 重写选中 ({selection.text.length}字)
            </button>
          </div>}
        </div>
      </div>
      <div style={{width:4,cursor:"col-resize",flexShrink:0}} onMouseDown={e=>startDrag("right",e)}><div style={{width:2,height:"100%",margin:"0 auto",background:"var(--ink-100)"}} onMouseEnter={e=>(e.currentTarget.style.background="var(--accent)")} onMouseLeave={e=>(e.currentTarget.style.background="var(--ink-100)")}/></div>

      {/* ═══ RIGHT: AI Panel ═══ */}
      <div style={{width:rightW,flexShrink:0,background:"var(--paper-card)",borderLeft:"1px solid var(--ink-100)",display:"flex",flexDirection:"column",overflow:"hidden"}}>
        <div style={{padding:"14px 16px 10px",borderBottom:"1px solid var(--ink-50)"}}><span style={{fontFamily:"var(--font-serif)",fontWeight:700,fontSize:14}}>🤖 AI 助手</span></div>
        {/* Stats */}
        <div style={{padding:"10px 16px",borderBottom:"1px solid var(--ink-50)",display:"flex",gap:12}}>
          <div style={{flex:1,textAlign:"center",background:"var(--ink-50)",borderRadius:6,padding:"8px 4px"}}><div style={{fontFamily:"var(--font-mono)",fontSize:18,fontWeight:700,color:"var(--ink-800)"}}>{words.toLocaleString()}</div><div style={{fontSize:10,color:"var(--ink-400)"}}>字数</div></div>
          <div style={{flex:1,textAlign:"center",background:"var(--ink-50)",borderRadius:6,padding:"8px 4px"}}><div style={{fontFamily:"var(--font-mono)",fontSize:18,fontWeight:700,color:"var(--ink-800)"}}>{elapsed}</div><div style={{fontSize:10,color:"var(--ink-400)"}}>写作(分)</div></div>
        </div>
        {/* Tab bar */}
        <div style={{display:"flex",borderBottom:"1px solid var(--ink-50)"}}>
          {([["outline","📋 大纲"],["inspire","✨ AI灵感"],["rewrite","✏️ 重写"]] as const).map(([k,l])=>
            <button key={k} onClick={()=>setAiTab(k)} style={{flex:1,padding:"8px 4px",border:"none",borderBottom:aiTab===k?"2px solid var(--accent)":"2px solid transparent",background:"transparent",color:aiTab===k?"var(--accent)":"var(--ink-400)",fontSize:11.5,fontWeight:aiTab===k?600:400,cursor:"pointer",fontFamily:"var(--font-sans)"}}>{l}</button>
          )}
        </div>
        <div style={{flex:1,overflowY:"auto",padding:"12px 16px"}}>
          {aiTab==="outline"&&<OutlineTab outline={ch?.outline||""} onChange={updateOutline}/>}
          {aiTab==="inspire"&&<InspireTab/>}
          {aiTab==="rewrite"&&<RewriteTab selection={selection}/>}
        </div>
      </div>
    </div>
  );
}

function OutlineTab({outline,onChange}:{outline:string;onChange:(v:string)=>void}) {
  return <div>
    <div style={{fontSize:12,fontWeight:600,color:"var(--ink-500)",marginBottom:8}}>章节剧情大纲</div>
    <textarea value={outline} onChange={e=>onChange(e.target.value)} rows={8} placeholder="在这里写这一章的剧情要点…&#10;&#10;例如：&#10;· 主角初入宗门&#10;· 与师兄发生冲突&#10;· 发现隐藏洞穴" style={{width:"100%",padding:"10px 12px",border:"1px solid var(--ink-200)",borderRadius:6,fontSize:13,fontFamily:"var(--font-sans)",outline:"none",resize:"vertical",lineHeight:1.6}}/>
    <div style={{marginTop:12,fontSize:11,color:"var(--ink-400)"}}>大纲将作为 Scene Planner 的输入，AI 根据大纲拆分场景并生成内容。</div>
  </div>;
}

function InspireTab() {
  const [expanded,setExpanded]=useState<number|null>(null);
  return <div>
    <div style={{fontSize:12,fontWeight:600,color:"var(--ink-500)",marginBottom:12}}>Film Pipeline 预览</div>
    {PIPELINE_DEMO.map((p,i)=><div key={i} style={{marginBottom:10}}>
      <div onClick={()=>setExpanded(expanded===i?null:i)} style={{display:"flex",alignItems:"center",gap:8,padding:"8px 10px",background:p.status==="done"?"var(--jade-light)":p.status==="active"?"var(--gold-light)":"var(--ink-50)",borderRadius:6,cursor:"pointer",transition:"all 0.15s"}}>
        <div style={{width:10,height:10,borderRadius:"50%",background:p.status==="done"?"var(--jade)":p.status==="active"?"var(--gold)":"var(--ink-200)",flexShrink:0,...(p.status==="active"?{animation:"pulse 1.5s infinite"}:{})}}/>
        <div style={{flex:1}}><div style={{fontSize:12,fontWeight:600,color:"var(--ink-700)"}}>{p.step}</div><div style={{fontSize:10,color:"var(--ink-400)"}}>{p.desc}</div></div>
        <span style={{fontSize:12,color:"var(--ink-300)"}}>{expanded===i?"▲":"▼"}</span>
      </div>
      {expanded===i&&<div style={{padding:"10px 12px",background:"var(--paper-warm)",borderRadius:"0 0 6px 6px",fontSize:12,lineHeight:1.7}}>
        {p.actors?<div>
          <div style={{fontWeight:600,color:"var(--ink-600)",marginBottom:6}}>角色对话原始记录：</div>
          {p.actors.map((a,j)=><div key={j} style={{marginBottom:8,padding:"6px 10px",background:"var(--paper-card)",borderRadius:4,borderLeft:"3px solid var(--accent)"}}>
            <div style={{fontWeight:600,color:"var(--ink-700)"}}>🎭 {a.name}</div>
            <div style={{color:"var(--ink-600)",fontFamily:"var(--font-serif)"}}>{a.line}</div>
            <div style={{color:"var(--ink-400)",fontStyle:"italic",fontSize:11}}>内心：{a.inner}</div>
          </div>)}
          <div style={{marginTop:8,padding:8,background:"var(--indigo-light)",borderRadius:4}}>
            <div style={{fontWeight:600,color:"var(--indigo)",fontSize:11}}>💡 剪辑方案（示例）</div>
            <div style={{display:"flex",gap:6,marginTop:4}}>{["方案A: 紧张对峙","方案B: 暗流涌动"].map((s,k)=><button key={k} style={{padding:"4px 10px",border:"1px solid var(--indigo)",borderRadius:12,background:"transparent",color:"var(--indigo)",fontSize:10,cursor:"pointer"}}>{s}</button>)}</div>
          </div>
        </div>
        :<div style={{color:"var(--ink-400)",fontStyle:"italic"}}>此步骤详情将在 AI 模型连接后展示</div>}
      </div>}
    </div>)}
    <div style={{marginTop:16,padding:"10px 12px",background:"var(--paper-warm)",borderRadius:6,fontSize:11,color:"var(--ink-400)",lineHeight:1.5}}>
      每章 ≥2000 字，按 ~600 字/段分段生成。连接模型后此面板将展示实时 Pipeline 进度。
    </div>
  </div>;
}

function RewriteTab({selection}:{selection:{start:number;end:number;text:string}|null}) {
  return <div>
    <div style={{fontSize:12,fontWeight:600,color:"var(--ink-500)",marginBottom:8}}>改写选中文本</div>
    {selection?<>
      <div style={{padding:"10px 12px",background:"var(--ink-50)",borderRadius:6,marginBottom:12,fontSize:12,color:"var(--ink-600)",lineHeight:1.6,maxHeight:120,overflowY:"auto",fontFamily:"var(--font-serif)",borderLeft:"3px solid var(--accent)"}}>
        "{selection.text.length>200?selection.text.slice(0,200)+"…":selection.text}"
      </div>
      <div style={{fontSize:11,color:"var(--ink-400)",marginBottom:8}}>选中了 {selection.text.length} 字（位置 {selection.start}-{selection.end}）</div>
      <button disabled style={{width:"100%",padding:"10px",background:"var(--accent-muted)",border:"1px solid var(--accent)",borderRadius:6,color:"var(--accent)",fontSize:13,fontWeight:600,cursor:"not-allowed",opacity:.6,fontFamily:"var(--font-sans)"}}>🔄 AI 重写此段落</button>
      <div style={{marginTop:8,fontSize:11,color:"var(--ink-400)"}}>连接模型后可用。AI 将保持上下文一致性进行局部重写。</div>
    </>:<div style={{padding:20,textAlign:"center",color:"var(--ink-400)",fontSize:13}}>
      <div style={{fontSize:28,marginBottom:8}}>🖱️</div>
      <p>在编辑器中选中文本，将出现「重写」按钮</p>
      <p style={{fontSize:11,marginTop:4}}>选中后切换到此面板进行 AI 重写</p>
    </div>}
  </div>;
}