import React, { useEffect, useState, useRef, useCallback, useMemo } from "react";
import { apiGet, apiPost, apiPut, apiDelete } from "../api/client";
import type { StoryNode, StoryEdge, ChapterOutline } from "../api/types";

const uid = () => `n_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
const COLORS = ["#c0392b", "#2d8c5a", "#3b5998", "#d4a853", "#8e44ad", "#e67e22", "#1abc9c", "#e74c3c"];
const NODE_W = 180;
const NODE_H = 100;
const GAP_X = 80;
const HEADER_H = 56;

export default function StorylinePage({ projectId }: { projectId: string }) {
  const [nodes, setNodes] = useState<StoryNode[]>([]);
  const [edges, setEdges] = useState<StoryEdge[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [dragging, setDragging] = useState<{ id: string; offX: number; offY: number } | null>(null);
  const [connecting, setConnecting] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const canvasRef = useRef<HTMLDivElement>(null);

  // --- Load ---
  useEffect(() => {
    const pid = projectId || "default";
    apiGet<{ items: StoryNode[] }>(`/api/data/projects/${pid}/storyline/nodes`)
      .then(data => {
        const nodeList = data.items || [];
        if (nodeList.length > 0) {
          setNodes(nodeList);
          // Load edges
          apiGet<{ items: StoryEdge[] }>(`/api/data/projects/${pid}/storyline/edges`)
            .then(ed => setEdges(ed.items || []))
            .catch(() => {});
        } else {
          const n1: StoryNode = { id: uid(), title: "第一章\u00B7开篇", summary: "故事的起点", x: 60, y: 60, color: COLORS[0], chapter_num: 1, characters: [] };
          const n2: StoryNode = { id: uid(), title: "第二章\u00B7发展", summary: "矛盾初显", x: 60 + NODE_W + GAP_X, y: 60, color: COLORS[1], chapter_num: 2, characters: [] };
          setNodes([n1, n2]);
          setEdges([{ id: uid(), from: n1.id, to: n2.id, label: "" }]);
        }
        setLoaded(true);
      })
      .catch(() => setLoaded(true));
  }, [projectId]);

  // --- Auto-save ---
  useEffect(() => {
    if (!loaded || !dirty) return;
    const t = setTimeout(() => {
      const pid = projectId || "default";
      apiPut(`/api/data/projects/${pid}/storyline/nodes`, { items: nodes }).catch(console.error);
      setDirty(false);
    }, 2000);
    return () => clearTimeout(t);
  }, [dirty, nodes, edges, loaded, projectId]);

  // --- Add node ---
  const addNode = useCallback(() => {
    const maxX = nodes.length > 0 ? Math.max(...nodes.map(n => n.x)) : 0;
    const n: StoryNode = {
      id: uid(),
      title: "新节点",
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

  // --- Auto layout ---
  const autoLayout = useCallback(() => {
    const sorted = [...nodes].sort((a, b) => (a.chapter_num || 999) - (b.chapter_num || 999));
    setNodes(sorted.map((n, i) => ({
      ...n,
      x: 60 + i * (NODE_W + GAP_X),
      y: 60,
    })));
    setDirty(true);
  }, [nodes]);

  // --- Sync from editor ---
  const syncFromEditor = useCallback(async () => {
    try {
      const pid = projectId || "default";
      const data = await apiGet<{ items: ChapterOutline[] }>(`/api/data/projects/${pid}/chapters`);
      const chapters = data.items || [];
      if (!chapters.length) return;

      const newNodes: StoryNode[] = [];
      chapters.forEach((ch, idx) => {
        const chNum = idx + 1;
        const existing = nodes.find(n => n.chapter_num === chNum);
        if (existing) {
          if (ch.synopsis && ch.synopsis !== existing.summary) {
            setNodes(prev => prev.map(n => n.id === existing.id ? { ...n, summary: ch.synopsis || "", title: ch.title } : n));
          }
        } else {
          newNodes.push({
            id: uid(),
            title: ch.title || `第${chNum}章`,
            summary: ch.synopsis || "",
            x: 60 + (chNum - 1) * (NODE_W + GAP_X),
            y: 60,
            color: COLORS[(chNum - 1) % COLORS.length],
            chapter_num: chNum,
            characters: [],
          });
        }
      });

      if (newNodes.length > 0) {
        setNodes(prev => [...prev, ...newNodes]);
        // Auto-connect sequential
        const allNodes = [...nodes, ...newNodes].sort((a, b) => (a.chapter_num || 0) - (b.chapter_num || 0));
        const newEdges: StoryEdge[] = [];
        for (let i = 1; i < allNodes.length; i++) {
          const from = allNodes[i - 1].id;
          const to = allNodes[i].id;
          if (!edges.some(e => e.from === from && e.to === to)) {
            newEdges.push({ id: uid(), from, to, label: "" });
          }
        }
        if (newEdges.length) setEdges(prev => [...prev, ...newEdges]);
        setDirty(true);
      }
    } catch (e) {
      console.error(e);
    }
  }, [nodes, edges, projectId]);

  // --- Delete node ---
  const delNode = (id: string) => {
    setNodes(prev => prev.filter(n => n.id !== id));
    setEdges(prev => prev.filter(e => e.from !== id && e.to !== id));
    if (selected === id) setSelected(null);
    setDirty(true);
  };

  // --- Update node ---
  const updateNode = (id: string, key: string, val: any) => {
    setNodes(prev => prev.map(n => n.id === id ? { ...n, [key]: val } : n));
    setDirty(true);
  };

  // --- Drag ---
  const onNodeMouseDown = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    if (connecting) {
      if (connecting !== id) {
        setEdges(prev => [...prev, { id: uid(), from: connecting, to: id, label: "" }]);
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
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [dragging]);

  // --- Computed ---
  const sel = useMemo(() => nodes.find(n => n.id === selected), [nodes, selected]);
  const canvasW = Math.max(1200, (nodes.length > 0 ? Math.max(...nodes.map(n => n.x)) : 0) + NODE_W + 200);
  const canvasH = Math.max(600, (nodes.length > 0 ? Math.max(...nodes.map(n => n.y)) : 0) + NODE_H + 200);

  // --- Edge paths (bezier) ---
  const edgePaths = useMemo(() => {
    return edges.map((edge, idx) => {
      const f = nodes.find(n => n.id === edge.from);
      const t = nodes.find(n => n.id === edge.to);
      if (!f || !t) return null;

      const x1 = f.x + NODE_W;
      const y1 = f.y + NODE_H / 2;
      const x2 = t.x;
      const y2 = t.y + NODE_H / 2;

      const dx = x2 - x1;
      let path: string;
      if (Math.abs(dx) > 50) {
        const cx = dx * 0.4;
        path = `M${x1},${y1} C${x1 + cx},${y1} ${x2 - cx},${y2} ${x2},${y2}`;
      } else {
        const arcX = Math.max(x1, x2) + 60;
        path = `M${x1},${y1} C${arcX},${y1} ${arcX},${y2} ${x2},${y2}`;
      }

      return { ...edge, path, mx: (x1 + x2) / 2, my: Math.min(y1, y2) - 12, idx };
    }).filter(Boolean) as Array<StoryEdge & { path: string; mx: number; my: number; idx: number }>;
  }, [edges, nodes]);

  if (!loaded) {
    return (
      <div className="loading" style={{ height: "100vh" }}>
        <div className="loading-spinner" />
        加载中...
      </div>
    );
  }

  return (
    <div className="page-full" style={{ flexDirection: "row" }}>
      {/* ======== Canvas ======== */}
      <div ref={canvasRef} style={{ flex: 1, overflow: "auto", background: "var(--bg-app)", position: "relative" }}>
        {/* Toolbar */}
        <div
          className="panel-header"
          style={{
            position: "sticky",
            top: 0,
            zIndex: 10,
            height: HEADER_H,
            gap: 10,
            background: "var(--bg-surface)",
            borderBottom: "1px solid var(--border)",
          }}
        >
          <h3>剧情线</h3>
          <div className="flex gap-8">
            <button className="btn-primary" style={{ fontSize: 12, padding: "5px 14px" }} onClick={addNode}>
              + 添加节点
            </button>
            <button
              className="btn"
              style={{
                fontSize: 12,
                padding: "5px 14px",
                background: connecting ? "var(--jade-subtle)" : undefined,
                color: connecting ? "var(--jade)" : undefined,
                borderColor: connecting ? "var(--jade)" : undefined,
              }}
              onClick={() => setConnecting(connecting ? null : (selected || null))}
              disabled={!selected}
            >
              {connecting ? "点击目标..." : "添加连线"}
            </button>
            <button className="btn" style={{ fontSize: 12, padding: "5px 14px" }} onClick={syncFromEditor}>
              同步大纲
            </button>
            <button className="btn" style={{ fontSize: 12, padding: "5px 14px" }} onClick={autoLayout}>
              自动布局
            </button>
          </div>
          <span className="text-xs text-muted" style={{ marginLeft: "auto" }}>
            ← 早 &middot; 时间线 &middot; 晚 → &nbsp;|&nbsp; 纵向并排 = 同时发生
          </span>
        </div>

        {/* SVG edges */}
        <svg
          style={{ position: "absolute", top: HEADER_H, left: 0, width: canvasW, height: canvasH, pointerEvents: "none", zIndex: 1 }}
        >
          <defs>
            <marker id="arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="8" markerHeight="8" orient="auto">
              <path d="M0,0 L10,5 L0,10 Z" fill="var(--text-tertiary)" />
            </marker>
          </defs>
          {edgePaths.map(ep => (
            <g key={ep.idx}>
              <path d={ep.path} fill="none" stroke="var(--text-tertiary)" strokeWidth={2} markerEnd="url(#arrow)" opacity={0.6} />
              {ep.label && (
                <text x={ep.mx} y={ep.my} textAnchor="middle" fontSize={10} fill="var(--text-tertiary)">
                  {ep.label}
                </text>
              )}
            </g>
          ))}
        </svg>

        {/* Nodes */}
        <div style={{ position: "relative", width: canvasW, height: canvasH, zIndex: 2 }}>
          {nodes.map(n => (
            <div
              key={n.id}
              className={`timeline-node ${selected === n.id ? "selected" : ""}`}
              onMouseDown={e => onNodeMouseDown(n.id, e)}
              style={{
                left: n.x,
                top: n.y,
                width: NODE_W,
                minHeight: NODE_H,
                borderTop: `4px solid ${n.color || "var(--accent)"}`,
                cursor: dragging?.id === n.id ? "grabbing" : connecting ? "crosshair" : "grab",
                transition: dragging ? "none" : "box-shadow 0.15s",
              }}
            >
              <div className="timeline-node-title">
                {n.chapter_num != null && (
                  <span style={{ color: n.color || "var(--accent)", marginRight: 4 }}>Ch{n.chapter_num}</span>
                )}
                {n.title}
              </div>
              <div className="timeline-node-meta" style={{ lineHeight: 1.4, height: 32, overflow: "hidden" }}>
                {n.summary || "(空)"}
              </div>
              {(n.characters?.length || 0) > 0 && (
                <div style={{ fontSize: 10, color: "var(--text-disabled)", marginTop: 4 }}>
                  {n.characters!.join(" \u00B7 ")}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* ======== Detail Panel ======== */}
      <div
        className="panel"
        style={{
          width: 280,
          flexShrink: 0,
          background: "var(--bg-surface)",
          borderLeft: "1px solid var(--border)",
          overflowY: "auto",
        }}
      >
        <div className="panel-header">
          <h3>节点详情</h3>
        </div>
        <div className="panel-body" style={{ padding: 16 }}>
          {sel ? (
            <>
              <div className="field mb-12">
                <label className="label">标题</label>
                <input
                  className="input"
                  value={sel.title}
                  onChange={e => updateNode(sel.id, "title", e.target.value)}
                />
              </div>
              <div className="field mb-12">
                <label className="label">章节号</label>
                <input
                  className="input"
                  type="number"
                  value={sel.chapter_num ?? ""}
                  onChange={e => updateNode(sel.id, "chapter_num", e.target.value ? +e.target.value : undefined)}
                  style={{ width: 100 }}
                />
              </div>
              <div className="field mb-12">
                <label className="label">摘要</label>
                <textarea
                  className="input"
                  value={sel.summary || ""}
                  onChange={e => updateNode(sel.id, "summary", e.target.value)}
                  rows={3}
                />
              </div>
              <div className="field mb-12">
                <label className="label">出场角色（逗号分隔）</label>
                <input
                  className="input"
                  value={(sel.characters || []).join(", ")}
                  onChange={e => updateNode(sel.id, "characters", e.target.value.split(",").map(s => s.trim()).filter(Boolean))}
                />
              </div>
              <div className="field mb-12">
                <label className="label">颜色</label>
                <div className="flex gap-4" style={{ flexWrap: "wrap" }}>
                  {COLORS.map(c => (
                    <div
                      key={c}
                      onClick={() => updateNode(sel.id, "color", c)}
                      style={{
                        width: 24,
                        height: 24,
                        borderRadius: 12,
                        background: c,
                        cursor: "pointer",
                        border: sel.color === c ? "3px solid var(--text-primary)" : "2px solid transparent",
                        transition: "border-color 0.15s",
                      }}
                    />
                  ))}
                </div>
              </div>

              <button
                className="btn w-full mt-12"
                style={{ justifyContent: "center", color: "var(--error)" }}
                onClick={() => delNode(sel.id)}
              >
                删除节点
              </button>

              {/* Edge list */}
              <div className="mt-24">
                <div className="label mb-8">连线</div>
                {edges.filter(e => e.from === sel.id || e.to === sel.id).map((edge) => {
                  const other = edge.from === sel.id
                    ? nodes.find(n => n.id === edge.to)
                    : nodes.find(n => n.id === edge.from);
                  return (
                    <div
                      key={edge.id}
                      className="flex items-center gap-6"
                      style={{ padding: "4px 0", borderBottom: "1px solid var(--border-subtle)", fontSize: 12 }}
                    >
                      <span className="text-muted">{edge.from === sel.id ? "\u2192" : "\u2190"}</span>
                      <span style={{ flex: 1, color: "var(--text-secondary)" }}>{other?.title || "?"}</span>
                      <button
                        className="btn-icon"
                        style={{ width: 18, height: 18, fontSize: 10 }}
                        onClick={() => {
                          setEdges(prev => prev.filter(e => e.id !== edge.id));
                          setDirty(true);
                        }}
                      >
                        &times;
                      </button>
                    </div>
                  );
                })}
              </div>
            </>
          ) : (
            <div className="empty-state" style={{ padding: "24px 0" }}>
              <p>点击节点查看详情</p>
              <p className="text-xs mt-4">拖拽移动 &middot; 「添加连线」创建关系</p>
              <p className="text-xs mt-4">「同步大纲」可自动从章节大纲生成节点</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
