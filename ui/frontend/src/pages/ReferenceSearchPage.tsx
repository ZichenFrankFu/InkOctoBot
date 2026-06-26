import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiGet, apiPost } from "../api/client";
import { useToast } from "../components/shared/Toast";
import { useDialog } from "../components/shared/Dialog";
import type { ReferenceWork } from "../api/types";
import CompareWorksPanel from "../components/CompareWorksPanel";
import CommonPatternLearningPanel from "../components/reference/CommonPatternLearningPanel";

interface SearchHit {
  id: string;
  text: string;
  distance: number;
  metadata: Record<string, any>;
}

interface SearchResponse {
  q: string;
  k: number;
  levels: string[];
  hits: SearchHit[];
}

interface IndexProgressRow {
  ref_id: string;
  level: string;
  backend: string;
  done: number;
  total: number;
  status: string;
  error?: string;
  last_ordinal?: number;
  updated_at?: string;
}

const SOURCE_LABEL: Record<string, string> = {
  chronicle_event: "大纲",
  character: "角色",
  setting: "设定",
  chapter_summary: "章节摘要",
  chapter_chunk: "正文片段",
};
const SOURCE_COLOR: Record<string, string> = {
  chronicle_event: "var(--accent)",
  character: "var(--purple)",
  setting: "var(--indigo)",
  chapter_summary: "var(--jade)",
  chapter_chunk: "var(--gold)",
};

const STATUS_LABEL: Record<string, { label: string; color: string }> = {
  done:            { label: "已完成", color: "var(--jade)" },
  running:         { label: "进行中", color: "var(--gold)" },
  pending:         { label: "等待中", color: "var(--accent)" },
  error:           { label: "出错",   color: "var(--error)" },
  not_applicable:  { label: "不适用", color: "var(--text-disabled)" },
};

type RsTab = "search" | "index" | "compare" | "learn";

interface Props {
  onNavigate?: (tab: string) => void;
  /** Force the page to open with a specific tab active. */
  initialTab?: RsTab;
  /** When true, render only the active tab's body — no tab bar.
   *  Used by InspirationSearchPage / InspirationLibraryPage when they
   *  mount this component to surface one capability per page. */
  hideTabs?: boolean;
  /** Replace the H1 title — lets the wrapper page brand the content. */
  pageTitle?: string;
  pageSubtitle?: string;
  /** Hosted as a subtab inside 参考总览: drop the page header and
   *  outer padding, keep the internal tool tabs. */
  embedded?: boolean;
}

