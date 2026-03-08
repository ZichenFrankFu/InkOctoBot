import React, { useEffect, useState, useRef, useCallback, useMemo } from "react";
import { apiGet, apiPut } from "../api/client";
import type { StoryNode, StoryEdge, ChapterOutline, Volume } from "../api/types";

const uid = () => `n_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
const COLORS = ["#c0392b", "#2d8c5a", "#3b5998", "#d4a853", "#8e44ad", "#e67e22", "#1abc9c", "#e74c3c"];
const NODE_W = 200;
const NODE_H = 120;
const GAP_X = 80;
const HEADER_H = 56;
const TIMELINE_H = 64;

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
    apiGet<{ nodes: StoryNode[]; edges: StoryEdge[] }>(`/api/data/storyline?project_id=${pid}`)
      .then(data => {
        const nodeList = data.nodes || [];
        if (nodeList.length > 0) {
          setNodes(nodeList);
          setEdges(data.edges || []);
        } else {
          const n1: StoryNode = { id: uid(), title: "第一章·开篇", summary: "故事的起点", x: 60, y: 60, color: COLORS[0], chapter_num: 1, characters: [], week: 1, time: "第1天·清晨", location: "起始之地" };
          const n2: StoryNode = { id: uid(), title: "第二章·发展", summary: "矛盾初显", x: 60 + NODE_W + GAP_X, y: 60, color: COLORS[1], chapter_num: 2, characters: [], week: 1, time: "第2天·午后", location: "城镇" };
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
      apiPut(`/api/data/storyline`, { project_id: pid, nodes, edges }).catch(console.error);
      setDirty(false);
    }, 2000);
    return () => clearTimeout(t);
  }, [dirty, nodes, edges, loaded, projectId]);

  // --- Add node ---
  const addNode = useCallback(() => {
    const maxX = nodes.length > 0 ? Math.max(...nodes.map(n => n.x)) : 0;
    const maxWeek = nodes.length > 0 ? Math.max(...nodes.map(n => n.week || 1)) : 1;
    const n: StoryNode = {
      id: uid(),
      title: "新节点",
      summary: "",
      x: maxX + NODE_W + GAP_X,
      y: 60,
      color: COLORS[nodes.length % COLORS.length],
      characters: [],
      week: maxWeek,
      time: "",
      location: "",
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
      const data = await apiGet<{ volumes: Volume[] }>(`/api/data/editor?project_id=${pid}`);
      const chapters: ChapterOutline[] = (data.volumes || []).flatMap(v => v.chapters || []);
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
            characters: (ch as any).characters || [],
            week: Math.ceil(chNum / 3),
            time: (ch as any).time || "",
            location: (ch as any).location || "",
          });
        }
      });

      if (newNodes.length > 0) {
        setNodes(prev => [...prev, ...newNodes]);
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

  // --- Week timeline data ---
  const weeks = useMemo(() => {
    const weekMap = new Map<number, StoryNode[]>();
    nodes.forEach(n => {
      const w = n.week || 1;
      if (!weekMap.has(w)) weekMap.set(w, []);
      weekMap.get(w)!.push(n);
    });
    return Array.from(weekMap.entries())
      .sort((a, b) => a[0] - b[0])
      .map(([week, wNodes]) => ({
        week,
        count: wNodes.length,
        nodeIds: wNodes.map(n => n.id),
      }));
  }, [nodes]);

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
    <div className="page-full" style={{ flexDirection: "column", display: "flex", height: "100%", overflow: "hidden" }}>
      <div style={{ display: "flex", flex: 1, minHeight: 0, overflow: "hidden" }}>
        {/* ======== Canvas ======== */}
        <div ref={canvasRef} style={{ flex: 1, minWidth: 0, overflow: "auto", background: "var(--bg-app)", position: "relative" }}>
          {/* Toolbar */}
          <div
            className="panel-header"
            style={{
              position: "sticky",
              top: 0,
              zIndex: 10,
              height: HEADER_H,
              gap: 10,
              display: "flex",
              alignItems: "center",
              padding: "0 16px",
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
              ↑ 早 &middot; 时间线 &middot; 晚 ↓ &nbsp;|&nbsp; 横向并排 = 同时发生
            </span>
          </div>

          {/* Vertical Timeline */}
          <div style={{ padding: "24px 20px", minHeight: "100%" }}>
            {(() => {
              // Group nodes by week
              const weekGroups = new Map<number, StoryNode[]>();
              const sorted = [...nodes].sort((a, b) => (a.week || 1) - (b.week || 1) || (a.chapter_num || 0) - (b.chapter_num || 0));
              sorted.forEach(n => {
                const w = n.week || 1;
                if (!weekGroups.has(w)) weekGroups.set(w, []);
                weekGroups.get(w)!.push(n);
              });
              const weekEntries = Array.from(weekGroups.entries()).sort((a, b) => a[0] - b[0]);

              return weekEntries.map(([week, weekNodes], wi) => (
                <div key={week} style={{ position: "relative", marginBottom: 8 }}>
                  {/* Week label + vertical line */}
                  <div style={{ display: "flex", alignItems: "flex-start", gap: 16 }}>
                    {/* Timeline spine */}
                    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", width: 48, flexShrink: 0 }}>
                      <div style={{
                        width: 32, height: 32, borderRadius: "50%",
                        background: "var(--accent-subtle)", border: "2px solid var(--accent)",
                        display: "flex", alignItems: "center", justifyContent: "center",
                        fontSize: 11, fontWeight: 700, color: "var(--accent)", zIndex: 2,
                      }}>
                        W{week}
                      </div>
                      {wi < weekEntries.length - 1 && (
                        <div style={{ width: 2, flex: 1, minHeight: 20, background: "var(--border)", marginTop: 4 }} />
                      )}
                    </div>
                    {/* Nodes row (parallel events at same time) */}
                    <div style={{ display: "flex", gap: 12, flexWrap: "wrap", flex: 1, paddingBottom: 16 }}>
                      {weekNodes.map(n => (
                        <div
                          key={n.id}
                          onClick={() => setSelected(n.id)}
                          className={`timeline-node ${selected === n.id ? "selected" : ""}`}
                          style={{
                            position: "relative",
                            left: "auto", top: "auto",
                            width: 220,
                            minHeight: NODE_H,
                            borderTop: `4px solid ${n.color || "var(--accent)"}`,
                            cursor: "pointer",
                            flexShrink: 0,
                          }}
                        >
                          <div className="timeline-node-title">
                            {n.chapter_num != null && (
                              <span style={{ color: n.color || "var(--accent)", marginRight: 4 }}>Ch{n.chapter_num}</span>
                            )}
                            {n.title}
                          </div>
                          <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 4 }}>
                            {n.time && (
                              <span style={{ fontSize: 9, padding: "1px 6px", borderRadius: 8, background: "var(--accent-subtle)", color: "var(--accent)" }}>
                                {n.time}
                              </span>
                            )}
                            {n.location && (
                              <span style={{ fontSize: 9, padding: "1px 6px", borderRadius: 8, background: "var(--jade-subtle)", color: "var(--jade)" }}>
                                {n.location}
                              </span>
                            )}
                          </div>
                          <div className="timeline-node-meta" style={{ lineHeight: 1.4, height: 28, overflow: "hidden" }}>
                            {n.summary || "(空)"}
                          </div>
                          {(n.characters?.length || 0) > 0 && (
                            <div style={{ fontSize: 10, color: "var(--text-disabled)", marginTop: 2, display: "flex", gap: 3, flexWrap: "wrap" }}>
                              {n.characters!.map((ch, i) => (
                                <span key={i} style={{ background: "var(--purple-subtle)", color: "var(--purple)", padding: "0 5px", borderRadius: 6, fontSize: 9 }}>{ch}</span>
                              ))}
                            </div>
                          )}
                          {/* Connection indicators */}
                          {edges.filter(e => e.from === n.id).length > 0 && (
                            <div style={{ position: "absolute", bottom: -2, left: "50%", transform: "translateX(-50%)", fontSize: 10, color: "var(--text-disabled)" }}>↓</div>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              ));
            })()}
          </div>
        </div>

        {/* ======== Detail Panel ======== */}
        <div
          className="panel"
          style={{
            width: 280,
            minWidth: 280,
            flexShrink: 0,
            background: "var(--bg-surface)",
            borderLeft: "1px solid var(--border)",
            overflowY: "auto",
            height: "100%",
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
                  <label className="label">时间</label>
                  <input
                    className="input"
                    value={sel.time || ""}
                    onChange={e => updateNode(sel.id, "time", e.target.value)}
                    placeholder="例：第3天·黄昏"
                  />
                </div>
                <div className="field mb-12">
                  <label className="label">地点</label>
                  <input
                    className="input"
                    value={sel.location || ""}
                    onChange={e => updateNode(sel.id, "location", e.target.value)}
                    placeholder="例：云隐山·剑庐"
                  />
                </div>
                <div className="field mb-12">
                  <label className="label">所属周</label>
                  <input
                    className="input"
                    type="number"
                    value={sel.week ?? 1}
                    onChange={e => updateNode(sel.id, "week", e.target.value ? +e.target.value : 1)}
                    min={1}
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

      {/* ======== Week Timeline Bar (bottom) ======== */}
      <div
        style={{
          height: TIMELINE_H,
          flexShrink: 0,
          background: "var(--bg-surface)",
          borderTop: "1px solid var(--border)",
          display: "flex",
          alignItems: "center",
          gap: 0,
          padding: "0 16px",
          overflowX: "auto",
        }}
      >
        <span style={{ fontSize: 11, color: "var(--text-secondary)", marginRight: 12, whiteSpace: "nowrap", fontWeight: 600 }}>
          时间周
        </span>
        {weeks.length === 0 ? (
          <span style={{ fontSize: 11, color: "var(--text-disabled)" }}>暂无周数据，在节点详情中设置「所属周」</span>
        ) : (
          <div style={{ display: "flex", gap: 2, flex: 1, alignItems: "stretch", height: 40 }}>
            {weeks.map(({ week, count, nodeIds }) => {
              const isActive = sel ? nodeIds.includes(sel.id) : false;
              return (
                <div
                  key={week}
                  onClick={() => {
                    if (nodeIds.length > 0) setSelected(nodeIds[0]);
                  }}
                  style={{
                    flex: count,
                    minWidth: 48,
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "center",
                    justifyContent: "center",
                    background: isActive ? "var(--accent-subtle)" : "var(--bg-secondary)",
                    border: isActive ? "1px solid var(--accent)" : "1px solid var(--border-subtle)",
                    borderRadius: 6,
                    cursor: "pointer",
                    transition: "all 0.15s",
                  }}
                >
                  <span style={{ fontSize: 12, fontWeight: 700, color: isActive ? "var(--accent)" : "var(--text-primary)" }}>
                    W{week}
                  </span>
                  <span style={{ fontSize: 9, color: "var(--text-tertiary)" }}>
                    {count}节点
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
