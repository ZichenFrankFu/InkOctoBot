import React, { useEffect, useState, useRef, useCallback, useMemo } from "react";
import { apiGet, apiPut } from "../api/client";
import { useToast } from "../components/shared/Toast";
import type { StoryNode, StoryEdge, ChapterOutline, Volume, Character } from "../api/types";

const uid = () => `n_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
// Muted ink-painting palette — 6 desaturated tones so per-情节 cards stay
// distinct without fighting the page's accent / gold / neutral language.
// Used as the card's top stripe and the bottom strip's left edge ONLY.
const COLORS = ["#a04545", "#5a8c6f", "#4a6794", "#c08a3e", "#856a9c", "#8b5e3c"];
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

  // --- Push character union (storyline → editor) ──
  // For each chapter the storyline knows about, replace the editor
  // chapter.characters with the UNION of characters across the chapter's
  // 情节 cards. The user wants「保持一致」 — storyline is the source of
  // truth when it has events for the chapter; chapters with no 情节 are
  // left alone (don't wipe the editor's own picks).
  const pushCharactersToEditor = useCallback(async () => {
    try {
      const pid = projectId || "default";
      const data = await apiGet<{ volumes: Volume[] }>(`/api/data/editor?project_id=${pid}`);
      let idx = 0;
      let changed = false;
      const nextVolumes = (data.volumes || []).map(v => ({
        ...v,
        chapters: (v.chapters || []).map(c => {
          idx += 1;
          const chapterNodes = nodes.filter(n => (n.chapter_num || 0) === idx);
          if (chapterNodes.length === 0) return c;
          const union = new Set<string>();
          chapterNodes.forEach(n => (n.characters || []).forEach(name => {
            const s = (name || "").trim();
            if (s) union.add(s);
          }));
          const next = Array.from(union).sort();
          const prev = (c.characters || []).slice().sort();
          if (JSON.stringify(next) !== JSON.stringify(prev)) {
            changed = true;
            return { ...c, characters: next };
          }
          return c;
        }),
      }));
      if (changed) {
        await apiPut(`/api/data/editor`, { project_id: pid, volumes: nextVolumes });
      }
    } catch (e: any) {
      // Silent — pushing characters is a side effect; surfacing this error
      // would interrupt the autosave UX.
      console.warn("pushCharactersToEditor failed:", e);
    }
  }, [projectId, nodes]);

  // --- Auto-save ---
  useEffect(() => {
    if (!loaded || !dirty) return;
    const t = setTimeout(async () => {
      const pid = projectId || "default";
      try {
        await apiPut(`/api/data/storyline`, { project_id: pid, nodes, edges });
        // Mirror character union to editor so 大纲 tab stays in sync
        // with whatever characters the user assigned to 情节 cards.
        await pushCharactersToEditor();
      } catch (e: any) {
        toast(e.message || "操作失败", "error");
      }
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
  // Card-level refs (used by the SVG overlay to measure exact
  // bottom-center / top-center positions for cross-row connectors).
  const cardRefs = useRef<Map<string, HTMLDivElement | null>>(new Map());
  const rowsContainerRef = useRef<HTMLDivElement | null>(null);
  const [cardRects, setCardRects] = useState<Map<string, { cx: number; top: number; bottom: number; left: number; right: number }>>(new Map());
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

  // --- 故事线 / 伏笔 多选筛选 ──
  // Empty set = highlight none (default — all lanes visible equally).
  // Non-empty = those lanes get sorted first, drawn boldly, and the
  // SVG overlay only renders their connections.
  const [activeLaneKeys, setActiveLaneKeys] = useState<Set<string>>(new Set());
  const toggleLane = useCallback((key: string) => {
    setActiveLaneKeys(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  }, []);
  const clearLanes = useCallback(() => setActiveLaneKeys(new Set()), []);

  // --- Thread / 伏笔 lane mapping ──
  // Each 故事线 (thread) and 伏笔 (hook) becomes a "lane" that holds a
  // unique color across the whole page. Cards belonging to that lane
  // render with that color stripe, and within each chapter row they
  // sort to the lane's column position so cards of the same lane line
  // up vertically across rows. The final "orphan" lane catches any
  // 情节 that hasn't been assigned to anything.
  const lanes = useMemo(() => {
    const list: Array<{
      key: string;
      type: "thread" | "hook" | "orphan";
      color: string;
      label: string;
    }> = [];
    threads.forEach((t, i) => list.push({
      key: `t:${t.thread_id}`,
      type: "thread",
      color: COLORS[i % COLORS.length],
      label: `${t.thread_type === "main" ? "主线" : "支线"}·${t.name}`,
    }));
    hooks.forEach((h, i) => list.push({
      key: `h:${h.id}`,
      type: "hook",
      color: COLORS[(i + threads.length) % COLORS.length],
      label: `伏笔·${(h.title || h.content || "").slice(0, 10)}`,
    }));
    list.push({ key: "__orphan__", type: "orphan", color: "var(--text-tertiary)", label: "未归属" });
    return list;
  }, [threads, hooks]);

  /** Normalize the (possibly legacy singular) thread/hook fields on a
   *  node to flat arrays — callers can stop worrying about which shape
   *  the row currently has. */
  const readThreadIds = useCallback((n: StoryNode): string[] => {
    if (n.thread_ids && n.thread_ids.length) return n.thread_ids;
    if (n.thread_id) return [n.thread_id];
    return [];
  }, []);
  const readHookIds = useCallback((n: StoryNode): string[] => {
    if (n.hook_ids && n.hook_ids.length) return n.hook_ids;
    if (n.hook_id) return [n.hook_id];
    return [];
  }, []);

  /** All lane keys a node belongs to (zero → ["__orphan__"]; otherwise
   *  thread keys first, then hook keys). Cards can belong to multiple
   *  threads + multiple hooks now. */
  const nodeLaneKeys = useCallback((n: StoryNode): string[] => {
    const keys: string[] = [];
    readThreadIds(n).forEach(id => keys.push(`t:${id}`));
    readHookIds(n).forEach(id => keys.push(`h:${id}`));
    if (keys.length === 0) keys.push("__orphan__");
    return keys;
  }, [readThreadIds, readHookIds]);

  /** Color stripes for the card's top accent. When NO 故事线 / 伏笔 is
   *  selected in the filter, ALL cards render a single neutral gray
   *  stripe — color is reserved as a signal for "active lane membership".
   *  When at least one lane is active, cards show one stripe per owned
   *  lane (multiple if the node belongs to several). Orphan / no-lane
   *  cards fall back to the same gray. */
  const nodeStripes = useCallback((n: StoryNode): string[] => {
    if (activeLaneKeys.size === 0) {
      return ["var(--text-tertiary)"];
    }
    const colors = nodeLaneKeys(n)
      .map(k => lanes.find(l => l.key === k)?.color)
      .filter((c): c is string => !!c && c !== "var(--text-tertiary)");
    return colors.length > 0 ? colors : ["var(--text-tertiary)"];
  }, [lanes, nodeLaneKeys, activeLaneKeys]);

  /** 智能排序 —— barycenter / Sugiyama-style sweep.
   *  Rewrites each card's `x` so cards with shared lanes line up across
   *  rows as much as possible. The algorithm allows breaking the
   *  "same-lane same column" rule when sticking to it forces a
   *  connector through another card; cards just settle into positions
   *  that minimise the sum of squared edge x-distances. After this,
   *  the user can still drag any card to override.
   */
  const smartSortNodes = useCallback(() => {
    if (nodes.length < 2) return;
    // Per-lane sorted card chains.
    const chains = new Map<string, StoryNode[]>();
    nodes.forEach(n => {
      nodeLaneKeys(n).forEach(k => {
        if (!chains.has(k)) chains.set(k, []);
        chains.get(k)!.push(n);
      });
    });
    chains.forEach(arr => arr.sort((a, b) =>
      (a.chapter_num || 0) - (b.chapter_num || 0)
      || (a.x || 0) - (b.x || 0)));

    // Group by chapter row.
    const rows = new Map<number, StoryNode[]>();
    nodes.forEach(n => {
      const ch = n.chapter_num || 0;
      if (!rows.has(ch)) rows.set(ch, []);
      rows.get(ch)!.push(n);
    });
    const rowKeys = [...rows.keys()].sort((a, b) => a - b);

    // Working x map seeded with current values.
    const xs = new Map<string, number>();
    nodes.forEach(n => xs.set(n.id, n.x || 0));
    const STEP = 240;

    const neighborsOf = (n: StoryNode, side: "prev" | "next" | "both"): StoryNode[] => {
      const out: StoryNode[] = [];
      nodeLaneKeys(n).forEach(k => {
        const lane = chains.get(k) || [];
        const idx = lane.findIndex(x => x.id === n.id);
        if (idx < 0) return;
        if ((side === "prev" || side === "both") && idx > 0) out.push(lane[idx - 1]);
        if ((side === "next" || side === "both") && idx < lane.length - 1) out.push(lane[idx + 1]);
      });
      return out;
    };

    const barycenter = (n: StoryNode, side: "prev" | "next" | "both"): number => {
      const ns = neighborsOf(n, side);
      if (ns.length === 0) return xs.get(n.id) || 0;
      const sum = ns.reduce((s, p) => s + (xs.get(p.id) || 0), 0);
      return sum / ns.length;
    };

    // Sugiyama-style sweep: top→bottom then bottom→top, iterate.
    for (let iter = 0; iter < 10; iter++) {
      let changed = false;
      const sweep = (dir: "down" | "up") => {
        const order = dir === "down" ? rowKeys : [...rowKeys].reverse();
        const side = dir === "down" ? "prev" : "next";
        order.forEach(rk => {
          const cards = rows.get(rk)!;
          const ranked = cards
            .map(c => ({ c, b: barycenter(c, side) }))
            .sort((a, b) => a.b - b.b);
          ranked.forEach((r, i) => {
            const newX = i * STEP;
            if (Math.abs((xs.get(r.c.id) || 0) - newX) > 0.5) {
              xs.set(r.c.id, newX);
              changed = true;
            }
          });
        });
      };
      sweep("down");
      sweep("up");
      if (!changed) break;
    }

    setNodes(prev => prev.map(n => ({ ...n, x: xs.get(n.id) ?? n.x })));
    setDirty(true);
  }, [nodes, nodeLaneKeys]);

  /** Lanes that have at least one card in the project. Used to drive
   *  the LaneFilterStrip — only show lanes the user actually has cards
   *  in (avoid empty chips for every thread the project defines). */
  const usedLanes = useMemo(() => {
    const usedKeys = new Set<string>();
    nodes.forEach(n => nodeLaneKeys(n).forEach(k => usedKeys.add(k)));
    return lanes.filter(l => usedKeys.has(l.key));
  }, [lanes, nodes, nodeLaneKeys]);

  // ── Measure card positions for the SVG connection overlay ──
  // After every render that may have changed the layout, walk the
  // card-ref map and capture each card's bottom-center / top-center in
  // the rowsContainer's coordinate space. Throttled via animation frame
  // so rapid drag updates don't thrash.
  useEffect(() => {
    let raf = 0;
    const measure = () => {
      const container = rowsContainerRef.current;
      if (!container) return;
      const cRect = container.getBoundingClientRect();
      const next = new Map<string, { cx: number; top: number; bottom: number; left: number; right: number }>();
      cardRefs.current.forEach((el, id) => {
        if (!el) return;
        const r = el.getBoundingClientRect();
        next.set(id, {
          cx: r.left + r.width / 2 - cRect.left,
          top: r.top - cRect.top,
          bottom: r.bottom - cRect.top,
          left: r.left - cRect.left,
          right: r.right - cRect.left,
        });
      });
      setCardRects(prev => {
        if (prev.size !== next.size) return next;
        for (const [k, v] of next) {
          const p = prev.get(k);
          if (!p
              || Math.abs(p.cx - v.cx) > 0.5
              || Math.abs(p.top - v.top) > 0.5
              || Math.abs(p.bottom - v.bottom) > 0.5
              || Math.abs(p.left - v.left) > 0.5
              || Math.abs(p.right - v.right) > 0.5) {
            return next;
          }
        }
        return prev;
      });
    };
    raf = requestAnimationFrame(measure);
    const obs = new ResizeObserver(() => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(measure);
    });
    if (rowsContainerRef.current) obs.observe(rowsContainerRef.current);
    return () => {
      cancelAnimationFrame(raf);
      obs.disconnect();
    };
  }, [nodes, lanes, activeLaneKeys]);

  // Pre-compute connector paths for the active lanes — each path connects
  // consecutive cards (by chapter then x) belonging to that lane.
  const connectorPaths = useMemo(() => {
    const paths: Array<{ d: string; color: string; key: string }> = [];
    if (cardRects.size === 0) return paths;
    const lanesToDraw = activeLaneKeys.size > 0
      ? lanes.filter(l => activeLaneKeys.has(l.key))
      : lanes.filter(l => l.type !== "orphan");
    lanesToDraw.forEach(lane => {
      const laneCards = nodes
        .filter(n => nodeLaneKeys(n).includes(lane.key))
        .sort((a, b) =>
          (a.chapter_num || 0) - (b.chapter_num || 0) ||
          (a.x || 0) - (b.x || 0),
        );
      for (let i = 0; i < laneCards.length - 1; i++) {
        const nodeA = laneCards[i];
        const nodeB = laneCards[i + 1];
        const a = cardRects.get(nodeA.id);
        const b = cardRects.get(nodeB.id);
        if (!a || !b) continue;
        const sameRow = (nodeA.chapter_num || 0) === (nodeB.chapter_num || 0);
        let d: string;
        if (sameRow) {
          // SAME CHAPTER — line lives in the horizontal gap BETWEEN the
          // two cards: right-edge / vertical-center of left card →
          // left-edge / vertical-center of right card. Tiny S-curve so
          // multiple parallel same-row connectors don't sit on top of
          // each other; the dx/3 control offset keeps the bow shallow
          // enough that it never re-enters either card's hitbox.
          const leftCard = a.left < b.left ? a : b;
          const rightCard = a.left < b.left ? b : a;
          const x1 = leftCard.right;
          const y1 = (leftCard.top + leftCard.bottom) / 2;
          const x2 = rightCard.left;
          const y2 = (rightCard.top + rightCard.bottom) / 2;
          const dx = Math.max(8, (x2 - x1) / 3);
          d = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
        } else {
          // CROSS-CHAPTER — line lives in the vertical gap BETWEEN rows:
          // bottom-center of A → top-center of B. The bezier control
          // points sit at the midpoint y so the horizontal sweep happens
          // entirely in the inter-row gap (where no card lives), not
          // inside either row's card area.
          const x1 = a.cx, y1 = a.bottom;
          const x2 = b.cx, y2 = b.top;
          const mid = (y1 + y2) / 2;
          d = `M ${x1} ${y1} C ${x1} ${mid}, ${x2} ${mid}, ${x2} ${y2}`;
        }
        paths.push({ d, color: lane.color, key: `${lane.key}:${nodeA.id}-${nodeB.id}` });
      }
    });
    return paths;
  }, [cardRects, activeLaneKeys, lanes, nodes, nodeLaneKeys]);

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
              padding: "0 20px",
              background: "var(--bg-surface)",
              borderBottom: "1px solid var(--border)",
              boxShadow: "0 1px 0 rgba(0,0,0,0.02)",
            }}
          >
            <h3 className="font-serif" style={{ letterSpacing: 0.5 }}>剧情线</h3>
            <span className="text-xs" style={{
              marginLeft: 12, color: "var(--text-tertiary)",
              padding: "2px 10px", borderRadius: 10,
              background: "var(--bg-secondary)",
            }}>
              {nodes.length} 情节 · {chapterTitles.size || 0} 章
            </span>
            <div className="flex gap-8" style={{ marginLeft: "auto" }}>
              <button className="btn-primary" style={{ fontSize: 12, padding: "6px 14px" }} onClick={addNode}>
                + 添加情节
              </button>
              <button
                className="btn"
                style={{ fontSize: 12, padding: "6px 14px" }}
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
          {/* Lane multi-select strip — pick which 故事线/伏笔 lines
              to highlight + connect. Empty selection = show all lanes
              equally (default). Active selection sorts those lanes
              first and draws connectors only for them. */}
          <LaneFilterStrip
            lanes={usedLanes}
            activeKeys={activeLaneKeys}
            onToggle={toggleLane}
            onClear={clearLanes}
            onSmartSort={smartSortNodes}
          />

          <div
            ref={rowsContainerRef}
            style={{
              position: "relative",
              padding: "24px 20px", minHeight: "100%",
              display: "flex", flexDirection: "column", gap: 16,
            }}
          >
            {/* SVG overlay — draws bezier curves from each card's
                bottom-center to the next-same-lane card's top-center.
                pointerEvents none so it doesn't steal clicks/drag. */}
            <svg
              style={{
                position: "absolute", left: 0, top: 0,
                width: "100%", height: "100%",
                pointerEvents: "none", zIndex: 1,
              }}
            >
              {connectorPaths.map(p => (
                <path key={p.key} d={p.d}
                  stroke={p.color} strokeWidth={2.4}
                  fill="none" opacity={0.85}
                  strokeLinecap="round" />
              ))}
            </svg>
            {(() => {
              // Group nodes by chapter_num so each row = one chapter. Sort
              // within a row by (primary lane → x) so 故事线 1 cards always
              // sit in column 0, 故事线 2 in column 1, etc. — cards in the
              // same lane stack chronologically by stored x.
              const chapGroups = new Map<number, StoryNode[]>();
              // Sort priority within a row: chapter → x. User's stored
              // `x` is the source of truth — drag updates x, 智能排序
              // rewrites x. This way manual reordering is respected and
              // the auto-sort doesn't fight the user back.
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
                      display: "flex", alignItems: "stretch",
                      background: isDropTarget ? "var(--accent-subtle)" : "var(--bg-surface)",
                      border: `1px solid ${isDropTarget ? "var(--accent)" : "var(--border-subtle)"}`,
                      borderRadius: 12,
                      transition: "background 0.18s ease, border-color 0.18s ease, box-shadow 0.18s ease",
                      overflow: "hidden",
                      boxShadow: isDropTarget
                        ? "0 0 0 3px var(--accent-glow, rgba(0,0,0,0.06))"
                        : "0 1px 2px rgba(0,0,0,0.04)",
                    }}>
                    {/* ── LEFT: chapter spine card ── */}
                    <div style={{
                      width: "clamp(220px, 26%, 320px)", flexShrink: 0,
                      padding: "16px 16px 14px",
                      background: "linear-gradient(180deg, var(--bg-surface-2) 0%, var(--bg-surface) 100%)",
                      borderRight: "1px solid var(--border-subtle)",
                      display: "flex", flexDirection: "column", gap: 10,
                      minWidth: 0,
                    }}>
                      {/* Chapter header: numeric badge + serif title */}
                      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                        <span style={{
                          minWidth: 38, height: 28, padding: "0 10px",
                          borderRadius: 14,
                          background: "var(--accent)", color: "#fff",
                          fontSize: 12, fontWeight: 700, letterSpacing: 0.3,
                          display: "inline-flex", alignItems: "center", justifyContent: "center",
                          boxShadow: "0 1px 3px rgba(0,0,0,0.12)",
                          flexShrink: 0,
                        }}>
                          {labelTop}
                        </span>
                        {chapterTitle ? (
                          <span className="font-serif" style={{
                            fontSize: 14, fontWeight: 600, color: "var(--text-primary)",
                            overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                            flex: 1, lineHeight: 1.3,
                          }} title={chapterTitle}>
                            {chapterTitle}
                          </span>
                        ) : (
                          <span className="text-xs" style={{ color: "var(--text-disabled)", fontStyle: "italic" }}>
                            未命名
                          </span>
                        )}
                      </div>

                      {/* 故事线 / 伏笔 chips */}
                      {(relatedThreads.length > 0 || relatedHooks.length > 0) && (
                        <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                          {relatedThreads.map(t => (
                            <span key={t.thread_id} className="tag" title={`${t.thread_type === "main" ? "主线" : "支线"}：${t.description}`}
                              style={{
                                fontSize: 10, padding: "2px 8px", borderRadius: 10,
                                background: t.thread_type === "main" ? "var(--accent-subtle)" : "var(--bg-surface)",
                                color: t.thread_type === "main" ? "var(--accent)" : "var(--text-secondary)",
                                borderColor: t.thread_type === "main" ? "var(--accent)" : "var(--border)",
                                fontWeight: 500,
                              }}>
                              {t.thread_type === "main" ? "主" : "支"} · {t.name}
                            </span>
                          ))}
                          {relatedHooks.map(h => (
                            <span key={h.id} className="tag" title={h.content}
                              style={{
                                fontSize: 10, padding: "2px 8px", borderRadius: 10,
                                background: "var(--gold-subtle)", color: "var(--gold)",
                                borderColor: "var(--gold)", fontWeight: 500,
                              }}>
                              伏 · {(h.title || h.content || "").slice(0, 12)}
                            </span>
                          ))}
                        </div>
                      )}

                      {/* Section header for the merged outline */}
                      <div style={{
                        display: "flex", alignItems: "center", justifyContent: "space-between",
                        marginTop: 2,
                      }}>
                        <span style={{
                          fontSize: 10, fontWeight: 600, letterSpacing: 0.6,
                          color: "var(--text-tertiary)", textTransform: "uppercase",
                        }}>
                          章节大纲 · {chapNodes.length} 情节
                        </span>
                        <button
                          className="btn"
                          style={{
                            fontSize: 10, padding: "2px 10px",
                            color: merged ? "var(--accent)" : undefined,
                            borderColor: merged ? "var(--accent)" : undefined,
                            background: merged ? "var(--accent-subtle)" : undefined,
                          }}
                          onClick={() => writeChapterOutlineToEditor(chap_num)}
                          disabled={!merged}
                          title="把这条合并大纲写回 编辑器 → 章节 synopsis">
                          写回编辑器
                        </button>
                      </div>

                      {/* Merged outline preview */}
                      <div style={{
                        flex: 1, minHeight: 90, maxHeight: 240, overflow: "auto",
                        background: "var(--bg-surface)",
                        border: "1px solid var(--border-subtle)", borderRadius: 8,
                        padding: "10px 12px",
                        fontSize: 11.5, lineHeight: 1.8, color: "var(--text-secondary)",
                        whiteSpace: "pre-wrap",
                      }}>
                        {merged || (
                          <span className="text-xs" style={{ color: "var(--text-disabled)", fontStyle: "italic" }}>
                            本章暂无情节。
                          </span>
                        )}
                      </div>
                    </div>

                    {/* ── RIGHT: 情节 cards arranged by lane.
                        Each 故事线/伏笔 owns one column across all rows so
                        cards of the same lane line up vertically. Cards
                        sharing a lane get a colored connector line in
                        the gutter so the user can trace a thread or 伏笔
                        across chapters at a glance. ── */}
                    <div style={{
                      flex: 1, minWidth: 0, minHeight: 184,
                      padding: "16px 18px",
                      display: "flex",
                      overflowX: "auto",
                      gap: 10,
                    }}>
                      {isDropTarget && chapNodes.length === 0 && (
                        <div className="text-xs" style={{
                          padding: "0 8px", lineHeight: 1.6, alignSelf: "center",
                          color: "var(--accent)", fontWeight: 600,
                        }}>
                          释放即可归属到本章
                        </div>
                      )}
                      {/* Cards laid out as a single flex row, sorted by
                          stored x. Manual order via drag, lane semantics
                          via the SVG connectors above. 智能排序 button
                          rewrites x values to minimise crossings — but
                          the user can always drag to override. */}
                      {chapNodes
                        .slice()
                        .sort((a, b) => (a.x || 0) - (b.x || 0))
                        .map(n => {
                        const isDragging = dragPreview?.id === n.id;
                        const isSelected = selected === n.id;
                        // The card-side chips only show the first assigned
                        // thread / hook (acts as a label); the full
                        // multi-lane membership is communicated through
                        // the stacked color stripes at the top of the card.
                        const _tids = readThreadIds(n);
                        const _hids = readHookIds(n);
                        const thread = _tids.length ? threads.find(t => t.thread_id === _tids[0]) : undefined;
                        const hook = _hids.length ? hooks.find(h => h.id === _hids[0]) : undefined;
                        const stripes = nodeStripes(n);
                        return (
                          <div
                            key={n.id}
                            ref={(el) => { cardRefs.current.set(n.id, el); }}
                            onMouseDown={(e) => onNodeMouseDown(n.id, e)}
                            onClick={() => setSelected(n.id)}
                            className={`timeline-node ${isSelected ? "selected" : ""}`}
                            style={{
                              position: "relative",
                              left: "auto", top: "auto",
                              width: NODE_W, minHeight: NODE_H,
                              // Multi-color stripes are rendered as inline
                              // sub-divs below; reserve borderTop only as a
                              // fallback when the card has no lane (orphan).
                              borderTop: undefined,
                              paddingTop: stripes.length > 0 ? 4 + stripes.length * 4 : 12,
                              borderRadius: 10,
                              padding: "12px 14px 10px",
                              cursor: "grab",
                              flexShrink: 0,
                              opacity: isDragging ? 0.35 : 1,
                              background: "var(--bg-card)",
                              overflow: "hidden",
                              boxShadow: isSelected
                                ? "0 0 0 2px var(--accent), 0 4px 12px rgba(0,0,0,0.08)"
                                : "0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04)",
                              transition: "transform 0.15s ease, box-shadow 0.15s ease, opacity 0.12s",
                            }}
                            onMouseEnter={(e) => {
                              if (isDragging || isSelected) return;
                              e.currentTarget.style.transform = "translateY(-2px)";
                              e.currentTarget.style.boxShadow = "0 4px 10px rgba(0,0,0,0.08), 0 2px 4px rgba(0,0,0,0.04)";
                            }}
                            onMouseLeave={(e) => {
                              if (isSelected) return;
                              e.currentTarget.style.transform = "translateY(0)";
                              e.currentTarget.style.boxShadow = "0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04)";
                            }}
                          >
                            {/* Stacked lane-color stripes — one 4px band
                                per lane the card belongs to (thread first,
                                then hook). When the card has none, the
                                fallback borderTop above kicks in. */}
                            {stripes.length > 0 && (
                              <div style={{
                                position: "absolute", top: 0, left: 0, right: 0,
                                display: "flex", flexDirection: "column",
                                pointerEvents: "none",
                              }}>
                                {stripes.map((c, i) => (
                                  <div key={i} style={{ height: 4, background: c }} />
                                ))}
                              </div>
                            )}
                            <div className="font-serif" style={{
                              fontSize: 13.5, fontWeight: 600, color: "var(--text-primary)",
                              marginBottom: 6, lineHeight: 1.35,
                              overflow: "hidden", textOverflow: "ellipsis",
                              display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
                            }} title={n.title}>
                              {n.title}
                            </div>
                            {(n.time || n.location) && (
                              <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 6 }}>
                                {n.time && (
                                  <span style={{
                                    fontSize: 9.5, padding: "1.5px 7px", borderRadius: 10,
                                    background: "transparent", color: "var(--text-secondary)",
                                    border: "1px solid var(--border-subtle)", fontWeight: 500,
                                  }}>
                                    {n.time}
                                  </span>
                                )}
                                {n.location && (
                                  <span style={{
                                    fontSize: 9.5, padding: "1.5px 7px", borderRadius: 10,
                                    background: "transparent", color: "var(--text-secondary)",
                                    border: "1px solid var(--border-subtle)", fontWeight: 500,
                                  }}>
                                    {n.location}
                                  </span>
                                )}
                              </div>
                            )}
                            {(thread || hook) && (
                              <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 6 }}>
                                {thread && (
                                  <span style={{
                                    fontSize: 9.5, padding: "1.5px 7px", borderRadius: 10,
                                    background: thread.thread_type === "main" ? "var(--accent-subtle)" : "transparent",
                                    color: thread.thread_type === "main" ? "var(--accent)" : "var(--text-secondary)",
                                    border: thread.thread_type === "main" ? "1px solid transparent" : "1px solid var(--border-subtle)",
                                    fontWeight: 500,
                                  }}
                                    title={thread.description}>
                                    {thread.thread_type === "main" ? "主线" : "支线"} · {thread.name}
                                  </span>
                                )}
                                {hook && (
                                  <span style={{
                                    fontSize: 9.5, padding: "1.5px 7px", borderRadius: 10,
                                    background: "var(--gold-subtle)", color: "var(--gold)", fontWeight: 500,
                                    border: "1px solid transparent",
                                  }}
                                    title={hook.content}>
                                    伏笔 · {(hook.title || hook.content || "").slice(0, 10)}
                                  </span>
                                )}
                              </div>
                            )}
                            <div style={{
                              fontSize: 11, lineHeight: 1.5, color: n.summary ? "var(--text-tertiary)" : "var(--text-disabled)",
                              fontStyle: n.summary ? "normal" : "italic",
                              overflow: "hidden",
                              display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical",
                            }}>
                              {n.summary || "（空）"}
                            </div>
                            {(n.characters?.length || 0) > 0 && (
                              <div style={{ marginTop: 6, display: "flex", gap: 3, flexWrap: "wrap" }}>
                                {n.characters!.map((ch, i) => (
                                  <span key={i} style={{
                                    background: "var(--bg-secondary)", color: "var(--text-tertiary)",
                                    padding: "1px 6px", borderRadius: 8, fontSize: 9.5, fontWeight: 500,
                                  }}>
                                    {ch}
                                  </span>
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
                          border: "1.5px dashed var(--border)", borderRadius: 10,
                          background: "transparent", color: "var(--text-tertiary)",
                          cursor: "pointer", fontSize: 13, fontWeight: 500,
                          display: "flex", alignItems: "center", justifyContent: "center",
                          gap: 6,
                          transition: "border-color 0.18s, color 0.18s, background 0.18s, transform 0.18s",
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
                        <span style={{ fontSize: 18, lineHeight: 1, fontWeight: 300 }}>+</span>
                        <span>添加情节</span>
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
            width: 300,
            minWidth: 300,
            flexShrink: 0,
            background: "var(--bg-surface)",
            borderLeft: "1px solid var(--border)",
            overflowY: "auto",
            height: "100%",
          }}
        >
          <div className="panel-header" style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "0 20px", height: HEADER_H,
            borderBottom: "1px solid var(--border)",
          }}>
            {sel && (
              <span style={{
                width: 10, height: 10, borderRadius: "50%",
                background: sel.color || "var(--accent)",
                boxShadow: `0 0 0 2px var(--bg-surface), 0 0 0 3px ${(sel.color || "var(--accent)")}33`,
                flexShrink: 0,
              }} />
            )}
            <h3 className="font-serif" style={{ letterSpacing: 0.5 }}>情节详情</h3>
          </div>
          <div className="panel-body" style={{ padding: "16px 18px" }}>
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
                  <label className="label">大纲</label>
                  <textarea
                    className="input"
                    value={sel.summary || ""}
                    onChange={e => updateNode(sel.id, "summary", e.target.value)}
                    onBlur={() => syncOutlineToEditor(sel)}
                    placeholder="本情节大纲（失焦后自动同步到编辑器对应章节）"
                    rows={3}
                  />
                </div>

                <SectionHeader>时空</SectionHeader>
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

                <SectionHeader>归属</SectionHeader>
                <div className="field mb-12">
                  <CharacterSelector
                    label="出场角色"
                    value={sel.characters || []}
                    options={characters}
                    onChange={(v) => updateNode(sel.id, "characters", v)}
                  />
                </div>
                <div className="field mb-12">
                  <label className="label">所属故事线（可多选）</label>
                  {threads.length === 0 ? (
                    <div className="text-xs text-muted">（故事中世界尚未创建故事线）</div>
                  ) : (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                      {threads.map((t, ti) => {
                        const on = readThreadIds(sel).includes(t.thread_id);
                        const color = COLORS[ti % COLORS.length];
                        return (
                          <button key={t.thread_id}
                            onClick={() => {
                              const cur = readThreadIds(sel);
                              const next = cur.includes(t.thread_id)
                                ? cur.filter(id => id !== t.thread_id)
                                : [...cur, t.thread_id];
                              updateNode(sel.id, "thread_ids", next);
                              // Drop the legacy singular field to keep
                              // data consistent on save.
                              updateNode(sel.id, "thread_id", undefined);
                            }}
                            title={t.description || (t.thread_type === "main" ? "主线" : "支线")}
                            style={{
                              fontSize: 11, padding: "3px 10px",
                              borderRadius: 12, cursor: "pointer",
                              border: `1.5px solid ${color}`,
                              background: on ? color : "transparent",
                              color: on ? "#fff" : color,
                              fontWeight: 600,
                              display: "inline-flex", alignItems: "center", gap: 5,
                            }}>
                            <span style={{
                              width: 6, height: 6, borderRadius: "50%",
                              background: on ? "#fff" : color,
                            }} />
                            {t.thread_type === "main" ? "主" : "支"}·{t.name}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>
                <div className="field mb-12">
                  <label className="label">所属伏笔（可多选）</label>
                  {hooks.length === 0 ? (
                    <div className="text-xs text-muted">（故事中世界尚未埋设伏笔）</div>
                  ) : (
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                      {hooks.map((h, hi) => {
                        const on = readHookIds(sel).includes(h.id);
                        const color = COLORS[(hi + threads.length) % COLORS.length];
                        const label = (h.title || h.content || "（无内容）").slice(0, 14);
                        return (
                          <button key={h.id}
                            onClick={() => {
                              const cur = readHookIds(sel);
                              const next = cur.includes(h.id)
                                ? cur.filter(id => id !== h.id)
                                : [...cur, h.id];
                              updateNode(sel.id, "hook_ids", next);
                              updateNode(sel.id, "hook_id", undefined);
                            }}
                            title={h.content}
                            style={{
                              fontSize: 11, padding: "3px 10px",
                              borderRadius: 12, cursor: "pointer",
                              border: `1.5px solid ${color}`,
                              background: on ? color : "transparent",
                              color: on ? "#fff" : color,
                              fontWeight: 600,
                              display: "inline-flex", alignItems: "center", gap: 5,
                            }}>
                            <span style={{
                              width: 6, height: 6, borderRadius: "50%",
                              background: on ? "#fff" : color,
                            }} />
                            伏·{label}
                          </button>
                        );
                      })}
                    </div>
                  )}
                </div>

                <button
                  className="btn w-full mt-16"
                  style={{
                    justifyContent: "center",
                    color: "var(--error)",
                    borderColor: "var(--error)",
                    background: "transparent",
                  }}
                  onClick={() => delNode(sel.id)}
                >
                  × 删除情节
                </button>
              </>
            ) : (
              <div className="empty-state" style={{
                padding: "40px 12px",
                textAlign: "center",
                color: "var(--text-tertiary)",
              }}>
                <div style={{
                  width: 56, height: 56, borderRadius: "50%",
                  background: "var(--bg-secondary)",
                  margin: "0 auto 14px",
                  display: "flex", alignItems: "center", justifyContent: "center",
                  fontSize: 22, color: "var(--text-disabled)",
                  fontFamily: "var(--font-serif)",
                  fontStyle: "italic",
                }}>
                  i
                </div>
                <p style={{ fontSize: 13, color: "var(--text-secondary)", marginBottom: 8 }}>
                  点击情节卡片查看详情
                </p>
                <p className="text-xs" style={{ color: "var(--text-tertiary)", lineHeight: 1.6 }}>
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
          padding: "0 20px",
          overflowX: "auto",
          boxShadow: "0 -1px 0 rgba(0,0,0,0.02)",
        }}
      >
        <div style={{
          display: "flex", alignItems: "center", gap: 6,
          marginRight: 16, whiteSpace: "nowrap", flexShrink: 0,
        }}>
          <span style={{
            width: 6, height: 6, borderRadius: "50%",
            background: "var(--accent)", display: "inline-block",
          }} />
          <span style={{ fontSize: 11, color: "var(--text-secondary)", fontWeight: 600, letterSpacing: 0.4 }}>
            故事中时间
          </span>
        </div>
        {episodeTimePoints.length === 0 ? (
          <span style={{ fontSize: 11, color: "var(--text-disabled)", fontStyle: "italic" }}>
            暂无故事中时间，在情节详情中填写「故事中时间」
          </span>
        ) : (
          <div style={{ display: "flex", gap: 8, alignItems: "stretch", height: 48 }}>
            {episodeTimePoints.map((n) => {
              const isActive = sel?.id === n.id;
              return (
                <div
                  key={n.id}
                  onClick={() => setSelected(n.id)}
                  title={`${n.chapter_num ? `第${n.chapter_num}章 · ` : ""}${n.title}`}
                  style={{
                    minWidth: 120, maxWidth: 220,
                    display: "flex",
                    flexDirection: "column",
                    alignItems: "flex-start",
                    justifyContent: "center",
                    background: "var(--bg-secondary)",
                    border: "1px solid var(--border-subtle)",
                    borderLeft: `3px solid ${n.color || "var(--text-secondary)"}`,
                    borderRadius: 8,
                    cursor: "pointer",
                    transition: "box-shadow 0.18s ease, transform 0.18s ease",
                    padding: "5px 12px",
                    flexShrink: 0,
                    boxShadow: isActive
                      ? "0 0 0 2px var(--accent), 0 2px 4px rgba(0,0,0,0.06)"
                      : "none",
                    transform: isActive ? "translateY(-1px)" : "none",
                  }}
                >
                  <span style={{
                    fontSize: 11.5, fontWeight: 700,
                    color: "var(--text-primary)",
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                    maxWidth: "100%",
                  }}>
                    {n.time}
                  </span>
                  <span style={{
                    fontSize: 9.5, color: "var(--text-tertiary)",
                    overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                    maxWidth: "100%", marginTop: 1,
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


/* ── SectionHeader ──
 * Small uppercase group label used to break the right-panel form into
 * 基本 / 时空 / 归属 / 外观 buckets. */
/* ── LaneFilterStrip ──
 * Multi-select chip strip for 故事线/伏笔 lanes. Clicking a chip toggles
 * its key in the parent's activeLaneKeys set. When at least one lane is
 * active, the canvas:
 *   · sorts cards so active lanes appear first within each row
 *   · only draws SVG connectors for active lanes
 *   · cards that DON'T own any active lane render faded
 * Empty selection = neutral mode (show all lanes equally, all connectors). */
function LaneFilterStrip({ lanes, activeKeys, onToggle, onClear, onSmartSort }: {
  lanes: Array<{ key: string; type: "thread" | "hook" | "orphan"; color: string; label: string }>;
  activeKeys: Set<string>;
  onToggle: (key: string) => void;
  onClear: () => void;
  onSmartSort?: () => void;
}) {
  const real = lanes.filter(l => l.type !== "orphan");
  if (real.length === 0) return null;
  return (
    <div style={{
      padding: "8px 16px", display: "flex", alignItems: "center",
      gap: 8, flexWrap: "wrap",
      background: "var(--bg-surface)", borderBottom: "1px solid var(--border)",
    }}>
      <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-secondary)", letterSpacing: 0.3 }}>
        故事线 / 伏笔
      </span>
      {real.map(lane => {
        const on = activeKeys.has(lane.key);
        return (
          <button key={lane.key}
            onClick={() => onToggle(lane.key)}
            title={lane.label}
            style={{
              fontSize: 11, padding: "3px 10px",
              borderRadius: 12, cursor: "pointer",
              border: `1.5px solid ${lane.color}`,
              background: on ? lane.color : "transparent",
              color: on ? "#fff" : lane.color,
              fontWeight: 600,
              display: "inline-flex", alignItems: "center", gap: 6,
              transition: "background 0.15s, color 0.15s",
            }}>
            <span style={{
              width: 8, height: 8, borderRadius: "50%",
              background: on ? "#fff" : lane.color,
            }} />
            {lane.label}
          </button>
        );
      })}
      {activeKeys.size > 0 && (
        <button className="btn-ghost"
          onClick={onClear}
          style={{ fontSize: 10, padding: "2px 10px", marginLeft: 4 }}>
          清除筛选
        </button>
      )}
      <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 10, color: "var(--text-tertiary)" }}>
          {activeKeys.size === 0
            ? "未选 → 卡片为灰色 / 不画连线"
            : `已选 ${activeKeys.size} 条 → 卡片着色 + 连线`}
        </span>
        {onSmartSort && (
          <button className="btn"
            onClick={onSmartSort}
            style={{ fontSize: 11, padding: "3px 12px" }}
            title="按所属故事线 / 伏笔 自动重排卡片位置，尽量减少连线穿过其他卡片">
            智能排序
          </button>
        )}
      </span>
    </div>
  );
}

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 8,
      marginTop: 14, marginBottom: 10,
    }}>
      <span style={{
        fontSize: 10, fontWeight: 700, letterSpacing: 1,
        color: "var(--text-tertiary)", textTransform: "uppercase",
      }}>
        {children}
      </span>
      <span style={{ flex: 1, height: 1, background: "var(--border-subtle)" }} />
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
                background: known ? "var(--bg-secondary)" : "transparent",
                color: known ? "var(--text-primary)" : "var(--text-tertiary)",
                borderColor: known ? "var(--border)" : "var(--border-subtle)",
                fontStyle: known ? "normal" : "italic",
              }}
              title={known ? "已登记角色" : "临时角色（未在角色库）"}>
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
              角色卡中暂无角色。
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
