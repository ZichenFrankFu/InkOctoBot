import React, { useEffect, useState, useCallback, useMemo } from "react";
import { apiGet } from "../api/client";
const put=(u:string,b:any)=>fetch(u,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)}).then(r=>r.json());
const post=(u:string,b:any)=>fetch(u,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)}).then(r=>r.json());
const del=(u:string)=>fetch(u,{method:"DELETE"}).then(r=>r.json());

interface Char { id:string; name:string; role:string; project_id:string; personality:string; background:string; speech_style:string; tags:string[]; quant_params:Record<string,number>; relationships:Record<string,{trust_alpha:number;trust_beta:number;loyalty:number;note:string}>; [k:string]:any; }
interface Project { id:string; name:string; }

export default function CharacterManagerPage({projectId,projects}:{projectId:string;projects:Project[]}) {
  const [items,setItems]=useState<Char[]>([]); const [loading,setLoading]=useState(true);
  const [editing,setEditing]=useState<Char|null>(null); const [dirty,setDirty]=useState(false);
  const [relTarget,setRelTarget]=useState("");

  const load=useCallback(()=>{setLoading(true);
    apiGet<{items:Char[]}>(`/api/data/characters${projectId?`?project_id=${projectId}`:""}`).then(r=>setItems(r.items||[])).catch(console.error).finally(()=>setLoading(false));
  },[projectId]);
  useEffect(()=>{load();},[projectId]);

  const create=async()=>{const c=await post("/api/data/characters",{name:"新角色",role:"配角",project_id:projectId});setItems([...items,c]);setEditing(c);setDirty(false);};
  const save=async()=>{if(!editing)return;await put(`/api/data/characters/${editing.id}`,editing);setDirty(false);load();};
  const remove=async(id:string)=>{if(!confirm("确定删除？"))return;await del(`/api/data/characters/${id}`);if(editing?.id===id)setEditing(null);load();};
  const u=(k:string,v:any)=>{if(!editing)return;setEditing({...editing,[k]:v});setDirty(true);};
  const uq=(k:string,v:number)=>{if(!editing)return;setEditing({...editing,quant_params:{...editing.quant_params,[k]:v}});setDirty(true);};

  // Other characters in same project for relationships
  const others=useMemo(()=>items.filter(c=>editing&&c.id!==editing.id),[items,editing]);
  const addRel=(targetId:string)=>{if(!editing||!targetId)return;const t=items.find(c=>c.id===targetId);if(!t)return;
    setEditing({...editing,relationships:{...editing.relationships,[targetId]:{trust_alpha:5,trust_beta:2,loyalty:0.7,note:""}}});setDirty(true);setRelTarget("");};
  const urel=(tid:string,k:string,v:any)=>{if(!editing)return;setEditing({...editing,relationships:{...editing.relationships,[tid]:{...editing.relationships[tid],[k]:v}}});setDirty(true);};
  const delRel=(tid:string)=>{if(!editing)return;const r={...editing.relationships};delete r[tid];setEditing({...editing,relationships:r});setDirty(true);};

  const roles=["主角","重要配角","配角","反派","导师","路人"];
  const projName=projects.find(p=>p.id===projectId)?.name||"未选择项目";

  return (
    <div style={{display:"flex",height:"100vh",overflow:"hidden"}}>
      <div style={{width:300,flexShrink:0,background:"var(--paper-card)",borderRight:"1px solid var(--ink-100)",display:"flex",flexDirection:"column"}}>
        <div style={{padding:"16px 16px 12px",borderBottom:"1px solid var(--ink-50)"}}>
          <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:6}}>
            <h3 style={{fontFamily:"var(--font-serif)",fontSize:16,fontWeight:700}}>👤 角色管理</h3>
            <button className="btn-primary" style={{padding:"6px 14px",fontSize:12}} onClick={create}>+ 新角色</button>
          </div>
          <div style={{fontSize:11,color:"var(--ink-400)"}}>📖 {projName}</div>
        </div>
        <div style={{flex:1,overflowY:"auto"}}>
          {loading?<div className="loading"><div className="loading-spinner"/></div>:items.length===0?
            <div className="empty-state" style={{padding:32}}><p>该项目暂无角色</p></div>:
            items.map(c=><div key={c.id} onClick={()=>{setEditing(c);setDirty(false);}} style={{padding:"12px 16px",borderBottom:"1px solid var(--ink-50)",cursor:"pointer",background:editing?.id===c.id?"var(--accent-muted)":"transparent",display:"flex",alignItems:"center",gap:12}}>
              <div style={{width:40,height:40,borderRadius:"50%",background:c.role==="主角"?"var(--accent-muted)":c.role==="反派"?"var(--ink-100)":"var(--jade-light)",display:"flex",alignItems:"center",justifyContent:"center",fontSize:18,flexShrink:0}}>{c.role==="主角"?"⭐":c.role==="反派"?"👿":"👤"}</div>
              <div style={{flex:1}}><div style={{fontSize:14,fontWeight:600}}>{c.name}</div><div style={{fontSize:11,color:"var(--ink-400)"}}>{c.role}{Object.keys(c.relationships||{}).length>0?` · ${Object.keys(c.relationships).length}段关系`:""}</div></div>
              <button onClick={e=>{e.stopPropagation();remove(c.id);}} style={{border:"none",background:"transparent",color:"var(--ink-300)",cursor:"pointer"}}>×</button>
            </div>)
          }
        </div>
      </div>
      <div style={{flex:1,overflowY:"auto",background:"var(--paper)"}}>
        {!editing?<div className="empty-state" style={{paddingTop:120}}><h4>选择或创建一个角色</h4></div>:
        <div style={{maxWidth:700,margin:"0 auto",padding:"28px 32px 48px"}}>
          <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:24}}>
            <h2 style={{fontFamily:"var(--font-serif)",fontSize:22,fontWeight:700}}>{editing.name}</h2>
            <button className="btn-primary" onClick={save} disabled={!dirty} style={{opacity:dirty?1:.5}}>{dirty?"保存":"已保存"}</button>
          </div>
          {/* Basic Info */}
          <div className="card" style={{marginBottom:20}}><div className="card-header"><h3>基本信息</h3></div><div className="card-body">
            <F label="姓名" value={editing.name} onChange={v=>u("name",v)}/>
            <div style={{marginBottom:12}}><label style={{fontSize:12,fontWeight:600,color:"var(--ink-500)",display:"block",marginBottom:4}}>角色定位</label>
              <div style={{display:"flex",gap:6,flexWrap:"wrap"}}>{roles.map(r=><button key={r} onClick={()=>u("role",r)} style={{padding:"5px 14px",borderRadius:20,border:editing.role===r?"2px solid var(--accent)":"1px solid var(--ink-200)",background:editing.role===r?"var(--accent-muted)":"transparent",color:editing.role===r?"var(--accent)":"var(--ink-600)",fontSize:12,cursor:"pointer"}}>{r}</button>)}</div></div>
            <F label="标签" value={(editing.tags||[]).join(", ")} onChange={v=>u("tags",v.split(",").map(s=>s.trim()).filter(Boolean))}/>
          </div></div>
          {/* Layer A */}
          <div className="card" style={{marginBottom:20}}><div className="card-header"><h3>Layer A: 自然语言描述</h3></div><div className="card-body">
            <A label="性格描述" value={editing.personality} onChange={v=>u("personality",v)} rows={3}/>
            <A label="背景故事" value={editing.background} onChange={v=>u("background",v)} rows={4}/>
            <A label="说话风格" value={editing.speech_style} onChange={v=>u("speech_style",v)} rows={2}/>
          </div></div>
          {/* Layer B */}
          <div className="card" style={{marginBottom:20}}><div className="card-header"><h3>Layer B: 量化决策参数</h3></div><div className="card-body">
            {[{k:"loss_aversion",l:"损失厌恶",min:1,max:5,step:.1},{k:"risk_aversion",l:"风险厌恶",min:0,max:1,step:.05},{k:"impulsive_p",l:"冲动概率",min:0,max:1,step:.05}].map(p=>
              <Slider key={p.k} {...p} value={editing.quant_params?.[p.k]} onChange={v=>uq(p.k,v)}/>)}
          </div></div>
          {/* Relationships */}
          <div className="card"><div className="card-header"><h3>🤝 角色关系</h3><p>对不同角色的信任度和忠诚度</p></div><div className="card-body">
            {Object.entries(editing.relationships||{}).map(([tid,rel])=>{const t=items.find(c=>c.id===tid);return(
              <div key={tid} style={{padding:"12px",background:"var(--ink-50)",borderRadius:8,marginBottom:10}}>
                <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:8}}>
                  <span style={{fontWeight:600,fontSize:13}}>→ {t?.name||tid}</span>
                  <button onClick={()=>delRel(tid)} style={{border:"none",background:"transparent",color:"var(--ink-400)",cursor:"pointer",fontSize:12}}>移除</button>
                </div>
                <Slider k="trust_alpha" l={`信任 α（当前: ${((rel.trust_alpha)/(rel.trust_alpha+rel.trust_beta)*100).toFixed(0)}%）`} min={1} max={20} step={1} value={rel.trust_alpha} onChange={v=>urel(tid,"trust_alpha",v)}/>
                <Slider k="trust_beta" l="信任 β" min={1} max={20} step={1} value={rel.trust_beta} onChange={v=>urel(tid,"trust_beta",v)}/>
                <Slider k="loyalty" l="忠诚度" min={0} max={1} step={.05} value={rel.loyalty} onChange={v=>urel(tid,"loyalty",v)}/>
                <F label="关系备注" value={rel.note||""} onChange={v=>urel(tid,"note",v)}/>
              </div>
            );})}
            {others.length>0&&<div style={{display:"flex",gap:8,marginTop:8}}>
              <select value={relTarget} onChange={e=>setRelTarget(e.target.value)} style={{flex:1,padding:"6px 10px",border:"1px solid var(--ink-200)",borderRadius:6,fontSize:12,outline:"none"}}>
                <option value="">选择角色…</option>
                {others.filter(o=>!editing.relationships?.[o.id]).map(o=><option key={o.id} value={o.id}>{o.name}</option>)}
              </select>
              <button onClick={()=>addRel(relTarget)} disabled={!relTarget} style={{padding:"6px 14px",background:"var(--jade)",color:"white",border:"none",borderRadius:6,fontSize:12,cursor:"pointer",opacity:relTarget?1:.5}}>+ 添加关系</button>
            </div>}
          </div></div>
        </div>}
      </div>
    </div>
  );
}
function F({label,value,onChange}:{label:string;value:string;onChange:(v:string)=>void}) {
  return <div style={{marginBottom:12}}><label style={{fontSize:12,fontWeight:600,color:"var(--ink-500)",display:"block",marginBottom:4}}>{label}</label><input value={value} onChange={e=>onChange(e.target.value)} style={{width:"100%",padding:"8px 12px",border:"1px solid var(--ink-200)",borderRadius:6,fontSize:13,outline:"none"}}/></div>;
}
function A({label,value,onChange,rows=3}:{label:string;value:string;onChange:(v:string)=>void;rows?:number}) {
  return <div style={{marginBottom:12}}><label style={{fontSize:12,fontWeight:600,color:"var(--ink-500)",display:"block",marginBottom:4}}>{label}</label><textarea value={value} onChange={e=>onChange(e.target.value)} rows={rows} style={{width:"100%",padding:"8px 12px",border:"1px solid var(--ink-200)",borderRadius:6,fontSize:13,outline:"none",resize:"vertical",lineHeight:1.6}}/></div>;
}
function Slider({k,l,min,max,step,value,onChange}:{k:string;l:string;min:number;max:number;step:number;value?:number;onChange:(v:number)=>void}) {
  const v=value??((min+max)/2);
  return <div style={{display:"flex",alignItems:"center",gap:12,marginBottom:8}}><span style={{width:90,fontSize:12,color:"var(--ink-500)",textAlign:"right"}}>{l}</span><input type="range" min={min} max={max} step={step} value={v} onChange={e=>onChange(+e.target.value)} style={{flex:1}}/><span style={{width:48,fontFamily:"var(--font-mono)",fontSize:12,textAlign:"right"}}>{v.toFixed(step<1?2:0)}</span></div>;
}