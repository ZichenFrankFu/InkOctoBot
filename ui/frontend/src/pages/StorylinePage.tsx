/**
 * 剧情线 — chapter-grouped event timeline.
 *
 * Vertical axis = chapters (driven by the editor's volumes/chapters list,
 * so the spine of the page stays in sync with what the user is actually
 * writing). Each chapter row carries:
 *   - chapter header (title + characters + time + location, pulled from
 *     角色管理 and edited inline)
 *   - a horizontal scroll of event cards, each a StoryNode tied to the
 *     chapter via `chapter_num` (multiple events per chapter is the
 *     point of the redesign — beat-level granularity inside a chapter)
 *   - the merged outline computed from the events' summaries, with a
 *     one-click 「写回章节大纲」that pushes it back into the editor's
 *     `synopsis` so the editor stays the source of truth for outlines.
 *
 * Top: a 故事线 / 伏笔 summary strip (read-only links to the data the
 * user maintains on the 故事中世界 page) so you can see "which threads
 * touch this chapter" without leaving the page.
 *
 * Persistence: still goes through /api/data/storyline (nodes + edges) for
 * backwards compat, but `x/y` positions are no longer relevant in this
 * layout — they're kept around so old data still loads cleanly.
 */
import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { apiGet, apiPut } from "../api/client";
import { useToast } from "../components/shared/Toast";
import { useDialog } from "../components/shared/Dialog";
import type {
  StoryNode, StoryEdge, ChapterOutline, Volume, Character,
} from "../api/types";


const uid = () => `n_${Date.now()}_${Math.random().toString(36).slice(2, 6)}`;
const COLORS = ["#c0392b", "#2d8c5a", "#3b5998", "#d4a853", "#8e44ad", "#e67e22", "#1abc9c", "#e74c3c"];


// ── Foreshadowing / thread types (light shape — we only need what the
//    summary strip + chapter-row chips display). Backend payloads carry
//    more, so we widen with index access for safety.
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


