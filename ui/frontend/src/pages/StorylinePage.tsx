import React, { useEffect, useState, useRef, useCallback, useMemo } from "react";
import { apiGet, apiPut } from "../api/client";
import { useToast } from "../components/shared/Toast";
import type { StoryNode, StoryEdge, ChapterOutline, Volume, Character } from "../api/types";

const uid = () => `n_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
const COLORS = ["#c0392b", "#2d8c5a", "#3b5998", "#d4a853", "#8e44ad", "#e67e22", "#1abc9c", "#e74c3c"];
const NODE_W = 220;
const NODE_H = 120;
const HEADER_H = 56;
const TIMELINE_H = 64;

// ── Lightweight types for storyland data (主线/支线 + 伏笔) shown in the
//    top summary strip and the per-chapter chips. We only need the
//    display-side fields; backend payloads carry more.
interface Thread {
  thread_id: string;
  name: string;
  description: string;
  status: string;
  thread_type: "main" | "sub";
  start_chapter: number;
  last_advanced_chapter: number | null;
}
interface Hook {
  id: string;
  title?: string;
  content: string;
  status: string;
  scale: string;
  origin_chapter: number | null;
  expected_payoff_chapter: number | null;
}

export default function StorylinePage({ projectId, onNavigate }: { projectId: string; onNavigate?: (page: string) => void }) {
  const { toast } = useToast();
  const [nodes, setNodes] = useState<StoryNode[]>([]);
  const [edges, setEdges] = useState<StoryEdge[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const canvasRef = useRef<HTMLDivElement>(null);

  // Extra data feeding the content layer of the timeline (visuals unchanged):
  //  · characters[] → CharacterSelector options (角色管理 ↔ 出场角色)
  //  · threads[] / hooks[] → 顶部 故事线/伏笔 概览 + 行内 chip 提示
  //  · chapterIndex / chapterTitles → 章节大纲合并写回 editor
  const [characters, setCharacters] = useState<Character[]>([]);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [hooks, setHooks] = useState<Hook[]>([]);
  const [chapterTitles, setChapterTitles] = useState<Map<number, string>>(new Map());

  useEffect(() => {
    const pid = projectId || "default";
    apiGet<{ items: Character[] }>(`/api/data/characters?project_id=${pid}`)
      .then(r => setCharacters(r.items || []))
      .catch(() => setCharacters([]));
    apiGet<{ items: Thread[] }>(`/api/storyland/subplots?project_id=${pid}`)
      .then(r => setThreads(r.items || []))
      .catch(() => setThreads([]));
    apiGet<{ items: Hook[] }>(`/api/data/foreshadowing/${pid}`)
      .then(r => setHooks(r.items || []))
      .catch(() => setHooks([]));
    apiGet<{ volumes: Volume[] }>(`/api/data/editor?project_id=${pid}`)
      .then(r => {
        const titles = new Map<number, string>();
        let i = 0;
        (r.volumes || []).forEach(v => (v.chapters || []).forEach(c => {
          i += 1;
          if (c.title) titles.set(i, c.title);
        }));
        setChapterTitles(titles);
      })
      .catch(() => setChapterTitles(new Map()));
  }, [projectId]);

  // Warn before leaving with unsaved changes
  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

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
          const n1: StoryNode = { id: uid(), title: "开篇·主角登场", summary: "故事的起点", x: 0, y: 0, color: COLORS[0], chapter_num: 1, characters: [], week: 1, time: "第1天·清晨", location: "起始之地" };
          const n2: StoryNode = { id: uid(), title: "矛盾初现", summary: "矛盾初显", x: 0, y: 0, color: COLORS[1], chapter_num: 2, characters: [], week: 1, time: "第2天·午后", location: "城镇" };
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
      apiPut(`/api/data/storyline`, { project_id: pid, nodes, edges }).catch((e: any) => toast(e.message || "操作失败", "error"));
      setDirty(false);
    }, 2000);
    return () => clearTimeout(t);
  }, [dirty, nodes, edges, loaded, projectId]);

  // --- Add 情节 ---
  // Global toolbar action: drop a new 情节 into the latest chapter.
  const addNode = useCallback(() => {
    const targetChap = nodes.length > 0
      ? Math.max(...nodes.map(n => n.chapter_num || 0))
      : 1;
    addEpisodeToChapterImpl(targetChap);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes]);

  /** Append a new 情节 card to a specific chapter row. Used by the per-
   *  row 「+ 添加情节」 button and the global 「+ 添加情节」 toolbar. */
  const addEpisodeToChapterImpl = (chap_num: number) => {
    const peers = nodes.filter(n => (n.chapter_num || 0) === chap_num);
    const lastX = peers.length > 0
      ? Math.max(...peers.map(n => n.x || 0))
      : 0;
    const n: StoryNode = {
      id: uid(),
      title: "新情节",
      summary: "",
      x: lastX + 240,           // 后追加，排在末尾
      y: 0,
      color: COLORS[peers.length % COLORS.length],
      characters: [],
      week: 1,
      time: "",
      location: "",
      chapter_num: chap_num,
    };
    setNodes(prev => [...prev, n]);
    setSelected(n.id);
    setDirty(true);
  };
  const addEpisodeToChapter = useCallback(addEpisodeToChapterImpl, [nodes]);

  // --- Sync one node's 大纲 back to the matching editor chapter ---
  const syncOutlineToEditor = useCallback(async (node: StoryNode) => {
    if (node?.chapter_num == null) return;
    try {
      const pid = projectId || "default";
      const data = await apiGet<{ volumes: Volume[] }>(`/api/data/editor?project_id=${pid}`);
      const volumes = data.volumes || [];
      let idx = 0;
      let changed = false;
      const next = volumes.map(v => ({
        ...v,
        chapters: (v.chapters || []).map(c => {
          idx += 1;
          if (idx === node.chapter_num) {
            if ((c.synopsis || "") !== (node.summary || "")) changed = true;
            return { ...c, synopsis: node.summary || "" };
          }
          return c;
        }),
      }));
      if (changed) await apiPut(`/api/data/editor`, { project_id: pid, volumes: next });
    } catch (e: any) {
      toast(e?.message || "同步到编辑器失败", "error");
    }
  }, [projectId, toast]);

  // --- Pull editor → storyline for chapters where storyline has nothing.
  //     Used on mount so the page shows existing editor content even if the
  //     user has never created 情节 cards. Storyline rows for chapters the
  //     user already populated are NEVER overwritten — the 同步大纲 button
  //     is for two-way reconciliation; this is only the missing-fills pass.
  const pullMissingFromEditor = useCallback(async () => {
    try {
      const pid = projectId || "default";
      const data = await apiGet<{ volumes: Volume[] }>(`/api/data/editor?project_id=${pid}`);
      const chapters: ChapterOutline[] = (data.volumes || []).flatMap(v => v.chapters || []);
      if (!chapters.length) return;

      setNodes(prev => {
        const merged = [...prev];
        let changed = false;
        chapters.forEach((ch, idx) => {
          const chNum = idx + 1;
          const has = merged.some(n => (n.chapter_num || 0) === chNum);
          if (!has && (ch.synopsis || "").trim()) {
            merged.push({
              id: uid(),
              title: ch.title || `第${chNum}章`,
              summary: ch.synopsis || "",
              x: 0, y: 0,
              color: COLORS[(chNum - 1) % COLORS.length],
              chapter_num: chNum,
              characters: ch.characters || [],
              time: ch.time || "",
              location: ch.location || "",
            });
            changed = true;
          }
        });
        if (!changed) return prev;
        return merged.sort((a, b) =>
          (a.chapter_num || 0) - (b.chapter_num || 0)
          || (a.x || 0) - (b.x || 0)
        );
      });
    } catch (e) {
      console.error(e);
    }
  }, [projectId]);

  // --- 同步大纲：bidirectional reconciliation between 故事线 and 编辑器 ──
  //     · 故事线 is the source of truth for chapters that have 情节 cards
  //       → merged outline overwrites the editor chapter synopsis.
  //     · 编辑器 wins for chapters where 故事线 has nothing → its synopsis
  //       gets pulled in as a new 情节 card.
  //     This makes the toolbar 「同步大纲」 a single, idempotent operation
  //     that converges both surfaces to the same state.
  const syncOutlines = useCallback(async () => {
    try {
      const pid = projectId || "default";
      const data = await apiGet<{ volumes: Volume[] }>(`/api/data/editor?project_id=${pid}`);
      const volumes = data.volumes || [];

      // Flatten editor chapters with their global index (= chapter_num).
      const editorChapters: { num: number; title: string; synopsis: string; chap: ChapterOutline }[] = [];
      {
        let i = 0;
        volumes.forEach(v => (v.chapters || []).forEach(c => {
          i += 1;
          editorChapters.push({
            num: i,
            title: c.title || "",
            synopsis: c.synopsis || "",
            chap: c,
          });
        }));
      }
      if (!editorChapters.length) {
        toast("编辑器还没有章节", "info");
        return;
      }

      // Build the next storyline state synchronously (pull missing in).
      const merged: StoryNode[] = [...nodes];
      let pulledIn = 0;
      editorChapters.forEach(ec => {
        const has = merged.some(n => (n.chapter_num || 0) === ec.num);
        if (!has && ec.synopsis.trim()) {
          merged.push({
            id: uid(),
            title: ec.title || `第${ec.num}章`,
            summary: ec.synopsis,
            x: 0, y: 0,
            color: COLORS[(ec.num - 1) % COLORS.length],
            chapter_num: ec.num,
            characters: ec.chap.characters || [],
            time: ec.chap.time || "",
            location: ec.chap.location || "",
          });
          pulledIn += 1;
        }
      });

      // Per-chapter merged outline from the (possibly enriched) storyline.
      const outlineByChap = new Map<number, string>();
      const grouped = new Map<number, StoryNode[]>();
      merged.forEach(n => {
        const k = n.chapter_num || 0;
        if (!grouped.has(k)) grouped.set(k, []);
        grouped.get(k)!.push(n);
      });
      grouped.forEach((evs, k) => {
        const sorted = evs.sort((a, b) => (a.x || 0) - (b.x || 0));
        const out = sorted.map((n, j) => {
          const head = (n.title || "").trim() || `事件 ${j + 1}`;
          const body = (n.summary || "").trim();
          return body ? `${j + 1}. ${head}：${body}` : `${j + 1}. ${head}`;
        }).join("\n");
        if (out) outlineByChap.set(k, out);
      });

      // Push storyline → editor for chapters that have events.
      let pushedOut = 0;
      let idx = 0;
      const nextVolumes = volumes.map(v => ({
        ...v,
        chapters: (v.chapters || []).map(c => {
          idx += 1;
          const out = outlineByChap.get(idx);
          if (out && out !== c.synopsis) {
            pushedOut += 1;
            return { ...c, synopsis: out };
          }
          return c;
        }),
      }));

      setNodes(merged.sort((a, b) =>
        (a.chapter_num || 0) - (b.chapter_num || 0)
        || (a.x || 0) - (b.x || 0)
      ));
      if (pulledIn > 0) setDirty(true);
      if (pushedOut > 0) {
        await apiPut(`/api/data/editor`, { project_id: pid, volumes: nextVolumes });
      }

      if (pulledIn === 0 && pushedOut === 0) {
        toast("故事线与编辑器章节大纲已同步", "info");
      } else {
        const parts: string[] = [];
        if (pushedOut > 0) parts.push(`${pushedOut} 章写入编辑器`);
        if (pulledIn > 0) parts.push(`${pulledIn} 章拉入故事线`);
        toast(`同步完成：${parts.join("，")}`, "success");
      }
    } catch (e: any) {
      toast(e?.message || "同步大纲失败", "error");
    }
  }, [projectId, nodes, toast]);

  // --- Auto-pull missing chapters from editor on first load ---
  const syncedOnMount = useRef(false);
  useEffect(() => {
    if (loaded && !syncedOnMount.current) {
      syncedOnMount.current = true;
      pullMissingFromEditor();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [loaded]);

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

  // --- Drag with row-snap ---
  // Each chapter row exposes its bounds via a ref keyed by chap_num so the
  // drop-target detection on mouseUp doesn't need elementFromPoint walking.
  // A node lifted from its row floats with the mouse (rendered as a fixed
  // preview), and on release its `chapter_num` snaps to the row under the
  // cursor; `x` is overwritten with the cursor's horizontal position
  // relative to that row, so re-ordering inside a row is also a drag.
  const rowRefs = useRef<Map<number, HTMLDivElement | null>>(new Map());
  const [dragOverChapter, setDragOverChapter] = useState<number | null>(null);
  const [dragPreview, setDragPreview] = useState<{
    id: string; clientX: number; clientY: number;
    offX: number; offY: number;
  } | null>(null);

  const onNodeMouseDown = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    const target = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setDragPreview({
      id,
      clientX: e.clientX, clientY: e.clientY,
      offX: e.clientX - target.left,
      offY: e.clientY - target.top,
    });
    setSelected(id);
  };

  useEffect(() => {
    if (!dragPreview) return;
    /** Resolve which chapter row the pointer is currently over (if any).
     *  We walk the row-ref map and compare the pointer's clientY against
     *  each row's bounding rect — O(rows) but rows are at most a couple
     *  dozen, so the cost is invisible. */
    const chapterAtPoint = (clientX: number, clientY: number): number | null => {
      let hit: number | null = null;
      for (const [chap_num, el] of rowRefs.current) {
        if (!el) continue;
        const r = el.getBoundingClientRect();
        if (clientY >= r.top && clientY <= r.bottom
            && clientX >= r.left && clientX <= r.right) {
          hit = chap_num;
          break;
        }
      }
      return hit;
    };
    const onMove = (e: MouseEvent) => {
      setDragPreview(prev => prev && { ...prev, clientX: e.clientX, clientY: e.clientY });
      setDragOverChapter(chapterAtPoint(e.clientX, e.clientY));
    };
    const onUp = (e: MouseEvent) => {
      const target = chapterAtPoint(e.clientX, e.clientY);
      if (target !== null) {
        const row = rowRefs.current.get(target);
        const xInRow = row
          ? Math.max(0, e.clientX - row.getBoundingClientRect().left)
          : 0;
        setNodes(prev => prev.map(n => n.id === dragPreview.id
          ? { ...n, chapter_num: target, x: xInRow, y: 0 }
          : n));
        setDirty(true);
      }
      setDragPreview(null);
      setDragOverChapter(null);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [dragPreview]);

  // --- Computed ---
  const sel = useMemo(() => nodes.find(n => n.id === selected), [nodes, selected]);

  // --- Bottom 故事中时间 strip: one chip per 情节 that has a non-empty
  //     `time` value (in-story timestamp), ordered along the reading path
  //     (chapter_num → x). Clicking a chip selects that 情节. Empty when
  //     no card has filled in 故事中时间.
  const episodeTimePoints = useMemo(() => {
    const sorted = [...nodes].sort((a, b) =>
      (a.chapter_num || 0) - (b.chapter_num || 0)
      || (a.x || 0) - (b.x || 0)
    );
    return sorted.filter(n => (n.time || "").trim().length > 0);
  }, [nodes]);

  // --- Merged chapter outline: for each chapter, concat the events'
  //     summaries into a numbered outline. Surfaced under each chapter
  //     row in the canvas and exposed via 「写回章节大纲」.
  const mergedOutlineForChapter = useCallback((chap_num: number): string => {
    const evs = nodes
      .filter(n => (n.chapter_num || 0) === chap_num)
      .sort((a, b) => (a.x || 0) - (b.x || 0));
    if (evs.length === 0) return "";
    return evs.map((n, i) => {
      const head = (n.title || "").trim() || `事件 ${i + 1}`;
      const body = (n.summary || "").trim();
      return body ? `${i + 1}. ${head}：${body}` : `${i + 1}. ${head}`;
    }).join("\n");
  }, [nodes]);

  // --- Write the merged outline of a chapter back to the editor's
  //     chapter.synopsis. Walks the volumes/chapters tree by chap_num so
  //     it stays correct under multi-volume projects.
  const writeChapterOutlineToEditor = useCallback(async (chap_num: number) => {
    try {
      const pid = projectId || "default";
      const merged = mergedOutlineForChapter(chap_num);
      if (!merged) {
        toast("该章节暂无事件，无内容可写回", "info");
        return;
      }
      const data = await apiGet<{ volumes: Volume[] }>(`/api/data/editor?project_id=${pid}`);
      let idx = 0;
      const next = (data.volumes || []).map(v => ({
        ...v,
        chapters: (v.chapters || []).map(c => {
          idx += 1;
          return idx === chap_num ? { ...c, synopsis: merged } : c;
        }),
      }));
      await apiPut(`/api/data/editor`, { project_id: pid, volumes: next });
      toast(`已写回 第${chap_num}章 大纲`, "success");
    } catch (e: any) {
      toast(e?.message || "写回章节大纲失败", "error");
    }
  }, [projectId, mergedOutlineForChapter, toast]);

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
            <div className="flex gap-8" style={{ marginLeft: "auto" }}>
              <button className="btn-primary" style={{ fontSize: 12, padding: "5px 14px" }} onClick={addNode}>
                + 添加情节
              </button>
              <button
                className="btn"
                style={{ fontSize: 12, padding: "5px 14px" }}
                onClick={syncOutlines}
                title="以故事线为准写入编辑器章节大纲；编辑器有而故事线没有的章节自动拉入故事线"
              >
                同步大纲
              </button>
            </div>
          </div>

          {/* Storyline / foreshadowing summary strip — read-only links to
              故事中世界 so the user can see主线/支线/伏笔 概览 而不切页 */}
          <ThreadSummaryStrip threads={threads} hooks={hooks}
            onOpenStoryland={() => onNavigate?.("storyland")} />

          {/* Vertical Timeline — one row per chapter (chapter_num).
              LEFT column of each row is the chapter spine card: chapter
              label + 相关主线/支线/伏笔 chips + merged outline (合并自
              所有情节卡) + 写回章节大纲 一键回写 editor synopsis.
              RIGHT column hosts the horizontally laid-out 情节 cards.
              Cards are draggable — release on a different row to re-assign
              their chapter_num (auto snap). */}
          <div style={{ padding: "20px 16px", minHeight: "100%", display: "flex", flexDirection: "column", gap: 14 }}>
            {(() => {
              // Group nodes by chapter_num so each row = one chapter. Sort
              // within a row by stored x so the user's drag order is honoured.
              const chapGroups = new Map<number, StoryNode[]>();
              const sorted = [...nodes].sort((a, b) =>
                (a.chapter_num || 0) - (b.chapter_num || 0)
                || (a.x || 0) - (b.x || 0),
              );
              sorted.forEach(n => {
                const key = n.chapter_num || 0;
                if (!chapGroups.has(key)) chapGroups.set(key, []);
                chapGroups.get(key)!.push(n);
              });
              // Ensure every numbered chapter in the editor has a row, even
              // if it carries no 情节 yet — gives the user an explicit drop
              // target instead of "this chapter doesn't exist on the page".
              const allChapNums = new Set<number>([...chapGroups.keys()]);
              chapterTitles.forEach((_, n) => allChapNums.add(n));
              const orderedChapNums = Array.from(allChapNums).sort((a, b) => a - b);

              return orderedChapNums.map((chap_num) => {
                const chapNodes = chapGroups.get(chap_num) || [];
                const chapterTitle = chapterTitles.get(chap_num) || "";
                const merged = mergedOutlineForChapter(chap_num);
                const relatedThreads = threads.filter(t =>
                  t.start_chapter <= chap_num
                  && (t.last_advanced_chapter == null || t.last_advanced_chapter >= chap_num),
                );
                const relatedHooks = hooks.filter(h =>
                  (h.origin_chapter || 0) <= chap_num
                  && (h.expected_payoff_chapter == null || h.expected_payoff_chapter >= chap_num)
                  && !["resolved", "abandoned"].includes(h.status),
                );
                const labelTop = chap_num === 0 ? "未指定" : `第 ${chap_num} 章`;
                const isDropTarget = dragOverChapter === chap_num;
                return (
                  <div
                    key={chap_num}
                    ref={(el) => { rowRefs.current.set(chap_num, el); }}
                    data-chapter-num={chap_num}
                    style={{
                      display: "flex", gap: 12, alignItems: "stretch",
                      background: isDropTarget ? "var(--accent-subtle)" : "var(--bg-surface)",
                      border: `1px solid ${isDropTarget ? "var(--accent)" : "var(--border)"}`,
                      borderRadius: 10,
                      transition: "background 0.12s, border-color 0.12s",
                      overflow: "hidden",
                    }}>
                    {/* ── LEFT: chapter spine card ── */}
                    <div style={{
                      width: "clamp(220px, 26%, 320px)", flexShrink: 0,
                      padding: "14px 14px 12px",
                      background: "var(--bg-surface-2)",
                      borderRight: "1px solid var(--border)",
                      display: "flex", flexDirection: "column", gap: 8,
                      minWidth: 0,
                    }}>
                      <div className="flex items-center gap-8">
                        <span style={{
                          padding: "3px 10px", borderRadius: 12,
                          background: "var(--accent)", color: "#fff",
                          fontSize: 11, fontWeight: 700,
                        }}>
                          {labelTop}
                        </span>
                        {chapterTitle && (
                          <span className="font-serif" style={{
                            fontSize: 13, fontWeight: 600, color: "var(--text-primary)",
                            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                            flex: 1,
                          }} title={chapterTitle}>
                            {chapterTitle}
                          </span>
                        )}
                      </div>
                      {(relatedThreads.length > 0 || relatedHooks.length > 0) && (
                        <div className="flex gap-4" style={{ flexWrap: "wrap" }}>
                          {relatedThreads.map(t => (
                            <span key={t.thread_id} className="tag" title={`${t.thread_type === "main" ? "主线" : "支线"}：${t.description}`}
                              style={{
                                fontSize: 10, padding: "1px 8px",
                                background: t.thread_type === "main" ? "var(--accent-subtle)" : undefined,
                                color: t.thread_type === "main" ? "var(--accent)" : undefined,
                                borderColor: t.thread_type === "main" ? "var(--accent)" : undefined,
                              }}>
                              {t.thread_type === "main" ? "主" : "支"} · {t.name}
                            </span>
                          ))}
                          {relatedHooks.map(h => (
                            <span key={h.id} className="tag" title={h.content}
                              style={{
                                fontSize: 10, padding: "1px 8px",
                                background: "var(--gold-subtle)", color: "var(--gold)",
                                borderColor: "var(--gold)",
                              }}>
                              伏 · {(h.title || h.content || "").slice(0, 12)}
                            </span>
                          ))}
                        </div>
                      )}
                      <div className="flex items-center justify-between" style={{ marginTop: 2 }}>
                        <span className="text-xs text-muted" style={{ fontSize: 10 }}>
                          合并章节大纲 · {chapNodes.length} 情节
                        </span>
                        <button
                          className="btn"
                          style={{ fontSize: 10, padding: "1px 8px" }}
                          onClick={() => writeChapterOutlineToEditor(chap_num)}
                          disabled={!merged}
                          title="把这条合并大纲写回 编辑器 → 章节 synopsis">
                          写回章节大纲
                        </button>
                      </div>
                      <div style={{
                        flex: 1, minHeight: 80, maxHeight: 220, overflow: "auto",
                        background: "var(--bg-surface)",
                        border: "1px solid var(--border)", borderRadius: 6,
                        padding: "8px 10px",
                        fontSize: 11, lineHeight: 1.7, color: "var(--text-secondary)",
                        whiteSpace: "pre-wrap", fontFamily: "var(--font-mono)",
                      }}>
                        {merged || (
                          <span className="text-xs text-muted">本章暂无情节。</span>
                        )}
                      </div>
                    </div>

                    {/* ── RIGHT: 情节 cards ── */}
                    <div style={{
                      flex: 1, minWidth: 0, minHeight: 168, padding: "14px 14px 10px",
                      display: "flex", gap: 10, overflowX: "auto", alignItems: "flex-start",
                    }}>
                      {isDropTarget && chapNodes.length === 0 && (
                        <div className="text-xs" style={{
                          padding: "20px 12px", lineHeight: 1.6,
                          alignSelf: "center", color: "var(--accent)",
                        }}>
                          释放即可归属到本章
                        </div>
                      )}
                      {chapNodes.map(n => {
                        const isDragging = dragPreview?.id === n.id;
                        const thread = n.thread_id ? threads.find(t => t.thread_id === n.thread_id) : undefined;
                        const hook = n.hook_id ? hooks.find(h => h.id === n.hook_id) : undefined;
                        return (
                          <div
                            key={n.id}
                            onMouseDown={(e) => onNodeMouseDown(n.id, e)}
                            onClick={() => setSelected(n.id)}
                            className={`timeline-node ${selected === n.id ? "selected" : ""}`}
                            style={{
                              position: "relative",
                              left: "auto", top: "auto",
                              width: NODE_W, minHeight: NODE_H,
                              borderTop: `4px solid ${n.color || "var(--accent)"}`,
                              cursor: "grab",
                              flexShrink: 0,
                              opacity: isDragging ? 0.35 : 1,
                              transition: "opacity 0.12s",
                            }}
                          >
                            <div className="timeline-node-title">{n.title}</div>
                            <div className="flex gap-4" style={{ flexWrap: "wrap", marginBottom: 4 }}>
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
                            {(thread || hook) && (
                              <div className="flex gap-3" style={{ flexWrap: "wrap", marginBottom: 4 }}>
                                {thread && (
                                  <span style={{ fontSize: 9, padding: "1px 6px", borderRadius: 8, background: "var(--accent-subtle)", color: "var(--accent)" }}
                                    title={thread.description}>
                                    {thread.thread_type === "main" ? "主线" : "支线"} · {thread.name}
                                  </span>
                                )}
                                {hook && (
                                  <span style={{ fontSize: 9, padding: "1px 6px", borderRadius: 8, background: "var(--gold-subtle)", color: "var(--gold)" }}
                                    title={hook.content}>
                                    伏笔 · {(hook.title || hook.content || "").slice(0, 10)}
                                  </span>
                                )}
                              </div>
                            )}
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
                          </div>
                        );
                      })}
                      {/* Per-row 「+ 添加情节」 button — same footprint as a 情节 card */}
                      <button
                        onClick={() => addEpisodeToChapter(chap_num)}
                        style={{
                          width: NODE_W, minHeight: NODE_H,
                          flexShrink: 0,
                          border: "1.5px dashed var(--border)", borderRadius: 8,
                          background: "transparent", color: "var(--text-tertiary)",
                          cursor: "pointer", fontSize: 13, fontWeight: 500,
                          display: "flex", alignItems: "center", justifyContent: "center",
                          transition: "border-color 0.15s, color 0.15s, background 0.15s",
                        }}
                        onMouseEnter={(e) => {
                          e.currentTarget.style.borderColor = "var(--accent)";
                          e.currentTarget.style.color = "var(--accent)";
                          e.currentTarget.style.background = "var(--accent-subtle)";
                        }}
                        onMouseLeave={(e) => {
                          e.currentTarget.style.borderColor = "var(--border)";
                          e.currentTarget.style.color = "var(--text-tertiary)";
                          e.currentTarget.style.background = "transparent";
                        }}
                        title={`在 ${labelTop} 添加一张情节卡`}>
                        + 添加情节
                      </button>
                    </div>
                  </div>
                );
              });
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
            <h3>情节详情</h3>
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
                  <label className="label">故事中时间</label>
                  <input
                    className="input"
                    value={sel.time || ""}
                    onChange={e => updateNode(sel.id, "time", e.target.value)}
                    placeholder="例：第一纪元 121年·秋"
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
                  <label className="label">大纲</label>
                  <textarea
                    className="input"
                    value={sel.summary || ""}
                    onChange={e => updateNode(sel.id, "summary", e.target.value)}
                    onBlur={() => syncOutlineToEditor(sel)}
                    placeholder="本章剧情大纲（失焦后自动同步到编辑器对应章节）"
                    rows={3}
                  />
                </div>
                <div className="field mb-12">
                  <CharacterSelector
                    label="出场角色"
                    value={sel.characters || []}
                    options={characters}
                    onChange={(v) => updateNode(sel.id, "characters", v)}
                  />
                </div>
                <div className="field mb-12">
                  <label className="label">所属故事线</label>
                  <select className="select" value={sel.thread_id || ""}
                    onChange={e => updateNode(sel.id, "thread_id", e.target.value || undefined)}
                    style={{ width: "100%", fontSize: 12 }}>
                    <option value="">未指定</option>
                    {threads.length === 0 && <option disabled>（故事中世界尚未创建故事线）</option>}
                    {threads.map(t => (
                      <option key={t.thread_id} value={t.thread_id}>
                        {t.thread_type === "main" ? "主线" : "支线"} · {t.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="field mb-12">
                  <label className="label">所属伏笔</label>
                  <select className="select" value={sel.hook_id || ""}
                    onChange={e => updateNode(sel.id, "hook_id", e.target.value || undefined)}
                    style={{ width: "100%", fontSize: 12 }}>
                    <option value="">未指定</option>
                    {hooks.length === 0 && <option disabled>（故事中世界尚未埋设伏笔）</option>}
                    {hooks.map(h => (
                      <option key={h.id} value={h.id}>
                        {(h.title || h.content || "").slice(0, 26) || "（无内容）"}
                      </option>
                    ))}
                  </select>
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
                  删除情节
                </button>

              </>
            ) : (
              <div className="empty-state" style={{ padding: "24px 0" }}>
                <p>点击情节卡片查看详情</p>
                <p className="text-xs mt-4" style={{ color: "var(--text-tertiary)" }}>
                  「同步大纲」可在故事线与编辑器章节大纲之间双向同步
                </p>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* ======== 故事中时间 strip (bottom) ========
          One chip per 情节 with a non-empty 故事中时间, ordered along
          the reading path. Click navigates to that 情节. */}
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
          故事中时间
        </span>
        {episodeTimePoints.length === 0 ? (
          <span style={{ fontSize: 11, color: "var(--text-disabled)" }}>
            暂无故事中时间，在情节详情中填写「故事中时间」
          </span>
        ) : (
          <div style={{ display: "flex", gap: 6, alignItems: "stretch", height: 44 }}>
            {episodeTimePoints.map((n) => {
              const isActive = sel?.id === n.id;
              return (
                <div
                  key={n.id}
                  onClick={() => setSelected(n.id)}
                  title={`${n.chapter_num ? `第${n.chapter_num}章 · ` : ""}${n.title}`}
                  style={{
                    minWidth: 110, maxWidth: 200,
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "flex-start",
                    justifyContent: "center",
                    background: isActive ? "var(--accent-subtle)" : "var(--bg-secondary)",
                    border: isActive ? "1px solid var(--accent)" : "1px solid var(--border-subtle)",
                    borderLeft: `3px solid ${n.color || "var(--accent)"}`,
                    borderRadius: 6,
                    cursor: "pointer",
                    transition: "all 0.15s",
                    padding: "4px 10px",
                    flexShrink: 0,
                  }}
                >
                  <span style={{
                    fontSize: 11, fontWeight: 700,
                    color: isActive ? "var(--accent)" : "var(--text-primary)",
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                    maxWidth: "100%",
                  }}>
                    {n.time}
                  </span>
                  <span style={{
                    fontSize: 9, color: "var(--text-tertiary)",
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                    maxWidth: "100%",
                  }}>
                    {n.chapter_num ? `第${n.chapter_num}章 · ` : ""}{n.title}
                  </span>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Drag-preview ghost — shadows the dragged 情节 card under the cursor
          so the user has clear feedback about what they're moving. Rendered
          as `position: fixed` so it floats above all other UI. */}
      {dragPreview && (() => {
        const ghost = nodes.find(n => n.id === dragPreview.id);
        if (!ghost) return null;
        return (
          <div style={{
            position: "fixed", zIndex: 999, pointerEvents: "none",
            left: dragPreview.clientX - dragPreview.offX,
            top: dragPreview.clientY - dragPreview.offY,
            width: 220,
            background: "var(--bg-surface)",
            border: "1px solid var(--accent)",
            borderTop: `4px solid ${ghost.color || "var(--accent)"}`,
            borderRadius: 6, padding: 10,
            boxShadow: "0 8px 24px rgba(0,0,0,0.25)",
            opacity: 0.95, transform: "rotate(-1deg)",
          }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>
              {ghost.title}
            </div>
            {ghost.summary && (
              <div className="text-xs text-muted" style={{
                marginTop: 4, maxHeight: 36, overflow: "hidden", lineHeight: 1.4,
              }}>
                {ghost.summary}
              </div>
            )}
          </div>
        );
      })()}
    </div>
  );
}


/* ── ThreadSummaryStrip ──
 * Shown above the timeline. Read-only at-a-glance row of the user's
 * main / sub storylines and currently-open foreshadowing items. Click
 * → 故事中世界 to edit (the storyline page itself stays focused on
 * chapter beats — threads live with the rest of the storyland state). */
function ThreadSummaryStrip({ threads, hooks, onOpenStoryland }: {
  threads: Thread[];
  hooks: Hook[];
  onOpenStoryland?: () => void;
}) {
  const mains = threads.filter(t => t.thread_type === "main");
  const subs = threads.filter(t => t.thread_type === "sub");
  const openHooks = hooks.filter(h => !["resolved", "abandoned"].includes(h.status));
  if (threads.length === 0 && openHooks.length === 0) return null;
  return (
    <div style={{
      padding: "8px 16px",
      background: "var(--bg-surface)",
      borderBottom: "1px solid var(--border)",
      fontSize: 11,
    }}>
      <div className="flex items-center justify-between mb-4">
        <span style={{ fontWeight: 600, color: "var(--text-secondary)" }}>
          故事线与伏笔总览
        </span>
        {onOpenStoryland && (
          <button className="btn" style={{ fontSize: 10, padding: "1px 8px" }}
            onClick={onOpenStoryland}>
            到 故事中世界 管理
          </button>
        )}
      </div>
      {mains.length > 0 && (
        <div className="flex gap-6 mb-4" style={{ flexWrap: "wrap" }}>
          <span className="text-xs text-muted" style={{ width: 36 }}>主线</span>
          {mains.map(t => (
            <span key={t.thread_id} className="tag" title={t.description}
              style={{
                fontSize: 10, background: "var(--accent-subtle)",
                color: "var(--accent)", border: "1px solid var(--accent)",
              }}>
              {t.name} · 第{t.start_chapter}章起
            </span>
          ))}
        </div>
      )}
      {subs.length > 0 && (
        <div className="flex gap-6 mb-4" style={{ flexWrap: "wrap" }}>
          <span className="text-xs text-muted" style={{ width: 36 }}>支线</span>
          {subs.map(t => (
            <span key={t.thread_id} className="tag" title={t.description}
              style={{ fontSize: 10 }}>
              {t.name} · 第{t.start_chapter}章起
            </span>
          ))}
        </div>
      )}
      {openHooks.length > 0 && (
        <div className="flex gap-6" style={{ flexWrap: "wrap" }}>
          <span className="text-xs text-muted" style={{ width: 36 }}>伏笔</span>
          {openHooks.map(h => (
            <span key={h.id} className="tag" title={h.content}
              style={{
                fontSize: 10, background: "var(--gold-subtle)",
                color: "var(--gold)", border: "1px solid var(--gold)",
              }}>
              {(h.title || h.content || "").slice(0, 18)}
              {h.expected_payoff_chapter ? ` · 收于第${h.expected_payoff_chapter}章` : " · 不限期"}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}


/* ── CharacterSelector ──
 * Replaces the legacy comma-separated input. Dropdown only offers the
 * characters that exist in 角色管理 (/api/data/characters) — single source
 * of truth — with an explicit 「临时」escape hatch for characters that
 * haven't been registered yet. Picked names render as removable chips.
 */
function CharacterSelector({ label, value, options, onChange }: {
  label: string;
  value: string[];
  options: Character[];
  onChange: (next: string[]) => void;
}) {
  const [open, setOpen] = React.useState(false);
  const [customInput, setCustomInput] = React.useState("");
  const wrapRef = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const toggle = (name: string) => {
    const next = value.includes(name)
      ? value.filter(v => v !== name)
      : [...value, name];
    onChange(next);
  };

  return (
    <div ref={wrapRef} style={{ position: "relative" }}>
      <label className="label">{label}</label>
      <div
        onClick={() => setOpen(o => !o)}
        style={{
          display: "flex", flexWrap: "wrap", gap: 4,
          minHeight: 32, padding: "4px 8px",
          background: "var(--bg-surface)",
          border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
          cursor: "pointer",
        }}>
        {value.length === 0 ? (
          <span className="text-xs text-muted">点击选择 / 添加角色…</span>
        ) : value.map(name => {
          const known = options.find(o => o.name === name);
          return (
            <span key={name} className="tag"
              style={{
                fontSize: 10,
                background: known ? "var(--purple-subtle)" : undefined,
                color: known ? "var(--purple)" : undefined,
                borderColor: known ? "var(--purple)" : undefined,
              }}>
              {name}
              <span onClick={(e) => { e.stopPropagation(); toggle(name); }}
                style={{ marginLeft: 4, cursor: "pointer", color: "var(--text-tertiary)" }}>×</span>
            </span>
          );
        })}
      </div>
      {open && (
        <div style={{
          position: "absolute", zIndex: 50, left: 0, right: 0, top: "100%",
          marginTop: 4, background: "var(--bg-surface)",
          border: "1px solid var(--border)", borderRadius: "var(--radius-sm)",
          boxShadow: "0 4px 12px rgba(0,0,0,0.15)",
          maxHeight: 260, overflowY: "auto",
        }}>
          {options.length === 0 ? (
            <div className="text-xs text-muted" style={{ padding: 10 }}>
              角色管理中暂无角色。
            </div>
          ) : (
            options.map(opt => {
              const on = value.includes(opt.name);
              return (
                <div key={opt.id}
                  onClick={() => toggle(opt.name)}
                  style={{
                    display: "flex", alignItems: "center", gap: 8,
                    padding: "5px 10px", cursor: "pointer",
                    background: on ? "var(--accent-subtle)" : undefined,
                  }}>
                  <input type="checkbox" checked={on} readOnly />
                  <span style={{ fontSize: 12, fontWeight: 500 }}>{opt.name}</span>
                  {opt.role && <span className="text-xs text-muted">· {opt.role}</span>}
                </div>
              );
            })
          )}
          <div style={{ borderTop: "1px solid var(--border)", padding: 8, display: "flex", gap: 6 }}>
            <input
              className="input" value={customInput}
              onChange={e => setCustomInput(e.target.value)}
              placeholder="临时角色名（不在角色库）"
              style={{ flex: 1, fontSize: 11, padding: "3px 8px" }}
              onKeyDown={e => {
                if (e.key === "Enter" && customInput.trim()) {
                  toggle(customInput.trim());
                  setCustomInput("");
                }
              }} />
            <button className="btn" style={{ fontSize: 10, padding: "2px 10px" }}
              disabled={!customInput.trim()}
              onClick={() => { toggle(customInput.trim()); setCustomInput(""); }}>
              添加
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
