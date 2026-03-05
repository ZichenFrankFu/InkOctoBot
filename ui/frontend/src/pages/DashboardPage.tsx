import React, { useEffect, useState, useMemo, useCallback } from "react";
import { apiGet } from "../api/client";
type P="" | "qidian" | "fanqie";
interface OV { novel_count:number; rank_list_count:number; snapshot_count:number; chapter_count:number; recent_snapshots:any[]; platform_breakdown:{platform:string;count:number}[]; categories:{main_category:string;count:number}[]; rank_families:any[]; }
interface TN { novel_uid:number; title:string; author:string; platform:string; main_category:string; appearances:number; best_rank:number; avg_rank:number; }
interface TS { tag_name:string; novel_count:number; }
interface ND { novel:any; titles:any[]; tags:any[]; rank_history:any[]; chapters:any[]; }

export default function DashboardPage() {
  const [plat,setPlat]=useState<P>(""); const [ov,setOv]=useState<OV|null>(null);
  const [top,setTop]=useState<TN[]>([]); const [tags,setTags]=useState<TS[]>([]); const [loading,setLoading]=useState(true);
  const [showPanel,setShowPanel]=useState(false); const [nd,setNd]=useState<ND|null>(null); const [ndL,setNdL]=useState(false);

  useEffect(()=>{setLoading(true);const p=plat||undefined;
    Promise.all([apiGet<OV>(`/api/db/overview${p?`?platform=${p}`:""}`),apiGet<{rows:TN[]}>(`/api/db/top_novels?limit=15${p?`&platform=${p}`:""}`),apiGet<{rows:TS[]}>(`/api/db/tag_stats?limit=15${p?`&platform=${p}`:""}`)])
    .then(([a,b,c])=>{setOv(a);setTop(b.rows);setTags(c.rows)}).catch(console.error).finally(()=>setLoading(false));
  },[plat]);

  const maxC=useMemo(()=>Math.max(1,...(ov?.categories.map(c=>c.count)||[1])),[ov]);
  const maxT=useMemo(()=>Math.max(1,...(tags.map(t=>t.novel_count)||[1])),[tags]);
  const pl=(p:string)=>p==="qidian"?"起点":p==="fanqie"?"番茄":p;

  const openDetail=useCallback(async(uid:number)=>{
    setShowPanel(true);setNdL(true);setNd(null);
    try{setNd(await apiGet<ND>(`/api/db/novel/${uid}`));}catch(e){console.error(e);}finally{setNdL(false);}
  },[]);

  return (
    <div className="page-container">
      <div className="page-header"><h2>首页</h2><p>市场数据概览与创作进度</p></div>

      <div className="card" style={{marginBottom:24}}><div className="card-header"><h3>📝 我的创作</h3></div><div className="card-body">
        <div className="stats-grid" style={{marginBottom:0}}>
          {[{i:"✍️",c:"gold",l:"累计创作字数"},{i:"📄",c:"jade",l:"已完成章节"},{i:"🤖",c:"indigo",l:"AI 辅助率"},{i:"💡",c:"red",l:"待处理建议"}].map(s=>
            <div className="stat-card" style={{opacity:.55}} key={s.l}><div className={`stat-icon ${s.c}`}>{s.i}</div><div className="stat-value" style={{color:"var(--ink-300)"}}>—</div><div className="stat-label">{s.l}</div></div>
          )}
        </div>
        <div className="placeholder-banner"><span>🚧</span><div><strong>创作模块开发中</strong><p>此区域将展示创作进度与 AI 辅助统计。</p></div></div>
      </div></div>

      <div style={{display:"flex",alignItems:"center",justifyContent:"space-between",marginBottom:20}}>
        <h3 className="font-serif" style={{fontSize:18,color:"var(--ink-800)"}}>📊 市场数据概览</h3>
        <div className="platform-tabs">
          <button className={`platform-tab${plat===""?" active":""}`} onClick={()=>setPlat("")}>全部</button>
          <button className={`platform-tab${plat==="qidian"?" active":""}`} onClick={()=>setPlat("qidian")}>起点</button>
          <button className={`platform-tab${plat==="fanqie"?" active":""}`} onClick={()=>setPlat("fanqie")}>番茄</button>
        </div>
      </div>

      {loading?<div className="loading"><div className="loading-spinner"/>加载中…</div>:!ov?<div className="empty-state"><div className="empty-icon">📭</div><h4>暂无数据</h4></div>:<>
        <div className="stats-grid">
          {[{i:"📚",c:"red",v:ov.novel_count,l:"收录小说"},{i:"🏆",c:"gold",v:ov.rank_list_count,l:"榜单类型"},{i:"📸",c:"jade",v:ov.snapshot_count,l:"榜单快照"},{i:"📖",c:"indigo",v:ov.chapter_count,l:"采集章节"}].map(s=>
            <div className="stat-card" key={s.l}><div className={`stat-icon ${s.c}`}>{s.i}</div><div className="stat-value">{s.v.toLocaleString()}</div><div className="stat-label">{s.l}</div></div>
          )}
        </div>
        <div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:20,marginBottom:24}}>
          <div className="card"><div className="card-header"><h3>题材分布</h3></div><div className="card-body"><div className="bar-chart">{ov.categories.slice(0,12).map(c=><div className="bar-row" key={c.main_category||"x"}><div className="bar-label">{c.main_category||"未分类"}</div><div className="bar-track"><div className="bar-fill red" style={{width:`${Math.max(4,(c.count/maxC)*100)}%`}}>{c.count}</div></div></div>)}</div></div></div>
          <div className="card"><div className="card-header"><h3>热门标签</h3></div><div className="card-body"><div className="bar-chart">{tags.slice(0,12).map(t=><div className="bar-row" key={t.tag_name}><div className="bar-label">{t.tag_name}</div><div className="bar-track"><div className="bar-fill indigo" style={{width:`${Math.max(4,(t.novel_count/maxT)*100)}%`}}>{t.novel_count}</div></div></div>)}</div></div></div>
        </div>
        {plat===""&&ov.platform_breakdown.length>0&&<div className="card" style={{marginBottom:24}}><div className="card-header"><h3>平台对比</h3></div><div className="card-body"><div style={{display:"flex",gap:24}}>{ov.platform_breakdown.map(pb=><div key={pb.platform} style={{flex:1,padding:"16px 20px",background:"var(--paper-warm)",borderRadius:"var(--radius-sm)",textAlign:"center"}}><div style={{fontSize:12,color:"var(--ink-400)",marginBottom:4}}>{pl(pb.platform)}</div><div style={{fontFamily:"var(--font-serif)",fontSize:24,fontWeight:700}}>{pb.count.toLocaleString()}</div></div>)}</div></div></div>}
        <div className="card"><div className="card-header"><h3>高频上榜作品</h3><p>点击查看详情</p></div>
          <div style={{maxHeight:440,overflowY:"auto"}}><table className="data-table"><thead><tr><th style={{width:50}}>#</th><th>书名</th><th>作者</th><th>平台</th><th>分类</th><th style={{textAlign:"right"}}>上榜</th><th style={{textAlign:"right"}}>最佳</th><th style={{textAlign:"right"}}>均排</th></tr></thead>
            <tbody>{top.map((n,i)=><tr key={n.novel_uid} className="clickable" onClick={()=>openDetail(n.novel_uid)}>
              <td><span className={`rank-badge ${i<3?"top3":i<10?"top10":"normal"}`}>{i+1}</span></td>
              <td style={{fontWeight:500,color:"var(--accent)"}}>{n.title||"(未知)"}</td><td className="text-muted">{n.author||"-"}</td>
              <td><span className={`tag ${n.platform}`}>{pl(n.platform)}</span></td><td>{n.main_category&&<span className="tag category">{n.main_category}</span>}</td>
              <td style={{textAlign:"right",fontFamily:"var(--font-mono)",fontWeight:600}}>{n.appearances}</td>
              <td style={{textAlign:"right",fontFamily:"var(--font-mono)"}}>{n.best_rank}</td><td style={{textAlign:"right",fontFamily:"var(--font-mono)"}}>{n.avg_rank}</td>
            </tr>)}</tbody></table></div>
        </div>
      </>}

      {/* Side Panel — no chapter content */}
      {showPanel&&<><div className="side-panel-overlay" onClick={()=>setShowPanel(false)}/><div className="side-panel">
        <div className="side-panel-header"><h3>{ndL?"加载中…":"作品详情"}</h3><button className="side-panel-close" onClick={()=>setShowPanel(false)}>✕</button></div>
        <div className="side-panel-body">{ndL?<div className="loading"><div className="loading-spinner"/></div>:nd?<NP d={nd}/>:<div className="text-muted">加载失败</div>}</div>
      </div></>}
    </div>
  );
}