export default function StorylinePage({ projectId, onNavigate }: {
  projectId: string;
  onNavigate?: (page: string) => void;
}) {
  const { toast } = useToast();
  const { confirm } = useDialog();
  const pid = projectId || "default";

  const [volumes, setVolumes] = useState<Volume[]>([]);
  const [chapters, setChapters] = useState<(ChapterOutline & { _vol_title: string })[]>([]);
  const [nodes, setNodes] = useState<StoryNode[]>([]);
  const [edges, setEdges] = useState<StoryEdge[]>([]);
  const [threads, setThreads] = useState<Thread[]>([]);
  const [hooks, setHooks] = useState<Hook[]>([]);
  const [characters, setCharacters] = useState<Character[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [editorDirty, setEditorDirty] = useState<Set<string>>(new Set()); // chapter_ids with unflushed outline changes

  // Warn before leaving with unsaved changes
  useEffect(() => {
    if (!dirty && editorDirty.size === 0) return;
    const handler = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty, editorDirty.size]);

  // ── Load all the data we need to render the timeline ──
  const loadAll = useCallback(async () => {
    try {
      const [story, editor, threadResp, hookResp, charResp] = await Promise.all([
        apiGet<{ nodes: StoryNode[]; edges: StoryEdge[] }>(
          `/api/data/storyline?project_id=${pid}`,
        ).catch(() => ({ nodes: [] as StoryNode[], edges: [] as StoryEdge[] })),
        apiGet<{ volumes: Volume[] }>(`/api/data/editor?project_id=${pid}`)
          .catch(() => ({ volumes: [] as Volume[] })),
        apiGet<{ items: Thread[] }>(`/api/storyland/subplots?project_id=${pid}`)
          .catch(() => ({ items: [] as Thread[] })),
        apiGet<{ items: Hook[] }>(`/api/data/foreshadowing/${pid}`)
          .catch(() => ({ items: [] as Hook[] })),
        apiGet<{ items: Character[] }>(`/api/data/characters?project_id=${pid}`)
          .catch(() => ({ items: [] as Character[] })),
      ]);
      setNodes(story.nodes || []);
      setEdges(story.edges || []);
      setVolumes(editor.volumes || []);
      const flat: (ChapterOutline & { _vol_title: string })[] = [];
      (editor.volumes || []).forEach(v =>
        (v.chapters || []).forEach(c => flat.push({ ...c, _vol_title: v.title })),
      );
      setChapters(flat);
      setThreads(threadResp.items || []);
      setHooks(hookResp.items || []);
      setCharacters(charResp.items || []);
      setLoaded(true);
    } catch (e) {
      console.error(e);
      setLoaded(true);
    }
  }, [pid]);
  useEffect(() => { loadAll(); }, [loadAll]);

  // ── Auto-save storyline (nodes + edges) ──
  useEffect(() => {
    if (!loaded || !dirty) return;
    const t = setTimeout(async () => {
      try {
        await apiPut(`/api/data/storyline`, { project_id: pid, nodes, edges });
        setDirty(false);
      } catch (e: any) {
        toast(e?.message || "剧情线保存失败", "error");
      }
    }, 1500);
    return () => clearTimeout(t);
  }, [dirty, nodes, edges, loaded, pid, toast]);

  // ── Helpers ──
  /** Global chapter_num for a chapter row (1-based, across volumes). */
  const chapterNumOf = useCallback((cidx: number) => cidx + 1, []);
  /** Events that belong to a chapter (filter by chapter_num). */
  const eventsForChapter = useCallback((chap_num: number) =>
    nodes.filter(n => (n.chapter_num || 0) === chap_num)
      .sort((a, b) => (a.x || 0) - (b.x || 0)),     // honour stored order
  [nodes]);

  /** Outline string assembled from a chapter's events. Each event becomes
   *  a bullet so the merged outline is human-readable; falls back to the
   *  raw editor synopsis when there are no events yet. */
  const mergedOutline = useCallback((chap_num: number, fallback: string) => {
    const evs = eventsForChapter(chap_num);
    if (evs.length === 0) return fallback || "";
    return evs.map((n, i) => {
      const head = n.title?.trim() || `事件 ${i + 1}`;
      const body = (n.summary || "").trim();
      return body ? `${i + 1}. ${head}：${body}` : `${i + 1}. ${head}`;
    }).join("\n");
  }, [eventsForChapter]);

  // ── Mutations ──
  const updateNode = (id: string, patch: Partial<StoryNode>) => {
    setNodes(prev => prev.map(n => n.id === id ? { ...n, ...patch } : n));
    setDirty(true);
    // any change to a node's text dirties the merged outline for its chapter
    const n = nodes.find(x => x.id === id);
    const chapNum = (patch.chapter_num ?? n?.chapter_num) || 0;
    const ch = chapters.find((_, i) => chapterNumOf(i) === chapNum);
    if (ch) setEditorDirty(prev => new Set(prev).add(ch.id));
  };

  const addEvent = (chap_num: number) => {
    // place new event at the end of its row (highest x among same-chapter events)
    const peers = eventsForChapter(chap_num);
    const lastX = peers.length > 0 ? Math.max(...peers.map(n => n.x || 0)) : 0;
    const n: StoryNode = {
      id: uid(),
      title: `事件 ${peers.length + 1}`,
      summary: "",
      x: lastX + 220,
      y: chap_num * 200,
      color: COLORS[peers.length % COLORS.length],
      chapter_num: chap_num,
      characters: [],
      week: 1,
      time: "",
      location: "",
    };
    setNodes(prev => [...prev, n]);
    setSelectedNodeId(n.id);
    setDirty(true);
    const ch = chapters.find((_, i) => chapterNumOf(i) === chap_num);
    if (ch) setEditorDirty(prev => new Set(prev).add(ch.id));
  };

  const deleteEvent = async (id: string) => {
    if (!(await confirm({ message: "删除该事件卡片？", destructive: true }))) return;
    const node = nodes.find(n => n.id === id);
    setNodes(prev => prev.filter(n => n.id !== id));
    setEdges(prev => prev.filter(e => e.from !== id && e.to !== id));
    if (selectedNodeId === id) setSelectedNodeId(null);
    setDirty(true);
    const chapNum = node?.chapter_num || 0;
    const ch = chapters.find((_, i) => chapterNumOf(i) === chapNum);
    if (ch) setEditorDirty(prev => new Set(prev).add(ch.id));
  };

  /** Write the merged outline back to the editor's chapter `synopsis` so
   *  the AI generation / 大纲 tab sees the same beat structure. */
  const flushOutlineToEditor = async (chapter_id: string) => {
    const chIdx = chapters.findIndex(c => c.id === chapter_id);
    if (chIdx < 0) return;
    const chap_num = chapterNumOf(chIdx);
    const outline = mergedOutline(chap_num, chapters[chIdx].synopsis || "");
    const next = volumes.map(v => ({
      ...v,
      chapters: (v.chapters || []).map(c =>
        c.id === chapter_id ? { ...c, synopsis: outline } : c,
      ),
    }));
    try {
      await apiPut(`/api/data/editor`, { project_id: pid, volumes: next });
      setVolumes(next);
      const flat: (ChapterOutline & { _vol_title: string })[] = [];
      next.forEach(v => (v.chapters || []).forEach(c => flat.push({ ...c, _vol_title: v.title })));
      setChapters(flat);
      setEditorDirty(prev => {
        const n = new Set(prev);
        n.delete(chapter_id);
        return n;
      });
      toast(`已写回 第${chap_num}章 大纲`, "success");
    } catch (e: any) {
      toast(e?.message || "写回章节大纲失败", "error");
    }
  };

  const updateChapterField = (chapter_id: string, field: "title" | "time" | "location" | "characters", value: any) => {
    const next = volumes.map(v => ({
      ...v,
      chapters: (v.chapters || []).map(c =>
        c.id === chapter_id ? { ...c, [field]: value } : c,
      ),
    }));
    setVolumes(next);
    const flat: (ChapterOutline & { _vol_title: string })[] = [];
    next.forEach(v => (v.chapters || []).forEach(c => flat.push({ ...c, _vol_title: v.title })));
    setChapters(flat);
    apiPut(`/api/data/editor`, { project_id: pid, volumes: next })
      .catch((e: any) => toast(e?.message || "章节字段保存失败", "error"));
  };

  const selectedNode = useMemo(
    () => nodes.find(n => n.id === selectedNodeId) || null,
    [nodes, selectedNodeId],
  );

  if (!loaded) {
    return (
      <div className="loading" style={{ height: "100vh" }}>
        <div className="loading-spinner" />加载中...
      </div>
    );
  }

  return (
    <div className="page-container" style={{ maxWidth: 1200 }}>
      {/* ── Page header ── */}
      <div className="page-header" style={{ paddingBottom: 12 }}>
        <div className="page-header-row">
          <div>
            <h2 className="font-serif">剧情线</h2>
            <p>章节为节点，每章内可并排多张事件卡；合并出的大纲可一键回写到编辑器</p>
          </div>
          <div className="flex gap-8">
            <span className="text-xs" style={{
              color: dirty ? "var(--gold)" : "var(--jade)", lineHeight: "32px",
            }}>{dirty ? "保存中…" : "已保存"}</span>
          </div>
        </div>
      </div>

      {/* ── Storyline thread + foreshadowing summary ── */}
      <ThreadSummaryStrip
        threads={threads}
        hooks={hooks}
        onOpenStoryland={() => onNavigate?.("storyland")}
      />

      {/* ── Empty state ── */}
      {chapters.length === 0 ? (
        <div className="card">
          <div className="card-body empty-state" style={{ padding: 32 }}>
            <p>项目还没有章节。请先到「编辑器」新建章节，回来后剧情线会以章节为脊柱自动展开。</p>
            {onNavigate && (
              <button className="btn-primary mt-12" onClick={() => onNavigate("editor")}>
                前往编辑器
              </button>
            )}
          </div>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {chapters.map((ch, idx) => {
            const chap_num = chapterNumOf(idx);
            const events = eventsForChapter(chap_num);
            const merged = mergedOutline(chap_num, ch.synopsis || "");
            const isDirty = editorDirty.has(ch.id);
            const relatedThreads = threads.filter(t =>
              t.start_chapter <= chap_num
              && (t.last_advanced_chapter == null || t.last_advanced_chapter >= chap_num),
            );
            const relatedHooks = hooks.filter(h =>
              (h.origin_chapter || 0) <= chap_num
              && (h.expected_payoff_chapter == null || h.expected_payoff_chapter >= chap_num)
              && !["resolved", "abandoned"].includes(h.status),
            );
            return (
              <ChapterRow
                key={ch.id}
                chapter={ch}
                chapter_num={chap_num}
                events={events}
                merged={merged}
                isDirty={isDirty}
                allCharacters={characters}
                relatedThreads={relatedThreads}
                relatedHooks={relatedHooks}
                selectedNodeId={selectedNodeId}
                onSelectNode={setSelectedNodeId}
                onAddEvent={() => addEvent(chap_num)}
                onUpdateNode={updateNode}
                onDeleteNode={deleteEvent}
                onUpdateChapter={(field, value) => updateChapterField(ch.id, field, value)}
                onFlushOutline={() => flushOutlineToEditor(ch.id)}
              />
            );
          })}
        </div>
      )}

      {/* ── Floating event detail panel ── */}
      {selectedNode && (
        <EventDetailDrawer
          node={selectedNode}
          allCharacters={characters}
          onChange={(patch) => updateNode(selectedNode.id, patch)}
          onClose={() => setSelectedNodeId(null)}
          onDelete={() => deleteEvent(selectedNode.id)}
        />
      )}
    </div>
  );
}


/* ── Thread + hook summary strip ──
 * Read-only at-a-glance row of the user's main storylines and the
 * currently-open foreshadowing items. Click → 故事中世界 to edit. */
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
    <div className="card mb-16">
      <div className="card-body" style={{ padding: "10px 14px" }}>
        <div className="flex items-center justify-between mb-8">
          <span className="label" style={{ marginBottom: 0, fontSize: 11 }}>
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
          <div className="flex gap-6" style={{ marginBottom: 6, flexWrap: "wrap" }}>
            <span className="text-xs text-muted" style={{ width: 36 }}>主线</span>
            {mains.map(t => (
              <span key={t.thread_id} className="tag"
                title={t.description}
                style={{ fontSize: 11, background: "var(--accent-subtle)", color: "var(--accent)", border: "1px solid var(--accent)" }}>
                {t.name} · 第{t.start_chapter}章起
              </span>
            ))}
          </div>
        )}
        {subs.length > 0 && (
          <div className="flex gap-6" style={{ marginBottom: 6, flexWrap: "wrap" }}>
            <span className="text-xs text-muted" style={{ width: 36 }}>支线</span>
            {subs.map(t => (
              <span key={t.thread_id} className="tag" title={t.description}
                style={{ fontSize: 11 }}>
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
                style={{ fontSize: 11, background: "var(--gold-subtle)", color: "var(--gold)", border: "1px solid var(--gold)" }}>
                {(h.title || h.content || "").slice(0, 18)}
                {h.expected_payoff_chapter ? ` · 收于第${h.expected_payoff_chapter}章` : " · 不限期"}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}


/* ── One chapter row ──
 * Header (chapter title + chapter-level fields) + horizontal event cards
 * + merged outline. The header characters / time / location are pulled
 * from / written back to the editor chapter directly. */
function ChapterRow({
  chapter, chapter_num, events, merged, isDirty,
  allCharacters, relatedThreads, relatedHooks,
  selectedNodeId, onSelectNode, onAddEvent, onUpdateNode, onDeleteNode,
  onUpdateChapter, onFlushOutline,
}: {
  chapter: ChapterOutline & { _vol_title: string };
  chapter_num: number;
  events: StoryNode[];
  merged: string;
  isDirty: boolean;
  allCharacters: Character[];
  relatedThreads: Thread[];
  relatedHooks: Hook[];
  selectedNodeId: string | null;
  onSelectNode: (id: string) => void;
  onAddEvent: () => void;
  onUpdateNode: (id: string, patch: Partial<StoryNode>) => void;
  onDeleteNode: (id: string) => void;
  onUpdateChapter: (field: "title" | "time" | "location" | "characters", value: any) => void;
  onFlushOutline: () => void;
}) {
  const [editingTitle, setEditingTitle] = useState(false);
  const [editingTime, setEditingTime] = useState(false);
  const [editingLocation, setEditingLocation] = useState(false);
  return (
    <div className="card" style={{ overflow: "visible" }}>
      <div className="card-header" style={{
        display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
        background: "var(--bg-surface)",
      }}>
        <span style={{
          fontSize: 11, fontWeight: 700, color: "var(--accent)",
          padding: "2px 10px", borderRadius: 12,
          background: "var(--accent-subtle)", border: "1px solid var(--accent)",
        }}>
          第 {chapter_num} 章
        </span>
        {editingTitle ? (
          <input className="input" autoFocus
            defaultValue={chapter.title}
            style={{ fontSize: 14, fontWeight: 600, maxWidth: 280 }}
            onBlur={e => { onUpdateChapter("title", e.target.value); setEditingTitle(false); }}
            onKeyDown={e => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
        ) : (
          <h3 onClick={() => setEditingTitle(true)} style={{ margin: 0, fontSize: 15, cursor: "text" }}
            title="点击修改章节标题">
            {chapter.title || `第${chapter_num}章`}
          </h3>
        )}
        <span className="text-xs text-muted">{chapter._vol_title}</span>

        {/* Time / location chips with inline edit */}
        <div className="flex gap-4" style={{ marginLeft: "auto" }}>
          {editingTime ? (
            <input className="input" autoFocus
              defaultValue={chapter.time}
              placeholder="时间，如 第3天·黄昏"
              style={{ fontSize: 11, maxWidth: 140 }}
              onBlur={e => { onUpdateChapter("time", e.target.value); setEditingTime(false); }}
              onKeyDown={e => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
          ) : (
            <span className="tag"
              title="点击修改时间"
              style={{ fontSize: 10, cursor: "text" }}
              onClick={() => setEditingTime(true)}>
              {chapter.time ? `时 ${chapter.time}` : "+ 时间"}
            </span>
          )}
          {editingLocation ? (
            <input className="input" autoFocus
              defaultValue={chapter.location}
              placeholder="地点，如 云隐山"
              style={{ fontSize: 11, maxWidth: 140 }}
              onBlur={e => { onUpdateChapter("location", e.target.value); setEditingLocation(false); }}
              onKeyDown={e => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
          ) : (
            <span className="tag"
              title="点击修改地点"
              style={{ fontSize: 10, cursor: "text" }}
              onClick={() => setEditingLocation(true)}>
              {chapter.location ? `地 ${chapter.location}` : "+ 地点"}
            </span>
          )}
        </div>
      </div>

      <div className="card-body" style={{ padding: 12 }}>
        {/* Chapter-level characters — pulled from 角色管理 */}
        <CharacterSelector
          value={chapter.characters || []}
          options={allCharacters}
          onChange={(v) => onUpdateChapter("characters", v)}
          label="出场角色"
        />

        {/* Related threads / hooks */}
        {(relatedThreads.length > 0 || relatedHooks.length > 0) && (
          <div className="flex gap-4 mt-8" style={{ flexWrap: "wrap" }}>
            {relatedThreads.map(t => (
              <span key={t.thread_id} className="tag"
                title={`${t.thread_type === "main" ? "主线" : "支线"}：${t.description}`}
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
              <span key={h.id} className="tag"
                title={h.content}
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

        {/* Horizontal event cards */}
        <div className="label mt-12 mb-4" style={{ fontSize: 11 }}>事件 ({events.length})</div>
        <div style={{
          display: "flex", gap: 10, overflowX: "auto",
          paddingBottom: 6,
        }}>
          {events.map(ev => (
            <EventCard key={ev.id}
              node={ev}
              selected={ev.id === selectedNodeId}
              onSelect={() => onSelectNode(ev.id)}
              onChange={(patch) => onUpdateNode(ev.id, patch)}
              onDelete={() => onDeleteNode(ev.id)} />
          ))}
          <button
            onClick={onAddEvent}
            style={{
              minWidth: 110, height: 110,
              border: "1px dashed var(--border)", borderRadius: 8,
              background: "transparent", color: "var(--text-tertiary)",
              cursor: "pointer", fontSize: 12, flexShrink: 0,
            }}>
            + 添加事件
          </button>
        </div>

        {/* Merged outline */}
        <div className="mt-12">
          <div className="flex items-center justify-between mb-4">
            <span className="label" style={{ fontSize: 11, marginBottom: 0 }}>
              合并大纲（事件汇总）
            </span>
            <button
              className={isDirty ? "btn-primary" : "btn"}
              style={{ fontSize: 10, padding: "2px 12px" }}
              onClick={onFlushOutline}
              disabled={!isDirty && merged === (chapter.synopsis || "")}
              title="把合并后的大纲写回编辑器的 章节synopsis 字段">
              {isDirty ? "回写章节大纲 ●" : "回写章节大纲"}
            </button>
          </div>
          {merged ? (
            <pre className="font-mono" style={{
              fontSize: 12, lineHeight: 1.6, color: "var(--text-secondary)",
              background: "var(--bg-surface)", borderRadius: "var(--radius-sm)",
              padding: "8px 10px", margin: 0, whiteSpace: "pre-wrap",
              border: "1px solid var(--border)",
            }}>{merged}</pre>
          ) : (
            <div className="text-xs text-muted" style={{ padding: "8px 4px" }}>
              添加事件卡片，合并后的大纲会自动出现在这里。
            </div>
          )}
        </div>
      </div>
    </div>
  );
}


/* ── Single event card inside a chapter row ── */
function EventCard({ node, selected, onSelect, onChange, onDelete }: {
  node: StoryNode;
  selected: boolean;
  onSelect: () => void;
  onChange: (patch: Partial<StoryNode>) => void;
  onDelete: () => void;
}) {
  return (
    <div
      onClick={onSelect}
      style={{
        minWidth: 200, maxWidth: 220, flexShrink: 0,
        background: "var(--bg-surface)",
        border: `1px solid ${selected ? "var(--accent)" : "var(--border)"}`,
        borderTop: `4px solid ${node.color || "var(--accent)"}`,
        borderRadius: 8, padding: "8px 10px",
        cursor: "pointer", position: "relative",
        boxShadow: selected ? "0 0 0 2px var(--accent-subtle)" : undefined,
      }}>
      <input
        value={node.title}
        onChange={e => onChange({ title: e.target.value })}
        onClick={e => e.stopPropagation()}
        style={{
          background: "transparent", border: "none", outline: "none",
          fontSize: 12, fontWeight: 600, color: "var(--text-primary)",
          width: "calc(100% - 18px)", padding: 0,
        }}
        placeholder="事件标题" />
      <button
        onClick={(e) => { e.stopPropagation(); onDelete(); }}
        title="删除"
        style={{
          position: "absolute", top: 4, right: 4,
          background: "none", border: "none", cursor: "pointer",
          color: "var(--text-tertiary)", fontSize: 13, lineHeight: 1,
        }}>×</button>
      <textarea
        value={node.summary || ""}
        onChange={e => onChange({ summary: e.target.value })}
        onClick={e => e.stopPropagation()}
        placeholder="一两句事件概要…"
        rows={3}
        style={{
          width: "100%", marginTop: 4,
          background: "transparent", border: "none", outline: "none",
          color: "var(--text-secondary)", fontSize: 11, lineHeight: 1.5,
          resize: "none", boxSizing: "border-box",
        }} />
      {(node.characters?.length || 0) > 0 && (
        <div className="flex gap-3" style={{ marginTop: 4, flexWrap: "wrap" }}>
          {node.characters!.slice(0, 3).map(c => (
            <span key={c} style={{
              fontSize: 9, padding: "1px 5px", borderRadius: 6,
              background: "var(--purple-subtle)", color: "var(--purple)",
            }}>{c}</span>
          ))}
          {(node.characters!.length || 0) > 3 && (
            <span className="text-xs text-muted">+{node.characters!.length - 3}</span>
          )}
        </div>
      )}
    </div>
  );
}


/* ── Character multi-select dropdown (chapter-level + event-level) ──
 * Sources its option list from /api/data/characters so the user can only
 * pick characters that actually exist in 角色管理. Falls back to a free-
 * text input behind a 「+ 自定义」 escape hatch when the character isn't
 * registered yet.
 */
function CharacterSelector({ value, options, onChange, label }: {
  value: string[];
  options: Character[];
  onChange: (next: string[]) => void;
  label: string;
}) {
  const [open, setOpen] = useState(false);
  const [customInput, setCustomInput] = useState("");
  const wrapRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
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
      <div className="label mb-4" style={{ fontSize: 11 }}>{label}</div>
      <div
        onClick={() => setOpen(o => !o)}
        style={{
          display: "flex", flexWrap: "wrap", gap: 4,
          minHeight: 30, padding: "4px 8px",
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


/* ── Event detail drawer (right side overlay) ──
 * Shown when a card is selected; exposes the longer-form fields that
 * don't fit on the card surface (time, location, character multi-select,
 * color swatch, delete). */
function EventDetailDrawer({ node, allCharacters, onChange, onClose, onDelete }: {
  node: StoryNode;
  allCharacters: Character[];
  onChange: (patch: Partial<StoryNode>) => void;
  onClose: () => void;
  onDelete: () => void;
}) {
  return (
    <>
      <div onClick={onClose} style={{
        position: "fixed", inset: 0, background: "rgba(0,0,0,0.18)", zIndex: 900,
      }} />
      <aside style={{
        position: "fixed", top: 0, right: 0, bottom: 0,
        width: "min(360px, 95vw)", background: "var(--bg-surface)",
        borderLeft: "1px solid var(--border)",
        boxShadow: "-4px 0 12px rgba(0,0,0,0.15)",
        zIndex: 901, display: "flex", flexDirection: "column",
      }}>
        <div className="panel-header">
          <h3>事件详情</h3>
          <button className="btn-icon" onClick={onClose} title="关闭"
            style={{ fontSize: 14 }}>×</button>
        </div>
        <div className="panel-body" style={{ padding: 14, overflowY: "auto" }}>
          <div className="field mb-12">
            <label className="label">标题</label>
            <input className="input" value={node.title}
              onChange={e => onChange({ title: e.target.value })} />
          </div>
          <div className="field mb-12">
            <label className="label">事件概要</label>
            <textarea className="input" rows={4}
              value={node.summary || ""}
              onChange={e => onChange({ summary: e.target.value })}
              placeholder="这件事在章节里发生了什么？" />
          </div>
          <div className="flex gap-8 mb-12">
            <div className="field" style={{ flex: 1 }}>
              <label className="label">时间</label>
              <input className="input" value={node.time || ""}
                onChange={e => onChange({ time: e.target.value })}
                placeholder="例：第3天·黄昏" />
            </div>
            <div className="field" style={{ flex: 1 }}>
              <label className="label">地点</label>
              <input className="input" value={node.location || ""}
                onChange={e => onChange({ location: e.target.value })}
                placeholder="例：剑庐" />
            </div>
          </div>
          <CharacterSelector
            value={node.characters || []}
            options={allCharacters}
            onChange={(v) => onChange({ characters: v })}
            label="出场角色" />
          <div className="field mb-12 mt-12">
            <label className="label">颜色</label>
            <div className="flex gap-6" style={{ flexWrap: "wrap" }}>
              {COLORS.map(c => (
                <div key={c}
                  onClick={() => onChange({ color: c })}
                  style={{
                    width: 22, height: 22, borderRadius: 12, background: c,
                    cursor: "pointer",
                    border: node.color === c ? "3px solid var(--text-primary)" : "2px solid transparent",
                  }} />
              ))}
            </div>
          </div>
          <button className="btn w-full mt-12"
            style={{ justifyContent: "center", color: "var(--error)" }}
            onClick={onDelete}>
            删除事件
          </button>
        </div>
      </aside>
    </>
  );
}
