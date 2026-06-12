import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiGet, apiPost } from "../api/client";
import { useToast } from "../components/shared/Toast";
import CommonPatternLearningPanel from "../components/reference/CommonPatternLearningPanel";
import { useDialog } from "../components/shared/Dialog";
import type { ReferenceWork } from "../api/types";
import CompareWorksPanel from "../components/CompareWorksPanel";

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
  done:    { label: "已完成", color: "var(--jade)" },
  running: { label: "进行中", color: "var(--gold)" },
  pending: { label: "等待中", color: "var(--accent)" },
  error:   { label: "出错",   color: "var(--error)" },
};

interface Props { onNavigate?: (tab: string) => void; }

export default function ReferenceSearchPage({ onNavigate }: Props) {
  const { toast } = useToast();
  const { confirm } = useDialog();
  const [works, setWorks] = useState<ReferenceWork[]>([]);
  const [progressByRef, setProgressByRef] = useState<Record<string, IndexProgressRow[]>>({});
  const [activeTab, setActiveTab] = useState<"search" | "index" | "compare" | "learn">("search");

  // Search state
  const [q, setQ] = useState("");
  const [k, setK] = useState(10);
  const [includeL3InSearch, setIncludeL3InSearch] = useState(false);
  const [hits, setHits] = useState<SearchHit[]>([]);

  // 灵感管理页跳转过来时携带的检索词 (sessionStorage handover)。
  useEffect(() => {
    try {
      const pending = sessionStorage.getItem("inspiration_search_query");
      if (pending) {
        sessionStorage.removeItem("inspiration_search_query");
        const query = pending.trim().slice(0, 600);
        setQ(query);
        runSearch(query);
      }
    } catch {}
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const [loading, setLoading] = useState(false);
  const [drillingRefId, setDrillingRefId] = useState<string | null>(null);
  const [drillHits, setDrillHits] = useState<SearchHit[]>([]);

  // Index state
  const [indexing, setIndexing] = useState<Record<string, boolean>>({});
  const [indexFilter, setIndexFilter] = useState<"all" | "indexed" | "unindexed">("all");

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
    const pairs = await Promise.all(refIds.map(async rid => {
      try {
        const r = await apiGet<{ items: IndexProgressRow[] }>(
          `/api/references/works/${rid}/index/progress`
        );
        return [rid, r.items || []] as const;
      } catch { return [rid, [] as IndexProgressRow[]] as const; }
    }));
    setProgressByRef(Object.fromEntries(pairs));
  }, []);

  const refreshAll = useCallback(async () => {
    await loadWorks();
  }, [loadWorks]);

  useEffect(() => { loadWorks(); }, [loadWorks]);
  useEffect(() => {
    if (works.length === 0) return;
    loadProgress(works.map(w => w.ref_id));
  }, [works, loadProgress]);

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

  const buildIndex = async (refId: string, includeL3: boolean) => {
    setIndexing(prev => ({ ...prev, [refId]: true }));
    try {
      // 性能预期·机制1: 执行前给出预计耗时（同硬件历史优先）。
      try {
        const est = await apiGet<any>(
          `/api/references/works/${refId}/index/estimate?include_l3=${includeL3}`);
        if (est?.display) {
          toast(`开始建立索引，预计耗时 ${est.display}` +
            (est.basis === "history" ? `（按 ${est.samples} 次历史运行估算）` : "（默认估算）"), "info");
        }
      } catch {}
      const r = await apiPost<any>(
        `/api/references/works/${refId}/index/run`,
        { level: "all", include_l3: includeL3 },
        { timeoutMs: 900_000 },
      );
      const parts: string[] = [];
      if (r?.L1?.embedded != null) parts.push(`L1=${r.L1.embedded}`);
      if (r?.L2?.embedded != null) parts.push(`L2=${r.L2.embedded}`);
      if (r?.L3?.embedded != null) parts.push(`L3=${r.L3.embedded}`);
      toast(`索引完成：${parts.join(", ") || "OK"}`, "success");
      await loadProgress([refId]);
    } catch (e: any) {
      toast(e?.message || "索引失败", "error");
    } finally { setIndexing(prev => ({ ...prev, [refId]: false })); }
  };

  const buildAllIndexes = async () => {
    if (!(await confirm({ message: `确认为全部 ${works.length} 部作品建立 L1+L2 索引？L3（正文片段）不会包含；如需逐部勾选 L3。` }))) return;
    for (const w of works) {
      // Sequential to avoid hammering local embedding backend with parallel requests.
      // eslint-disable-next-line no-await-in-loop
      await buildIndex(w.ref_id, false);
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
    <div style={{ padding: "16px 20px", maxWidth: 1400, margin: "0 auto" }}>
      <div className="page-header" style={{ paddingBottom: 12 }}>
        <div className="page-header-row">
          <div>
            <h2>灵感搜索</h2>
            <p>自然语言跨作品检索 · 找到你想参考的作品段落、角色、设定</p>
          </div>
          <div className="flex gap-8">
            <button className="btn" onClick={refreshAll}>刷新</button>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <div className="flex" style={{ marginBottom: 12, gap: 4, borderBottom: "1px solid var(--border)" }}>
        {([
          { key: "search"  as const, label: "灵感搜索" },
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
      </div>

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
              <div className="text-xs text-muted" style={{ marginTop: 8, lineHeight: 1.55 }}>
                提示：作品需先建立索引才能被搜索到。请到「索引管理」 tab 为感兴趣的作品建立索引。
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

      {activeTab === "index" && (
        <>
          <div className="card" style={{ marginBottom: 14 }}>
            <div className="card-body">
              <div className="flex items-center gap-12" style={{ flexWrap: "wrap" }}>
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
                <div style={{ flex: 1 }} />
                <label className="flex items-center gap-4" style={{ fontSize: 12, color: "var(--text-secondary)", cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    checked={includeL3InSearch}
                    onChange={e => setIncludeL3InSearch(e.target.checked)}
                    style={{ width: 14, height: 14 }}
                  />
                  「全部建索引」时包含 L3 正文
                </label>
                <button className="btn-primary" onClick={buildAllIndexes} disabled={Object.values(indexing).some(Boolean)}>
                  为全部作品建索引
                </button>
              </div>
              <div className="text-xs text-muted" style={{ marginTop: 8, lineHeight: 1.55 }}>
                L1（大纲/角色/设定）+ L2（章节摘要）= 默认。L3（正文片段，~10 万字单作品需 1-3 分钟）按需勾选。已索引作品会被自动跳过的部分。
              </div>
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
                  onBuild={(l3) => buildIndex(w.ref_id, l3)}
                  onClear={() => clearIndex(w.ref_id)}
                  onOpen={() => openDetail(w.ref_id)}
                  topBorder={idx > 0}
                />
              ))}
            </div>
          </div>
        </>
      )}

      {activeTab === "compare" && <CompareWorksPanel />}
      {activeTab === "learn" && <CommonPatternLearningPanel works={works} />}
    </div>
  );
}

function IndexRow({ w, rows, indexing, onBuild, onClear, onOpen, topBorder }: {
  w: ReferenceWork;
  rows: IndexProgressRow[];
  indexing: boolean;
  onBuild: (includeL3: boolean) => void;
  onClear: () => void;
  onOpen: () => void;
  topBorder: boolean;
}) {
  const byLevel: Record<string, IndexProgressRow> = {};
  for (const r of rows) byLevel[r.level] = r;
  return (
    <div style={{
      display: "flex", alignItems: "center", gap: 12,
      padding: "12px 16px",
      borderTop: topBorder ? "1px solid var(--border)" : "none",
    }}>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="flex items-center gap-8" style={{ marginBottom: 3 }}>
          <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>{w.title}</span>
          {w.creator && <span className="text-xs text-muted">· {w.creator}</span>}
        </div>
        <div className="flex gap-6" style={{ flexWrap: "wrap" }}>
          {(["L1", "L2", "L3"] as const).map(lv => {
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
        <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
                onClick={() => onBuild(false)} disabled={indexing}
                title="为本作品建立 L1 + L2 索引（大纲/角色/设定 + 章节摘要）">
          {indexing ? "索引中..." : "建 L1+L2"}
        </button>
        <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
                onClick={() => onBuild(true)} disabled={indexing}
                title="为本作品建立 L1 + L2 + L3 索引（含正文 1500-字片段）">
          建 +L3
        </button>
        <button className="btn" style={{ fontSize: 11, padding: "3px 10px", color: "var(--text-tertiary)" }}
                onClick={onClear} disabled={indexing}>
          清除
        </button>
        <button className="btn-primary" style={{ fontSize: 11, padding: "3px 10px" }} onClick={onOpen}>
          打开
        </button>
      </div>
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
