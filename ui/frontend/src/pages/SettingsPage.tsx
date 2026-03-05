import React, { useEffect, useState } from "react";
import { apiGet } from "../api/client";
const put=(u:string,b:any)=>fetch(u,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)}).then(r=>r.json());

interface Settings {
  theme:string; auto_save:boolean; auto_save_interval:number; cost_confirm:boolean; export_format:string;
  providers:Record<string,{enabled:boolean;api_key_set?:boolean;base_url?:string;api_key?:string}>;
  model_preset:string; saved_at?:number;
}

export default function SettingsPage() {
  const [s,setS]=useState<Settings|null>(null); const [loading,setLoading]=useState(true); const [saving,setSaving]=useState(false); const [dirty,setDirty]=useState(false);

  useEffect(()=>{apiGet<Settings>("/api/data/settings").then(r=>{setS(r);setLoading(false);}).catch(e=>{console.error(e);setLoading(false);});},[]);
  const update=(k:string,v:any)=>{if(!s)return;setS({...s,[k]:v});setDirty(true);};
  const updateP=(provider:string,k:string,v:any)=>{if(!s)return;setS({...s,providers:{...s.providers,[provider]:{...s.providers[provider],[k]:v}}});setDirty(true);};
  const save=async()=>{if(!s)return;setSaving(true);await put("/api/data/settings",s);setDirty(false);setSaving(false);};

  if(loading)return<div className="loading" style={{paddingTop:120}}><div className="loading-spinner"/>加载中…</div>;
  if(!s)return<div className="empty-state" style={{paddingTop:120}}><h4>设置加载失败</h4></div>;

  const providers=[
    {key:"openai",name:"OpenAI",desc:"GPT-4o / GPT-4o-mini",hasKey:true},
    {key:"anthropic",name:"Anthropic",desc:"Claude Sonnet / Haiku",hasKey:true},
    {key:"deepseek",name:"DeepSeek",desc:"DeepSeek-V3",hasKey:true},
    {key:"ollama",name:"Ollama 本地",desc:"Qwen / Llama / etc.",hasKey:false,hasUrl:true},
    {key:"vllm",name:"vLLM 本地",desc:"自定义模型服务",hasKey:false,hasUrl:true},
  ];
  const presets=[
    {key:"cost_optimal",name:"成本优先",desc:"本地模型为主，仅关键步骤用云端"},
    {key:"balanced",name:"均衡",desc:"本地+云端混合"},
    {key:"quality_first",name:"质量优先",desc:"全部使用最强云端模型"},
  ];

  return (
    <div className="page-container">
      <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:24}}>
        <div className="page-header" style={{marginBottom:0}}><h2>设置</h2><p>模型配置、API Key、系统参数</p></div>
        <button className="btn-primary" onClick={save} disabled={!dirty||saving} style={{opacity:dirty?1:.5}}>{saving?"保存中…":dirty?"保存设置":"已保存"}</button>
      </div>

      <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:20,maxWidth:900}}>
        {/* Model Providers */}
        <div className="card"><div className="card-header"><h3>🤖 模型提供商</h3></div><div className="card-body">
          {providers.map(p=>{const prov=s.providers?.[p.key]||{enabled:false};return(
            <div key={p.key} style={{padding:"12px 0",borderBottom:"1px solid var(--ink-50)"}}>
              <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:6}}>
                <div><div style={{fontSize:14,fontWeight:600}}>{p.name}</div><div style={{fontSize:11,color:"var(--ink-400)"}}>{p.desc}</div></div>
                <label style={{position:"relative",width:40,height:22,cursor:"pointer"}}><input type="checkbox" checked={!!prov.enabled} onChange={e=>updateP(p.key,"enabled",e.target.checked)} style={{opacity:0,position:"absolute"}}/>
                  <div style={{width:40,height:22,borderRadius:11,background:prov.enabled?"var(--jade)":"var(--ink-200)",transition:"background 0.2s",position:"relative"}}><div style={{width:18,height:18,borderRadius:9,background:"white",position:"absolute",top:2,left:prov.enabled?20:2,transition:"left 0.2s",boxShadow:"0 1px 2px rgba(0,0,0,.15)"}}/></div></label>
              </div>
              {prov.enabled&&p.hasKey&&<div style={{marginTop:6}}>
                <input type="password" placeholder={`${p.name} API Key`} value={prov.api_key||""} onChange={e=>updateP(p.key,"api_key",e.target.value)}
                  style={{width:"100%",padding:"6px 10px",border:"1px solid var(--ink-200)",borderRadius:4,fontSize:12,fontFamily:"var(--font-mono)",outline:"none"}}/>
              </div>}
              {prov.enabled&&p.hasUrl&&<div style={{marginTop:6}}>
                <input placeholder="Base URL" value={prov.base_url||""} onChange={e=>updateP(p.key,"base_url",e.target.value)}
                  style={{width:"100%",padding:"6px 10px",border:"1px solid var(--ink-200)",borderRadius:4,fontSize:12,fontFamily:"var(--font-mono)",outline:"none"}}/>
              </div>}
            </div>
          );})}
        </div></div>

        {/* Presets */}
        <div className="card"><div className="card-header"><h3>📋 模型预设方案</h3></div><div className="card-body">
          {presets.map(p=><div key={p.key} onClick={()=>update("model_preset",p.key)} style={{padding:"12px 14px",borderRadius:8,marginBottom:8,border:s.model_preset===p.key?"2px solid var(--accent)":"1px solid var(--ink-100)",background:s.model_preset===p.key?"var(--accent-muted)":"transparent",cursor:"pointer",transition:"all 0.15s"}}>
            <div style={{fontSize:14,fontWeight:600,color:s.model_preset===p.key?"var(--accent)":"var(--ink-700)"}}>{p.name}</div>
            <div style={{fontSize:12,color:"var(--ink-400)",marginTop:2}}>{p.desc}</div>
          </div>)}
        </div></div>

        {/* System Settings */}
        <div className="card"><div className="card-header"><h3>🔧 系统设置</h3></div><div className="card-body">
          <Toggle label="自动保存" desc="每隔一段时间自动保存草稿" checked={s.auto_save} onChange={v=>update("auto_save",v)}/>
          {s.auto_save&&<div style={{marginBottom:12,paddingLeft:12}}><label style={{fontSize:11,color:"var(--ink-400)"}}>保存间隔（秒）</label>
            <input type="number" value={s.auto_save_interval} onChange={e=>update("auto_save_interval",+e.target.value)} min={5} max={300} style={{width:80,marginLeft:8,padding:"4px 8px",border:"1px solid var(--ink-200)",borderRadius:4,fontSize:12,outline:"none"}}/></div>}
          <Toggle label="成本确认" desc="商业 API 调用前显示预估" checked={s.cost_confirm} onChange={v=>update("cost_confirm",v)}/>
          <div style={{marginBottom:12}}><label style={{fontSize:12,fontWeight:600,color:"var(--ink-500)",display:"block",marginBottom:4}}>导出格式</label>
            <div style={{display:"flex",gap:6}}>{["txt","docx","epub"].map(f=><button key={f} onClick={()=>update("export_format",f)} style={{padding:"5px 16px",borderRadius:16,border:s.export_format===f?"2px solid var(--accent)":"1px solid var(--ink-200)",background:s.export_format===f?"var(--accent-muted)":"transparent",color:s.export_format===f?"var(--accent)":"var(--ink-600)",fontSize:12,cursor:"pointer",fontFamily:"var(--font-mono)",textTransform:"uppercase"}}>{f}</button>)}</div>
          </div>
        </div></div>

        {/* Data Info */}
        <div className="card"><div className="card-header"><h3>💾 数据存储</h3></div><div className="card-body">
          <div style={{fontSize:13,color:"var(--ink-600)",lineHeight:1.8}}>
            所有创作数据存储在项目根目录的 <code style={{background:"var(--ink-50)",padding:"2px 6px",borderRadius:3,fontFamily:"var(--font-mono)",fontSize:12}}>/data/</code> 文件夹中：
          </div>
          <div style={{marginTop:12,fontSize:12,color:"var(--ink-400)",lineHeight:2}}>
            📁 data/characters/ — 角色卡 JSON<br/>
            📁 data/worldbook/ — 世界书条目 JSON<br/>
            📁 data/editor/ — 编辑器正文 JSON<br/>
            📄 data/settings.json — 系统设置
          </div>
          <div style={{marginTop:12,padding:"10px 12px",background:"var(--jade-light)",borderRadius:6,fontSize:11,color:"var(--jade)"}}>
            ✓ 所有数据仅保存在本地，不上传到任何服务器。
          </div>
        </div></div>
      </div>
    </div>
  );
}

function Toggle({label,desc,checked,onChange}:{label:string;desc:string;checked:boolean;onChange:(v:boolean)=>void}) {
  return <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",padding:"8px 0",borderBottom:"1px solid var(--ink-50)",marginBottom:8}}>
    <div><div style={{fontSize:13,fontWeight:500}}>{label}</div><div style={{fontSize:11,color:"var(--ink-400)"}}>{desc}</div></div>
    <label style={{position:"relative",width:40,height:22,cursor:"pointer"}}><input type="checkbox" checked={checked} onChange={e=>onChange(e.target.checked)} style={{opacity:0,position:"absolute"}}/>
      <div style={{width:40,height:22,borderRadius:11,background:checked?"var(--jade)":"var(--ink-200)",transition:"background 0.2s",position:"relative"}}><div style={{width:18,height:18,borderRadius:9,background:"white",position:"absolute",top:2,left:checked?20:2,transition:"left 0.2s",boxShadow:"0 1px 2px rgba(0,0,0,.15)"}}/></div></label>
  </div>;
}
