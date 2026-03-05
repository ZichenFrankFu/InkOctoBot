import React, { useEffect, useState, useMemo } from "react";
import { apiGet } from "../api/client";
type Sub="tags"|"categories"|"opportunities"|"cooccurrence"|"cross";
interface AR { start_date:string; end_date:string; platform:string; lookback?:string; top_k?:number; empty:boolean; error?:string; message?:string; tag_rollup:any[]; cat_rollup:any[]; opportunities:any[]; new_entry:any[]; pairs:any[]; triples:any[]; cross_platform:any[]; }

export default function TrendAnalysisPage() {
  const [plat,setPlat]=useState("both"); const [lb,setLb]=useState("all"); const [tk,setTk]=useState(20);
  const [data,setData]=useState<AR|null>(null); const [loading,setLoading]=useState(false); const [err,setErr]=useState("");
  const [sub,setSub]=useState<Sub>("tags"); const [dr,setDr]=useState<{min_date:string;max_date:string}|null>(null);

  useEffect(()=>{apiGet<any>("/api/analysis/date_range").then(setDr).catch(()=>{});},[]);
  const run=()=>{setLoading(true);setErr("");
    apiGet<AR>(`/api/analysis/run?platform=${plat}&lookback=${lb}&top_k=${tk}`)
      .then(r=>{if(r.error){setErr(r.message||r.error);setData(null);}else setData(r);})
      .catch(e=>{setErr(String(e));setData(null);}).finally(()=>setLoading(false));
  };
  useEffect(()=>{run();},[]);

  const pl=(p:string)=>p==="qidian"?"起点":p==="fanqie"?"番茄":p;
  const fmt=(v:number|null,d:number)=>v==null||isNaN(v)?"—":v.toFixed(d);
  const Slope=({v}:{v:number|null})=>{if(v==null||isNaN(v))return<span className="text-muted font-mono">—</span>;const c=v>0.001?"var(--jade)":v<-0.001?"var(--accent)":"var(--ink-400)";const a=v>0.001?"↑":v<-0.001?"↓":"→";return<span className="font-mono" style={{color:c,fontSize:12}}>{a} {Math.abs(v).toFixed(4)}</span>;};
  const Delta=({v,pct}:{v:number|null;pct?:boolean})=>{if(v==null||isNaN(v))return<span className="text-muted font-mono">—</span>;const c=v>0?"var(--jade)":v<0?"var(--accent)":"var(--ink-400)";const s=v>0?"+":"";return<span className="font-mono" style={{color:c,fontSize:12,fontWeight:600}}>{pct?`${s}${(v*100).toFixed(1)}%`:`${s}${v.toFixed(0)}`}</span>;};
  const Stage=({s}:{s:string})=>{const m:Record<string,{bg:string;c:string;l:string}>={safe:{bg:"var(--jade-light)",c:"var(--jade)",l:"稳定"},chance:{bg:"var(--gold-light)",c:"#92700c",l:"机会"},stable:{bg:"var(--ink-50)",c:"var(--ink-500)",l:"平稳"},unknown:{bg:"var(--ink-50)",c:"var(--ink-400)",l:"—"}};const x=m[s]||m.unknown;return<span className="tag" style={{background:x.bg,color:x.c}}>{x.l}</span>;};

  return (
    <div className="page-container">
      <div className="page-header"><h2>趋势分析</h2><p>题材趋势、热度变化与开书机会</p></div>
      <div className="card" style={{marginBottom:24}}><div className="card-body">
        <div style={{display:"flex",gap:20,alignItems:"flex-end",flexWrap:"wrap"}}>
          <div className="control-group"><label className="control-label">平台</label><select className="control-select" value={plat} onChange={e=>setPlat(e.target.value)}><option value="both">全部平台</option><option value="qidian">起点</option><option value="fanqie">番茄</option></select></div>
          <div className="control-group"><label className="control-label">时间窗口</label><select className="control-select" value={lb} onChange={e=>setLb(e.target.value)}><option value="week">一周</option><option value="month">一月</option><option value="quarter">一季</option><option value="year">一年</option><option value="all">全部</option></select></div>
          <div className="control-group"><label className="control-label">Top K</label><select className="control-select" value={tk} onChange={e=>setTk(+e.target.value)}>{[10,15,20,30,50].map(k=><option key={k} value={k}>{k}</option>)}</select></div>
          <button className="btn-primary" onClick={run} disabled={loading}>{loading?"分析中…":"运行分析"}</button>
          {dr?.min_date&&<span className="text-xs text-muted">数据: {dr.min_date} ~ {dr.max_date}</span>}
        </div>
      </div></div>

      {err&&<div className="card" style={{marginBottom:16,borderLeft:"3px solid var(--accent)"}}><div className="card-body">
        <div style={{fontSize:14,fontWeight:600,color:"var(--accent)",marginBottom:6}}>⚠️ 分析出错</div>
        <div style={{fontSize:13,color:"var(--ink-600)",whiteSpace:"pre-wrap",lineHeight:1.6}}>{err}</div>
        <div style={{marginTop:10,fontSize:12,color:"var(--ink-400)"}}>提示：请检查 analysis/ 目录是否完整，以及数据库中是否有快照数据。</div>
      </div></div>}

      {loading?<div className="loading"><div className="loading-spinner"/>正在运行分析…</div>:!data?null:data.empty?
        <div className="empty-state"><div className="empty-icon">📭</div><h4>暂无数据</h4><p>所选范围没有可分析数据</p></div>:<>
        <div style={{fontSize:12,color:"var(--ink-400)",marginBottom:16}}>📅 {data.start_date} ~ {data.end_date} · {data.platform==="both"?"全部平台":pl(data.platform)}</div>
        <div className="platform-tabs" style={{marginBottom:24}}>
          <button className={`platform-tab${sub==="tags"?" active":""}`} onClick={()=>setSub("tags")}>标签趋势</button>
          <button className={`platform-tab${sub==="categories"?" active":""}`} onClick={()=>setSub("categories")}>分类趋势</button>
          <button className={`platform-tab${sub==="opportunities"?" active":""}`} onClick={()=>setSub("opportunities")}>开书机会</button>
          <button className={`platform-tab${sub==="cooccurrence"?" active":""}`} onClick={()=>setSub("cooccurrence")}>标签共现</button>
          {data.platform==="both"&&<button className={`platform-tab${sub==="cross"?" active":""}`} onClick={()=>setSub("cross")}>跨平台对比</button>}
        </div>

        {sub==="tags"&&<div className="card"><div className="card-header"><h3>标签趋势汇总</h3></div><div style={{maxHeight:560,overflowY:"auto"}}><table className="data-table"><thead><tr><th>平台</th><th>榜单</th><th>标签</th><th>阶段</th><th style={{textAlign:"right"}}>份额</th><th style={{textAlign:"right"}}>热度</th><th style={{textAlign:"right"}}>均排名</th><th style={{textAlign:"right"}}>热度趋势</th><th style={{textAlign:"right"}}>份额趋势</th></tr></thead>
          <tbody>{[...data.tag_rollup].sort((a,b)=>(b.avg_heat??0)-(a.avg_heat??0)).map((r,i)=><tr key={i}><td><span className={`tag ${r.platform}`}>{pl(r.platform)}</span></td><td className="text-xs text-muted">{r.rank_family}{r.rank_sub_cat?` · ${r.rank_sub_cat}`:""}</td><td style={{fontWeight:500}}>{r.tag_u}</td><td><Stage s={r.stage}/></td><td style={{textAlign:"right"}} className="font-mono">{fmt(r.mean_share,3)}</td><td style={{textAlign:"right"}} className="font-mono">{fmt(r.avg_heat,0)}</td><td style={{textAlign:"right"}} className="font-mono">{fmt(r.mean_rank,1)}</td><td style={{textAlign:"right"}}><Slope v={r.heat_slope}/></td><td style={{textAlign:"right"}}><Slope v={r.share_slope}/></td></tr>)}</tbody></table></div></div>}

        {sub==="categories"&&<div className="card"><div className="card-header"><h3>分类趋势汇总</h3></div><div style={{maxHeight:560,overflowY:"auto"}}><table className="data-table"><thead><tr><th>平台</th><th>分类</th><th style={{textAlign:"right"}}>份额</th><th style={{textAlign:"right"}}>热度</th><th style={{textAlign:"right"}}>均排名</th><th style={{textAlign:"right"}}>热度趋势</th><th style={{textAlign:"right"}}>份额趋势</th></tr></thead>
          <tbody>{[...data.cat_rollup].sort((a,b)=>(b.avg_heat??0)-(a.avg_heat??0)).map((r,i)=><tr key={i}><td><span className={`tag ${r.platform}`}>{pl(r.platform)}</span></td><td style={{fontWeight:500}}>{r.cat_u}</td><td style={{textAlign:"right"}} className="font-mono">{fmt(r.mean_share,3)}</td><td style={{textAlign:"right"}} className="font-mono">{fmt(r.avg_heat,0)}</td><td style={{textAlign:"right"}} className="font-mono">{fmt(r.mean_rank,1)}</td><td style={{textAlign:"right"}}><Slope v={r.heat_slope}/></td><td style={{textAlign:"right"}}><Slope v={r.share_slope}/></td></tr>)}</tbody></table></div></div>}

        {sub==="opportunities"&&<><div className="card" style={{marginBottom:20}}><div className="card-header"><h3>🎯 开书机会榜</h3><p>综合份额增长+热度增长+新书占比</p></div><div style={{maxHeight:480,overflowY:"auto"}}>{data.opportunities.length===0?<div className="empty-state"><p>数据不足</p></div>:<table className="data-table"><thead><tr><th>平台</th><th>分类</th><th>标签</th><th style={{textAlign:"right"}}>份额变化</th><th style={{textAlign:"right"}}>热度变化</th><th style={{textAlign:"right"}}>新书比</th><th style={{textAlign:"right"}}>机会分</th></tr></thead>
          <tbody>{data.opportunities.map((r,i)=><tr key={i}><td><span className={`tag ${r.platform}`}>{pl(r.platform)}</span></td><td className="text-muted">{r.cat_u}</td><td style={{fontWeight:500}}>{r.tag_u}</td><td style={{textAlign:"right"}}><Delta v={r.share_delta} pct/></td><td style={{textAlign:"right"}}><Delta v={r.heat_delta}/></td><td style={{textAlign:"right"}} className="font-mono">{fmt(r.new_entry_ratio,2)}</td><td style={{textAlign:"right",fontWeight:700,fontFamily:"var(--font-mono)"}}>{fmt(r.opportunity_score,2)}</td></tr>)}</tbody></table>}</div></div>
        <div className="card"><div className="card-header"><h3>新入榜占比</h3></div><div style={{maxHeight:360,overflowY:"auto"}}>{data.new_entry.length===0?<div className="empty-state"><p>暂无</p></div>:<table className="data-table"><thead><tr><th>平台</th><th>标签</th><th style={{textAlign:"right"}}>总书</th><th style={{textAlign:"right"}}>新书</th><th style={{textAlign:"right"}}>新书比</th></tr></thead><tbody>{data.new_entry.map((r,i)=><tr key={i}><td><span className={`tag ${r.platform}`}>{pl(r.platform)}</span></td><td style={{fontWeight:500}}>{r.tag_u}</td><td style={{textAlign:"right"}} className="font-mono">{r.total_books}</td><td style={{textAlign:"right"}} className="font-mono">{r.new_books}</td><td style={{textAlign:"right"}}><Delta v={r.new_entry_ratio} pct/></td></tr>)}</tbody></table>}</div></div></>}

        {sub==="cooccurrence"&&<div style={{display:"grid",gridTemplateColumns:"1fr 1fr",gap:20}}>
          <div className="card"><div className="card-header"><h3>标签共现（二元）</h3></div><div style={{maxHeight:480,overflowY:"auto"}}>{data.pairs.length===0?<div className="empty-state"><p>暂无</p></div>:<table className="data-table"><thead><tr><th>标签A</th><th>标签B</th><th style={{textAlign:"right"}}>共现</th></tr></thead><tbody>{data.pairs.map((r,i)=><tr key={i}><td><span className="tag category">{r.tag_a}</span></td><td><span className="tag category">{r.tag_b}</span></td><td style={{textAlign:"right",fontFamily:"var(--font-mono)",fontWeight:600}}>{r.count}</td></tr>)}</tbody></table>}</div></div>
          <div className="card"><div className="card-header"><h3>标签共现（三元）</h3></div><div style={{maxHeight:480,overflowY:"auto"}}>{data.triples.length===0?<div className="empty-state"><p>暂无</p></div>:<table className="data-table"><thead><tr><th>A</th><th>B</th><th>C</th><th style={{textAlign:"right"}}>共现</th></tr></thead><tbody>{data.triples.map((r,i)=><tr key={i}><td><span className="tag category">{r.tag_a}</span></td><td><span className="tag category">{r.tag_b}</span></td><td><span className="tag category">{r.tag_c}</span></td><td style={{textAlign:"right",fontFamily:"var(--font-mono)",fontWeight:600}}>{r.count}</td></tr>)}</tbody></table>}</div></div>
        </div>}

        {sub==="cross"&&<div className="card"><div className="card-header"><h3>跨平台分类差异</h3><p>仅展示两平台共有的分类</p></div><div style={{maxHeight:560,overflowY:"auto"}}>{data.cross_platform.filter(d=>d.presence==="both").length===0?<div className="empty-state"><p>暂无可对比数据</p></div>:<table className="data-table"><thead><tr><th>分类</th><th style={{textAlign:"right"}}>起点份额</th><th style={{textAlign:"right"}}>番茄份额</th><th style={{textAlign:"right"}}>份额差</th><th style={{textAlign:"right"}}>热度差</th><th style={{textAlign:"right"}}>排名差</th></tr></thead>
          <tbody>{data.cross_platform.filter(d=>d.presence==="both").sort((a,b)=>Math.abs(b.share_diff??0)-Math.abs(a.share_diff??0)).map((r,i)=><tr key={i}><td style={{fontWeight:500}}>{r.cat_u}</td><td style={{textAlign:"right"}} className="font-mono">{fmt(r.share_qidian,3)}</td><td style={{textAlign:"right"}} className="font-mono">{fmt(r.share_fanqie,3)}</td><td style={{textAlign:"right"}}><Delta v={r.share_diff} pct/></td><td style={{textAlign:"right"}}><Delta v={r.heat_diff}/></td><td style={{textAlign:"right"}} className="font-mono">{fmt(r.rank_diff,1)}</td></tr>)}</tbody></table>}</div></div>}
      </>}
    </div>
  );
}
