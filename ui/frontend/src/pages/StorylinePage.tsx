import React, { useEffect, useState, useRef, useCallback, useMemo } from "react";
import { apiGet } from "../api/client";
const apiPut=(u:string,b:any)=>fetch(u,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify(b)}).then(r=>r.json());

interface SNode { id:string; title:string; summary:string; x:number; y:number; color:string; chapter?:number; characters:string[]; }
interface SEdge { from:string; to:string; label:string; }
const uid=()=>`n_${Date.now()}_${Math.random().toString(36).slice(2,6)}`;
const COLORS=["#c0392b","#2d8c5a","#3b5998","#d4a853","#8e44ad","#e67e22","#1abc9c","#e74c3c"];
const LANE_H=140; const NODE_W=180; const NODE_H=100; const GAP_X=60;

export default function StorylinePage({projectId}:{projectId:string}) {
  const [nodes,setNodes]=useState<SNode[]>([]); const [edges,setEdges]=useState<SEdge[]>([]);
  const [loaded,setLoaded]=useState(false); const [selected,setSelected]=useState<string|null>(null);
  const [dragging,setDragging]=useState<{id:string;offX:number;offY:number}|null>(null);
  const [connecting,setConnecting]=useState<string|null>(null);
  const canvasRef=useRef<HTMLDivElement>(null);
  const [scroll,setScroll]=useState({x:0,y:0});
  const [dirty,setDirty]=useState(false);

  useEffect(()=>{
    apiGet<any>(`/api/data/storyline?project_id=${projectId||"default"}`).then(d=>{
      if(d.nodes?.length){setNodes(d.nodes);setEdges(d.edges||[]);}
      else{// Demo data
        const demo:SNode[]=[
          {id:uid(),title:"第一章·初遇",summary:"主角初入宗门，遇到师兄",x:40,y:40,color:COLORS[0],chapter:1,characters:["张远","李四"]},
          {id:uid(),title:"第二章·试炼",summary:"宗门试炼开始",x:40+NODE_W+GAP_X,y:40,color:COLORS[1],chapter:2,characters:["张远"]},
          {id:uid(),title:"第二章·暗线",summary:"反派在暗中观察",x:40+NODE_W+GAP_X,y:40+LANE_H,color:COLORS[4],chapter:2,characters:["暗影"]},
          {id:uid(),title:"第三章·对决",summary:"试炼中遭遇危机",x:40+2*(NODE_W+GAP_X),y:40,color:COLORS[2],chapter:3,characters:["张远","暗影"]},
        ];
        setNodes(demo);
        setEdges([{from:demo[0].id,to:demo[1].id,label:"时间推进"},{from:demo[1].id,to:demo[3].id,label:"汇合"},{from:demo[2].id,to:demo[3].id,label:"暗线汇入"}]);
      }
      setLoaded(true);
    }).catch(()=>setLoaded(true));
  },[projectId]);

  const save=useCallback(()=>{
    apiPut("/api/data/storyline",{project_id:projectId||"default",nodes,edges}).catch(console.error);
    setDirty(false);
  },[nodes,edges,projectId]);

  // Auto-save
  useEffect(()=>{if(!loaded||!dirty)return;const t=setTimeout(save,2000);return()=>clearTimeout(t);},[dirty,loaded]);

  const addNode=()=>{
    const maxX=nodes.reduce((m,n)=>Math.max(m,n.x),0);
    const n:SNode={id:uid(),title:"新节点",summary:"",x:maxX+NODE_W+GAP_X,y:40,color:COLORS[nodes.length%COLORS.length],characters:[]};
    setNodes([...nodes,n]);setSelected(n.id);setDirty(true);
  };
  const delNode=(id:string)=>{setNodes(nodes.filter(n=>n.id!==id));setEdges(edges.filter(e=>e.from!==id&&e.to!==id));if(selected===id)setSelected(null);setDirty(true);};
  const updateNode=(id:string,k:string,v:any)=>{setNodes(nodes.map(n=>n.id===id?{...n,[k]:v}:n));setDirty(true);};

  // Drag
  const onMouseDown=(id:string,e:React.MouseEvent)=>{
    if(connecting){// Complete connection
      if(connecting!==id){setEdges([...edges,{from:connecting,to:id,label:""}]);setDirty(true);}
      setConnecting(null);return;
    }
    const node=nodes.find(n=>n.id===id);if(!node)return;
    setDragging({id,offX:e.clientX-node.x,offY:e.clientY-node.y});
    setSelected(id);
  };
  useEffect(()=>{
    const onMove=(e:MouseEvent)=>{if(!dragging)return;
      setNodes(prev=>prev.map(n=>n.id===dragging.id?{...n,x:Math.max(0,e.clientX-dragging.offX),y:Math.max(0,e.clientY-dragging.offY)}:n));setDirty(true);};
    const onUp=()=>setDragging(null);
    window.addEventListener("mousemove",onMove);window.addEventListener("mouseup",onUp);
    return()=>{window.removeEventListener("mousemove",onMove);window.removeEventListener("mouseup",onUp);};
  },[dragging]);

  const sel=useMemo(()=>nodes.find(n=>n.id===selected),[nodes,selected]);
  const canvasW=Math.max(1200,nodes.reduce((m,n)=>Math.max(m,n.x+NODE_W+100),0));
  const canvasH=Math.max(600,nodes.reduce((m,n)=>Math.max(m,n.y+NODE_H+100),0));

  return (
    <div style={{display:"flex",height:"100vh",overflow:"hidden"}}>
      {/* Canvas */}
      <div style={{flex:1,overflow:"auto",background:"var(--paper-warm)",position:"relative"}} ref={canvasRef}>
        <div style={{padding:"16px 20px",background:"var(--paper-card)",borderBottom:"1px solid var(--ink-100)",display:"flex",alignItems:"center",gap:12,position:"sticky",top:0,zIndex:10}}>
          <h3 style={{fontFamily:"var(--font-serif)",fontSize:16,fontWeight:700}}>🗺️ 剧情线</h3>
          <button onClick={addNode} style={{padding:"5px 14px",background:"var(--accent)",color:"white",border:"none",borderRadius:6,fontSize:12,cursor:"pointer"}}>+ 新节点</button>
          <button onClick={()=>setConnecting(connecting?null:(selected||null))} disabled={!selected} style={{padding:"5px 14px",background:connecting?"var(--jade)":"var(--ink-50)",color:connecting?"white":"var(--ink-600)",border:"1px solid var(--ink-200)",borderRadius:6,fontSize:12,cursor:"pointer",opacity:selected?1:.5}}>
            {connecting?"🔗 点击目标节点":"🔗 连线"}
          </button>
          <span style={{fontSize:11,color:"var(--ink-400)",marginLeft:"auto"}}>← 早 · 时间线 · 晚 → &nbsp; | &nbsp; 纵向并排 = 同时发生</span>
        </div>
        {/* SVG edges */}
        <svg style={{position:"absolute",top:0,left:0,width:canvasW,height:canvasH,pointerEvents:"none",zIndex:1}}>
          {edges.map((e,i)=>{const f=nodes.find(n=>n.id===e.from),t=nodes.find(n=>n.id===e.to);if(!f||!t)return null;
            const x1=f.x+NODE_W,y1=f.y+NODE_H/2+60,x2=t.x,y2=t.y+NODE_H/2+60;
            const mx=(x1+x2)/2;
            return <g key={i}>
              <path d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`} fill="none" stroke="var(--ink-300)" strokeWidth={2} markerEnd="url(#arrow)"/>
              {e.label&&<text x={mx} y={Math.min(y1,y2)-8} textAnchor="middle" fontSize={10} fill="var(--ink-400)">{e.label}</text>}
            </g>;
          })}
          <defs><marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="8" markerHeight="8" orient="auto"><path d="M0,0 L10,5 L0,10 Z" fill="var(--ink-300)"/></marker></defs>
        </svg>
        {/* Nodes */}
        <div style={{position:"relative",width:canvasW,height:canvasH,zIndex:2}}>
          {nodes.map(n=><div key={n.id} onMouseDown={e=>onMouseDown(n.id,e)} style={{
            position:"absolute",left:n.x,top:n.y+60,width:NODE_W,height:NODE_H,
            background:"var(--paper-card)",border:selected===n.id?`2px solid ${n.color}`:"1px solid var(--ink-200)",
            borderRadius:10,padding:"10px 12px",cursor:dragging?.id===n.id?"grabbing":"grab",
            boxShadow:selected===n.id?"var(--shadow-md)":"var(--shadow-sm)",transition:dragging?"none":"box-shadow 0.15s",
            borderTop:`4px solid ${n.color}`,overflow:"hidden",
          }}>
            <div style={{fontSize:13,fontWeight:700,color:"var(--ink-800)",marginBottom:4,overflow:"hidden",textOverflow:"ellipsis",whiteSpace:"nowrap"}}>{n.chapter!=null&&<span style={{color:n.color,marginRight:4}}>Ch{n.chapter}</span>}{n.title}</div>
            <div style={{fontSize:11,color:"var(--ink-400)",lineHeight:1.4,overflow:"hidden",height:32}}>{n.summary||"(空)"}</div>
            <div style={{fontSize:10,color:"var(--ink-300)",marginTop:4}}>{n.characters.join(" · ")||""}</div>
          </div>)}
        </div>
      </div>
      {/* Detail panel */}
      <div style={{width:280,flexShrink:0,background:"var(--paper-card)",borderLeft:"1px solid var(--ink-100)",overflowY:"auto"}}>
        <div style={{padding:"16px",borderBottom:"1px solid var(--ink-50)"}}>
          <div style={{fontSize:12,fontWeight:600,color:"var(--ink-500)"}}>节点详情</div>
        </div>
        {sel?<div style={{padding:16}}>
          <div style={{marginBottom:12}}><label style={{fontSize:11,fontWeight:600,color:"var(--ink-400)",display:"block",marginBottom:4}}>标题</label>
            <input value={sel.title} onChange={e=>updateNode(sel.id,"title",e.target.value)} style={{width:"100%",padding:"6px 10px",border:"1px solid var(--ink-200)",borderRadius:6,fontSize:13,outline:"none"}}/></div>
          <div style={{marginBottom:12}}><label style={{fontSize:11,fontWeight:600,color:"var(--ink-400)",display:"block",marginBottom:4}}>章节号</label>
            <input type="number" value={sel.chapter??""} onChange={e=>updateNode(sel.id,"chapter",e.target.value?+e.target.value:undefined)} style={{width:80,padding:"6px 10px",border:"1px solid var(--ink-200)",borderRadius:6,fontSize:13,outline:"none"}}/></div>
          <div style={{marginBottom:12}}><label style={{fontSize:11,fontWeight:600,color:"var(--ink-400)",display:"block",marginBottom:4}}>摘要</label>
            <textarea value={sel.summary} onChange={e=>updateNode(sel.id,"summary",e.target.value)} rows={3} style={{width:"100%",padding:"6px 10px",border:"1px solid var(--ink-200)",borderRadius:6,fontSize:12,outline:"none",resize:"vertical"}}/></div>
          <div style={{marginBottom:12}}><label style={{fontSize:11,fontWeight:600,color:"var(--ink-400)",display:"block",marginBottom:4}}>出场角色（逗号分隔）</label>
            <input value={sel.characters.join(", ")} onChange={e=>updateNode(sel.id,"characters",e.target.value.split(",").map(s=>s.trim()).filter(Boolean))} style={{width:"100%",padding:"6px 10px",border:"1px solid var(--ink-200)",borderRadius:6,fontSize:12,outline:"none"}}/></div>
          <div style={{marginBottom:12}}><label style={{fontSize:11,fontWeight:600,color:"var(--ink-400)",display:"block",marginBottom:4}}>颜色</label>
            <div style={{display:"flex",gap:4,flexWrap:"wrap"}}>{COLORS.map(c=><div key={c} onClick={()=>updateNode(sel.id,"color",c)} style={{width:24,height:24,borderRadius:12,background:c,cursor:"pointer",border:sel.color===c?"3px solid var(--ink-800)":"2px solid transparent"}}/>)}</div></div>
          <button onClick={()=>delNode(sel.id)} style={{width:"100%",padding:"8px",background:"var(--accent-muted)",color:"var(--accent)",border:"none",borderRadius:6,fontSize:12,cursor:"pointer",marginTop:12}}>🗑️ 删除节点</button>
          {/* Edge list */}
          <div style={{marginTop:20}}><div style={{fontSize:11,fontWeight:600,color:"var(--ink-400)",marginBottom:6}}>连线</div>
            {edges.filter(e=>e.from===sel.id||e.to===sel.id).map((e,i)=>{const other=e.from===sel.id?nodes.find(n=>n.id===e.to):nodes.find(n=>n.id===e.from);return(
              <div key={i} style={{display:"flex",alignItems:"center",gap:6,padding:"4px 0",borderBottom:"1px solid var(--ink-50)",fontSize:11}}>
                <span style={{color:"var(--ink-400)"}}>{e.from===sel.id?"→":"←"}</span>
                <span style={{flex:1}}>{other?.title||"?"}</span>
                <button onClick={()=>{setEdges(edges.filter((_,j)=>j!==edges.indexOf(e)));setDirty(true);}} style={{border:"none",background:"transparent",color:"var(--ink-300)",cursor:"pointer",fontSize:10}}>×</button>
              </div>
            );})}
          </div>
        </div>:<div style={{padding:20,textAlign:"center",color:"var(--ink-400)",fontSize:13}}><p>点击节点查看详情</p><p style={{fontSize:11,marginTop:4}}>拖拽移动 · 「连线」创建关系</p></div>}
      </div>
    </div>
  );
}
