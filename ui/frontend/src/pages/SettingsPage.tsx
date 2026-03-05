import React, { useEffect, useState } from "react";
import { apiGet } from "../api/client";
const put=(u:string,b:any)=>fetch(u,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)}).then(r=>r.json());

interface S { theme:string; auto_save:boolean; auto_save_interval:number; cost_confirm:boolean; export_format:string;
  providers:Record<string,{enabled:boolean;api_key?:string;base_url?:string;models:string[];selected_model?:string}>;
  pipeline:Record<string,{provider:string;model:string;compare_models:string[]}>;
  saved_at?:number; }
interface LM { name:string; file:string; size_mb:number; is_dir?:boolean; }

const STEPS=[
  {k:"scene_planner",n:"Scene Planner",d:"将章节大纲拆为场景蓝图"},
  {k:"scene_director",n:"Scene Director",d:"为每个场景生成导演指令"},
  {k:"actor_default",n:"Actor (默认)",d:"配角的角色扮演"},
  {k:"actor_protagonist",n:"Actor (主角)",d:"主角的角色扮演"},
  {k:"editor_stylist",n:"Editor-Stylist",d:"剪辑+文学风格化"},
  {k:"editor_agent",n:"Editor Agent",d:"市场优化建议"},
  {k:"evaluator",n:"Evaluator",d:"约束/一致性/重复检测"},
];

const PROVS=[
  {k:"openai",n:"OpenAI",hasKey:true,defModels:["gpt-4o","gpt-4o-mini","o4-mini"]},
  {k:"anthropic",n:"Anthropic",hasKey:true,defModels:["claude-sonnet-4-5-20250929","claude-haiku-4-5-20251001"]},
  {k:"deepseek",n:"DeepSeek",hasKey:true,defModels:["deepseek-chat","deepseek-reasoner"]},
  {k:"ollama",n:"Ollama",hasKey:false,hasUrl:true,defModels:["qwen2.5:7b","qwen2.5:14b","qwen2.5:32b","llama3:8b"]},
  {k:"vllm",n:"vLLM",hasKey:false,hasUrl:true,defModels:[]},
  {k:"local",n:"本地模型",hasKey:false,defModels:[]},
];

