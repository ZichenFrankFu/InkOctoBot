import React, { useEffect, useState, useRef, useCallback, useMemo } from "react";
import { apiGet } from "../api/client";
const apiPut = (u: string, b: any) => fetch(u, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) }).then(r => r.json());

interface SNode { id: string; title: string; summary: string; x: number; y: number; color: string; chapter?: number; characters: string[]; }
interface SEdge { from: string; to: string; label: string; }
const uid = () => `n_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
const COLORS = ["#c0392b", "#2d8c5a", "#3b5998", "#d4a853", "#8e44ad", "#e67e22", "#1abc9c", "#e74c3c"];
const NODE_W = 180;
const NODE_H = 100;
const GAP_X = 80;
const HEADER_H = 56;

export default function StorylinePage({ projectId }: { projectId: string }) {
  const [nodes, setNodes] = useState<SNode[]>([]);
  const [edges, setEdges] = useState<SEdge[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [dragging, setDragging] = useState<{ id: string; offX: number; offY: number } | null>(null);
  const [connecting, setConnecting] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const canvasRef = useRef<HTMLDivElement>(null);

  // Load
  useEffect(() => {
    apiGet<any>(`/api/data/storyline?project_id=${projectId || "default"}`).then(d => {
      if (d.nodes?.length) { setNodes(d.nodes); setEdges(d.edges || []); }
      else {
        const n1: SNode = { id: uid(), title: "第一章·开篇", summary: "故事的起点", x: 60, y: 60, color: COLORS[0], chapter: 1, characters: [] };
        const n2: SNode = { id: uid(), title: "第二章·发展", summary: "矛盾初显", x: 60 + NODE_W + GAP_X, y: 60, color: COLORS[1], chapter: 2, characters: [] };
        setNodes([n1, n2]);
        setEdges([{ from: n1.id, to: n2.id, label: "" }]);
      }
      setLoaded(true);
    }).catch(() => setLoaded(true));
  }, [projectId]);

  // Auto-save
  useEffect(() => {
    if (!loaded || !dirty) return;
    const t = setTimeout(() => {
      apiPut("/api/data/storyline", { project_id: projectId || "default", nodes, edges }).catch(console.error);
      setDirty(false);
    }, 2000);
    return () => clearTimeout(t);
  }, [dirty, nodes, edges, loaded, projectId]);

  // Add node — place to the right of rightmost node
  const addNode = useCallback(() => {
    const maxX = nodes.length > 0 ? Math.max(...nodes.map(n => n.x)) : 0;
    const n: SNode = {
      id: uid(),
      title: `新节点`,
      summary: "",
      x: maxX + NODE_W + GAP_X,
      y: 60,
      color: COLORS[nodes.length % COLORS.length],
      characters: [],
    };
    setNodes(prev => [...prev, n]);
    setSelected(n.id);
    setDirty(true);
  }, [nodes]);

  // Sync from editor outlines
  const syncFromEditor = useCallback(async () => {
    try {
      const data = await apiGet<any>(`/api/data/editor?project_id=${projectId || "default"}`);
      if (!data.volumes?.length) return;
      const newNodes: SNode[] = [];
      let chNum = 0;
      for (const vol of data.volumes) {
        for (const ch of vol.chapters || []) {
          chNum++;
          const existing = nodes.find(n => n.chapter === chNum);
          if (existing) {
            // Update summary from outline
            if (ch.outline && ch.outline !== existing.summary) {
              setNodes(prev => prev.map(n => n.id === existing.id ? { ...n, summary: ch.outline, title: ch.title } : n));
            }
          } else {
            newNodes.push({
              id: uid(),
              title: ch.title || `第${chNum}章`,
              summary: ch.outline || "",
              x: 60 + (chNum - 1) * (NODE_W + GAP_X),
              y: 60,
              color: COLORS[(chNum - 1) % COLORS.length],
              chapter: chNum,
              characters: [],
            });
          }
        }
      }
      if (newNodes.length > 0) {
        setNodes(prev => [...prev, ...newNodes]);
        // Auto-connect sequential new nodes
        const allNodes = [...nodes, ...newNodes].sort((a, b) => (a.chapter || 0) - (b.chapter || 0));
        const newEdges: SEdge[] = [];
        for (let i = 1; i < allNodes.length; i++) {
          const from = allNodes[i - 1].id;
          const to = allNodes[i].id;
          if (!edges.some(e => e.from === from && e.to === to)) {
            newEdges.push({ from, to, label: "" });
          }
        }
        if (newEdges.length) setEdges(prev => [...prev, ...newEdges]);
        setDirty(true);
      }
    } catch (e) { console.error(e); }
  }, [nodes, edges, projectId]);

  const delNode = (id: string) => {
    setNodes(prev => prev.filter(n => n.id !== id));
    setEdges(prev => prev.filter(e => e.from !== id && e.to !== id));
    if (selected === id) setSelected(null);
    setDirty(true);
  };

  const updateNode = (id: string, k: string, v: any) => {
    setNodes(prev => prev.map(n => n.id === id ? { ...n, [k]: v } : n));
    setDirty(true);
  };

  // Drag
  const onNodeMouseDown = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (connecting) {
      if (connecting !== id) {
        setEdges(prev => [...prev, { from: connecting, to: id, label: "" }]);
        setDirty(true);
      }
      setConnecting(null);
      return;
    }
    const node = nodes.find(n => n.id === id);
    if (!node) return;
    const rect = canvasRef.current?.getBoundingClientRect();
    const scrollLeft = canvasRef.current?.scrollLeft || 0;
    const scrollTop = canvasRef.current?.scrollTop || 0;
    setDragging({
      id,
      offX: e.clientX - (rect?.left || 0) + scrollLeft - node.x,
      offY: e.clientY - (rect?.top || 0) + scrollTop - node.y - HEADER_H,
    });
    setSelected(id);
  };

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!dragging || !canvasRef.current) return;
      const rect = canvasRef.current.getBoundingClientRect();
      const scrollLeft = canvasRef.current.scrollLeft;
      const scrollTop = canvasRef.current.scrollTop;
      const x = Math.max(0, e.clientX - rect.left + scrollLeft - dragging.offX);
      const y = Math.max(0, e.clientY - rect.top + scrollTop - dragging.offY - HEADER_H);
      setNodes(prev => prev.map(n => n.id === dragging.id ? { ...n, x, y } : n));
      setDirty(true);
    };
    const onUp = () => setDragging(null);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => { window.removeEventListener("mousemove", onMove); window.removeEventListener("mouseup", onUp); };
  }, [dragging]);

  const sel = useMemo(() => nodes.find(n => n.id === selected), [nodes, selected]);
  const canvasW = Math.max(1200, (nodes.length > 0 ? Math.max(...nodes.map(n => n.x)) : 0) + NODE_W + 200);
  const canvasH = Math.max(600, (nodes.length > 0 ? Math.max(...nodes.map(n => n.y)) : 0) + NODE_H + 200);

  // Compute edge paths with Y offset to avoid overlapping
  const edgePaths = useMemo(() => {
    // Group edges by (from, to) pair to detect overlaps
    return edges.map((edge, idx) => {
      const f = nodes.find(n => n.id === edge.from);
      const t = nodes.find(n => n.id === edge.to);
      if (!f || !t) return null;

      const x1 = f.x + NODE_W;
      const y1 = f.y + NODE_H / 2;
      const x2 = t.x;
      const y2 = t.y + NODE_H / 2;

      // Count how many edges share same from or to node to offset
      const sameGroup = edges.filter((e, i) =>
        i !== idx && ((e.from === edge.from && e.to === edge.to) || (e.from === edge.to && e.to === edge.from))
      );
      const offsetIdx = sameGroup.length > 0 ? idx % 3 : 0;
      const yOff = offsetIdx * 20;

      // Use different control point strategy based on relative position
      const dx = x2 - x1;
      const dy = y2 - y1;

      let path: string;
      if (Math.abs(dx) > 50) {
        // Normal left-to-right: smooth cubic bezier
        const cx = dx * 0.4;
        path = `M${x1},${y1} C${x1 + cx},${y1 + yOff} ${x2 - cx},${y2 - yOff} ${x2},${y2}`;
      } else {
        // Nodes are vertically stacked: arc around
        const arcX = Math.max(x1, x2) + 60;
        path = `M${x1},${y1} C${arcX},${y1} ${arcX},${y2} ${x2},${y2}`;
      }

      return { ...edge, path, mx: (x1 + x2) / 2, my: Math.min(y1, y2) - 12 - yOff, idx };
    }).filter(Boolean) as Array<SEdge & { path: string; mx: number; my: number; idx: number }>;
  }, [edges, nodes]);

  return (
    <div style={{ display: "flex", height: "100vh", overflow: "hidden" }}>
      {/* Canvas */}
      <div ref={canvasRef} style={{ flex: 1, overflow: "auto", background: "var(--paper-warm)", position: "relative" }}>
        <div style={{ padding: "14px 20px", background: "var(--paper-card)", borderBottom: "1px solid var(--ink-100)", display: "flex", alignItems: "center", gap: 10, position: "sticky", top: 0, zIndex: 10, height: HEADER_H }}>
          <h3 style={{ fontFamily: "var(--font-serif)", fontSize: 16, fontWeight: 700 }}>🗺️ 剧情线</h3>
          <button onClick={addNode} style={{ padding: "5px 14px", background: "var(--accent)", color: "white", border: "none", borderRadius: 6, fontSize: 12, cursor: "pointer" }}>+ 新节点</button>
          <button onClick={() => setConnecting(connecting ? null : (selected || null))} disabled={!selected}
            style={{ padding: "5px 14px", background: connecting ? "var(--jade)" : "var(--ink-50)", color: connecting ? "white" : "var(--ink-600)", border: "1px solid var(--ink-200)", borderRadius: 6, fontSize: 12, cursor: "pointer", opacity: selected ? 1 : .5 }}>
            {connecting ? "🔗 点击目标…" : "🔗 连线"}
          </button>
          <button onClick={syncFromEditor} style={{ padding: "5px 14px", background: "var(--indigo-light)", color: "var(--indigo)", border: "1px solid var(--indigo)", borderRadius: 6, fontSize: 12, cursor: "pointer" }}>📥 从编辑器同步</button>
          <span style={{ fontSize: 11, color: "var(--ink-400)", marginLeft: "auto" }}>← 早 · 时间线 · 晚 → &nbsp;|&nbsp; 纵向并排 = 同时发生</span>
        </div>

        {/* SVG edges */}
        <svg style={{ position: "absolute", top: HEADER_H, left: 0, width: canvasW, height: canvasH, pointerEvents: "none", zIndex: 1 }}>
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="8" markerHeight="8" orient="auto">
              <path d="M0,0 L10,5 L0,10 Z" fill="var(--ink-300)" />
            </marker>
          </defs>
          {edgePaths.map(ep => (
            <g key={ep.idx}>
              <path d={ep.path} fill="none" stroke="var(--ink-300)" strokeWidth={2} markerEnd="url(#arrow)" />
              {ep.label && <text x={ep.mx} y={ep.my} textAnchor="middle" fontSize={10} fill="var(--ink-400)">{ep.label}</text>}
            </g>
          ))}
        </svg>

        {/* Nodes */}
        <div style={{ position: "relative", width: canvasW, height: canvasH, zIndex: 2 }}>
          {nodes.map(n => (
            <div key={n.id} onMouseDown={e => onNodeMouseDown(n.id, e)}
              style={{
                position: "absolute", left: n.x, top: n.y, width: NODE_W, height: NODE_H,
                background: "var(--paper-card)",
                border: selected === n.id ? `2px solid ${n.color}` : "1px solid var(--ink-200)",
                borderRadius: 10, padding: "10px 12px",
                cursor: dragging?.id === n.id ? "grabbing" : connecting ? "crosshair" : "grab",
                boxShadow: selected === n.id ? "var(--shadow-md)" : "var(--shadow-sm)",
                borderTop: `4px solid ${n.color}`, overflow: "hidden",
                transition: dragging ? "none" : "box-shadow 0.15s",
              }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: "var(--ink-800)", marginBottom: 4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {n.chapter != null && <span style={{ color: n.color, marginRight: 4 }}>Ch{n.chapter}</span>}{n.title}
              </div>
              <div style={{ fontSize: 11, color: "var(--ink-400)", lineHeight: 1.4, overflow: "hidden", height: 32 }}>{n.summary || "(空)"}</div>
              <div style={{ fontSize: 10, color: "var(--ink-300)", marginTop: 4 }}>{n.characters.join(" · ") || ""}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Detail panel */}
      <div style={{ width: 280, flexShrink: 0, background: "var(--paper-card)", borderLeft: "1px solid var(--ink-100)", overflowY: "auto" }}>
        <div style={{ padding: "16px", borderBottom: "1px solid var(--ink-50)" }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-500)" }}>节点详情</div>
        </div>
        {sel ? <div style={{ padding: 16 }}>
          <div style={{ marginBottom: 12 }}><label style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-400)", display: "block", marginBottom: 4 }}>标题</label>
            <input value={sel.title} onChange={e => updateNode(sel.id, "title", e.target.value)} style={{ width: "100%", padding: "6px 10px", border: "1px solid var(--ink-200)", borderRadius: 6, fontSize: 13, outline: "none" }} /></div>
          <div style={{ marginBottom: 12 }}><label style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-400)", display: "block", marginBottom: 4 }}>章节号</label>
            <input type="number" value={sel.chapter ?? ""} onChange={e => updateNode(sel.id, "chapter", e.target.value ? +e.target.value : undefined)} style={{ width: 80, padding: "6px 10px", border: "1px solid var(--ink-200)", borderRadius: 6, fontSize: 13, outline: "none" }} /></div>
          <div style={{ marginBottom: 12 }}><label style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-400)", display: "block", marginBottom: 4 }}>摘要</label>
            <textarea value={sel.summary} onChange={e => updateNode(sel.id, "summary", e.target.value)} rows={3} style={{ width: "100%", padding: "6px 10px", border: "1px solid var(--ink-200)", borderRadius: 6, fontSize: 12, outline: "none", resize: "vertical" }} /></div>
          <div style={{ marginBottom: 12 }}><label style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-400)", display: "block", marginBottom: 4 }}>出场角色（逗号分隔）</label>
            <input value={sel.characters.join(", ")} onChange={e => updateNode(sel.id, "characters", e.target.value.split(",").map(s => s.trim()).filter(Boolean))} style={{ width: "100%", padding: "6px 10px", border: "1px solid var(--ink-200)", borderRadius: 6, fontSize: 12, outline: "none" }} /></div>
          <div style={{ marginBottom: 12 }}><label style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-400)", display: "block", marginBottom: 4 }}>颜色</label>
            <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>{COLORS.map(c => <div key={c} onClick={() => updateNode(sel.id, "color", c)} style={{ width: 24, height: 24, borderRadius: 12, background: c, cursor: "pointer", border: sel.color === c ? "3px solid var(--ink-800)" : "2px solid transparent" }} />)}</div></div>
          <button onClick={() => delNode(sel.id)} style={{ width: "100%", padding: "8px", background: "var(--accent-muted)", color: "var(--accent)", border: "none", borderRadius: 6, fontSize: 12, cursor: "pointer", marginTop: 12 }}>🗑️ 删除节点</button>

          {/* Edge list */}
          <div style={{ marginTop: 20 }}>
            <div style={{ fontSize: 11, fontWeight: 600, color: "var(--ink-400)", marginBottom: 6 }}>连线</div>
            {edges.filter(e => e.from === sel.id || e.to === sel.id).map((edge, i) => {
              const other = edge.from === sel.id ? nodes.find(n => n.id === edge.to) : nodes.find(n => n.id === edge.from);
              const edgeIdx = edges.indexOf(edge);
              return (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: 6, padding: "4px 0", borderBottom: "1px solid var(--ink-50)", fontSize: 11 }}>
                  <span style={{ color: "var(--ink-400)" }}>{edge.from === sel.id ? "→" : "←"}</span>
                  <span style={{ flex: 1 }}>{other?.title || "?"}</span>
                  <button onClick={() => { setEdges(prev => prev.filter((_, j) => j !== edgeIdx)); setDirty(true); }}
                    style={{ border: "none", background: "transparent", color: "var(--ink-300)", cursor: "pointer", fontSize: 10 }}>×</button>
                </div>
              );
            })}
          </div>
        </div> : <div style={{ padding: 20, textAlign: "center", color: "var(--ink-400)", fontSize: 13 }}>
          <p>点击节点查看详情</p>
          <p style={{ fontSize: 11, marginTop: 4 }}>拖拽移动 · 「连线」创建关系</p>
          <p style={{ fontSize: 11, marginTop: 4 }}>「从编辑器同步」可自动从章节大纲生成节点</p>
        </div>}
      </div>
    </div>
  );
}