import React, { useEffect, useState, useRef, useCallback, useMemo } from "react";
import { apiGet, apiPut, apiPost, apiDelete } from "../api/client";
import { useToast } from "../components/shared/Toast";
import ChapterTimeline from "../components/shared/ChapterTimeline";
import type { StoryNode, StoryEdge, ChapterOutline, Volume, Character } from "../api/types";

const uid = () => `n_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
// Muted ink-painting palette — 6 desaturated tones so per-情节 cards stay
// distinct without fighting the page's accent / gold / neutral language.
// Used as the card's top stripe and the bottom strip's left edge ONLY.
// Red is reserved for the SELECTED state (var(--accent)), so the
// reddish 朱砂 is intentionally absent.
const COLORS = ["#5a8c6f", "#4a6794", "#c08a3e", "#856a9c", "#8b5e3c", "#3b7a8c"];
const NODE_W = 220;
const NODE_H = 120;
const HEADER_H = 56;
const TIMELINE_H = 64;

// ── 故事线 / 伏笔 管理 tab 用的中文枚举映射 ──
//    与 故事中世界 (legacy 入口) 保持一致。
const SCALE_LABEL: Record<string, string> = {
  boomerang: "回旋镖(≤3章)", event_clue: "事件线索(≤20章)",
  grand_plan: "大计划(≤100章)", world_truth: "世界真相",
};
const HOOK_STATUS_LABEL: Record<string, string> = {
  open: "埋设", progressing: "推进中", pressured: "超期/待推进",
  near_payoff: "临近回收", resolved: "已回收", abandoned: "已放弃",
};
const THREAD_STATUS_LABEL: Record<string, string> = {
  setup: "开启", building: "推进", climax: "高潮",
  resolution: "完结", dormant: "搁置",
};
// 编辑入口（情节详情）暴露给用户的精简状态选项；legacy 数值在 UI 中
// 用 fallback option 显示原值，但不强制用户继续使用。
const THREAD_STATUS_EDIT: Record<string, string> = {
  setup: "开启", building: "推进", resolution: "完结",
};
const HOOK_STATUS_EDIT: Record<string, string> = {
  open: "埋设", progressing: "推进", resolved: "已回收",
};
// （legacy 单天时段常量已并入 StoryTimeField 内部，外部不再使用）

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
  // Time-axis highlight: an in-story moment chosen via the bottom
  // scrubber. Independent of `selected` — picking a time does NOT pick a
  // card; instead every card at that time gets a light-red overlay.
  const [highlightedTime, setHighlightedTime] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const canvasRef = useRef<HTMLDivElement>(null);
  const pageRef = useRef<HTMLDivElement>(null);

  // ── 详情面板 宽度 + 折叠 (persisted) ──
  // Resize via the splitter handle on the panel's left edge; collapsed
  // state hides the panel entirely behind a thin re-open strip so the
  // canvas takes the full width.
  const [detailWidth, setDetailWidth] = useState<number>(() => {
    const v = parseInt(localStorage.getItem("storyline_detail_width") || "300");
    return isNaN(v) ? 300 : Math.max(240, Math.min(720, v));
  });
  const [detailCollapsed, setDetailCollapsed] = useState<boolean>(
    // 默认收起：除非 localStorage 显式存了 "0"（用户上次展开过），
    // 否则打开 故事线 page 时 情节详情 默认折叠，把横向空间留给时间线。
    () => localStorage.getItem("storyline_detail_collapsed") !== "0",
  );
  useEffect(() => { localStorage.setItem("storyline_detail_width", String(detailWidth)); }, [detailWidth]);
  useEffect(() => { localStorage.setItem("storyline_detail_collapsed", detailCollapsed ? "1" : "0"); }, [detailCollapsed]);
  const [resizingDetail, setResizingDetail] = useState(false);
  useEffect(() => {
    if (!resizingDetail) return;
    const onMove = (e: MouseEvent) => {
      const rect = pageRef.current?.getBoundingClientRect();
      if (!rect) return;
      const next = rect.right - e.clientX;
      setDetailWidth(Math.max(240, Math.min(720, next)));
    };
    const onUp = () => setResizingDetail(false);
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [resizingDetail]);

  // Extra data feeding the content layer of the timeline (visuals unchanged):
  //  · characters[] → CharacterSelector options (角色管理 ↔ 出场角色)
  //  · threads[] / hooks[] → 顶部 故事线/伏笔 概览 + 行内 chip 提示
  //  · chapterIndex / chapterTitles → 章节大纲合并写回 editor
  const [characters, setCharacters] = useState<Character[]>([]);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [hooks, setHooks] = useState<Hook[]>([]);
  const [locationEntities, setLocationEntities] = useState<Array<{ entity_id: string; name: string; description?: string }>>([]);
  const [chapterTitles, setChapterTitles] = useState<Map<number, string>>(new Map());
  const [chapterStatuses, setChapterStatuses] = useState<Map<number, string>>(new Map());

  const reloadThreadsHooks = useCallback(async () => {
    const pid = projectId || "default";
    try {
      // include_resolved=true 让 已回收 view 也能看见用户回收过的伏笔
      const [t, h] = await Promise.all([
        apiGet<{ items: Thread[] }>(`/api/storyland/subplots?project_id=${pid}`),
        apiGet<{ items: Hook[] }>(`/api/data/foreshadowing/${pid}?include_resolved=true`),
      ]);
      setThreads(t.items || []);
      setHooks(h.items || []);
    } catch (_e) { /* silent */ }
  }, [projectId]);

  const reloadLocations = useCallback(async () => {
    const pid = projectId || "default";
    try {
      const r = await apiGet<{ items: Array<{ entity_id: string; name: string; description?: string; entity_type: string }> }>(
        `/api/entities?project_id=${pid}&entity_type=location`
      );
      setLocationEntities((r.items || []).map(e => ({ entity_id: e.entity_id, name: e.name, description: e.description })));
    } catch (_e) { setLocationEntities([]); }
  }, [projectId]);

  useEffect(() => {
    const pid = projectId || "default";
    apiGet<{ items: Character[] }>(`/api/data/characters?project_id=${pid}`)
      .then(r => setCharacters(r.items || []))
      .catch(() => setCharacters([]));
    reloadThreadsHooks();
    reloadLocations();
    apiGet<{ volumes: Volume[] }>(`/api/data/editor?project_id=${pid}`)
      .then(r => {
        const titles = new Map<number, string>();
        const statuses = new Map<number, string>();
        let i = 0;
        (r.volumes || []).forEach(v => (v.chapters || []).forEach(c => {
          i += 1;
          if (c.title) titles.set(i, c.title);
          if (c.status) statuses.set(i, c.status);
        }));
        setChapterTitles(titles);
        setChapterStatuses(statuses);
      })
      .catch(() => { setChapterTitles(new Map()); setChapterStatuses(new Map()); });
  }, [projectId, reloadThreadsHooks, reloadLocations]);

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

  // --- Push 情节-driven fields (storyline → editor) ──
  // For each chapter the storyline knows about, mirror the union of
  // characters AND the first 情节 card's time + location into the editor
  // chapter row. Without this, the editor's chapter.time / chapter.location
  // stay empty and the prompt-context loaders inject empty 时间与地点
  // blocks. Chapters with no 情节 cards are left alone so we don't wipe
  // direct edits the user made elsewhere.
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
          // union characters across the 情节 cards
          const charUnion = new Set<string>();
          chapterNodes.forEach(n => (n.characters || []).forEach(name => {
            const s = (name || "").trim();
            if (s) charUnion.add(s);
          }));
          const nextChars = Array.from(charUnion).sort();
          const prevChars = (c.characters || []).slice().sort();
          // Time / location: union the non-empty values from EVERY 情节
          // card in the chapter, preserving card order, de-duping repeats.
          // The loader renders them as a single line each, so we join with
          // "；". 允许部分情节卡为空：那些卡片直接跳过，剩下的依然合并。
          const collect = (key: "time" | "location") => {
            const seen = new Set<string>();
            const out: string[] = [];
            for (const n of chapterNodes) {
              const v = (n[key] || "").trim();
              if (!v || seen.has(v)) continue;
              seen.add(v);
              out.push(v);
            }
            return out.join("；");
          };
          const nextTime = collect("time");
          const nextLoc = collect("location");
          const charsChanged = JSON.stringify(nextChars) !== JSON.stringify(prevChars);
          const timeChanged = nextTime !== ((c.time || "").trim());
          const locChanged = nextLoc !== ((c.location || "").trim());
          if (!charsChanged && !timeChanged && !locChanged) return c;
          changed = true;
          const next: any = { ...c };
          if (charsChanged) next.characters = nextChars;
          // Only overwrite when the storyline has at least one value —
          // never wipe the editor's own picks just because every 情节
          // card is blank.
          if (timeChanged && nextTime) next.time = nextTime;
          if (locChanged && nextLoc) next.location = nextLoc;
          return next;
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

  // --- Add 章节 ---
  // Appends a new chapter to the editor's last volume, then mirrors the
  // chapter title into chapterTitles so a fresh empty row appears in the
  // canvas immediately (no need to wait for a manual reload).
  const addChapter = useCallback(async () => {
    const pid = projectId || "default";
    try {
      const data = await apiGet<{ volumes: Volume[] }>(`/api/data/editor?project_id=${pid}`);
      const volumes = (data.volumes || []).map(v => ({
        ...v,
        chapters: [...(v.chapters || [])],
      })) as any[];
      let chCount = 0;
      volumes.forEach((v: any) => { chCount += (v.chapters || []).length; });
      const nextNum = chCount + 1;
      const newId = `ch_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 6)}`;
      if (volumes.length === 0) {
        volumes.push({
          id: `vol_${Date.now().toString(36)}`,
          project_id: pid,
          title: "第一卷",
          order: 0,
          chapters: [{ id: newId, title: `第${nextNum}章`, synopsis: "", characters: [], order: 0 }],
        });
      } else {
        const lastVol = volumes[volumes.length - 1];
        const lastOrder = (lastVol.chapters || []).reduce(
          (m: number, c: any) => Math.max(m, c.order || 0), 0,
        );
        lastVol.chapters.push({
          id: newId,
          volume_id: lastVol.id,
          title: `第${nextNum}章`,
          synopsis: "",
          characters: [],
          order: lastOrder + 1,
        });
      }
      await apiPut(`/api/data/editor`, { project_id: pid, volumes });
      setChapterTitles(prev => {
        const next = new Map(prev);
        next.set(nextNum, `第${nextNum}章`);
        return next;
      });
      toast(`已新增 第${nextNum}章`, "success");
    } catch (e: any) {
      toast(e?.message || "添加章节失败", "error");
    }
  }, [projectId, toast]);

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

  // --- Status edit (lives on the thread/hook globally, but the UI for
  //     it is reached through the 情节详情 panel). ---
  const updateThreadStatus = useCallback(async (id: string, status: string) => {
    try { await apiPut(`/api/storyland/subplots/${id}`, { status }); await reloadThreadsHooks(); }
    catch (e: any) { toast(e?.message || "更新失败", "error"); }
  }, [reloadThreadsHooks, toast]);
  const updateHookStatus = useCallback(async (id: string, status: string) => {
    try { await apiPut(`/api/storyland/hooks/${id}`, { status }); await reloadThreadsHooks(); }
    catch (e: any) { toast(e?.message || "更新失败", "error"); }
  }, [reloadThreadsHooks, toast]);

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

  // ── 缩放 ──
  // CSS `zoom` scales both layout AND visuals, so scrollbars, getBounding
  // ClientRect, and the SVG overlay all stay in sync with the rendered
  // size — no need to divide measured coords by the zoom factor.
  const [zoom, setZoom] = useState(1);
  // ── 拖拽 — 阈值激活 + 预览 placeholder ──
  //  · `mousedown` 只记录起点，并不立刻进入拖拽态（避免轻微抖动触发排序）
  //  · 当 cursor 移动距离 > DRAG_THRESHOLD 时，drag 才正式 active
  //  · active 之后：原卡片从 row 渲染中移除（露出空位的视觉），目标位置
  //    插入一个淡灰色虚影 placeholder；其他卡片自然 flex 重排
  //  · 松手 → 若 active，commit reorder (覆写 x 序号 + 改 chapter_num)
  //           若未 active（即没超阈值），仅算 click → 仅 setSelected
  type DragState = {
    id: string;
    startX: number; startY: number;
    clientX: number; clientY: number;
    offX: number; offY: number;
    active: boolean;
    targetChapter: number | null;
    targetIndex: number;
  };
  const DRAG_THRESHOLD = 6;
  const REORDER_STEP = 240;
  const dragRef = useRef<DragState | null>(null);
  const [dragSnap, setDragSnap] = useState<DragState | null>(null);
  // Keep `nodes` accessible inside drag listeners without re-subscribing
  // every move tick — read via ref instead of closure capture.
  const nodesRef = useRef(nodes);
  useEffect(() => { nodesRef.current = nodes; }, [nodes]);

  const onNodeMouseDown = (id: string, e: React.MouseEvent) => {
    e.stopPropagation();
    const target = (e.currentTarget as HTMLElement).getBoundingClientRect();
    const ds: DragState = {
      id,
      startX: e.clientX, startY: e.clientY,
      clientX: e.clientX, clientY: e.clientY,
      offX: e.clientX - target.left,
      offY: e.clientY - target.top,
      active: false,
      targetChapter: null,
      targetIndex: 0,
    };
    dragRef.current = ds;
    setDragSnap(ds);
    setSelected(id);
  };

  useEffect(() => {
    if (!dragSnap) return;
    const onMove = (e: MouseEvent) => {
      const cur = dragRef.current;
      if (!cur) return;
      const dx = e.clientX - cur.startX;
      const dy = e.clientY - cur.startY;
      if (!cur.active && Math.hypot(dx, dy) > DRAG_THRESHOLD) cur.active = true;
      cur.clientX = e.clientX;
      cur.clientY = e.clientY;
      if (cur.active) {
        const ns = nodesRef.current;
        let targetChapter: number | null = null;
        let targetIndex = 0;
        for (const [chap_num, el] of rowRefs.current) {
          if (!el) continue;
          const r = el.getBoundingClientRect();
          if (e.clientY >= r.top && e.clientY <= r.bottom
              && e.clientX >= r.left && e.clientX <= r.right) {
            targetChapter = chap_num;
            const peers = ns
              .filter(n => (n.chapter_num || 0) === chap_num && n.id !== cur.id)
              .sort((a, b) => (a.x || 0) - (b.x || 0));
            let idx = peers.length;
            for (let i = 0; i < peers.length; i++) {
              const cel = cardRefs.current.get(peers[i].id);
              if (!cel) continue;
              const cr = cel.getBoundingClientRect();
              if (e.clientX < cr.left + cr.width / 2) { idx = i; break; }
            }
            targetIndex = idx;
            break;
          }
        }
        cur.targetChapter = targetChapter;
        cur.targetIndex = targetIndex;
      }
      setDragSnap({ ...cur });
    };
    const onUp = () => {
      const cur = dragRef.current;
      if (cur && cur.active && cur.targetChapter !== null) {
        const targetChap = cur.targetChapter;
        const targetIdx = cur.targetIndex;
        const draggedId = cur.id;
        setNodes(allNodes => {
          const peers = allNodes
            .filter(n => (n.chapter_num || 0) === targetChap && n.id !== draggedId)
            .sort((a, b) => (a.x || 0) - (b.x || 0));
          const dragged = allNodes.find(n => n.id === draggedId);
          if (!dragged) return allNodes;
          const newOrder = [...peers];
          newOrder.splice(targetIdx, 0, dragged);
          const xMap = new Map<string, number>();
          newOrder.forEach((n, i) => xMap.set(n.id, i * REORDER_STEP));
          return allNodes.map(n => {
            if (n.id === draggedId) return { ...n, chapter_num: targetChap, x: xMap.get(n.id) ?? 0, y: 0 };
            if (xMap.has(n.id)) return { ...n, x: xMap.get(n.id)! };
            return n;
          });
        });
        setDirty(true);
      }
      dragRef.current = null;
      setDragSnap(null);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, [dragSnap !== null]);

  // --- Computed ---
  const sel = useMemo(() => nodes.find(n => n.id === selected), [nodes, selected]);

  // ── 详情面板用的辅助派生 ──
  // 章节号下拉：项目中所有已知章号（来自 editor 大纲）。
  // 故事中时间：结构化 "第N天·时段" → 拆 day / period 双控件。
  // 地点：复用项目中已出现过的地点，加 「新增」 escape。
  // 所属故事线：主线先于支线，并保留各自创建顺序。
  const chapterOptions = useMemo(
    () => Array.from(chapterTitles.keys()).sort((a, b) => a - b),
    [chapterTitles],
  );
  const uniqueLocations = useMemo(() => {
    const s = new Set<string>();
    nodes.forEach(n => {
      const v = (n.location || "").trim();
      if (v) s.add(v);
    });
    return Array.from(s).sort();
  }, [nodes]);
  const sortedThreadsForPanel = useMemo(() => {
    return [...threads].sort((a, b) =>
      a.thread_type === "main" && b.thread_type === "sub" ? -1 :
      a.thread_type === "sub" && b.thread_type === "main" ? 1 : 0
    );
  }, [threads]);

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

  /** Per-情节 status helpers — read with sensible defaults. */
  const readThreadStatus = useCallback((n: StoryNode, tid: string): string => {
    return n.thread_statuses?.[tid] || "setup";
  }, []);
  const readHookStatus = useCallback((n: StoryNode, hid: string): string => {
    return n.hook_statuses?.[hid] || "open";
  }, []);
  /** Write a per-情节 thread/hook status on a single node. */
  const setNodeThreadStatus = useCallback((nodeId: string, tid: string, status: string) => {
    setNodes(prev => prev.map(n =>
      n.id === nodeId
        ? { ...n, thread_statuses: { ...(n.thread_statuses || {}), [tid]: status } }
        : n
    ));
    setDirty(true);
  }, []);
  const setNodeHookStatus = useCallback((nodeId: string, hid: string, status: string) => {
    setNodes(prev => prev.map(n =>
      n.id === nodeId
        ? { ...n, hook_statuses: { ...(n.hook_statuses || {}), [hid]: status } }
        : n
    ));
    setDirty(true);
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

  /** Color stripes for the card's top accent. Driven by the card's own
   *  thread/hook membership, not the filter state:
   *    · 1 stripe per assigned 故事线/伏笔 (multi-stack if several)
   *    · single neutral gray stripe when the card is orphan (no lane)
   *  The filter only affects which lanes get SVG connector lines — it
   *  never repaints the cards themselves. */
  const nodeStripes = useCallback((n: StoryNode): string[] => {
    const colors = nodeLaneKeys(n)
      .map(k => lanes.find(l => l.key === k)?.color)
      .filter((c): c is string => !!c && c !== "var(--text-tertiary)");
    return colors.length > 0 ? colors : ["var(--text-tertiary)"];
  }, [lanes, nodeLaneKeys]);

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

  // ── Auto-trigger 智能排序 only on lane-membership changes ──
  // A signature of every node's (thread_ids + hook_ids). When this
  // string changes between renders, exactly one card switched lanes
  // (or got created / deleted) → re-run smartSortNodes. Manual drags
  // only move x, which isn't in the signature, so dragging never
  // triggers an auto-resort.
  const laneSignature = useMemo(() => {
    return nodes.map(n => {
      const t = readThreadIds(n).join(",");
      const h = readHookIds(n).join(",");
      return `${n.id}:${t}|${h}`;
    }).sort().join(";");
  }, [nodes, readThreadIds, readHookIds]);
  const prevLaneSig = useRef<string | null>(null);
  useEffect(() => {
    if (prevLaneSig.current === null) {
      prevLaneSig.current = laneSignature;
      return;
    }
    if (prevLaneSig.current !== laneSignature) {
      prevLaneSig.current = laneSignature;
      smartSortNodes();
    }
  }, [laneSignature, smartSortNodes]);

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
  }, [nodes, lanes, activeLaneKeys, zoom]);

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

  // --- Bottom 故事中时间 strip: group cards by their `time` value so the
  //     timeline has ONE tick per unique in-story moment, regardless of
  //     how many 情节 happen at it. Each slot keeps the list of cards that
  //     share that moment — clicking a tick sets `highlightedTime`, and
  //     `timeHighlightIds` (below) drives the light-red overlay on every
  //     card in that slot. Tick clicks do NOT touch card selection.
  const normTime = (t: string | undefined): string => (t || "").trim();
  /** Compute a sortable "story-day index" for each 情节.
   *  · 精确日期 (Y-M-D) → epoch-style day count.
   *  · 模糊日期 第N天     → integer N anchored against the earliest
   *    precise 情节 (treating that 情节's chapter as the anchor day).
   *    For the example "第1章 = 1年1月1日, 第2章 = 第2天 → 1年1月2日":
   *    anchor = { chapter: 1, epoch: dateToEpoch(1,1,1) }. 第2天 →
   *    epoch = anchor.epoch + (2 - 1) = +1 day.
   *  · 无 time → use chapter_num as fallback story-day. */
  const computeStoryDayMap = useCallback(() => {
    const ds = (y: number, m: number, d: number): number => {
      const dt = new Date(y, m - 1, d);
      return Math.floor(dt.getTime() / (24 * 60 * 60 * 1000));
    };
    // Pick the earliest (by chapter_num) 情节 with a fully-specified
    // precise date as the anchor.
    const sortedByChapter = [...nodes].sort((a, b) =>
      (a.chapter_num || 0) - (b.chapter_num || 0)
    );
    let anchorEpoch: number | undefined;
    let anchorChapter: number | undefined;
    for (const n of sortedByChapter) {
      const p = parseStoryTimeNew(n.time || "");
      if (p.mode === "precise" && p.year && p.month && p.day) {
        anchorEpoch = ds(p.year, p.month, p.day);
        anchorChapter = n.chapter_num || 1;
        break;
      }
    }
    const out = new Map<string, number>();
    nodes.forEach(n => {
      const p = parseStoryTimeNew(n.time || "");
      if (p.mode === "precise" && p.year && p.month && p.day) {
        const epoch = ds(p.year, p.month, p.day);
        if (anchorEpoch !== undefined && anchorChapter !== undefined) {
          out.set(n.id, anchorChapter + (epoch - anchorEpoch));
        } else {
          out.set(n.id, epoch);
        }
      } else if (p.mode === "dayN" && p.day) {
        out.set(n.id, p.day);
      } else {
        out.set(n.id, n.chapter_num || 0);
      }
    });
    return out;
  }, [nodes]);

  const timeSlots = useMemo(() => {
    const storyDays = computeStoryDayMap();
    // Sort by (computed story-day, x within row) so the bottom time axis
    // shows 情节 in chronological order — precise dates anchor; fuzzy
    // dates fall on the inferred day; ties resolved by chapter_num then x.
    const sorted = [...nodes].sort((a, b) => {
      const da = storyDays.get(a.id) ?? (a.chapter_num || 0);
      const db = storyDays.get(b.id) ?? (b.chapter_num || 0);
      if (da !== db) return da - db;
      return ((a.chapter_num || 0) - (b.chapter_num || 0))
        || ((a.x || 0) - (b.x || 0));
    });
    const map = new Map<string, StoryNode[]>();
    sorted.forEach(n => {
      const t = normTime(n.time);
      if (!t) return;
      if (!map.has(t)) map.set(t, []);
      map.get(t)!.push(n);
    });
    const slots: { time: string; nodes: StoryNode[] }[] = [];
    map.forEach((ns, t) => slots.push({ time: t, nodes: ns }));
    return slots;
  }, [nodes, computeStoryDayMap]);

  /** Cards in the slot currently highlighted by the bottom time scrubber.
   *  These get a light-red `--accent-subtle` background overlay so a
   *  whole moment lights up at once — distinct from the SELECTED card,
   *  which keeps its red BORDER. The two states are independent: clicking
   *  a time tick does NOT select any card, and clicking a card does NOT
   *  move the time highlight. */
  const timeHighlightIds = useMemo(() => {
    if (!highlightedTime) return new Set<string>();
    const slot = timeSlots.find(s => s.time === highlightedTime);
    if (!slot) return new Set<string>();
    return new Set(slot.nodes.map(n => n.id));
  }, [highlightedTime, timeSlots]);

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
    <div ref={pageRef} className="page-full" style={{ flexDirection: "column", display: "flex", height: "100%", overflow: "hidden" }}>
      <div style={{ display: "flex", flex: 1, minHeight: 0, overflow: "hidden" }}>
        {/* ======== Canvas ======== */}
        <div ref={canvasRef} style={{ flex: 1, minWidth: 0, overflow: "auto", background: "var(--bg-app)", position: "relative" }}>
          {/* Toolbar — wraps to a second row on narrow viewports so the
              buttons never overlap the title or each other. */}
          <div
            className="panel-header"
            style={{
              position: "sticky",
              top: 0,
              zIndex: 10,
              minHeight: HEADER_H,
              gap: 10,
              display: "flex",
              alignItems: "center",
              flexWrap: "wrap",
              padding: "10px 20px",
              background: "var(--bg-surface)",
              borderBottom: "1px solid var(--border)",
              boxShadow: "0 1px 0 rgba(0,0,0,0.02)",
            }}
          >
            <h3 className="font-serif" style={{ letterSpacing: 0.5 }}>故事线</h3>
            <span className="text-xs" style={{
              marginLeft: 12, color: "var(--text-tertiary)",
              padding: "2px 10px", borderRadius: 10,
              background: "var(--bg-secondary)",
            }}>
              {nodes.length} 情节 · {chapterTitles.size || 0} 章
            </span>
            <div className="flex gap-8" style={{ marginLeft: "auto", alignItems: "center", flexWrap: "wrap" }}>
              {/* Zoom controls — scale the rows canvas via CSS zoom so
                  layout + SVG measurements scale together. */}
              <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <button className="btn" style={{ fontSize: 11, padding: "4px 9px" }}
                  onClick={() => setZoom(z => Math.max(0.4, +(z - 0.1).toFixed(2)))}
                  title="缩小">−</button>
                <span style={{ fontSize: 10, color: "var(--text-tertiary)", minWidth: 36, textAlign: "center" }}>
                  {Math.round(zoom * 100)}%
                </span>
                <button className="btn" style={{ fontSize: 11, padding: "4px 9px" }}
                  onClick={() => setZoom(z => Math.min(2, +(z + 0.1).toFixed(2)))}
                  title="放大">+</button>
                <button className="btn" style={{ fontSize: 10, padding: "4px 9px" }}
                  onClick={() => setZoom(1)}
                  title="100%">重置</button>
              </div>
              <button className="btn" style={{ fontSize: 12, padding: "6px 14px" }} onClick={addChapter}
                title="在末尾新建一章（同步进编辑器卷/章 结构）">
                + 添加章节
              </button>
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

          {/* 故事线 / 伏笔 总览 + CRUD —— 直接在本页 inline 编辑，
              不再跳到 故事中世界 page。 */}
          <ThreadSummaryStrip
            projectId={projectId || "default"}
            threads={threads} hooks={hooks}
            nodes={nodes}
            chapterTitles={chapterTitles}
            chapterStatuses={chapterStatuses}
            reload={reloadThreadsHooks}
            toast={toast} />

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
              // max-content so the column expands to the widest row.
              // Canvas scrolls horizontally when total width > viewport.
              // zoom = CSS scaling that ALSO scales layout, so scrollbars
              // and getBoundingClientRect stay in sync.
              width: "max-content", minWidth: "calc(100% - 0px)",
              display: "flex", flexDirection: "column", gap: 16,
              zoom,
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
                const isActiveDrag = !!(dragSnap && dragSnap.active);
                const isDropTarget = isActiveDrag && dragSnap?.targetChapter === chap_num;
                return (
                  <div
                    key={chap_num}
                    ref={(el) => { rowRefs.current.set(chap_num, el); }}
                    data-chapter-num={chap_num}
                    style={{
                      display: "flex", alignItems: "stretch",
                      // Row takes natural width — wider rows extend
                      // past the canvas so the WHOLE canvas scrolls
                      // horizontally, instead of each row scrolling
                      // individually inside its own cards box.
                      width: "max-content", minWidth: "100%",
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
                                background: t.thread_type === "main" ? "var(--jade-subtle)" : "var(--bg-surface)",
                                color: t.thread_type === "main" ? "var(--jade)" : "var(--text-secondary)",
                                borderColor: t.thread_type === "main" ? "var(--jade)" : "var(--border)",
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
                      minHeight: 184,
                      padding: "16px 18px",
                      display: "flex",
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
                          stored x. During an active drag the dragged
                          card is filtered out of its row and replaced by
                          a dashed 淡灰色虚影 placeholder at the proposed
                          drop index — flex naturally re-flows the other
                          cards so the user sees the new order before
                          releasing. Manual order via drag; lane semantics
                          via the SVG connectors above. */}
                      {(() => {
                        const sortedCards = chapNodes
                          .slice()
                          .sort((a, b) => (a.x || 0) - (b.x || 0))
                          .filter(n => !(isActiveDrag && dragSnap!.id === n.id));
                        const placeholder = (
                          <div key="__drop__" style={{
                            width: NODE_W, minHeight: NODE_H, flexShrink: 0,
                            borderRadius: 10,
                            border: "2px dashed var(--text-tertiary)",
                            background: "var(--bg-secondary)",
                            opacity: 0.55,
                            transition: "opacity 0.12s",
                          }} />
                        );
                        const renderCard = (n: StoryNode) => {
                        const isSelected = selected === n.id;
                        const isTimeHighlighted = timeHighlightIds.has(n.id);
                        // The card-side chips only show the first assigned
                        // thread / hook (acts as a label); the full
                        // multi-lane membership is communicated through
                        // the stacked color stripes at the top of the card.
                        const _tids = readThreadIds(n);
                        const _hids = readHookIds(n);
                        const stripes = nodeStripes(n);
                        // Two independent states on a card:
                        //  · isSelected → red 2px border (picked via click)
                        //  · isTimeHighlighted → light-red bg overlay
                        //    (picked via the bottom time scrubber; whole
                        //    slot lights up so the user sees every 情节
                        //    happening at that in-story moment)
                        const baseShadow = "0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04)";
                        const selectedShadow = "0 0 0 2px var(--accent), 0 4px 12px rgba(0,0,0,0.08)";
                        const hoverShadow = "0 4px 10px rgba(0,0,0,0.08), 0 2px 4px rgba(0,0,0,0.04)";
                        return (
                          <div
                            key={n.id}
                            ref={(el) => { cardRefs.current.set(n.id, el); }}
                            onMouseDown={(e) => onNodeMouseDown(n.id, e)}
                            onClick={() => setSelected(n.id)}
                            onDoubleClick={() => {
                              setSelected(n.id);
                              if (detailCollapsed) setDetailCollapsed(false);
                            }}
                            className={`timeline-node ${isSelected ? "selected" : ""}`}
                            title={isTimeHighlighted ? `「${highlightedTime}」时段` : undefined}
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
                              background: isTimeHighlighted ? "var(--accent-subtle)" : "var(--bg-card)",
                              overflow: "hidden",
                              boxShadow: isSelected ? selectedShadow : baseShadow,
                              transition: "transform 0.15s ease, box-shadow 0.15s ease, background 0.18s",
                            }}
                            onMouseEnter={(e) => {
                              if (isSelected) return;
                              e.currentTarget.style.transform = "translateY(-2px)";
                              e.currentTarget.style.boxShadow = hoverShadow;
                            }}
                            onMouseLeave={(e) => {
                              if (isSelected) return;
                              e.currentTarget.style.transform = "translateY(0)";
                              e.currentTarget.style.boxShadow = baseShadow;
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
                            {(_tids.length > 0 || _hids.length > 0) && (
                              <div style={{ display: "flex", gap: 4, flexWrap: "wrap", marginBottom: 6 }}>
                                {_tids.map(tid => {
                                  const t = threads.find(tt => tt.thread_id === tid);
                                  if (!t) return null;
                                  const resolved = readThreadStatus(n, tid) === "resolution";
                                  if (resolved) return (
                                    <span key={`t-${tid}`} style={{
                                      fontSize: 9.5, padding: "1.5px 7px", borderRadius: 10,
                                      background: "var(--bg-secondary)", color: "var(--text-tertiary)",
                                      border: "1px solid var(--border)", fontWeight: 500,
                                      opacity: 0.7,
                                    }} title={`${t.description || ""} · 此情节完结`}>
                                      {t.thread_type === "main" ? "主线" : "支线"} · {t.name}
                                    </span>
                                  );
                                  return (
                                    <span key={`t-${tid}`} style={{
                                      fontSize: 9.5, padding: "1.5px 7px", borderRadius: 10,
                                      background: t.thread_type === "main" ? "var(--jade-subtle)" : "transparent",
                                      color: t.thread_type === "main" ? "var(--jade)" : "var(--text-secondary)",
                                      border: t.thread_type === "main" ? "1px solid var(--jade)" : "1px solid var(--border-subtle)",
                                      fontWeight: 500,
                                    }} title={t.description}>
                                      {t.thread_type === "main" ? "主线" : "支线"} · {t.name}
                                    </span>
                                  );
                                })}
                                {_hids.map(hid => {
                                  const h = hooks.find(hh => hh.id === hid);
                                  if (!h) return null;
                                  const resolved = readHookStatus(n, hid) === "resolved";
                                  if (resolved) return (
                                    <span key={`h-${hid}`} style={{
                                      fontSize: 9.5, padding: "1.5px 7px", borderRadius: 10,
                                      background: "var(--bg-secondary)", color: "var(--text-tertiary)", fontWeight: 500,
                                      border: "1px solid var(--border)", opacity: 0.7,
                                    }} title={`${h.content} · 此情节回收`}>
                                      伏笔 · {(h.title || h.content || "").slice(0, 10)}
                                    </span>
                                  );
                                  return (
                                    <span key={`h-${hid}`} style={{
                                      fontSize: 9.5, padding: "1.5px 7px", borderRadius: 10,
                                      background: "var(--gold-subtle)", color: "var(--gold)", fontWeight: 500,
                                      border: "1px solid transparent",
                                    }} title={h.content}>
                                      伏笔 · {(h.title || h.content || "").slice(0, 10)}
                                    </span>
                                  );
                                })}
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
                        };
                        const out: React.ReactNode[] = [];
                        sortedCards.forEach((n, i) => {
                          if (isDropTarget && dragSnap!.targetIndex === i) out.push(placeholder);
                          out.push(renderCard(n));
                        });
                        if (isDropTarget && dragSnap!.targetIndex >= sortedCards.length) {
                          out.push(placeholder);
                        }
                        return out;
                      })()}
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
        {detailCollapsed ? (
          // Collapsed: thin re-open strip on the right edge. Keeps the
          // detail UI one click away without consuming horizontal room.
          <button
            onClick={() => setDetailCollapsed(false)}
            title="展开 情节详情"
            style={{
              width: 28, flexShrink: 0,
              border: "none", borderLeft: "1px solid var(--border)",
              background: "var(--bg-surface)",
              color: "var(--text-tertiary)",
              cursor: "pointer", padding: 0,
              writingMode: "vertical-rl",
              fontSize: 11, letterSpacing: 4,
              transition: "background 0.15s, color 0.15s",
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.background = "var(--accent-subtle)";
              e.currentTarget.style.color = "var(--accent)";
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.background = "var(--bg-surface)";
              e.currentTarget.style.color = "var(--text-tertiary)";
            }}>
            ‹ 情节详情
          </button>
        ) : (
        <>
        {/* Splitter handle — drag to resize the detail panel width. */}
        <div
          onMouseDown={(e) => { e.preventDefault(); setResizingDetail(true); }}
          title="拖动调整宽度"
          style={{
            width: 4, flexShrink: 0,
            background: resizingDetail ? "var(--accent)" : "var(--border)",
            cursor: "col-resize",
            transition: "background 0.12s",
          }}
          onMouseEnter={(e) => { if (!resizingDetail) e.currentTarget.style.background = "var(--accent-subtle)"; }}
          onMouseLeave={(e) => { if (!resizingDetail) e.currentTarget.style.background = "var(--border)"; }}
        />
        <div
          className="panel"
          style={{
            width: detailWidth,
            minWidth: 240,
            maxWidth: 720,
            flexShrink: 0,
            background: "var(--bg-surface)",
            borderLeft: "1px solid var(--border)",
            overflowY: "auto",
            height: "100%",
          }}
        >
          <div className="panel-header" style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "0 12px 0 20px", height: HEADER_H,
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
            <h3 className="font-serif" style={{ letterSpacing: 0.5, flex: 1, minWidth: 0 }}>情节详情</h3>
            <button
              onClick={() => setDetailCollapsed(true)}
              title="收起面板"
              style={{
                border: "none", background: "transparent",
                color: "var(--text-tertiary)",
                cursor: "pointer", fontSize: 16, lineHeight: 1,
                padding: "4px 8px", borderRadius: 6,
                flexShrink: 0,
              }}
              onMouseEnter={(e) => { e.currentTarget.style.color = "var(--accent)"; e.currentTarget.style.background = "var(--accent-subtle)"; }}
              onMouseLeave={(e) => { e.currentTarget.style.color = "var(--text-tertiary)"; e.currentTarget.style.background = "transparent"; }}>
              ›
            </button>
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
                <ChapterNumField
                  value={sel.chapter_num}
                  chapterTitles={chapterTitles}
                  maxChapter={chapterOptions.length > 0 ? Math.max(...chapterOptions) : undefined}
                  onChange={(v) => updateNode(sel.id, "chapter_num", v)}
                />
                <StoryTimeField
                  value={sel.time || ""}
                  onChange={(v) => updateNode(sel.id, "time", v)}
                />
                <LocationField
                  value={sel.location || ""}
                  entities={locationEntities}
                  fallback={uniqueLocations}
                  projectId={projectId || "default"}
                  reload={reloadLocations}
                  toast={toast}
                  onChange={(v) => updateNode(sel.id, "location", v)}
                />

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
                  <label className="label">所属故事线</label>
                  {sortedThreadsForPanel.length === 0 ? (
                    <div className="text-xs text-muted">尚未创建故事线</div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                      {sortedThreadsForPanel.map((t) => {
                        const on = readThreadIds(sel).includes(t.thread_id);
                        const isMain = t.thread_type === "main";
                        const color = isMain ? "var(--jade)" : "var(--text-secondary)";
                        return (
                          <div key={t.thread_id} style={{
                            display: "flex", alignItems: "center", gap: 6,
                          }}>
                            <button
                              onClick={() => {
                                const cur = readThreadIds(sel);
                                const next = cur.includes(t.thread_id)
                                  ? cur.filter(id => id !== t.thread_id)
                                  : [...cur, t.thread_id];
                                updateNode(sel.id, "thread_ids", next);
                                updateNode(sel.id, "thread_id", undefined);
                              }}
                              title={t.description || (isMain ? "主线" : "支线")}
                              style={{
                                fontSize: 11, padding: "4px 10px",
                                borderRadius: 12, cursor: "pointer",
                                border: `${on ? 2 : 1}px solid ${color}`,
                                background: "transparent",
                                color: on ? color : "var(--text-secondary)",
                                fontWeight: on ? 700 : 500,
                                display: "inline-flex", alignItems: "center", gap: 5,
                                flex: 1, justifyContent: "flex-start",
                              }}>
                              <span style={{ color, fontWeight: 700, fontSize: 14, lineHeight: 1 }}>·</span>
                              {isMain ? "主" : "支"} · {t.name}
                            </button>
                            {on && (
                              <StatusChipToggle
                                color={color}
                                options={THREAD_STATUS_EDIT}
                                value={readThreadStatus(sel, t.thread_id)}
                                onChange={(v) => setNodeThreadStatus(sel.id, t.thread_id, v)}
                              />
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
                <div className="field mb-12">
                  <label className="label">所属伏笔</label>
                  {hooks.filter(h => !["resolved", "abandoned"].includes(h.status)).length === 0 ? (
                    <div className="text-xs text-muted">暂无活跃伏笔</div>
                  ) : (
                    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                      {hooks.filter(h => !["resolved", "abandoned"].includes(h.status)).map((h) => {
                        const on = readHookIds(sel).includes(h.id);
                        const label = (h.title || h.content || "（无内容）").slice(0, 18);
                        const color = "var(--gold)";
                        const perStatus = readHookStatus(sel, h.id);
                        return (
                          <div key={h.id} style={{
                            display: "flex", alignItems: "center", gap: 6,
                          }}>
                            <button
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
                                fontSize: 11, padding: "4px 10px",
                                borderRadius: 12, cursor: "pointer",
                                border: `${on ? 2 : 1}px solid ${color}`,
                                background: "transparent",
                                color: on ? color : "var(--text-secondary)",
                                fontWeight: on ? 700 : 500,
                                display: "inline-flex", alignItems: "center", gap: 5,
                                flex: 1, justifyContent: "flex-start",
                              }}>
                              <span style={{ color, fontWeight: 700, fontSize: 14, lineHeight: 1 }}>·</span>
                              伏 · {label}
                            </button>
                            {on && (
                              <HookStatusChipToggle
                                color={color}
                                value={perStatus}
                                onChangeStatus={(v) => setNodeHookStatus(sel.id, h.id, v)}
                              />
                            )}
                          </div>
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
        </>
        )}
      </div>

      {/* ======== 故事中时间 PR-style scrub bar (bottom) ========
          Horizontal track with one tick per UNIQUE 故事中时间 (multi-card
          moments collapse onto a single tick). A circular playhead sits
          at the currently-highlighted slot. Click / drag → SET
          highlightedTime (does NOT select any card); every card at that
          time gets a light-red overlay on the canvas. The card-selection
          and time-highlight states are independent. */}
      <StoryTimeScrubber
        slots={timeSlots}
        highlightedTime={highlightedTime}
        onHighlight={setHighlightedTime}
      />

      {/* Drag-preview ghost — only renders once the threshold is exceeded
          (dragSnap.active). Floats with cursor as `position: fixed`. */}
      {dragSnap && dragSnap.active && (() => {
        const ghost = nodes.find(n => n.id === dragSnap.id);
        if (!ghost) return null;
        return (
          <div style={{
            position: "fixed", zIndex: 999, pointerEvents: "none",
            left: dragSnap.clientX - dragSnap.offX,
            top: dragSnap.clientY - dragSnap.offY,
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
/* ── StoryTimeScrubber ──
 * PR-style scrub bar for the bottom 故事中时间 strip. Renders a
 * horizontal track + one tick per 情节 that has a non-empty `time`,
 * plus a circular playhead at the currently-selected tick. Drag the
 * playhead (or click anywhere on the track) to snap to the nearest
 * tick → setSelected. Pan the track horizontally when ticks overflow. */
function StoryTimeScrubber({ slots, highlightedTime, onHighlight }: {
  slots: { time: string; nodes: StoryNode[] }[];
  highlightedTime: string | null;
  onHighlight: (time: string | null) => void;
}) {
  const STEP = 92;
  // PAD = inner-track horizontal padding. Bumped from 24 → 56 so the
  // first / last tick labels (centered on their tick) don't get clipped
  // by the scroll container's left edge before the user scrolls.
  const PAD = 56;
  const trackInnerRef = React.useRef<HTMLDivElement>(null);
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const dragging = React.useRef(false);
  // Pan-with-grab when the user middle-clicks / holds the empty bar.
  const panStart = React.useRef<{ x: number; scroll: number } | null>(null);

  const highlightedIdx = slots.findIndex(s => s.time === highlightedTime);
  const cur = highlightedIdx >= 0 ? slots[highlightedIdx] : null;

  // Keep the playhead in view when the highlight changes from outside.
  React.useEffect(() => {
    if (highlightedIdx < 0 || !scrollRef.current) return;
    const handleX = PAD + highlightedIdx * STEP;
    const view = scrollRef.current;
    const left = view.scrollLeft;
    const right = left + view.clientWidth;
    if (handleX < left + 40) view.scrollTo({ left: Math.max(0, handleX - 40), behavior: "smooth" });
    else if (handleX > right - 40) view.scrollTo({ left: handleX - view.clientWidth + 40, behavior: "smooth" });
  }, [highlightedIdx]);

  /** Resolve a slot under `clientX` and apply highlight semantics.
   *  In `toggle` mode (initial pointer down) we deselect the slot if it
   *  is already the current highlight — so click→same-tick→clear works.
   *  In `set` mode (drag-scrub) we always commit the slot under cursor
   *  so dragging doesn't accidentally clear by returning to the start. */
  const snapToPointer = (clientX: number, mode: "toggle" | "set") => {
    if (!trackInnerRef.current) return;
    const rect = trackInnerRef.current.getBoundingClientRect();
    const x = clientX - rect.left - PAD;
    const idx = Math.round(x / STEP);
    const clamped = Math.max(0, Math.min(slots.length - 1, idx));
    const slot = slots[clamped];
    if (!slot) return;
    if (mode === "toggle" && highlightedTime === slot.time) onHighlight(null);
    else onHighlight(slot.time);
  };

  const onPointerDownTrack = (e: React.PointerEvent) => {
    // Middle button or Alt-drag = pan; otherwise treat as scrub.
    if (e.button === 1 || e.altKey) {
      panStart.current = { x: e.clientX, scroll: scrollRef.current?.scrollLeft || 0 };
      (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
      return;
    }
    e.preventDefault();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    dragging.current = true;
    snapToPointer(e.clientX, "toggle");
  };
  const onPointerMoveTrack = (e: React.PointerEvent) => {
    if (panStart.current && scrollRef.current) {
      const dx = e.clientX - panStart.current.x;
      scrollRef.current.scrollLeft = panStart.current.scroll - dx;
      return;
    }
    if (!dragging.current) return;
    snapToPointer(e.clientX, "set");
  };
  const onPointerUpTrack = (e: React.PointerEvent) => {
    if ((e.currentTarget as HTMLElement).hasPointerCapture(e.pointerId)) {
      try { (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId); } catch { /* noop */ }
    }
    dragging.current = false;
    panStart.current = null;
  };

  // 缩短轴高 + 把 track 推到上 1/3：原 (76, 50) → (68, 30)。这样
  // 整条轴占用更少底部空间（视觉上「往上一些」），同时 track 下方
  // 仍有 ~30px 给「第N章 · X 情节」副标签。
  const TIMELINE_H = 68;
  const TRACK_Y = 30;
  return (
    <div style={{
      height: TIMELINE_H, flexShrink: 0,
      background: "var(--bg-surface)",
      borderTop: "1px solid var(--border)",
      display: "flex", alignItems: "stretch",
      boxShadow: "0 -1px 0 rgba(0,0,0,0.02)",
    }}>
      <div style={{
        display: "flex", flexDirection: "column", alignItems: "flex-start",
        gap: 2, padding: "8px 16px", whiteSpace: "nowrap", flexShrink: 0,
        borderRight: "1px solid var(--border-subtle)", minWidth: 140,
        justifyContent: "center",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{
            width: 6, height: 6, borderRadius: "50%",
            background: "var(--accent)", display: "inline-block",
          }} />
          <span style={{ fontSize: 11, fontWeight: 700, color: "var(--text-secondary)", letterSpacing: 0.4 }}>
            故事中时间
          </span>
        </div>
        <span style={{
          fontSize: 12, fontWeight: 600, color: cur ? "var(--accent)" : "var(--text-disabled)",
          fontStyle: cur ? "normal" : "italic",
          overflow: "hidden", textOverflow: "ellipsis",
          maxWidth: 200,
        }} title={cur ? `${cur.time} · ${cur.nodes.length} 情节` : undefined}>
          {cur ? `${cur.time}${cur.nodes.length > 1 ? ` · ${cur.nodes.length} 情节` : ""}` : "—"}
        </span>
      </div>

      {slots.length === 0 ? (
        <div style={{ flex: 1, display: "flex", alignItems: "center", padding: "0 16px" }}>
          <span style={{ fontSize: 11, color: "var(--text-disabled)", fontStyle: "italic" }}>
            暂无故事中时间，在情节详情中填写「故事中时间」
          </span>
        </div>
      ) : (
        <div
          ref={scrollRef}
          style={{
            flex: 1, minWidth: 0, position: "relative",
            overflowX: "auto", overflowY: "hidden",
            cursor: dragging.current ? "grabbing" : "default",
          }}
          onPointerDown={onPointerDownTrack}
          onPointerMove={onPointerMoveTrack}
          onPointerUp={onPointerUpTrack}
        >
          <div
            ref={trackInnerRef}
            style={{
              position: "relative",
              width: PAD * 2 + Math.max(0, slots.length - 1) * STEP,
              minWidth: "100%",
              height: TIMELINE_H,
              touchAction: "none",
              userSelect: "none",
            }}
          >
            {/* Horizontal track line */}
            <div style={{
              position: "absolute",
              top: TRACK_Y, left: PAD, right: PAD, height: 2,
              background: "var(--border)", borderRadius: 1,
            }} />
            {/* Time labels above the track. First / last labels anchor
                to the tick's left / right edge instead of centering, so
                long text doesn't get clipped at the scroll boundary. */}
            {slots.map((s, i) => {
              const x = PAD + i * STEP;
              const isCurrent = i === highlightedIdx;
              const isFirst = i === 0;
              const isLast = i === slots.length - 1;
              const transform = isFirst ? "none"
                : isLast ? "translateX(-100%)"
                : "translateX(-50%)";
              const textAlign: React.CSSProperties["textAlign"] = isFirst ? "left"
                : isLast ? "right" : "center";
              return (
                <div key={`label-${s.time}`} style={{
                  position: "absolute",
                  left: x, top: 4,
                  transform,
                  fontSize: 10,
                  fontWeight: isCurrent ? 700 : 500,
                  color: isCurrent ? "var(--accent)" : "var(--text-tertiary)",
                  whiteSpace: "nowrap",
                  pointerEvents: "none",
                  maxWidth: STEP + 20,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  textAlign,
                }} title={s.time}>
                  {s.time}
                </div>
              );
            })}
            {/* Tick marks on the track */}
            {slots.map((s, i) => {
              const x = PAD + i * STEP;
              const isCurrent = i === highlightedIdx;
              return (
                <div key={`tick-${s.time}`} style={{
                  position: "absolute",
                  left: x, top: TRACK_Y - 5,
                  transform: "translateX(-50%)",
                  width: 2, height: 12,
                  background: isCurrent ? "var(--accent)" : "var(--text-tertiary)",
                  opacity: isCurrent ? 1 : 0.6,
                  pointerEvents: "none",
                  borderRadius: 1,
                }} />
              );
            })}
            {/* Chapter/episode count sub-label below tick — same edge
                anchoring as the time labels so first/last sub-text isn't
                clipped either. */}
            {slots.map((s, i) => {
              const x = PAD + i * STEP;
              const isCurrent = i === highlightedIdx;
              const firstNode = s.nodes[0];
              const chapText = firstNode.chapter_num ? `第${firstNode.chapter_num}章` : "";
              const countText = s.nodes.length > 1 ? `${s.nodes.length} 情节` : "";
              const label = [chapText, countText].filter(Boolean).join(" · ");
              const isFirst = i === 0;
              const isLast = i === slots.length - 1;
              const transform = isFirst ? "none"
                : isLast ? "translateX(-100%)"
                : "translateX(-50%)";
              const textAlign: React.CSSProperties["textAlign"] = isFirst ? "left"
                : isLast ? "right" : "center";
              return (
                <div key={`sub-${s.time}`} style={{
                  position: "absolute",
                  left: x, top: TRACK_Y + 12,
                  transform,
                  fontSize: 9,
                  color: isCurrent ? "var(--accent)" : "var(--text-disabled)",
                  whiteSpace: "nowrap",
                  pointerEvents: "none",
                  maxWidth: STEP + 20,
                  overflow: "hidden",
                  textOverflow: "ellipsis",
                  textAlign,
                }}>
                  {label}
                </div>
              );
            })}
            {/* Circular playhead at the highlighted tick */}
            {highlightedIdx >= 0 && (
              <div style={{
                position: "absolute",
                left: PAD + highlightedIdx * STEP, top: TRACK_Y,
                transform: "translate(-50%, -50%)",
                width: 16, height: 16, borderRadius: "50%",
                background: "var(--accent)",
                border: "2px solid var(--bg-surface)",
                boxShadow: "0 0 0 1px var(--accent), 0 2px 6px rgba(0,0,0,0.18)",
                cursor: "grab",
                pointerEvents: "none",
              }} />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

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


/* ── WheelNumberInput ──
 * 数字 input + 滚轮加减。focus 时滚轮 wheel 拦截页面滚动并加减 value。 */
function WheelNumberInput({ value, onChange, min, max, placeholder, width, step = 1 }: {
  value: number | undefined;
  onChange: (v: number | undefined) => void;
  min?: number; max?: number;
  placeholder?: string;
  width?: number | string;
  step?: number;
}) {
  return (
    <input className="input" type="number"
      value={value ?? ""}
      placeholder={placeholder}
      min={min} max={max} step={step}
      onChange={e => onChange(e.target.value ? +e.target.value : undefined)}
      onWheel={e => {
        if (document.activeElement !== e.currentTarget) return;
        e.preventDefault();
        const cur = value ?? 0;
        const delta = e.deltaY < 0 ? step : -step;
        let next = cur + delta;
        if (min !== undefined) next = Math.max(min, next);
        if (max !== undefined) next = Math.min(max, next);
        onChange(next);
      }}
      style={{ fontSize: 12, width }}
    />
  );
}


/* ── ChapterNumField ──
 * 章节号 stepper：左 − / 中数字 + "第 N 章" / 右 +。
 * 中部 input 支持：直接键盘输入；focus 后滚轮加减；点击聚焦后大字号
 * 显示当前章号。下方显示对应章节标题（来自 editor 大纲）作 helper. */
function ChapterNumField({ value, chapterTitles, maxChapter, onChange }: {
  value: number | undefined;
  chapterTitles: Map<number, string>;
  maxChapter?: number;
  onChange: (v: number | undefined) => void;
}) {
  const title = value ? chapterTitles.get(value) : undefined;
  const upperBound = maxChapter ? maxChapter + 50 : undefined;
  const dec = () => {
    if (value === undefined) { onChange(1); return; }
    if (value > 1) onChange(value - 1);
  };
  const inc = () => {
    if (value === undefined) { onChange(1); return; }
    const next = value + 1;
    if (upperBound !== undefined) onChange(Math.min(upperBound, next));
    else onChange(next);
  };
  return (
    <div className="field mb-12">
      <label className="label">章节号</label>
      <div style={{
        display: "inline-flex", alignItems: "stretch",
        height: 44, borderRadius: 10, overflow: "hidden",
        border: "1px solid var(--border)",
        background: "var(--bg-card, var(--bg-surface))",
        boxShadow: "0 1px 2px rgba(0,0,0,0.04)",
      }}>
        <button
          onClick={dec}
          disabled={!value || value <= 1}
          title="上一章"
          style={{
            width: 40, border: "none",
            background: "var(--bg-secondary)",
            color: "var(--text-primary)",
            cursor: (!value || value <= 1) ? "not-allowed" : "pointer",
            fontSize: 18, fontWeight: 600,
            opacity: (!value || value <= 1) ? 0.4 : 1,
            borderRight: "1px solid var(--border)",
          }}>−</button>
        <div style={{
          flex: 1, minWidth: 140,
          display: "flex", alignItems: "center", justifyContent: "center",
          gap: 4,
        }}>
          <span style={{ fontSize: 11, color: "var(--text-tertiary)", fontWeight: 500 }}>第</span>
          <input type="text" inputMode="numeric" pattern="[0-9]*"
            value={value ?? ""}
            placeholder="—"
            onChange={e => {
              const v = e.target.value.replace(/[^0-9]/g, "");
              if (!v) { onChange(undefined); return; }
              let n = +v;
              n = Math.max(1, n);
              if (upperBound !== undefined) n = Math.min(upperBound, n);
              onChange(n);
            }}
            onWheel={e => {
              if (document.activeElement !== e.currentTarget) return;
              e.preventDefault();
              const cur = value ?? 0;
              const delta = e.deltaY < 0 ? 1 : -1;
              let next = cur + delta;
              next = Math.max(1, next);
              if (upperBound !== undefined) next = Math.min(upperBound, next);
              onChange(next);
            }}
            style={{
              border: "none", background: "transparent",
              fontSize: 20, fontWeight: 700,
              color: value ? "var(--accent)" : "var(--text-disabled)",
              textAlign: "center", width: 64, padding: 0, outline: "none",
            }} />
          <span style={{ fontSize: 11, color: "var(--text-tertiary)", fontWeight: 500 }}>章</span>
        </div>
        <button
          onClick={inc}
          title="下一章"
          style={{
            width: 40, border: "none",
            background: "var(--bg-secondary)",
            color: "var(--text-primary)",
            cursor: "pointer",
            fontSize: 18, fontWeight: 600,
            borderLeft: "1px solid var(--border)",
          }}>+</button>
      </div>
      <div style={{
        marginTop: 6, fontSize: 11, lineHeight: 1.5,
        color: value ? "var(--text-secondary)" : "var(--text-disabled)",
        minHeight: 18,
      }}>
        {!value ? (
          <span style={{ fontStyle: "italic" }}>未指定章节</span>
        ) : title ? (
          <span><strong style={{ color: "var(--text-primary)" }}>{title}</strong></span>
        ) : (
          <span style={{ color: "var(--text-disabled)", fontStyle: "italic" }}>
            编辑器中尚无此章
          </span>
        )}
      </div>
    </div>
  );
}


/* ── DateStepperRow ──
 * 单行 stepper：[label] [− N +]。复用于 故事中时间·精确日期 模式的
 * 年/月/日，外观与 章节号 控件一致。空值显示为「—」。 */
function DateStepperRow({ label, value, onChange, min, max }: {
  label: string;
  value: number | undefined;
  onChange: (v: number | undefined) => void;
  min?: number;
  max?: number;
}) {
  const clamp = (n: number): number => {
    if (min !== undefined) n = Math.max(min, n);
    if (max !== undefined) n = Math.min(max, n);
    return n;
  };
  const dec = () => {
    if (value === undefined) { onChange(min ?? 1); return; }
    if (min === undefined || value > min) onChange(clamp(value - 1));
  };
  const inc = () => {
    if (value === undefined) { onChange(min ?? 1); return; }
    if (max === undefined || value < max) onChange(clamp(value + 1));
  };
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
      <span style={{
        fontSize: 11, color: "var(--text-tertiary)", width: 48,
        flexShrink: 0, fontWeight: 500,
      }}>{label}</span>
      <div style={{
        display: "inline-flex", alignItems: "stretch",
        height: 36, borderRadius: 8, overflow: "hidden",
        border: "1px solid var(--border)",
        background: "var(--bg-card, var(--bg-surface))",
        flex: 1,
      }}>
        <button onClick={dec}
          disabled={value !== undefined && min !== undefined && value <= min}
          style={{
            width: 36, border: "none",
            background: "var(--bg-secondary)",
            color: "var(--text-primary)",
            cursor: (value !== undefined && min !== undefined && value <= min) ? "not-allowed" : "pointer",
            fontSize: 16, fontWeight: 600,
            opacity: (value !== undefined && min !== undefined && value <= min) ? 0.4 : 1,
            borderRight: "1px solid var(--border)",
          }}>−</button>
        <input type="text" inputMode="numeric" pattern="[0-9]*"
          value={value ?? ""}
          placeholder="—"
          onChange={e => {
            const v = e.target.value.replace(/[^0-9]/g, "");
            if (!v) { onChange(undefined); return; }
            onChange(clamp(+v));
          }}
          onWheel={e => {
            if (document.activeElement !== e.currentTarget) return;
            e.preventDefault();
            const cur = value ?? (min ?? 1);
            const delta = e.deltaY < 0 ? 1 : -1;
            onChange(clamp(cur + delta));
          }}
          style={{
            flex: 1, border: "none", background: "transparent",
            fontSize: 16, fontWeight: 700,
            color: value !== undefined ? "var(--accent)" : "var(--text-disabled)",
            textAlign: "center", padding: 0, outline: "none", minWidth: 0,
          }} />
        <button onClick={inc}
          disabled={value !== undefined && max !== undefined && value >= max}
          style={{
            width: 36, border: "none",
            background: "var(--bg-secondary)",
            color: "var(--text-primary)",
            cursor: (value !== undefined && max !== undefined && value >= max) ? "not-allowed" : "pointer",
            fontSize: 16, fontWeight: 600,
            opacity: (value !== undefined && max !== undefined && value >= max) ? 0.4 : 1,
            borderLeft: "1px solid var(--border)",
          }}>+</button>
      </div>
    </div>
  );
}


/* ── StoryTimeField ──
 * 两种故事中时间模式：
 *   · 精确日期：年 / 月 / 日 — 任一可空
 *   · 第N天 + 季节：N (number) + 季节 (春/夏/秋/冬，可空)
 * 时段（凌晨/上午/下午/夜晚）所有模式都可附加，且可空。
 *
 * 写入 node.time 的字符串协议（多张 情节 卡可据此 grouping）：
 *   precise:  "{Y}年{M}月{D}日[·{period}]"
 *   dayN:     "[{season}]第{N}天[·{period}]" 或仅 "{season}"
 *   仅时段:   "·{period}"   (头部为空时仍保留前导 · 让 parse 还能识别)
 *
 * Mode 切换 bug：useEffect 不再无条件同步 mode；只有当 parsed value
 * 有实质内容（year/month/day/season 之一）时才更新 mode；否则保留
 * 用户当前的 mode 选择，避免「先选时段后无法切换 tab」的循环。 */
type StoryTimeMode = "precise" | "dayN";
type StoryTimePeriod = "凌晨" | "上午" | "下午" | "夜晚" | "";
const NEW_TIME_PERIODS: StoryTimePeriod[] = ["凌晨", "上午", "下午", "夜晚"];
const SEASONS = ["春", "夏", "秋", "冬"] as const;

function parseStoryTimeNew(s: string): {
  mode: StoryTimeMode; year?: number; month?: number; day?: number;
  season?: typeof SEASONS[number]; period: StoryTimePeriod;
} {
  s = (s || "").trim();
  // 兼容 "·凌晨" 与 legacy 裸 "凌晨"。
  const periodRe = /·?(凌晨|上午|下午|夜晚)$/;
  const pm = s.match(periodRe);
  const period = (pm?.[1] || "") as StoryTimePeriod;
  let body = pm ? s.slice(0, pm.index) : s;
  // 去除中间的 · 分隔符
  body = body.replace(/·$/, "");

  // [season]第N天 或单独 season（兼容 "春" 或 "春天"）
  const dn = body.match(/^(春|夏|秋|冬)?(?:第(\d+)天)?$/);
  if (dn && (dn[1] || dn[2])) {
    return {
      mode: "dayN",
      season: dn[1] as any,
      day: dn[2] ? +dn[2] : undefined,
      period,
    };
  }
  for (const season of SEASONS) {
    if (body === `${season}天`) {
      return { mode: "dayN", season, period };
    }
  }

  // 精确：YYYY年M月D日 (各可空)
  const pm2 = body.match(/^(?:(\d+)年)?(?:(\d+)月)?(?:(\d+)日)?$/);
  if (pm2) {
    return {
      mode: "precise",
      year: pm2[1] ? +pm2[1] : undefined,
      month: pm2[2] ? +pm2[2] : undefined,
      day: pm2[3] ? +pm2[3] : undefined,
      period,
    };
  }
  return { mode: "precise", period };
}

function formatStoryTimeNew(v: {
  mode: StoryTimeMode; year?: number; month?: number; day?: number;
  season?: typeof SEASONS[number]; period: StoryTimePeriod;
}): string {
  const periodSuffix = v.period ? `·${v.period}` : "";
  let head = "";
  if (v.mode === "dayN") {
    const seasonPart = v.season || "";
    const dayPart = v.day ? `第${v.day}天` : "";
    head = seasonPart + dayPart;
  } else {
    const parts: string[] = [];
    if (v.year) parts.push(`${v.year}年`);
    if (v.month) parts.push(`${v.month}月`);
    if (v.day) parts.push(`${v.day}日`);
    head = parts.join("");
  }
  // 头部为空但有 period → 保留前导 · 让 parser 还能识别为「仅时段」
  if (!head && periodSuffix) return periodSuffix;
  return head + periodSuffix;
}

function StoryTimeField({ value, onChange }: {
  value: string;
  onChange: (v: string) => void;
}) {
  const parsed = parseStoryTimeNew(value);
  const [mode, setMode] = React.useState<StoryTimeMode>(parsed.mode);

  // 只有 value 带实质 head 字段（年/月/日/季节）时才反向同步 mode。
  // 仅 period 的情况保留用户当前模式选择，避免切 tab 后被 parse fallback
  // 重置回 precise 的死循环。
  React.useEffect(() => {
    const p = parseStoryTimeNew(value);
    const hasContent = !!(p.year || p.month || p.day || p.season);
    if (hasContent) setMode(p.mode);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);

  const setField = (patch: Partial<typeof parsed>) => {
    const next = { ...parsed, mode, ...patch };
    onChange(formatStoryTimeNew(next));
  };

  const tabStyle = (active: boolean): React.CSSProperties => ({
    fontSize: 11, padding: "4px 10px",
    border: "none", borderRadius: 6,
    background: active ? "var(--accent)" : "transparent",
    color: active ? "#fff" : "var(--text-tertiary)",
    cursor: "pointer", fontWeight: 600,
  });

  return (
    <div className="field mb-12">
      <label className="label">故事中时间</label>
      <div style={{
        display: "flex", border: "1px solid var(--border)",
        borderRadius: 8, padding: 1, marginBottom: 10, width: "100%",
      }}>
        {(["precise", "dayN"] as StoryTimeMode[]).map(m => (
          <button key={m}
            onClick={() => {
              setMode(m);
              // 切换模式时只保留 period，其他字段清空。
              onChange(formatStoryTimeNew({ mode: m, period: parsed.period }));
            }}
            style={{
              ...tabStyle(mode === m),
              flex: 1, padding: "6px 10px",
            }}>
            {m === "precise" ? "精确日期" : "模糊日期"}
          </button>
        ))}
      </div>

      {mode === "precise" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <DateStepperRow label="年"
            value={parsed.year}
            onChange={v => setField({ year: v })}
            min={1} />
          <DateStepperRow label="月"
            value={parsed.month}
            onChange={v => setField({ month: v })}
            min={1} max={12} />
          <DateStepperRow label="日"
            value={parsed.day}
            onChange={v => setField({ day: v })}
            min={1} max={31} />
        </div>
      )}
      {mode === "dayN" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <DateStepperRow label="第 N 天"
            value={parsed.day}
            onChange={v => setField({ day: v })}
            min={1} />
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 11, color: "var(--text-tertiary)", width: 48 }}>季节</span>
            <div style={{
              display: "flex", border: "1px solid var(--border)",
              borderRadius: 8, padding: 1, flex: 1,
            }}>
              <button onClick={() => setField({ season: undefined })}
                style={{ ...tabStyle(!parsed.season), flex: 1 }}>—</button>
              {SEASONS.map(s => (
                <button key={s}
                  onClick={() => setField({ season: s })}
                  style={{ ...tabStyle(parsed.season === s), flex: 1 }}>
                  {s}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      <div style={{ marginTop: 8 }}>
        <div style={{ fontSize: 10, color: "var(--text-tertiary)", marginBottom: 4 }}>
          时段（可空）
        </div>
        <div style={{
          display: "inline-flex", border: "1px solid var(--border)",
          borderRadius: 8, padding: 1, flexWrap: "wrap",
        }}>
          <button onClick={() => setField({ period: "" })}
            style={tabStyle(!parsed.period)}>—</button>
          {NEW_TIME_PERIODS.map(p => (
            <button key={p}
              onClick={() => setField({ period: p })}
              style={tabStyle(parsed.period === p)}>
              {p}
            </button>
          ))}
        </div>
      </div>

      <div className="text-xs" style={{ color: "var(--text-tertiary)", marginTop: 6, lineHeight: 1.4 }}>
        当前：{value || <span style={{ fontStyle: "italic", opacity: 0.7 }}>未指定</span>}
      </div>
    </div>
  );
}


/* ── LocationField ──
 * 搜索框式地点选择。数据源：location 类型 entities (/api/entities)
 * 与项目里已使用的 location string 合并。下拉显示 filter 结果；
 * 用户可点选已有项，也可在搜索框 query 不命中时点「+ 新增"X"」
 * 直接 POST /api/entities 创建新 location entity，避免拼写歧义。 */
function LocationField({ value, entities, fallback, projectId, reload, toast, onChange }: {
  value: string;
  entities: Array<{ entity_id: string; name: string; description?: string }>;
  fallback: string[];
  projectId: string;
  reload: () => Promise<void>;
  toast: (m: string, t?: any) => void;
  onChange: (v: string) => void;
}) {
  const [open, setOpen] = React.useState(false);
  const [query, setQuery] = React.useState("");
  const [creating, setCreating] = React.useState(false);
  const wrapRef = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [open]);

  const options = React.useMemo(() => {
    const map = new Map<string, { name: string; description?: string; isEntity: boolean }>();
    entities.forEach(e => map.set(e.name, { name: e.name, description: e.description, isEntity: true }));
    fallback.forEach(name => {
      if (!map.has(name)) map.set(name, { name, isEntity: false });
    });
    return Array.from(map.values()).sort((a, b) => a.name.localeCompare(b.name));
  }, [entities, fallback]);

  const filtered = React.useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return options;
    return options.filter(o =>
      o.name.toLowerCase().includes(q)
      || (o.description || "").toLowerCase().includes(q)
    );
  }, [options, query]);

  const exactMatch = query.trim() && options.some(o => o.name === query.trim());

  const pick = (name: string) => {
    onChange(name);
    setOpen(false);
    setQuery("");
  };

  const createNew = async () => {
    const name = query.trim();
    if (!name) return;
    setCreating(true);
    try {
      await apiPost("/api/entities", {
        project_id: projectId,
        name,
        entity_type: "location",
        description: "",
      });
      await reload();
      onChange(name);
      setOpen(false);
      setQuery("");
    } catch (e: any) {
      toast(e?.message || "新增地点失败", "error");
    } finally {
      setCreating(false);
    }
  };

  const matchedEntity = entities.find(e => e.name === value);

  return (
    <div ref={wrapRef} className="field mb-12" style={{ position: "relative" }}>
      <label className="label">地点</label>
      <div
        onClick={() => setOpen(o => !o)}
        style={{
          display: "flex", alignItems: "center", gap: 8,
          minHeight: 38, padding: "6px 10px",
          background: "var(--bg-card, var(--bg-surface))",
          border: `1px solid ${open ? "var(--accent)" : "var(--border)"}`,
          borderRadius: 8,
          cursor: "pointer",
          transition: "border-color 0.15s",
        }}>
        {value ? (
          <>
            <span style={{
              fontSize: 11, color: matchedEntity ? "var(--text-primary)" : "var(--text-secondary)",
              fontStyle: matchedEntity ? "normal" : "italic",
              flex: 1,
            }}>
              {value}
              {!matchedEntity && (
                <span className="text-xs" style={{ color: "var(--text-disabled)", marginLeft: 6 }}>
                  · 未登记
                </span>
              )}
            </span>
            <button onClick={(e) => { e.stopPropagation(); onChange(""); }}
              title="清除"
              style={{
                border: "none", background: "transparent", padding: "0 6px",
                color: "var(--text-tertiary)", cursor: "pointer",
                fontSize: 14, lineHeight: 1,
              }}>×</button>
          </>
        ) : (
          <span style={{
            fontSize: 12, color: "var(--text-tertiary)",
            display: "inline-flex", alignItems: "center", gap: 6,
          }}>
            <span style={{ fontSize: 14, lineHeight: 1, opacity: 0.7 }}>+</span>
            点击搜索 / 选择地点...
          </span>
        )}
      </div>
      {open && (
        <div style={{
          position: "absolute", zIndex: 50, left: 0, right: 0, top: "100%",
          marginTop: 4, background: "var(--bg-surface)",
          border: "1px solid var(--border)", borderRadius: 8,
          boxShadow: "0 8px 20px rgba(0,0,0,0.12)",
          maxHeight: 320, display: "flex", flexDirection: "column",
        }}>
          <div style={{ padding: "8px 10px", borderBottom: "1px solid var(--border)" }}>
            <input className="input" autoFocus
              placeholder="搜索 / 输入新地点名"
              value={query}
              onChange={e => setQuery(e.target.value)}
              onKeyDown={e => {
                if (e.key === "Enter") {
                  const q = query.trim();
                  if (q) {
                    const exact = options.find(o => o.name === q);
                    if (exact) pick(exact.name);
                    else createNew();
                  }
                }
                if (e.key === "Escape") setOpen(false);
              }}
              style={{ fontSize: 12, width: "100%" }} />
          </div>
          <div style={{ overflowY: "auto", flex: 1 }}>
            {filtered.length === 0 ? (
              <div className="text-xs text-muted" style={{ padding: 12, textAlign: "center" }}>
                {query.trim() ? "无匹配地点" : "项目中暂无地点"}
              </div>
            ) : filtered.map(opt => (
              <div key={opt.name}
                onClick={() => pick(opt.name)}
                style={{
                  display: "flex", alignItems: "center", gap: 10,
                  padding: "8px 12px", cursor: "pointer",
                  background: value === opt.name ? "var(--accent-subtle)" : undefined,
                  borderLeft: value === opt.name ? "3px solid var(--accent)" : "3px solid transparent",
                  transition: "background 0.12s",
                }}
                onMouseEnter={e => { if (value !== opt.name) e.currentTarget.style.background = "var(--bg-secondary)"; }}
                onMouseLeave={e => { if (value !== opt.name) e.currentTarget.style.background = ""; }}>
                <span style={{
                  width: 22, height: 22, borderRadius: 5,
                  background: opt.isEntity ? "var(--accent-subtle)" : "var(--bg-secondary)",
                  color: opt.isEntity ? "var(--accent)" : "var(--text-tertiary)",
                  fontSize: 11, fontWeight: 700,
                  display: "inline-flex", alignItems: "center", justifyContent: "center",
                  flexShrink: 0,
                }} title={opt.isEntity ? "地点实体（已登记）" : "未登记（仅在情节中出现过）"}>
                  ⌂
                </span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 600 }}>{opt.name}</div>
                  {opt.description && (
                    <div style={{ fontSize: 10, color: "var(--text-tertiary)", marginTop: 1 }}>
                      {opt.description.slice(0, 40)}
                    </div>
                  )}
                </div>
              </div>
            ))}
          </div>
          {query.trim() && !exactMatch && (
            <div style={{ borderTop: "1px solid var(--border)", background: "var(--bg-secondary)" }}>
              <button
                disabled={creating}
                onClick={createNew}
                style={{
                  width: "100%", padding: "10px 12px", textAlign: "left",
                  background: "transparent", border: "none",
                  color: "var(--accent)", cursor: "pointer",
                  fontSize: 12, fontWeight: 600,
                  display: "flex", alignItems: "center", gap: 8,
                }}>
                <span style={{ fontSize: 14, lineHeight: 1 }}>+</span>
                {creating ? "新增中..." : `新增地点实体「${query.trim()}」`}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}


/* ── StatusChipToggle ──
 * 主题色 chip-toggle group 用于编辑故事线 / 伏笔的状态。比原生
 * `<select>` 视觉更轻、与黑红主题更搭。Legacy 状态值（不在 options
 * 里）以"原值"display 但点击 options 后即覆盖。 */
function StatusChipToggle({ color, options, value, onChange, fallbackLabel }: {
  color: string;
  options: Record<string, string>;
  value: string;
  onChange: (v: string) => void;
  fallbackLabel?: string;
}) {
  const inOptions = Object.keys(options).includes(value);
  return (
    <div style={{
      display: "inline-flex", border: `1px solid ${color}`,
      borderRadius: 8, padding: 1, height: 26,
      background: "var(--bg-card, var(--bg-surface))",
    }}>
      {!inOptions && value && (
        <span style={{
          fontSize: 10, padding: "0 8px",
          color: "var(--text-disabled)",
          display: "inline-flex", alignItems: "center",
          fontStyle: "italic",
          borderRight: "1px solid var(--border)",
        }} title="非标准状态值">
          {fallbackLabel || value}
        </span>
      )}
      {Object.entries(options).map(([k, v]) => (
        <button key={k}
          onClick={() => onChange(k)}
          style={{
            fontSize: 10, padding: "0 10px",
            border: "none", borderRadius: 6,
            background: value === k ? color : "transparent",
            color: value === k ? "#fff" : color,
            cursor: "pointer", fontWeight: 600,
          }}>
          {v}
        </button>
      ))}
    </div>
  );
}


/* ── HookStatusChipToggle ──
 * 伏笔在本 情节 卡中的角色：埋设 / 推进 / 回收。完全用户可选 —
 * per-情节 状态由用户决定本 情节 在伏笔生命周期里扮演什么角色。
 * 写回 node.hook_statuses[hook_id]；全局 hook.status 不受影响，直到
 * 用户在 总览栏 主动 fully-resolve。 */
function HookStatusChipToggle({ color, value, onChangeStatus }: {
  color: string;
  value: string;
  onChangeStatus: (v: string) => void;
}) {
  const stages: Array<["open" | "progressing" | "resolved", string]> = [
    ["open", "埋设"],
    ["progressing", "推进"],
    ["resolved", "回收"],
  ];
  const currentStage: string = value === "resolved" ? "resolved"
    : value === "progressing" || value === "pressured" || value === "near_payoff" ? "progressing"
    : "open";
  return (
    <div style={{
      display: "inline-flex", border: `1px solid ${color}`,
      borderRadius: 8, padding: 1, height: 26,
      background: "var(--bg-card, var(--bg-surface))",
    }}>
      {stages.map(([k, label]) => {
        const active = currentStage === k;
        return (
          <button key={k}
            onClick={() => onChangeStatus(k)}
            title={`此情节角色：${label}`}
            style={{
              fontSize: 10, padding: "0 10px",
              border: "none", borderRadius: 6,
              background: active ? color : "transparent",
              color: active ? "#fff" : color,
              cursor: "pointer", fontWeight: 600,
            }}>
            {label}
          </button>
        );
      })}
    </div>
  );
}


/* ── ThreadSummaryStrip ──
 * Overview section above the timeline. Per-row layout:
 *   [label] [chip…] [+ 新增]
 * Chips show ONLY the name when collapsed; clicking expands them inline
 * to reveal description + related chapter list (drawn from the project's
 * 情节 cards). New-entry form opens BELOW the row as a tall card with
 * separate title / description fields; cancel is an `×` icon.
 *
 * 伏笔 has a view toggle (active vs inactive). Active view (default)
 * shows open/progressing/pressured/near_payoff hooks and DOES NOT
 * expose deletion — 回收 is the only destructive op. Inactive view
 * shows resolved/abandoned hooks and surfaces × for permanent delete.
 *
 * Colors: 主线 = jade, 支线 = neutral, 伏笔 = gold. Red reserved for
 * the selected card border + the time-axis highlight overlay only. */
const CHAPTER_STATUS_LABEL: Record<string, string> = {
  draft: "草稿", generating: "生成中", review: "待审", final: "终稿",
};

function ThreadSummaryStrip({ projectId, threads, hooks, nodes, chapterTitles, chapterStatuses, reload, toast }: {
  projectId: string;
  threads: Thread[];
  hooks: Hook[];
  nodes: StoryNode[];
  chapterTitles: Map<number, string>;
  chapterStatuses: Map<number, string>;
  reload: () => Promise<void>;
  toast: (m: string, t?: any) => void;
}) {
  const mains = threads.filter(t => t.thread_type === "main");
  const subs = threads.filter(t => t.thread_type === "sub");
  const activeHooks = hooks.filter(h => !["resolved", "abandoned"].includes(h.status));
  const inactiveHooks = hooks.filter(h => ["resolved", "abandoned"].includes(h.status));

  const [hookView, setHookView] = useState<"active" | "inactive">("active");
  const [adding, setAdding] = useState<null | "main" | "sub" | "hook">(null);
  /** Search filter for the overview chips. Applies to thread name/desc
   *  and hook title/content. Each row independently shrinks down to
   *  matches; rows with zero matches stay collapsed-empty but still
   *  show their label + 「+ 新增」 button. */
  const [chipSearch, setChipSearch] = useState("");
  const matchesSearch = useCallback((haystacks: Array<string | undefined>) => {
    const q = chipSearch.trim().toLowerCase();
    if (!q) return true;
    return haystacks.some(s => (s || "").toLowerCase().includes(q));
  }, [chipSearch]);
  const [newThread, setNewThread] = useState<{ name: string; description: string }>({ name: "", description: "" });
  const [newHook, setNewHook] = useState<{ description: string; scale: string; origin_chapter: number; title: string }>({
    title: "", description: "", scale: "event_clue", origin_chapter: 1,
  });
  const [expandedChips, setExpandedChips] = useState<Set<string>>(new Set());
  const toggleExpand = (id: string) => {
    setExpandedChips(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };

  /** Chapters where any 情节 card references this thread or hook. */
  const relatedChaptersFor = (predicate: (n: StoryNode) => boolean) => {
    const m = new Map<number, StoryNode[]>();
    nodes.filter(predicate).forEach(n => {
      const k = n.chapter_num || 0;
      if (!k) return;
      if (!m.has(k)) m.set(k, []);
      m.get(k)!.push(n);
    });
    return Array.from(m.entries())
      .map(([num, eps]) => ({ num, title: chapterTitles.get(num) || "", episodes: eps }))
      .sort((a, b) => a.num - b.num);
  };

  const createThread = async (kind: "main" | "sub") => {
    if (!newThread.name.trim()) { toast("故事线名称必填", "error"); return; }
    try {
      await apiPost("/api/storyland/subplots", { project_id: projectId, thread_type: kind, ...newThread });
      setNewThread({ name: "", description: "" });
      setAdding(null);
      await reload();
    } catch (e: any) { toast(e.message || "创建失败", "error"); }
  };
  const createHook = async () => {
    if (!newHook.description.trim()) { toast("伏笔概述必填", "error"); return; }
    try {
      await apiPost("/api/storyland/hooks", {
        project_id: projectId,
        title: newHook.title.trim() || undefined,
        description: newHook.description,
        scale: newHook.scale,
        origin_chapter: newHook.origin_chapter,
      });
      setNewHook({ title: "", description: "", scale: "event_clue", origin_chapter: 1 });
      setAdding(null);
      await reload();
    } catch (e: any) { toast(e.message || "创建失败", "error"); }
  };
  const delThread = async (id: string) => {
    try { await apiDelete(`/api/storyland/subplots/${id}`); await reload(); }
    catch (e: any) { toast(e.message || "删除失败", "error"); }
  };
  const delHook = async (id: string) => {
    try { await apiDelete(`/api/storyland/hooks/${id}`); await reload(); }
    catch (e: any) { toast(e.message || "删除失败", "error"); }
  };
  const reactivateHook = async (id: string) => {
    try {
      await apiPost(`/api/data/foreshadowing/${id}/reactivate`, {});
      await reload();
      toast("伏笔已重新激活", "success");
    } catch (e: any) { toast(e?.message || "重新激活失败", "error"); }
  };

  // Color tokens — NEVER red.
  const mainFg = "var(--jade)", mainBorder = "var(--jade)";
  const subFg = "var(--text-secondary)", subBorder = "var(--border)";
  const hookFg = "var(--gold)", hookBorder = "var(--gold)";

  /** Collapsed: just the name in a pill. Expanded: same pill + a panel
   *  below showing description + related-chapter list. */
  const renderThreadChip = (t: Thread, isMain: boolean) => {
    const expanded = expandedChips.has(t.thread_id);
    const fg = isMain ? mainFg : subFg;
    const border = isMain ? mainBorder : subBorder;
    const related = relatedChaptersFor(n => {
      const tids = n.thread_ids || (n.thread_id ? [n.thread_id] : []);
      return tids.includes(t.thread_id);
    });
    return (
      <div key={t.thread_id} style={{ display: "inline-flex", flexDirection: "column", gap: 4 }}>
        <span
          onClick={() => toggleExpand(t.thread_id)}
          title="点击展开 / 收起"
          style={{
            fontSize: 10, padding: "2px 6px 2px 10px",
            background: "transparent", color: fg,
            border: `1px solid ${border}`, borderRadius: 12,
            cursor: "pointer",
            display: "inline-flex", alignItems: "center", gap: 4,
            fontWeight: 600,
          }}>
          {t.name}
          <span style={{ fontSize: 9, opacity: 0.6, marginLeft: 2 }}>
            {expanded ? "▾" : "▸"}
          </span>
          <button onClick={(e) => { e.stopPropagation(); delThread(t.thread_id); }}
            title="删除该故事线"
            style={{
              border: "none", background: "transparent", padding: "0 2px",
              fontSize: 11, lineHeight: 1, cursor: "pointer",
              color: fg, opacity: 0.55,
            }}>×</button>
        </span>
        {expanded && (
          <div style={{
            border: `1px solid ${border}`, borderRadius: 8,
            padding: "8px 10px", background: "var(--bg-surface)",
            display: "flex", flexDirection: "column", gap: 6,
            minWidth: 280, maxWidth: 420,
            fontSize: 11,
          }}>
            {t.description ? (
              <div style={{ color: "var(--text-secondary)", lineHeight: 1.5 }}>
                {t.description}
              </div>
            ) : (
              <div className="text-xs" style={{ color: "var(--text-disabled)", fontStyle: "italic" }}>
                （暂无概述）
              </div>
            )}
            <div>
              <div style={{
                fontSize: 9, fontWeight: 700, letterSpacing: 1,
                color: "var(--text-tertiary)", textTransform: "uppercase",
                marginBottom: 4,
              }}>
                相关章节 · {related.length}
              </div>
              {related.length === 0 ? (
                <div className="text-xs" style={{ color: "var(--text-disabled)" }}>
                  尚未在任何情节中引用。
                </div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {related.map(c => {
                    const stat = chapterStatuses.get(c.num);
                    return (
                      <div key={c.num} style={{
                        fontSize: 10.5, color: "var(--text-secondary)",
                      }}>
                        <div style={{
                          display: "flex", alignItems: "center", gap: 6,
                          marginBottom: 2,
                        }}>
                          <span style={{ fontWeight: 600, color: fg }}>第{c.num}章</span>
                          {c.title && <span>· {c.title}</span>}
                          <span style={{ flex: 1 }} />
                          {stat && (
                            <span style={{
                              fontSize: 9, padding: "1px 6px", borderRadius: 8,
                              background: stat === "final" ? "var(--jade-subtle)"
                                : stat === "review" ? "var(--gold-subtle)"
                                : stat === "generating" ? "var(--accent-subtle)"
                                : "var(--bg-secondary)",
                              color: stat === "final" ? "var(--jade)"
                                : stat === "review" ? "var(--gold)"
                                : stat === "generating" ? "var(--accent)"
                                : "var(--text-tertiary)",
                              fontWeight: 600,
                            }}>
                              {CHAPTER_STATUS_LABEL[stat] || stat}
                            </span>
                          )}
                        </div>
                        {/* Per-情节 thread-status list. */}
                        {c.episodes.map(ep => {
                          const epStat = (ep.thread_statuses?.[t.thread_id]) || "setup";
                          const lbl = THREAD_STATUS_EDIT[epStat] || THREAD_STATUS_LABEL[epStat] || epStat;
                          return (
                            <div key={ep.id} style={{
                              paddingLeft: 14,
                              display: "flex", alignItems: "center", gap: 4,
                              fontSize: 10, color: "var(--text-tertiary)",
                            }}>
                              <span style={{ color: fg, fontWeight: 700 }}>·</span>
                              <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                {ep.title || "（未命名）"}
                              </span>
                              <span style={{
                                fontSize: 9, padding: "1px 6px", borderRadius: 6,
                                background: epStat === "resolution"
                                  ? "var(--bg-secondary)"
                                  : "var(--jade-subtle)",
                                color: epStat === "resolution"
                                  ? "var(--text-tertiary)" : fg,
                                fontWeight: 600,
                                opacity: epStat === "resolution" ? 0.7 : 1,
                              }}>{lbl}</span>
                            </div>
                          );
                        })}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    );
  };

  const renderHookChip = (h: Hook, allowDelete: boolean) => {
    const expanded = expandedChips.has(h.id);
    const name = (h.title || h.content || "").slice(0, 24);
    const related = relatedChaptersFor(n => {
      const hids = n.hook_ids || (n.hook_id ? [n.hook_id] : []);
      return hids.includes(h.id);
    });
    // Inactive view → dim the chip + dashed border so the user can see
    // at a glance these are no longer in play. allowDelete is the same
    // flag the strip uses to mark inactive entries.
    const isInactive = allowDelete;
    return (
      <div key={h.id} style={{ display: "inline-flex", flexDirection: "column", gap: 4 }}>
        <span
          onClick={() => toggleExpand(h.id)}
          title="点击展开 / 收起"
          style={{
            fontSize: 10, padding: "2px 6px 2px 10px",
            background: "transparent",
            color: isInactive ? "var(--text-tertiary)" : hookFg,
            border: isInactive
              ? "1px dashed var(--text-tertiary)"
              : `1px solid ${hookBorder}`,
            borderRadius: 12,
            cursor: "pointer",
            display: "inline-flex", alignItems: "center", gap: 4,
            fontWeight: 600,
            opacity: isInactive ? 0.75 : 1,
          }}>
          {name}
          <span style={{ fontSize: 9, opacity: 0.6, marginLeft: 2 }}>
            {expanded ? "▾" : "▸"}
          </span>
          {isInactive && (
            <>
              <button onClick={(e) => { e.stopPropagation(); reactivateHook(h.id); }}
                title="重新激活伏笔（清除回收章节的回收事件，埋设 / 推进保留）"
                style={{
                  border: "none", background: "transparent", padding: "0 4px",
                  fontSize: 10, lineHeight: 1, cursor: "pointer",
                  color: "var(--jade)", fontWeight: 700,
                }}>↺</button>
              <button onClick={(e) => { e.stopPropagation(); delHook(h.id); }}
                title="彻底删除（操作不可撤销）"
                style={{
                  border: "none", background: "transparent", padding: "0 2px",
                  fontSize: 11, lineHeight: 1, cursor: "pointer",
                  color: "var(--error)", opacity: 0.7,
                }}>×</button>
            </>
          )}
        </span>
        {expanded && (
          <div style={{
            border: `1px solid ${hookBorder}`, borderRadius: 8,
            padding: "8px 10px", background: "var(--bg-surface)",
            display: "flex", flexDirection: "column", gap: 6,
            minWidth: 280, maxWidth: 420,
            fontSize: 11,
          }}>
            {h.content ? (
              <div style={{ color: "var(--text-secondary)", lineHeight: 1.5 }}>
                {h.content}
              </div>
            ) : (
              <div className="text-xs" style={{ color: "var(--text-disabled)", fontStyle: "italic" }}>
                （暂无概述）
              </div>
            )}
            <div>
              <div style={{
                fontSize: 9, fontWeight: 700, letterSpacing: 1,
                color: "var(--text-tertiary)", textTransform: "uppercase",
                marginBottom: 4,
              }}>
                相关章节 · {related.length}
              </div>
              {related.length === 0 ? (
                <div className="text-xs" style={{ color: "var(--text-disabled)" }}>
                  尚未在任何情节中引用。
                </div>
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  {related.map(c => {
                    const stat = chapterStatuses.get(c.num);
                    return (
                      <div key={c.num} style={{
                        fontSize: 10.5, color: "var(--text-secondary)",
                      }}>
                        <div style={{
                          display: "flex", alignItems: "center", gap: 6,
                          marginBottom: 2,
                        }}>
                          <span style={{ fontWeight: 600, color: hookFg }}>第{c.num}章</span>
                          {c.title && <span>· {c.title}</span>}
                          <span style={{ flex: 1 }} />
                          {stat && (
                            <span style={{
                              fontSize: 9, padding: "1px 6px", borderRadius: 8,
                              background: stat === "final" ? "var(--jade-subtle)"
                                : stat === "review" ? "var(--gold-subtle)"
                                : stat === "generating" ? "var(--accent-subtle)"
                                : "var(--bg-secondary)",
                              color: stat === "final" ? "var(--jade)"
                                : stat === "review" ? "var(--gold)"
                                : stat === "generating" ? "var(--accent)"
                                : "var(--text-tertiary)",
                              fontWeight: 600,
                            }}>
                              {CHAPTER_STATUS_LABEL[stat] || stat}
                            </span>
                          )}
                        </div>
                        {/* Per-情节 hook-status list. */}
                        {c.episodes.map(ep => {
                          const epStat = (ep.hook_statuses?.[h.id]) || "open";
                          const lbl = HOOK_STATUS_EDIT[epStat] || HOOK_STATUS_LABEL[epStat] || epStat;
                          const isResolved = epStat === "resolved";
                          return (
                            <div key={ep.id} style={{
                              paddingLeft: 14,
                              display: "flex", alignItems: "center", gap: 4,
                              fontSize: 10, color: "var(--text-tertiary)",
                            }}>
                              <span style={{ color: hookFg, fontWeight: 700 }}>·</span>
                              <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                {ep.title || "（未命名）"}
                              </span>
                              <span style={{
                                fontSize: 9, padding: "1px 6px", borderRadius: 6,
                                background: isResolved ? "var(--bg-secondary)" : "var(--gold-subtle)",
                                color: isResolved ? "var(--text-tertiary)" : hookFg,
                                fontWeight: 600,
                                opacity: isResolved ? 0.7 : 1,
                              }}>{lbl}</span>
                            </div>
                          );
                        })}
                      </div>
                    );
                  })}
                </div>
              )}
              <div style={{ fontSize: 10, color: "var(--text-tertiary)", marginTop: 6 }}>
                状态：{HOOK_STATUS_LABEL[h.status] || h.status}
                {h.status !== "resolved" && h.expected_payoff_chapter && (
                  <>
                    {" · "}
                    <span style={{ color: "var(--gold)", fontWeight: 600 }}>
                      推荐 {Math.max(0, (h.expected_payoff_chapter || 0) - (h.origin_chapter || 0))} 章内回收
                    </span>
                    {h.origin_chapter ? ` (第${h.origin_chapter}章起 → 第${h.expected_payoff_chapter}章前)` : ""}
                  </>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    );
  };

  /** Tall vertical "new entry" card. Opens BELOW the row instead of
   *  inline, with stacked title / description fields. Cancel = `×`. */
  const newEntryCard = (kind: "main" | "sub" | "hook") => {
    const colorFg = kind === "hook" ? hookFg : (kind === "main" ? mainFg : subFg);
    const colorBorder = kind === "hook" ? hookBorder : (kind === "main" ? mainBorder : subBorder);
    const titleLabel = kind === "hook" ? "伏笔名（可空）" : `${kind === "main" ? "主线" : "支线"}名称`;
    const descrLabel = kind === "hook" ? "伏笔概述（含待揭示的真相）" : "故事线一段话概述";
    return (
      <div style={{
        marginTop: 8,
        border: `1.5px solid ${colorBorder}`,
        background: "var(--bg-surface)",
        borderRadius: 10, padding: 12,
        display: "flex", flexDirection: "column", gap: 8,
        position: "relative",
      }}>
        <button onClick={() => setAdding(null)} title="取消"
          style={{
            position: "absolute", top: 6, right: 6,
            border: "none", background: "transparent",
            color: "var(--text-tertiary)", cursor: "pointer",
            fontSize: 14, lineHeight: 1, padding: "4px 8px",
          }}>×</button>
        <div style={{
          fontSize: 11, fontWeight: 700, letterSpacing: 0.4,
          color: colorFg,
        }}>
          新增 {kind === "main" ? "主线" : kind === "sub" ? "支线" : "伏笔"}
        </div>
        {kind === "hook" ? (
          <>
            <div className="field">
              <label className="label">标题</label>
              <input className="input"
                value={newHook.title}
                onChange={e => setNewHook({ ...newHook, title: e.target.value })}
                placeholder={titleLabel}
                style={{ fontSize: 12 }} autoFocus />
            </div>
            <div className="field">
              <label className="label">概述</label>
              <textarea className="input"
                value={newHook.description}
                onChange={e => setNewHook({ ...newHook, description: e.target.value })}
                placeholder={descrLabel} rows={2}
                style={{ fontSize: 12 }} />
            </div>
            <div className="flex gap-6" style={{ alignItems: "center" }}>
              <div className="field" style={{ flex: 1 }}>
                <label className="label">规模</label>
                <select className="select" value={newHook.scale}
                  onChange={e => setNewHook({ ...newHook, scale: e.target.value })}
                  style={{ fontSize: 12, width: "100%" }}>
                  {Object.entries(SCALE_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                </select>
              </div>
              <div className="field">
                <label className="label">埋设章节</label>
                <input className="input" type="number" min={1}
                  value={newHook.origin_chapter}
                  onChange={e => setNewHook({ ...newHook, origin_chapter: parseInt(e.target.value) || 1 })}
                  style={{ fontSize: 12, width: 80 }} />
              </div>
            </div>
            <button className="btn-primary" onClick={createHook}
              style={{ fontSize: 12, padding: "6px 14px", alignSelf: "flex-start" }}>
              埋设
            </button>
          </>
        ) : (
          <>
            <div className="field">
              <label className="label">名称</label>
              <input className="input"
                value={newThread.name}
                onChange={e => setNewThread({ ...newThread, name: e.target.value })}
                placeholder={titleLabel}
                style={{ fontSize: 12 }} autoFocus />
            </div>
            <div className="field">
              <label className="label">概述</label>
              <textarea className="input"
                value={newThread.description}
                onChange={e => setNewThread({ ...newThread, description: e.target.value })}
                placeholder={descrLabel} rows={2}
                style={{ fontSize: 12 }} />
            </div>
            <button className="btn-primary" onClick={() => createThread(kind)}
              style={{ fontSize: 12, padding: "6px 14px", alignSelf: "flex-start" }}>
              新建
            </button>
          </>
        )}
      </div>
    );
  };

  const addBtn = (kind: "main" | "sub" | "hook", label: string) => (
    <button onClick={() => setAdding(kind)}
      style={{
        fontSize: 10, padding: "2px 10px", height: 22,
        border: "1px dashed var(--border)", borderRadius: 10,
        color: "var(--text-tertiary)", background: "transparent",
        cursor: "pointer",
      }}>
      + {label}
    </button>
  );

  // Filter chips by the search box; mains / subs / hooks each draw from
  // these filtered lists so the user can quickly home in on what they
  // want when the project accumulates many 故事线 / 伏笔.
  const filteredMains = mains.filter(t => matchesSearch([t.name, t.description]));
  const filteredSubs = subs.filter(t => matchesSearch([t.name, t.description]));
  const filteredActiveHooks = activeHooks.filter(h => matchesSearch([h.title, h.content]));
  const filteredInactiveHooks = inactiveHooks.filter(h => matchesSearch([h.title, h.content]));

  /** Row layout: [label] [scrollable chip strip…] [+ 新增] right-aligned.
   *  The chip strip uses `overflow-x: auto` with a thin scrollbar so a
   *  long list of 主线 / 支线 / 伏笔 doesn't force the new button off
   *  the right edge of the row. */
  const renderRow = (
    label: string,
    leadingExtras: React.ReactNode | null,
    chips: React.ReactNode[],
    addAction: React.ReactNode | null,
  ) => (
    <div className="flex" style={{
      gap: 8, alignItems: "center", marginBottom: 8,
      minHeight: 28,
    }}>
      <span className="text-xs text-muted" style={{ width: 36, flexShrink: 0 }}>
        {label}
      </span>
      {leadingExtras}
      <div style={{
        flex: 1, minWidth: 0,
        overflowX: "auto", overflowY: "hidden",
        display: "flex", gap: 6, alignItems: "flex-start",
        paddingBottom: 2,
      }}>
        {chips.length === 0 ? (
          <span style={{
            fontSize: 10, color: "var(--text-disabled)", fontStyle: "italic",
            alignSelf: "center",
          }}>
            {chipSearch ? "无匹配" : "（暂无）"}
          </span>
        ) : chips}
      </div>
      {addAction && (
        <div style={{ flexShrink: 0, marginLeft: "auto" }}>
          {addAction}
        </div>
      )}
    </div>
  );

  return (
    <div style={{
      padding: "10px 16px",
      background: "var(--bg-surface)",
      borderBottom: "1px solid var(--border)",
      fontSize: 11,
    }}>
      <div className="flex items-center justify-between" style={{ marginBottom: 8, gap: 8 }}>
        <span style={{ fontWeight: 600, color: "var(--text-secondary)" }}>
          故事线与伏笔总览
        </span>
        <div style={{ display: "flex", alignItems: "center", gap: 6, flex: 1, justifyContent: "flex-end", maxWidth: 320 }}>
          <input
            className="input"
            placeholder="搜索 名称 / 概述..."
            value={chipSearch}
            onChange={e => setChipSearch(e.target.value)}
            style={{ fontSize: 11, padding: "3px 10px", height: 24, flex: 1 }}
          />
          {chipSearch && (
            <button onClick={() => setChipSearch("")} title="清除搜索"
              style={{
                border: "none", background: "transparent",
                color: "var(--text-tertiary)", cursor: "pointer",
                fontSize: 12, padding: "0 4px",
              }}>×</button>
          )}
        </div>
      </div>
      {renderRow(
        "主线",
        null,
        filteredMains.map(t => renderThreadChip(t, true)),
        adding !== "main" ? addBtn("main", "主线") : null,
      )}
      {adding === "main" && newEntryCard("main")}
      {renderRow(
        "支线",
        null,
        filteredSubs.map(t => renderThreadChip(t, false)),
        adding !== "sub" ? addBtn("sub", "支线") : null,
      )}
      {adding === "sub" && newEntryCard("sub")}
      {renderRow(
        "伏笔",
        (
          <div style={{
            display: "inline-flex", border: "1px solid var(--border)",
            borderRadius: 8, padding: 1, height: 22, flexShrink: 0,
          }}>
            <button onClick={() => setHookView("active")}
              style={{
                fontSize: 10, padding: "0 10px",
                border: "none", borderRadius: 6,
                background: hookView === "active" ? "var(--gold)" : "transparent",
                color: hookView === "active" ? "#fff" : "var(--text-tertiary)",
                cursor: "pointer", fontWeight: 600,
              }}>
              活跃 {activeHooks.length > 0 && `· ${activeHooks.length}`}
            </button>
            <button onClick={() => setHookView("inactive")}
              style={{
                fontSize: 10, padding: "0 10px",
                border: "none", borderRadius: 6,
                background: hookView === "inactive" ? "var(--text-tertiary)" : "transparent",
                color: hookView === "inactive" ? "#fff" : "var(--text-tertiary)",
                cursor: "pointer", fontWeight: 600,
              }}>
              已回收 {inactiveHooks.length > 0 && `· ${inactiveHooks.length}`}
            </button>
          </div>
        ),
        (hookView === "active" ? filteredActiveHooks : filteredInactiveHooks)
          .map(h => renderHookChip(h, hookView === "inactive")),
        adding !== "hook" && hookView === "active" ? addBtn("hook", "伏笔") : null,
      )}
      {adding === "hook" && newEntryCard("hook")}
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
  const [search, setSearch] = React.useState("");
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

  const filteredOptions = React.useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return options;
    return options.filter(o =>
      o.name.toLowerCase().includes(q)
      || (o.role || "").toLowerCase().includes(q)
    );
  }, [options, search]);

  return (
    <div ref={wrapRef} style={{ position: "relative" }}>
      <label className="label">{label}</label>
      <div
        onClick={() => setOpen(o => !o)}
        style={{
          display: "flex", flexWrap: "wrap", gap: 6,
          minHeight: 38, padding: "6px 10px",
          background: open ? "var(--bg-surface)" : "var(--bg-card, var(--bg-surface))",
          border: `1px solid ${open ? "var(--accent)" : "var(--border)"}`,
          borderRadius: 8,
          cursor: "pointer",
          transition: "border-color 0.15s, background 0.15s",
          alignItems: "center",
        }}>
        {value.length === 0 ? (
          <span style={{
            fontSize: 12, color: "var(--text-tertiary)",
            display: "inline-flex", alignItems: "center", gap: 6,
          }}>
            <span style={{ fontSize: 14, lineHeight: 1, opacity: 0.7 }}>+</span>
            点击选择角色...
          </span>
        ) : value.map(name => {
          const known = options.find(o => o.name === name);
          return (
            <span key={name}
              style={{
                fontSize: 11, padding: "3px 4px 3px 10px",
                background: known ? "var(--bg-secondary)" : "transparent",
                color: known ? "var(--text-primary)" : "var(--text-tertiary)",
                border: `1px solid ${known ? "var(--border)" : "var(--border-subtle)"}`,
                borderRadius: 12,
                fontStyle: known ? "normal" : "italic",
                fontWeight: 500,
                display: "inline-flex", alignItems: "center", gap: 4,
              }}
              title={known ? "已登记角色" : "临时角色（未在角色库）"}>
              {known && (
                <span style={{
                  width: 16, height: 16, borderRadius: "50%",
                  background: "var(--accent-subtle)", color: "var(--accent)",
                  fontSize: 10, fontWeight: 700,
                  display: "inline-flex", alignItems: "center", justifyContent: "center",
                  marginRight: 2,
                }}>
                  {name.slice(0, 1)}
                </span>
              )}
              {name}
              <button onClick={(e) => { e.stopPropagation(); toggle(name); }}
                title="移除"
                style={{
                  border: "none", background: "transparent", padding: "0 4px",
                  cursor: "pointer", color: "var(--text-tertiary)",
                  fontSize: 12, lineHeight: 1,
                }}>×</button>
            </span>
          );
        })}
      </div>
      {open && (
        <div style={{
          position: "absolute", zIndex: 50, left: 0, right: 0, top: "100%",
          marginTop: 4, background: "var(--bg-surface)",
          border: "1px solid var(--border)", borderRadius: 8,
          boxShadow: "0 8px 20px rgba(0,0,0,0.12)",
          maxHeight: 320, display: "flex", flexDirection: "column",
        }}>
          <div style={{ padding: "8px 10px", borderBottom: "1px solid var(--border)" }}>
            <input className="input" autoFocus
              placeholder="搜索角色名 / 角色定位"
              value={search}
              onChange={e => setSearch(e.target.value)}
              onKeyDown={e => { if (e.key === "Escape") setOpen(false); }}
              style={{ fontSize: 12, width: "100%" }} />
          </div>
          <div style={{ overflowY: "auto", flex: 1 }}>
          {options.length === 0 ? (
            <div className="text-xs text-muted" style={{ padding: 12, textAlign: "center" }}>
              角色卡中暂无角色 — 请到「角色管理」创建。
            </div>
          ) : filteredOptions.length === 0 ? (
            <div className="text-xs text-muted" style={{ padding: 12, textAlign: "center" }}>
              无匹配角色
            </div>
          ) : (
            filteredOptions.map(opt => {
              const on = value.includes(opt.name);
              return (
                <div key={opt.id}
                  onClick={() => toggle(opt.name)}
                  style={{
                    display: "flex", alignItems: "center", gap: 10,
                    padding: "7px 12px", cursor: "pointer",
                    background: on ? "var(--accent-subtle)" : undefined,
                    borderLeft: on ? "3px solid var(--accent)" : "3px solid transparent",
                    transition: "background 0.12s",
                  }}
                  onMouseEnter={e => { if (!on) e.currentTarget.style.background = "var(--bg-secondary)"; }}
                  onMouseLeave={e => { if (!on) e.currentTarget.style.background = ""; }}>
                  <span style={{
                    width: 26, height: 26, borderRadius: "50%",
                    background: on ? "var(--accent)" : "var(--bg-secondary)",
                    color: on ? "#fff" : "var(--text-primary)",
                    fontSize: 12, fontWeight: 700,
                    display: "inline-flex", alignItems: "center", justifyContent: "center",
                    flexShrink: 0,
                  }}>
                    {opt.name.slice(0, 1)}
                  </span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>
                      {opt.name}
                    </div>
                    {opt.role && (
                      <div style={{ fontSize: 10, color: "var(--text-tertiary)", marginTop: 1 }}>
                        {opt.role}
                      </div>
                    )}
                  </div>
                  {on && (
                    <span style={{
                      fontSize: 10, color: "var(--accent)", fontWeight: 700,
                    }}>✓</span>
                  )}
                </div>
              );
            })
          )}
          </div>
          <div style={{
            borderTop: "1px solid var(--border)",
            padding: "8px 10px", display: "flex", gap: 6,
            background: "var(--bg-secondary)",
          }}>
            <input
              className="input" value={customInput}
              onChange={e => setCustomInput(e.target.value)}
              placeholder="+ 临时角色名（不在角色库中）"
              style={{ flex: 1, fontSize: 11, padding: "4px 8px" }}
              onKeyDown={e => {
                if (e.key === "Enter" && customInput.trim()) {
                  toggle(customInput.trim());
                  setCustomInput("");
                }
              }} />
            <button className="btn" style={{ fontSize: 10, padding: "2px 12px" }}
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