function NP({d}:{d:ND}) {
  const {novel:n,titles,tags,rank_history:rh,chapters:chs}=d;
  const t=titles.find((x:any)=>x.is_primary)?.title||n.author||"(未知)";
  const pl=(p:string)=>p==="qidian"?"起点":p==="fanqie"?"番茄":p;
  return <>
    <div style={{marginBottom:20}}><h3 style={{fontFamily:"var(--font-serif)",fontSize:20,fontWeight:700,marginBottom:6}}>{t}</h3>
      <div style={{display:"flex",gap:8,flexWrap:"wrap"}}><span className={`tag ${n.platform}`}>{pl(n.platform)}</span>{n.main_category&&<span className="tag category">{n.main_category}</span>}{n.status&&<span className={`tag ${n.status==="completed"?"status-completed":"status-ongoing"}`}>{n.status==="completed"?"已完本":"连载中"}</span>}</div>
    </div>
    <div className="detail-section"><h4>基本信息</h4><div className="detail-grid">
      <span className="label">作者</span><span className="value">{n.author||"—"}</span>
      <span className="label">总字数</span><span className="value">{n.total_words?`${(n.total_words/10000).toFixed(1)}万字`:"—"}</span>
      <span className="label">首次采集</span><span className="value">{n.created_date||"—"}</span>
      <span className="label">最近出现</span><span className="value">{n.last_seen_date||"—"}</span>
      {n.url&&<><span className="label">链接</span><span className="value"><a href={n.url} target="_blank" rel="noopener noreferrer" style={{color:"var(--accent)",textDecoration:"underline",fontSize:12}}>查看原文 ↗</a></span></>}
    </div></div>
    {n.intro&&<div className="detail-section"><h4>简介</h4><p style={{fontSize:13,lineHeight:1.7,color:"var(--ink-600)",whiteSpace:"pre-wrap"}}>{n.intro}</p></div>}
    {tags.length>0&&<div className="detail-section"><h4>标签</h4><div style={{display:"flex",gap:6,flexWrap:"wrap"}}>{tags.map((tg:any)=><span key={tg.tag_id} className="tag category">{tg.tag_name}</span>)}</div></div>}
    {rh.length>0&&<div className="detail-section"><h4>排名历史</h4><div style={{maxHeight:200,overflowY:"auto"}}><table className="data-table"><thead><tr><th>日期</th><th>榜单</th><th style={{textAlign:"right"}}>排名</th></tr></thead><tbody>{rh.slice(0,20).map((h:any,i:number)=><tr key={i}><td className="font-mono" style={{fontSize:12}}>{h.snapshot_date}</td><td style={{fontSize:12}}>{h.rank_family}{h.rank_sub_cat&&` · ${h.rank_sub_cat}`}</td><td style={{textAlign:"right",fontFamily:"var(--font-mono)",fontWeight:600}}>{h.rank}</td></tr>)}</tbody></table></div></div>}
    {/* Chapters: only show count and titles — NO content */}
    {chs.length>0&&<div className="detail-section"><h4>已采集章节（{chs.length} 章）</h4>
      <div style={{fontSize:12,color:"var(--ink-500)",lineHeight:1.8}}>{chs.map((ch:any)=><div key={ch.chapter_num} style={{padding:"4px 0",borderBottom:"1px solid var(--ink-50)"}}><span className="font-mono" style={{color:"var(--ink-400)",marginRight:8}}>第{ch.chapter_num}章</span>{ch.chapter_title}<span style={{float:"right",color:"var(--ink-300)"}}>{ch.word_count?`${ch.word_count}字`:""}</span></div>)}</div>
    </div>}
  </>;
}
