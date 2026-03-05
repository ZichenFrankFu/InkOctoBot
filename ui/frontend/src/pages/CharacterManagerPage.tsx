import React, { useEffect, useState, useCallback } from "react";
import { apiGet } from "../api/client";
const put=(u:string,b:any)=>fetch(u,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)}).then(r=>r.json());
const post=(u:string,b:any)=>fetch(u,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)}).then(r=>r.json());
const del=(u:string)=>fetch(u,{method:"DELETE"}).then(r=>r.json());

interface Char { id:string; name:string; role:string; personality:string; background:string; speech_style:string; tags:string[]; quant_params:Record<string,number>; created_at?:number; updated_at?:number; }

export default function CharacterManagerPage() {
  const [items,setItems]=useState<Char[]>([]); const [loading,setLoading]=useState(true);
  const [editing,setEditing]=useState<Char|null>(null); const [dirty,setDirty]=useState(false);

  const load=useCallback(()=>{setLoading(true);apiGet<{items:Char[]}>("/api/data/characters").then(r=>setItems(r.items||[])).catch(console.error).finally(()=>setLoading(false));},[]);
  useEffect(()=>{load();},[]);

  const create=async()=>{const c=await post("/api/data/characters",{name:"新角色",role:"配角"});setItems([...items,c]);setEditing(c);setDirty(false);};
  const save=async()=>{if(!editing)return;await put(`/api/data/characters/${editing.id}`,editing);setDirty(false);load();};
  const remove=async(id:string)=>{if(!confirm("确定删除此角色？"))return;await del(`/api/data/characters/${id}`);if(editing?.id===id)setEditing(null);load();};
  const update=(k:string,v:any)=>{if(!editing)return;setEditing({...editing,[k]:v});setDirty(true);};
  const updateQ=(k:string,v:number)=>{if(!editing)return;setEditing({...editing,quant_params:{...editing.quant_params,[k]:v}});setDirty(true);};

  const roles=["主角","重要配角","配角","反派","导师","路人"];

  return (
    <div style={{display:"flex",height:"100vh",overflow:"hidden"}}>
      {/* List */}
      <div style={{width:300,flexShrink:0,background:"var(--paper-card)",borderRight:"1px solid var(--ink-100)",display:"flex",flexDirection:"column"}}>
        <div style={{padding:"16px 16px 12px",borderBottom:"1px solid var(--ink-50)",display:"flex",alignItems:"center",justifyContent:"space-between"}}>
          <h3 style={{fontFamily:"var(--font-serif)",fontSize:16,fontWeight:700}}>👤 角色管理</h3>
          <button className="btn-primary" style={{padding:"6px 14px",fontSize:12}} onClick={create}>+ 新角色</button>
        </div>
        <div style={{flex:1,overflowY:"auto"}}>
          {loading?<div className="loading"><div className="loading-spinner"/></div>:items.length===0?
            <div className="empty-state" style={{padding:32}}><p>还没有角色，点击上方按钮创建</p></div>:
            items.map(c=><div key={c.id} onClick={()=>{setEditing(c);setDirty(false);}} style={{padding:"12px 16px",borderBottom:"1px solid var(--ink-50)",cursor:"pointer",background:editing?.id===c.id?"var(--accent-muted)":"transparent",transition:"background 0.15s",display:"flex",alignItems:"center",gap:12}}>
              <div style={{width:40,height:40,borderRadius:"50%",background:c.role==="主角"?"var(--accent-muted)":c.role==="反派"?"var(--ink-100)":"var(--jade-light)",display:"flex",alignItems:"center",justifyContent:"center",fontSize:18,flexShrink:0}}>
                {c.role==="主角"?"⭐":c.role==="反派"?"👿":"👤"}
              </div>
              <div style={{flex:1,overflow:"hidden"}}><div style={{fontSize:14,fontWeight:600,color:"var(--ink-800)"}}>{c.name}</div><div style={{fontSize:11,color:"var(--ink-400)"}}>{c.role}</div></div>
              <button onClick={e=>{e.stopPropagation();remove(c.id);}} style={{width:24,height:24,border:"none",background:"transparent",color:"var(--ink-300)",cursor:"pointer",fontSize:14}}>×</button>
            </div>)
          }
        </div>
      </div>

      {/* Edit form */}
      <div style={{flex:1,overflowY:"auto",background:"var(--paper)"}}>
        {!editing?<div className="empty-state" style={{paddingTop:120}}><div className="empty-icon">👈</div><h4>选择或创建一个角色</h4></div>:
        <div style={{maxWidth:700,margin:"0 auto",padding:"28px 32px 48px"}}>
          <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:24}}>
            <h2 style={{fontFamily:"var(--font-serif)",fontSize:22,fontWeight:700}}>{editing.name}</h2>
            <button className="btn-primary" onClick={save} disabled={!dirty} style={{opacity:dirty?1:.5}}>{dirty?"保存":"已保存"}</button>
          </div>

          <div className="card" style={{marginBottom:20}}><div className="card-header"><h3>基本信息</h3></div><div className="card-body">
            <Field label="姓名" value={editing.name} onChange={v=>update("name",v)}/>
            <div style={{marginBottom:12}}><label style={{fontSize:12,fontWeight:600,color:"var(--ink-500)",display:"block",marginBottom:4}}>角色定位</label>
              <div style={{display:"flex",gap:6,flexWrap:"wrap"}}>{roles.map(r=><button key={r} onClick={()=>update("role",r)} style={{padding:"5px 14px",borderRadius:20,border:editing.role===r?"2px solid var(--accent)":"1px solid var(--ink-200)",background:editing.role===r?"var(--accent-muted)":"transparent",color:editing.role===r?"var(--accent)":"var(--ink-600)",fontSize:12,cursor:"pointer",fontFamily:"var(--font-sans)"}}>{r}</button>)}</div>
            </div>
            <Field label="标签（逗号分隔）" value={(editing.tags||[]).join(", ")} onChange={v=>update("tags",v.split(",").map(s=>s.trim()).filter(Boolean))}/>
          </div></div>

          <div className="card" style={{marginBottom:20}}><div className="card-header"><h3>Layer A: 自然语言描述</h3><p>用于 LLM prompt 的角色描述</p></div><div className="card-body">
            <Area label="性格描述" value={editing.personality} onChange={v=>update("personality",v)} rows={3}/>
            <Area label="背景故事" value={editing.background} onChange={v=>update("background",v)} rows={4}/>
            <Area label="说话风格" value={editing.speech_style} onChange={v=>update("speech_style",v)} rows={2}/>
          </div></div>

          <div className="card"><div className="card-header"><h3>Layer B: 量化决策参数</h3><p>用于 Python 决策引擎</p></div><div className="card-body">
            {[{k:"loss_aversion",l:"损失厌恶",min:1,max:5,step:.1},{k:"risk_aversion",l:"风险厌恶",min:0,max:1,step:.05},{k:"impulsive_p",l:"冲动概率",min:0,max:1,step:.05},{k:"loyalty",l:"忠诚度",min:0,max:1,step:.05},{k:"trust_alpha",l:"信任 α",min:1,max:20,step:1},{k:"trust_beta",l:"信任 β",min:1,max:20,step:1}].map(p=>
              <div key={p.k} style={{display:"flex",alignItems:"center",gap:12,marginBottom:10}}>
                <span style={{width:80,fontSize:12,color:"var(--ink-500)",textAlign:"right"}}>{p.l}</span>
                <input type="range" min={p.min} max={p.max} step={p.step} value={editing.quant_params?.[p.k]??((p.min+p.max)/2)} onChange={e=>updateQ(p.k,+e.target.value)} style={{flex:1}}/>
                <span style={{width:48,fontFamily:"var(--font-mono)",fontSize:12,color:"var(--ink-700)",textAlign:"right"}}>{(editing.quant_params?.[p.k]??((p.min+p.max)/2)).toFixed(p.step<1?2:0)}</span>
              </div>
            )}
          </div></div>
        </div>}
      </div>
    </div>
  );
}

function Field({label,value,onChange}:{label:string;value:string;onChange:(v:string)=>void}) {
  return <div style={{marginBottom:12}}><label style={{fontSize:12,fontWeight:600,color:"var(--ink-500)",display:"block",marginBottom:4}}>{label}</label>
    <input value={value} onChange={e=>onChange(e.target.value)} style={{width:"100%",padding:"8px 12px",border:"1px solid var(--ink-200)",borderRadius:6,fontSize:13,fontFamily:"var(--font-sans)",outline:"none"}}/></div>;
}
function Area({label,value,onChange,rows=3}:{label:string;value:string;onChange:(v:string)=>void;rows?:number}) {
  return <div style={{marginBottom:12}}><label style={{fontSize:12,fontWeight:600,color:"var(--ink-500)",display:"block",marginBottom:4}}>{label}</label>
    <textarea value={value} onChange={e=>onChange(e.target.value)} rows={rows} style={{width:"100%",padding:"8px 12px",border:"1px solid var(--ink-200)",borderRadius:6,fontSize:13,fontFamily:"var(--font-sans)",outline:"none",resize:"vertical",lineHeight:1.6}}/></div>;
}