export default function SettingsPage() {
  const [s,setS]=useState<S|null>(null); const [loading,setLoading]=useState(true);
  const [dirty,setDirty]=useState(false); const [saving,setSaving]=useState(false);
  const [localModels,setLocalModels]=useState<LM[]>([]);
  const [tab,setTab]=useState<"pipeline"|"providers"|"system">("pipeline");

  useEffect(()=>{
    apiGet<S>("/api/data/settings").then(r=>{setS(r);setLoading(false);}).catch(()=>setLoading(false));
    apiGet<{models:LM[]}>("/api/data/local_models").then(r=>setLocalModels(r.models||[])).catch(()=>{});
  },[]);

  const u=(k:string,v:any)=>{if(!s)return;setS({...s,[k]:v});setDirty(true);};
  const up=(pk:string,k:string,v:any)=>{if(!s)return;setS({...s,providers:{...s.providers,[pk]:{...s.providers[pk],[k]:v}}});setDirty(true);};
  const upi=(step:string,k:string,v:any)=>{if(!s)return;setS({...s,pipeline:{...s.pipeline,[step]:{...s.pipeline[step],[k]:v}}});setDirty(true);};
  const save=async()=>{if(!s)return;setSaving(true);await put("/api/data/settings",s);setDirty(false);setSaving(false);};

  // All available models across all enabled providers
  const allModels=React.useMemo(()=>{
    if(!s)return[];
    const m:Array<{provider:string;model:string;label:string}>=[];
    for(const[pk,pv]of Object.entries(s.providers)){
      if(!pv.enabled)continue;
      const pn=PROVS.find(p=>p.k===pk)?.n||pk;
      for(const model of pv.models||[]) m.push({provider:pk,model,label:`${pn} / ${model}`});
    }
    // Local models
    for(const lm of localModels) m.push({provider:"local",model:lm.name,label:`本地 / ${lm.name} (${lm.size_mb}MB)`});
    return m;
  },[s,localModels]);

  if(loading||!s)return<div className="loading" style={{paddingTop:120}}><div className="loading-spinner"/>加载中…</div>;

  return (
    <div className="page-container" style={{maxWidth:1000}}>
      <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:20}}>
        <div className="page-header" style={{marginBottom:0}}><h2>设置</h2><p>Pipeline 模型配置、提供商管理、系统参数</p></div>
        <button className="btn-primary" onClick={save} disabled={!dirty||saving} style={{opacity:dirty?1:.5}}>{saving?"保存中…":dirty?"保存":"已保存"}</button>
      </div>

      <div className="platform-tabs" style={{marginBottom:24}}>
        <button className={`platform-tab${tab==="pipeline"?" active":""}`} onClick={()=>setTab("pipeline")}>🔧 Pipeline 模型</button>
        <button className={`platform-tab${tab==="providers"?" active":""}`} onClick={()=>setTab("providers")}>🤖 模型提供商</button>
        <button className={`platform-tab${tab==="system"?" active":""}`} onClick={()=>setTab("system")}>⚙️ 系统</button>
      </div>

      {/* ═══ Pipeline Tab ═══ */}
      {tab==="pipeline"&&<div>
        <div style={{fontSize:13,color:"var(--ink-500)",marginBottom:16}}>为 Film Pipeline 每个步骤选择模型。多选时将同时生成以供对比。</div>
        {STEPS.map(step=>{const cfg=s.pipeline?.[step.k]||{provider:"",model:"",compare_models:[]};return(
          <div key={step.k} className="card" style={{marginBottom:12}}>
            <div className="card-body" style={{padding:"14px 20px"}}>
              <div style={{display:"flex",alignItems:"center",gap:16,flexWrap:"wrap"}}>
                <div style={{width:160}}><div style={{fontSize:14,fontWeight:600}}>{step.n}</div><div style={{fontSize:11,color:"var(--ink-400)"}}>{step.d}</div></div>
                <div style={{flex:1,minWidth:200}}>
                  <label style={{fontSize:10,color:"var(--ink-400)"}}>主模型</label>
                  <select value={`${cfg.provider}|${cfg.model}`} onChange={e=>{const[p,m]=e.target.value.split("|");upi(step.k,"provider",p);upi(step.k,"model",m);}}
                    style={{width:"100%",padding:"6px 10px",border:"1px solid var(--ink-200)",borderRadius:6,fontSize:12,outline:"none"}}>
                    <option value="|">未选择</option>
                    {allModels.map((m,i)=><option key={i} value={`${m.provider}|${m.model}`}>{m.label}</option>)}
                  </select>
                </div>
                <div style={{flex:1,minWidth:200}}>
                  <label style={{fontSize:10,color:"var(--ink-400)"}}>对比模型（多选）</label>
                  <div style={{display:"flex",gap:4,flexWrap:"wrap",marginTop:4}}>
                    {allModels.filter(m=>!(m.provider===cfg.provider&&m.model===cfg.model)).map((m,i)=>{
                      const sel=(cfg.compare_models||[]).includes(`${m.provider}|${m.model}`);
                      return <button key={i} onClick={()=>{const k=`${m.provider}|${m.model}`;const cm=cfg.compare_models||[];upi(step.k,"compare_models",sel?cm.filter(x=>x!==k):[...cm,k]);}}
                        style={{padding:"3px 8px",borderRadius:12,border:sel?"1px solid var(--accent)":"1px solid var(--ink-200)",background:sel?"var(--accent-muted)":"transparent",color:sel?"var(--accent)":"var(--ink-500)",fontSize:10,cursor:"pointer"}}>{m.label.split(" / ")[1]}</button>;
                    })}
                  </div>
                </div>
              </div>
            </div>
          </div>
        );})}
      </div>}

      {/* ═══ Providers Tab ═══ */}
      {tab==="providers"&&<div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:20}}>
        {PROVS.map(p=>{const prov=s.providers?.[p.k]||{enabled:false,models:p.defModels};return(
          <div key={p.k} className="card"><div className="card-header" style={{display:"flex",alignItems:"center",justifyContent:"space-between"}}>
            <h3>{p.n}</h3>
            <Tog checked={!!prov.enabled} onChange={v=>up(p.k,"enabled",v)}/>
          </div>
          {prov.enabled&&<div className="card-body">
            {p.hasKey&&<div style={{marginBottom:10}}><label style={{fontSize:11,color:"var(--ink-400)"}}>API Key</label>
              <input type="password" value={prov.api_key||""} onChange={e=>up(p.k,"api_key",e.target.value)} placeholder="sk-..." style={{width:"100%",padding:"6px 10px",border:"1px solid var(--ink-200)",borderRadius:4,fontSize:12,fontFamily:"var(--font-mono)",outline:"none"}}/></div>}
            {p.hasUrl&&<div style={{marginBottom:10}}><label style={{fontSize:11,color:"var(--ink-400)"}}>Base URL</label>
              <input value={prov.base_url||""} onChange={e=>up(p.k,"base_url",e.target.value)} style={{width:"100%",padding:"6px 10px",border:"1px solid var(--ink-200)",borderRadius:4,fontSize:12,outline:"none"}}/></div>}
            <div><label style={{fontSize:11,color:"var(--ink-400)"}}>可用模型（逗号分隔）</label>
              <input value={(prov.models||[]).join(", ")} onChange={e=>up(p.k,"models",e.target.value.split(",").map(s=>s.trim()).filter(Boolean))} style={{width:"100%",padding:"6px 10px",border:"1px solid var(--ink-200)",borderRadius:4,fontSize:12,outline:"none"}}/>
              {p.defModels.length>0&&<div style={{marginTop:4,fontSize:10,color:"var(--ink-300)"}}>默认: {p.defModels.join(", ")}</div>}
            </div>
          </div>}
          </div>
        );})}

        {/* Local Models */}
        <div className="card" style={{gridColumn:"1/-1"}}>
          <div className="card-header"><h3>💾 本地模型 (/models/)</h3><p>放置在项目根目录 /models/ 下的模型文件</p></div>
          <div className="card-body">
            {localModels.length===0?<div style={{color:"var(--ink-400)",fontSize:13}}>未检测到本地模型。将 .gguf / .safetensors / .pt 文件放入项目根目录的 /models/ 文件夹即可识别。</div>:
            <div style={{display:"grid",gridTemplateColumns:"repeat(auto-fill,minmax(200px,1fr))",gap:10}}>
              {localModels.map(m=><div key={m.name} style={{padding:"10px 14px",background:"var(--ink-50)",borderRadius:6}}>
                <div style={{fontSize:13,fontWeight:600}}>{m.name}</div>
                <div style={{fontSize:11,color:"var(--ink-400)"}}>{m.file} · {m.size_mb} MB</div>
              </div>)}
            </div>}
          </div>
        </div>
      </div>}

      {/* ═══ System Tab ═══ */}
      {tab==="system"&&<div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:20,maxWidth:800}}>
        <div className="card"><div className="card-header"><h3>🔧 系统设置</h3></div><div className="card-body">
          <Tog label="自动保存" desc="定期保存草稿" checked={s.auto_save} onChange={v=>u("auto_save",v)}/>
          {s.auto_save&&<div style={{marginBottom:12,paddingLeft:12}}><label style={{fontSize:11,color:"var(--ink-400)"}}>间隔（秒）</label>
            <input type="number" value={s.auto_save_interval} onChange={e=>u("auto_save_interval",+e.target.value)} min={5} max={300} style={{width:80,marginLeft:8,padding:"4px 8px",border:"1px solid var(--ink-200)",borderRadius:4,fontSize:12,outline:"none"}}/></div>}
          <Tog label="成本确认" desc="API 调用前显示预估" checked={s.cost_confirm} onChange={v=>u("cost_confirm",v)}/>
          <div style={{marginBottom:12}}><label style={{fontSize:12,fontWeight:600,color:"var(--ink-500)",display:"block",marginBottom:4}}>导出格式</label>
            <div style={{display:"flex",gap:6}}>{["txt","docx","epub"].map(f=><button key={f} onClick={()=>u("export_format",f)} style={{padding:"5px 16px",borderRadius:16,border:s.export_format===f?"2px solid var(--accent)":"1px solid var(--ink-200)",background:s.export_format===f?"var(--accent-muted)":"transparent",color:s.export_format===f?"var(--accent)":"var(--ink-600)",fontSize:12,cursor:"pointer",fontFamily:"var(--font-mono)",textTransform:"uppercase"}}>{f}</button>)}</div></div>
        </div></div>
        <div className="card"><div className="card-header"><h3>💾 数据存储</h3></div><div className="card-body">
          <div style={{fontSize:13,color:"var(--ink-600)",lineHeight:1.8}}>数据存储在 <code style={{background:"var(--ink-50)",padding:"2px 6px",borderRadius:3,fontFamily:"var(--font-mono)",fontSize:12}}>/data/</code></div>
          <div style={{marginTop:12,fontSize:12,color:"var(--ink-400)",lineHeight:2}}>
            📁 data/projects/ — 项目列表<br/>📁 data/characters/ — 角色卡<br/>📁 data/worldbook/ — 世界书<br/>
            📁 data/editor/ — 编辑器正文<br/>📁 data/storylines/ — 剧情线<br/>📁 models/ — 本地模型<br/>📄 data/settings.json — 设置
          </div>
          <div style={{marginTop:12,padding:"10px 12px",background:"var(--jade-light)",borderRadius:6,fontSize:11,color:"var(--jade)"}}>✓ 所有数据仅保存在本地</div>
        </div></div>
      </div>}
    </div>
  );
}

function Tog({label,desc,checked,onChange}:{label?:string;desc?:string;checked:boolean;onChange:(v:boolean)=>void}) {
  return <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",padding:label?"8px 0":"0",borderBottom:label?"1px solid var(--ink-50)":"none",marginBottom:label?8:0}}>
    {label&&<div><div style={{fontSize:13,fontWeight:500}}>{label}</div>{desc&&<div style={{fontSize:11,color:"var(--ink-400)"}}>{desc}</div>}</div>}
    <label style={{position:"relative",width:40,height:22,cursor:"pointer"}}><input type="checkbox" checked={checked} onChange={e=>onChange(e.target.checked)} style={{opacity:0,position:"absolute"}}/>
      <div style={{width:40,height:22,borderRadius:11,background:checked?"var(--jade)":"var(--ink-200)",transition:"background 0.2s",position:"relative"}}><div style={{width:18,height:18,borderRadius:9,background:"white",position:"absolute",top:2,left:checked?20:2,transition:"left 0.2s",boxShadow:"0 1px 2px rgba(0,0,0,.15)"}}/></div></label>
  </div>;
}