export default function ReferenceSearchPage({ onNavigate, initialTab, hideTabs, pageTitle, pageSubtitle, embedded }: Props) {
  const { toast } = useToast();
  const { confirm } = useDialog();
  const [works, setWorks] = useState<ReferenceWork[]>([]);
  const [progressByRef, setProgressByRef] = useState<Record<string, IndexProgressRow[]>>({});
  const [activeTab, setActiveTab] = useState<RsTab>(initialTab || "search");

  // Search state
  const [q, setQ] = useState("");
  const [k, setK] = useState(10);
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [drillingRefId, setDrillingRefId] = useState<string | null>(null);
  const [drillHits, setDrillHits] = useState<SearchHit[]>([]);

  // Index state
  const [indexing, setIndexing] = useState<Record<string, boolean>>({});
  const [indexFilter, setIndexFilter] = useState<"all" | "indexed" | "unindexed">("all");
  // Multi-select 索引级别（默认 L1 + L2；L3 较重，按需勾选）。
  const [selectedLevels, setSelectedLevels] = useState<Set<"L1" | "L2" | "L3">>(
    new Set(["L1", "L2"]),
  );
  // Multi-select works for the batch builder. Empty by default — the user
  // has to opt into which works get re-indexed (no accidental "rebuild all").
  const [selectedRefIds, setSelectedRefIds] = useState<Set<string>>(new Set());

  // ── Loaders ──

  const loadWorks = useCallback(async () => {
    try {
      const r = await apiGet<{ items: ReferenceWork[] }>("/api/references/works?limit=500");
      setWorks(r.items || []);
    } catch (e) { console.warn("works load failed:", e); }
  }, []);

  const loadProgress = useCallback(async (refIds: string[]) => {
    // Fan-out one request per work. With a reasonable library (<500 works)
    // this is fine; we batch in Promise.all to keep latency low.
    // Merge into existing state so partial refreshes (during polling)
    // don't wipe out progress for other works.
    const pairs = await Promise.all(refIds.map(async rid => {
      try {
        const r = await apiGet<{ items: IndexProgressRow[] }>(
          `/api/references/works/${rid}/index/progress`
        );
        return [rid, r.items || []] as const;
      } catch { return [rid, [] as IndexProgressRow[]] as const; }
    }));
    setProgressByRef(prev => ({ ...prev, ...Object.fromEntries(pairs) }));
  }, []);

  const refreshAll = useCallback(async () => {
    await loadWorks();
  }, [loadWorks]);

  useEffect(() => { loadWorks(); }, [loadWorks]);
  useEffect(() => {
    if (works.length === 0) return;
    loadProgress(works.map(w => w.ref_id));
  }, [works, loadProgress]);

  // Live progress polling — while any work is being indexed, refresh
  // its progress row every 1.5s so the UI shows a moving progress bar
  // instead of jumping from 0 → done at the very end of the build.
  useEffect(() => {
    const activeIds = Object.keys(indexing).filter(id => indexing[id]);
    if (activeIds.length === 0) return;
    let cancelled = false;
    const tick = async () => {
      if (cancelled) return;
      await loadProgress(activeIds);
    };
    tick();
    const t = setInterval(tick, 1500);
    return () => { cancelled = true; clearInterval(t); };
  }, [indexing, loadProgress]);

  // ── Search actions ──

  const runSearch = useCallback(async (queryText?: string) => {
    const query = (queryText ?? q).trim();
    if (!query) return;
    setLoading(true);
    setHits([]);
    setDrillingRefId(null);
    setDrillHits([]);
    try {
      const params = new URLSearchParams({
        q: query, k: String(k), levels: "L1,L2",
      });
      const r = await apiGet<SearchResponse>(`/api/references/search?${params}`);
      setHits(r.hits || []);
      if ((r.hits || []).length === 0) {
        toast("无匹配结果。可能尚未对作品建立索引。", "info");
      }
    } catch (e: any) {
      toast(e?.message || "搜索失败", "error");
    } finally { setLoading(false); }
  }, [q, k, toast]);

  const drillDeep = async (refId: string) => {
    if (!q.trim()) return;
    setDrillingRefId(refId);
    setDrillHits([]);
    setLoading(true);
    try {
      const params = new URLSearchParams({
        q: q.trim(), k: String(k),
        levels: "L3", ref_id: refId,
      });
      const r = await apiGet<SearchResponse>(`/api/references/search?${params}`);
      setDrillHits(r.hits || []);
      if ((r.hits || []).length === 0) {
        toast("该作品尚未建立 L3 深度索引。请先到「索引管理」 tab 建立。", "info");
      }
    } catch (e: any) {
      toast(e?.message || "深度搜索失败", "error");
    } finally { setLoading(false); }
  };

  // ── Index actions ──

  /** Build a specific level for one work. The /index/run API accepts
   *  one level at a time (or "all" for L1+L2). For arbitrary level
   *  combinations we just call it level-by-level. */
  const buildLevel = async (
    refId: string, level: "L1" | "L2" | "L3",
  ): Promise<void> => {
    setIndexing(prev => ({ ...prev, [refId]: true }));
    try {
      const r = await apiPost<any>(
        `/api/references/works/${refId}/index/run`,
        { level, include_l3: level === "L3" },
        { timeoutMs: 900_000 },
      );
      const lv = r?.[level];
      if (lv?.embedded != null) {
        toast(`${level} 索引完成：${lv.embedded}`, "success");
      } else {
        toast(`${level} 索引完成`, "success");
      }
      await loadProgress([refId]);
    } catch (e: any) {
      toast(e?.message || `${level} 索引失败`, "error");
    } finally {
      setIndexing(prev => ({ ...prev, [refId]: false }));
    }
  };

  const buildAllIndexes = async () => {
    const levels = Array.from(selectedLevels) as ("L1" | "L2" | "L3")[];
    if (levels.length === 0) {
      toast("请先勾选要建立的索引级别（L1 / L2 / L3）", "info");
      return;
    }
    if (selectedRefIds.size === 0) {
      toast("请先勾选要建索引的作品（可点击「全选」选中当前筛选下的全部作品）", "info");
      return;
    }
    const orderedLevels = (["L1", "L2", "L3"] as const).filter(l => levels.includes(l));
    const includesL3 = orderedLevels.includes("L3");
    const summary = orderedLevels.join(" + ");
    const targetWorks = works.filter(w => selectedRefIds.has(w.ref_id));
    if (!(await confirm({
      message: `确认为已选 ${targetWorks.length} 部作品建立 ${summary} 索引？`
        + (includesL3 ? "\nL3 较重，~10 万字单作品需 1–3 分钟。" : "")
        + "\n纯设定作品会自动跳过 L2 / L3。",
    }))) return;
    for (const w of targetWorks) {
      const isPureSetting = (w as any).structure_type === "setting_collection";
      for (const level of orderedLevels) {
        // 纯设定作品没有章节正文，跳过 L2/L3。
        if (isPureSetting && (level === "L2" || level === "L3")) continue;
        // Sequential to avoid hammering local embedding backend with parallel requests.
        // eslint-disable-next-line no-await-in-loop
        await buildLevel(w.ref_id, level);
      }
    }
  };

  const clearIndex = async (refId: string) => {
    if (!(await confirm({ message: "确认删除本作品的全部向量索引？", destructive: true }))) return;
    try {
      await fetch(`/api/references/works/${refId}/index`, { method: "DELETE" });
      toast("已清除", "success");
      await loadProgress([refId]);
    } catch (e: any) {
      toast(e?.message || "清除失败", "error");
    }
  };

  // Hits grouped by ref_id for the per-work card rendering
  const hitsByWork = useMemo(() => {
    const groups: Record<string, { title: string; rows: SearchHit[] }> = {};
    for (const h of hits) {
      const refId = h.metadata.ref_id as string;
      const title = (h.metadata.title as string) || refId;
      if (!groups[refId]) groups[refId] = { title, rows: [] };
      groups[refId].rows.push(h);
    }
    return Object.entries(groups)
      .map(([refId, g]) => ({
        ref_id: refId,
        title: g.title,
        rows: g.rows.sort((a, b) => a.distance - b.distance),
        best_dist: Math.min(...g.rows.map(r => r.distance)),
      }))
      .sort((a, b) => a.best_dist - b.best_dist);
  }, [hits]);

  const filteredWorks = useMemo(() => {
    if (indexFilter === "all") return works;
    return works.filter(w => {
      const rows = progressByRef[w.ref_id] || [];
      const hasIndex = rows.some(r => r.status === "done" && r.done > 0);
      return indexFilter === "indexed" ? hasIndex : !hasIndex;
    });
  }, [works, progressByRef, indexFilter]);

  const openDetail = (refId: string) => {
    try { sessionStorage.setItem("ref_open_ref_id", refId); } catch { /* noop */ }
    onNavigate?.("references");
  };

  return (
    <div style={embedded ? {} : { padding: "16px 20px", maxWidth: 1400, margin: "0 auto" }}>
      {!embedded && (
        <div className="page-header" style={{ paddingBottom: 12 }}>
          <div className="page-header-row">
            <div>
              <h2>{pageTitle || "参考数据库工具"}</h2>
              <p>{pageSubtitle || "参考作品搜索 · 作品对比 · 索引管理"}</p>
            </div>
            <div className="flex gap-8">
              <button className="btn" onClick={refreshAll}>刷新</button>
            </div>
          </div>
        </div>
      )}

      {/* Tabs */}
      {!hideTabs && (
      <div className="flex" style={{ marginBottom: 12, gap: 4, borderBottom: "1px solid var(--border)", alignItems: "center" }}>
        {([
          // 灵感库 tab removed — promoted to a dedicated page in the
          // 灵感数据库 nav group (see InspirationLibraryPage).
          { key: "search"  as const, label: "参考作品搜索" },
          { key: "compare" as const, label: "作品对比" },
          { key: "learn"   as const, label: "共通点学习" },
          { key: "index"   as const, label: `索引管理 · ${works.length} 部作品` },
        ]).map(t => (
          <button
            key={t.key}
            className="btn-ghost"
            onClick={() => setActiveTab(t.key)}
            style={{
              padding: "8px 16px",
              fontSize: 13,
              fontWeight: activeTab === t.key ? 600 : 400,
              color: activeTab === t.key ? "var(--accent)" : "var(--text-secondary)",
              borderBottom: activeTab === t.key ? "2px solid var(--accent)" : "2px solid transparent",
              marginBottom: -1,
              borderRadius: 0,
            }}
          >{t.label}</button>
        ))}
        {embedded && (
          <button className="btn" style={{ marginLeft: "auto", fontSize: 11, padding: "3px 12px" }} onClick={refreshAll}>
            刷新
          </button>
        )}
      </div>
      )}

      {activeTab === "search" && (
        <>
          <div className="card" style={{ marginBottom: 14 }}>
            <div className="card-body">
              <div className="flex gap-8 items-center" style={{ flexWrap: "wrap" }}>
                <input
                  className="input"
                  placeholder='例如："主角第一次觉醒能力的场景" / "穿越后第一次见到女主"'
                  value={q}
                  onChange={e => setQ(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter") runSearch(); }}
                  style={{ flex: 1, minWidth: 280 }}
                />
                <label className="flex items-center gap-4" style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                  <span>top-k</span>
                  <input
                    type="number" value={k}
                    onChange={e => setK(Math.max(1, Math.min(50, parseInt(e.target.value, 10) || 10)))}
                    className="input" style={{ width: 60 }}
                  />
                </label>
                <button className="btn-primary" onClick={() => runSearch()} disabled={loading || !q.trim()}>
                  {loading ? "搜索中..." : "搜索"}
                </button>
              </div>
            </div>
          </div>

          {hitsByWork.length === 0 ? (
            loading ? (
              <div className="empty-state" style={{ paddingTop: 60 }}><p>搜索中...</p></div>
            ) : (
              <div className="empty-state" style={{ paddingTop: 40 }}>
                <p>输入查询并回车开始搜索。</p>
              </div>
            )
          ) : (
            <div className="flex flex-col gap-12">
              {hitsByWork.map(g => (
                <div key={g.ref_id} className="card">
                  <div className="card-header" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                    <h3>
                      {g.title}
                      <span className="text-xs text-muted" style={{ marginLeft: 8, fontWeight: 400 }}>
                        {g.rows.length} 条匹配 · 最佳距离 {g.best_dist.toFixed(3)}
                      </span>
                    </h3>
                    <div className="flex gap-6">
                      <button
                        className="btn"
                        style={{ fontSize: 11, padding: "3px 10px" }}
                        onClick={() => drillDeep(g.ref_id)}
                        disabled={loading}
                      >查看本作品更多匹配片段</button>
                      <button
                        className="btn-primary"
                        style={{ fontSize: 11, padding: "3px 10px" }}
                        onClick={() => openDetail(g.ref_id)}
                      >打开详情</button>
                    </div>
                  </div>
                  <div className="card-body">
                    <ResultsList rows={g.rows} />
                    {drillingRefId === g.ref_id && drillHits.length > 0 && (
                      <div style={{
                        marginTop: 14, padding: 10,
                        background: "var(--bg-surface)",
                        border: "1px dashed var(--gold)",
                        borderRadius: 4,
                      }}>
                        <div style={{ fontSize: 11, fontWeight: 600, color: "var(--gold)", marginBottom: 6 }}>
                          L3 深度匹配（正文片段，按距离排序）
                        </div>
                        <ResultsList rows={drillHits} />
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {/* 灵感库 tab removed — see InspirationLibraryPage. */}

      {activeTab === "index" && (() => {
        const filteredIds = filteredWorks.map(w => w.ref_id);
        const allSelectedInFilter = filteredIds.length > 0
          && filteredIds.every(id => selectedRefIds.has(id));
        const someSelectedInFilter = filteredIds.some(id => selectedRefIds.has(id));
        const toggleSelectAll = () => {
          setSelectedRefIds(prev => {
            const next = new Set(prev);
            if (allSelectedInFilter) {
              filteredIds.forEach(id => next.delete(id));
            } else {
              filteredIds.forEach(id => next.add(id));
            }
            return next;
          });
        };
        const toggleWork = (refId: string) => {
          setSelectedRefIds(prev => {
            const next = new Set(prev);
            if (next.has(refId)) next.delete(refId); else next.add(refId);
            return next;
          });
        };
        return (
        <>
          <div className="card" style={{ marginBottom: 14 }}>
            <div className="card-body">
              <div className="flex items-center gap-12" style={{
                flexWrap: "wrap", marginBottom: 12,
              }}>
                <div className="flex gap-4">
                  {([
                    { key: "all" as const, label: `全部 ${works.length}` },
                    { key: "indexed" as const, label: `已索引` },
                    { key: "unindexed" as const, label: `未索引` },
                  ]).map(f => (
                    <button
                      key={f.key}
                      className={indexFilter === f.key ? "btn-primary" : "btn"}
                      style={{ fontSize: 11, padding: "3px 10px" }}
                      onClick={() => setIndexFilter(f.key)}
                    >{f.label}</button>
                  ))}
                </div>
                <span className="text-xs text-muted">
                  已选 {selectedRefIds.size} / {works.length} 部作品
                </span>
                <div style={{ flex: 1 }} />
                <button className="btn"
                        onClick={toggleSelectAll}
                        disabled={filteredIds.length === 0}>
                  {allSelectedInFilter ? "取消全选" : "全选"}
                </button>
                <button className="btn-primary"
                        onClick={buildAllIndexes}
                        disabled={Object.values(indexing).some(Boolean)
                          || selectedLevels.size === 0
                          || selectedRefIds.size === 0}>
                  建立索引（{selectedRefIds.size} 部 × {selectedLevels.size} 级）
                </button>
              </div>
              <LevelMultiSelect
                selected={selectedLevels}
                onChange={setSelectedLevels}
              />
              {someSelectedInFilter && selectedLevels.has("L3") && (
                <div className="text-xs text-muted" style={{ marginTop: 8, lineHeight: 1.5 }}>
                  提示：L3 较重，~10 万字单作品需 1–3 分钟；纯设定作品会自动跳过 L2 / L3。
                </div>
              )}
            </div>
          </div>

          <div className="card">
            <div className="card-body" style={{ padding: 0 }}>
              {filteredWorks.length === 0 ? (
                <div className="text-xs text-muted text-center" style={{ padding: 24 }}>
                  无匹配作品。
                </div>
              ) : filteredWorks.map((w, idx) => (
                <IndexRow
                  key={w.ref_id}
                  w={w}
                  rows={progressByRef[w.ref_id] || []}
                  indexing={!!indexing[w.ref_id]}
                  selected={selectedRefIds.has(w.ref_id)}
                  onToggleSelect={() => toggleWork(w.ref_id)}
                  onClear={() => clearIndex(w.ref_id)}
                  onOpen={() => openDetail(w.ref_id)}
                  topBorder={idx > 0}
                />
              ))}
            </div>
          </div>
        </>
        );
      })()}

      {activeTab === "compare" && <CompareWorksPanel />}
      {activeTab === "learn" && <CommonPatternLearningPanel works={works} />}
    </div>
  );
}

/** Per-level meta — drives the multi-select description cards. */
const LEVEL_META = {
  "L1": {
    label: "L1 · 作品级",
    color: "var(--jade)",
    desc: "大纲 / 角色 / 设定 — 跨作品检索的基础索引，建立最快。",
  },
  "L2": {
    label: "L2 · 章节级",
    color: "var(--gold)",
    desc: "章节摘要 — 章节粒度的语义检索，覆盖每章关键节拍。",
  },
  "L3": {
    label: "L3 · 正文片段",
    color: "var(--accent)",
    desc: "1500 字片段 — 正文级精细检索，~10 万字单作品需 1–3 分钟。",
  },
} as const;

function LevelMultiSelect({
  selected, onChange,
}: {
  selected: Set<"L1" | "L2" | "L3">;
  onChange: (next: Set<"L1" | "L2" | "L3">) => void;
}) {
  const toggle = (lv: "L1" | "L2" | "L3") => {
    const next = new Set(selected);
    if (next.has(lv)) next.delete(lv); else next.add(lv);
    onChange(next);
  };
  return (
    <div style={{
      display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 10,
    }}>
      {(["L1", "L2", "L3"] as const).map(lv => {
        const meta = LEVEL_META[lv];
        const on = selected.has(lv);
        return (
          <label
            key={lv}
            onClick={(e) => { e.preventDefault(); toggle(lv); }}
            style={{
              display: "flex", alignItems: "flex-start", gap: 10,
              padding: "10px 12px",
              border: `1px solid ${on ? meta.color : "var(--border)"}`,
              borderRadius: "var(--radius-sm)",
              background: on ? "var(--bg-surface-2)" : "var(--bg-surface)",
              cursor: "pointer",
              transition: "border-color 0.15s, background 0.15s",
            }}>
            <input
              className="checkbox-pretty"
              type="checkbox"
              checked={on}
              onChange={() => toggle(lv)}
              onClick={(e) => e.stopPropagation()}
              style={{ marginTop: 2 }}
            />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{
                fontSize: 13, fontWeight: 700,
                color: on ? meta.color : "var(--text-primary)",
                marginBottom: 2,
              }}>{meta.label}</div>
              <div className="text-xs" style={{
                color: "var(--text-tertiary)", lineHeight: 1.6,
              }}>{meta.desc}</div>
            </div>
          </label>
        );
      })}
    </div>
  );
}

function IndexRow({ w, rows, indexing, selected, onToggleSelect, onClear, onOpen, topBorder }: {
  w: ReferenceWork;
  rows: IndexProgressRow[];
  indexing: boolean;
  selected: boolean;
  onToggleSelect: () => void;
  onClear: () => void;
  onOpen: () => void;
  topBorder: boolean;
}) {
  const isPureSetting = (w as any).structure_type === "setting_collection";
  const visibleLevels = isPureSetting
    ? (["L1"] as const)
    : (["L1", "L2", "L3"] as const);
  const byLevel: Record<string, IndexProgressRow> = {};
  for (const r of rows) byLevel[r.level] = r;
  // Aggregate progress across levels — used by the live progress bar
  // when this work is currently being indexed.
  let totalDone = 0, totalTarget = 0;
  for (const r of rows) {
    totalDone += r.done || 0;
    totalTarget += r.total || 0;
  }
  const pct = totalTarget > 0 ? Math.min(100, Math.round((totalDone / totalTarget) * 100)) : 0;
  return (
    <div style={{
      padding: "12px 16px",
      borderTop: topBorder ? "1px solid var(--border)" : "none",
      background: selected ? "var(--accent-subtle)" : undefined,
      transition: "background 0.15s",
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <input
          type="checkbox"
          className="checkbox-pretty"
          checked={selected}
          onChange={onToggleSelect}
          disabled={indexing}
          style={{ flexShrink: 0 }}
          aria-label={`选择 ${w.title}`}
        />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="flex items-center gap-8" style={{ marginBottom: 3, flexWrap: "wrap" }}>
            <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>{w.title}</span>
            {w.creator && <span className="text-xs text-muted">· {w.creator}</span>}
            {isPureSetting && (
              <span className="tag" style={{
                fontSize: 10, padding: "1px 6px",
                color: "var(--indigo)", background: "var(--bg-surface-2)",
                border: "1px solid var(--indigo)",
              }}>纯设定</span>
            )}
          </div>
          <div className="flex gap-6" style={{ flexWrap: "wrap" }}>
            {visibleLevels.map(lv => {
              const row = byLevel[lv];
              const status = row?.status;
              const meta = STATUS_LABEL[status || ""] || { label: "未索引", color: "var(--text-tertiary)" };
              return (
                <span key={lv}
                  title={row ? `${meta.label} · 已完成 ${row.done}/${row.total || "?"}${row.error ? ` · 错误：${row.error}` : ""}` : "尚未索引"}
                  className="tag"
                  style={{
                    fontSize: 10, padding: "1px 6px",
                    color: meta.color,
                    background: "var(--bg-surface-2)",
                    border: `1px solid ${meta.color}`,
                  }}
                >{lv} · {meta.label}{row?.done ? ` (${row.done})` : ""}</span>
              );
            })}
          </div>
        </div>
        <div className="flex gap-6" style={{ flexShrink: 0 }}>
          <button className="btn" style={{ fontSize: 11, padding: "3px 10px", color: "var(--text-tertiary)" }}
                  onClick={onClear} disabled={indexing}>
            清除
          </button>
          <button className="btn-primary" style={{ fontSize: 11, padding: "3px 10px" }} onClick={onOpen}>
            打开
          </button>
        </div>
      </div>
      {/* Live progress bar while this work is actively being indexed. */}
      {indexing && (
        <div style={{ marginTop: 8 }}>
          <div style={{
            height: 6, background: "var(--bg-surface-2)",
            borderRadius: 3, overflow: "hidden",
          }}>
            <div style={{
              height: "100%", width: `${pct}%`,
              background: "var(--accent)",
              transition: "width 0.3s var(--ease-out)",
            }} />
          </div>
          <div className="flex items-center" style={{
            gap: 8, marginTop: 4,
            fontSize: 11, color: "var(--text-tertiary)",
            fontFamily: "var(--font-mono)",
          }}>
            <span>{totalDone.toLocaleString()} / {totalTarget > 0 ? totalTarget.toLocaleString() : "?"}</span>
            <span style={{ color: "var(--accent)", fontWeight: 700 }}>{pct}%</span>
          </div>
        </div>
      )}
    </div>
  );
}

function ResultsList({ rows }: { rows: SearchHit[] }) {
  return (
    <div className="flex flex-col gap-8">
      {rows.map(h => <ResultRow key={h.id} h={h} />)}
    </div>
  );
}

/** One search hit. The matched passage collapses to 5 lines by default;
 *  long passages get a 展开 / 收起 toggle. */
function ResultRow({ h }: { h: SearchHit }) {
  const [expanded, setExpanded] = useState(false);
  const src = (h.metadata.source_type as string) || "chapter_chunk";
  const tm = (h.metadata.time_marker as string)
          || (h.metadata.first_seen_at as string)
          || (h.metadata.first_introduced_at as string)
          || (typeof h.metadata.chapter === "number" ? `第 ${h.metadata.chapter} 章` : "");
  const color = SOURCE_COLOR[src] || "var(--text-tertiary)";
  const longText = (h.text || "").length > 120;
  return (
    <div style={{
      padding: "8px 12px",
      background: "var(--bg-surface)",
      borderRadius: 4,
      borderLeft: `3px solid ${color}`,
    }}>
      <div className="flex items-center gap-6" style={{ marginBottom: 4, flexWrap: "wrap" }}>
        <span className="tag" style={{
          fontSize: 10, padding: "1px 6px",
          color, background: "var(--bg-surface-2)",
          border: `1px solid ${color}`,
        }}>{SOURCE_LABEL[src] || src}</span>
        {tm && (
          <span className="tag" style={{
            fontSize: 10, padding: "1px 6px",
            color: "var(--accent)", background: "var(--accent-subtle)",
            border: "1px solid var(--accent)",
          }}>{tm}</span>
        )}
        <span className="text-xs text-muted" style={{ marginLeft: "auto", fontFamily: "var(--font-mono)" }}>
          距离 {h.distance.toFixed(3)}
        </span>
      </div>
      <div style={{
        fontSize: 12, lineHeight: 1.6,
        color: "var(--text-secondary)",
        whiteSpace: "pre-wrap",
        ...(expanded ? {} : {
          display: "-webkit-box",
          WebkitBoxOrient: "vertical" as const,
          WebkitLineClamp: 5,
          overflow: "hidden",
        }),
      }}>{h.text}</div>
      {longText && (
        <button className="btn-ghost" onClick={() => setExpanded(e => !e)}
          style={{ fontSize: 11, padding: "3px 0 0", color: "var(--accent)" }}>
          {expanded ? "收起 ▲" : "展开 ▼"}
        </button>
      )}
    </div>
  );
}
