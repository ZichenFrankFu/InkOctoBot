import React, { useEffect, useState, useCallback } from "react";
import { apiGet } from "../api/client";
import { useToast } from "../components/shared/Toast";

/* ── trend analysis types ── */
interface TrendResult {
  start_date: string;
  end_date: string;
  platform: string;
  lookback: string;
  top_k: number;
  empty: boolean;
  error?: string;
  message?: string;
  tag_rollup: TagRollup[];
  cat_rollup: CatRollup[];
  opportunities: Opportunity[];
  new_entry: NewEntry[];
  pairs: TagPair[];
  triples: TagTriple[];
  cross_platform: CrossPlatform[];
}

interface TagRollup {
  platform: string;
  rank_family?: string;
  rank_sub_cat?: string;
  tag: string;
  heat_slope: number;
  share_slope: number;
  latest_count: number;
  latest_share: number;
  avg_heat?: number;
  avg_rank?: number;
  stage?: string;
}

interface CatRollup {
  platform: string;
  category: string;
  count_slope: number;
  share_slope?: number;
  latest_count: number;
  avg_heat?: number;
  avg_rank?: number;
  heat_slope?: number;
}

interface Opportunity {
  platform: string;
  category: string;
  tag: string;
  share_delta: number;
  heat_delta: number;
  new_entry_ratio: number;
  opportunity_score: number;
}

interface NewEntry {
  platform: string;
  category: string;
  new_ratio: number;
  total: number;
}

interface TagPair { tag_a: string; tag_b: string; count: number; }
interface TagTriple { tag_a: string; tag_b: string; tag_c: string; count: number; }
interface CrossPlatform {
  category: string;
  presence: string;
  share_qidian?: number;
  share_fanqie?: number;
  share_diff?: number;
  heat_diff?: number;
  rank_diff?: number;
}

type SortDir = "asc" | "desc";
interface SortConfig { key: string; dir: SortDir; }

type MainTab = "trends";
type SubTab = "tags" | "categories" | "opportunities" | "cooccurrence" | "cross";

const platformLabel = (p: string) =>
  p === "qidian" ? "起点" : p === "fanqie" ? "番茄" : p === "both" ? "全部" : p || "未知";

const fmt = (v: number | null | undefined, d: number) =>
  v == null || isNaN(v) ? "—" : v.toFixed(d);

function SlopeCell({ value }: { value: number | null | undefined }) {
  if (value == null || isNaN(value)) return <span className="text-muted font-mono">—</span>;
  const color = value > 0.001 ? "var(--jade)" : value < -0.001 ? "var(--accent)" : "var(--text-tertiary)";
  const arrow = value > 0.001 ? "↑" : value < -0.001 ? "↓" : "→";
  return <span className="font-mono" style={{ color, fontSize: 12 }}>{arrow} {Math.abs(value).toFixed(4)}</span>;
}

function DeltaCell({ value, pct }: { value: number | null | undefined; pct?: boolean }) {
  if (value == null || isNaN(value)) return <span className="text-muted font-mono">—</span>;
  const color = value > 0 ? "var(--jade)" : value < 0 ? "var(--accent)" : "var(--text-tertiary)";
  const sign = value > 0 ? "+" : "";
  return <span className="font-mono" style={{ color, fontSize: 12, fontWeight: 600 }}>{pct ? `${sign}${(value * 100).toFixed(1)}%` : `${sign}${value.toFixed(0)}`}</span>;
}

function StageBadge({ stage }: { stage?: string }) {
  const map: Record<string, { bg: string; color: string; label: string }> = {
    safe: { bg: "var(--jade-subtle)", color: "var(--jade)", label: "稳定" },
    chance: { bg: "var(--gold-subtle)", color: "var(--gold)", label: "机会" },
    rising: { bg: "var(--indigo-subtle)", color: "var(--indigo)", label: "上升" },
    declining: { bg: "var(--accent-subtle)", color: "var(--accent)", label: "下降" },
    stable: { bg: "var(--bg-surface-2)", color: "var(--text-secondary)", label: "平稳" },
  };
  const s = map[stage || ""] || { bg: "var(--bg-surface-2)", color: "var(--text-disabled)", label: "—" };
  return <span className="tag" style={{ background: s.bg, color: s.color }}>{s.label}</span>;
}

function SortableHeader({ label, field, sort, onSort, align }: {
  label: string; field: string; sort: SortConfig; onSort: (field: string) => void; align?: "left" | "right";
}) {
  const isActive = sort.key === field;
  return (
    <th style={{ textAlign: align || "left", cursor: "pointer", userSelect: "none" }} onClick={() => onSort(field)}>
      {label} {isActive ? <span style={{ color: "var(--accent)" }}>{sort.dir === "asc" ? "↑" : "↓"}</span> : <span style={{ color: "var(--text-disabled)" }}>⇅</span>}
    </th>
  );
}

function sortBy<T>(arr: T[], key: string, dir: SortDir): T[] {
  return [...arr].sort((a, b) => {
    const va = (a as any)[key] ?? 0;
    const vb = (b as any)[key] ?? 0;
    if (typeof va === "string") return dir === "asc" ? va.localeCompare(vb) : vb.localeCompare(va);
    return dir === "asc" ? va - vb : vb - va;
  });
}

export default function AnalysisDashboardPage() {
  const { toast } = useToast();
  const [mainTab, setMainTab] = useState<MainTab>("trends");

  /* ── Trend analysis state ── */
  const [trendPlatform, setTrendPlatform] = useState("both");
  const [lookback, setLookback] = useState("all");
  const [topK, setTopK] = useState(20);
  const [trendData, setTrendData] = useState<TrendResult | null>(null);
  const [loadingTrend, setLoadingTrend] = useState(false);
  const [trendError, setTrendError] = useState("");
  const [subTab, setSubTab] = useState<SubTab>("tags");
  const [autoRan, setAutoRan] = useState(false);

  /* sort states */
  const [tagSort, setTagSort] = useState<SortConfig>({ key: "avg_heat", dir: "desc" });
  const [catSort, setCatSort] = useState<SortConfig>({ key: "avg_heat", dir: "desc" });
  const [oppSort, setOppSort] = useState<SortConfig>({ key: "opportunity_score", dir: "desc" });
  const [pairSort, setPairSort] = useState<SortConfig>({ key: "count", dir: "desc" });
  const [crossSort, setCrossSort] = useState<SortConfig>({ key: "share_diff", dir: "desc" });

  const toggleSort = (setter: React.Dispatch<React.SetStateAction<SortConfig>>) => (field: string) => {
    setter(prev => prev.key === field ? { key: field, dir: prev.dir === "asc" ? "desc" : "asc" } : { key: field, dir: "desc" });
  };

  /* ── Auto-run trend analysis on mount ── */
  useEffect(() => {
    if (!autoRan && !trendData && !loadingTrend) {
      setAutoRan(true);
      runTrendAnalysis();
    }
  }, []);

  /* ── Run trend analysis (GET /api/analysis/run) ── */
  const runTrendAnalysis = useCallback(() => {
    setLoadingTrend(true);
    setTrendError("");
    const params = new URLSearchParams({
      platform: trendPlatform,
      lookback,
      top_k: String(topK),
    });
    apiGet<TrendResult>(`/api/analysis/run?${params}`)
      .then(res => {
        if (res.error) {
          setTrendError(res.message || res.error);
          setTrendData(null);
        } else {
          setTrendData(res);
        }
      })
      .catch(e => {
        const msg = e?.message || String(e);
        setTrendError(msg);
        setTrendData(null);
        toast(msg || "分析失败", "error");
      })
      .finally(() => setLoadingTrend(false));
  }, [trendPlatform, lookback, topK, toast]);

  const trendSubTabs: { key: SubTab; label: string; show?: boolean }[] = [
    { key: "tags", label: "标签趋势" },
    { key: "categories", label: "类目趋势" },
    { key: "opportunities", label: "机会分析" },
    { key: "cooccurrence", label: "标签共现" },
    { key: "cross", label: "跨平台对比", show: trendPlatform === "both" },
  ];

  return (
    <div className="page-container">
      {/* Header */}
      <div className="page-header" style={{ marginBottom: 20 }}>
        <div className="page-header-row">
          <div>
            <h2>分析面板</h2>
            <p>市场规模、标签热度、趋势分析与开书机会</p>
          </div>
        </div>
      </div>

      {/* ═══════ TRENDS TAB ═══════ */}
      {mainTab === "trends" && (
        <>
          {/* Control Panel */}
          <div className="card" style={{ marginBottom: 24 }}>
            <div className="card-body">
              <div style={{ display: "flex", gap: 20, alignItems: "flex-end", flexWrap: "wrap" }}>
                <div className="field">
                  <label className="label">平台</label>
                  <select className="select" value={trendPlatform} onChange={e => setTrendPlatform(e.target.value)}>
                    <option value="both">全部平台</option>
                    <option value="qidian">起点</option>
                    <option value="fanqie">番茄</option>
                  </select>
                </div>
                <div className="field">
                  <label className="label">时间范围</label>
                  <select className="select" value={lookback} onChange={e => setLookback(e.target.value)}>
                    <option value="week">最近 7 天</option>
                    <option value="month">最近 30 天</option>
                    <option value="quarter">最近 90 天</option>
                    <option value="year">最近 365 天</option>
                    <option value="all">全部数据</option>
                  </select>
                </div>
                <div className="field">
                  <label className="label">Top K</label>
                  <select className="select" value={topK} onChange={e => setTopK(Number(e.target.value))}>
                    {[10, 15, 20, 30, 50].map(k => <option key={k} value={k}>{k}</option>)}
                  </select>
                </div>
                <button className="btn-primary" onClick={runTrendAnalysis} disabled={loadingTrend}>
                  {loadingTrend ? "分析中..." : "重新分析"}
                </button>
              </div>
            </div>
          </div>

          {/* Error */}
          {trendError && (
            <div className="card" style={{ marginBottom: 20, borderLeft: "3px solid var(--accent)" }}>
              <div className="card-body">
                <div style={{ fontSize: 14, fontWeight: 600, color: "var(--accent)", marginBottom: 6 }}>分析出错</div>
                <div style={{ fontSize: 13, color: "var(--text-secondary)", whiteSpace: "pre-wrap", lineHeight: 1.6 }}>{trendError}</div>
                <div style={{ marginTop: 10, fontSize: 12, color: "var(--text-tertiary)" }}>提示：请确认数据库中有足够的快照数据。</div>
              </div>
            </div>
          )}

          {/* Loading */}
          {loadingTrend && <div className="loading"><div className="loading-spinner" />正在运行趋势分析...</div>}

          {/* Results */}
          {!loadingTrend && trendData && !trendData.empty && (
            <>
              <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginBottom: 16, display: "flex", gap: 16 }}>
                <span>{trendData.start_date} ~ {trendData.end_date}</span>
                <span>{platformLabel(trendData.platform)}</span>
                <span>{trendData.lookback || "全部"}</span>
              </div>

              {/* Sub tab bar */}
              <div className="tab-bar" style={{ marginBottom: 24 }}>
                {trendSubTabs.filter(t => t.show !== false).map(t => (
                  <button key={t.key} className={`tab-item${subTab === t.key ? " active" : ""}`} onClick={() => setSubTab(t.key)}>{t.label}</button>
                ))}
              </div>

              {/* Tags subtab */}
              {subTab === "tags" && (
                <div className="card">
                  <div className="card-header"><h3>标签趋势汇总</h3><span className="text-xs text-muted">{(trendData.tag_rollup || []).length} 条</span></div>
                  <div style={{ maxHeight: 560, overflowY: "auto" }}>
                    {(trendData.tag_rollup || []).length === 0 ? <div className="empty-state"><p>暂无标签趋势数据</p></div> : (
                      <table className="data-table">
                        <thead><tr>
                          <th>平台</th>
                          <SortableHeader label="标签" field="tag" sort={tagSort} onSort={toggleSort(setTagSort)} />
                          <th>阶段</th>
                          <SortableHeader label="份额" field="latest_share" sort={tagSort} onSort={toggleSort(setTagSort)} align="right" />
                          <SortableHeader label="热度" field="avg_heat" sort={tagSort} onSort={toggleSort(setTagSort)} align="right" />
                          <SortableHeader label="热度趋势" field="heat_slope" sort={tagSort} onSort={toggleSort(setTagSort)} align="right" />
                          <SortableHeader label="份额趋势" field="share_slope" sort={tagSort} onSort={toggleSort(setTagSort)} align="right" />
                        </tr></thead>
                        <tbody>
                          {sortBy(trendData.tag_rollup, tagSort.key, tagSort.dir).map((r, i) => (
                            <tr key={i}>
                              <td><span className={`tag ${r.platform}`}>{platformLabel(r.platform)}</span></td>
                              <td><span className="tag category">{r.tag}</span></td>
                              <td><StageBadge stage={r.stage} /></td>
                              <td style={{ textAlign: "right" }} className="font-mono">{fmt(r.latest_share, 3)}</td>
                              <td style={{ textAlign: "right" }} className="font-mono">{fmt(r.avg_heat, 0)}</td>
                              <td style={{ textAlign: "right" }}><SlopeCell value={r.heat_slope} /></td>
                              <td style={{ textAlign: "right" }}><SlopeCell value={r.share_slope} /></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                </div>
              )}

              {/* Categories subtab */}
              {subTab === "categories" && (
                <div className="card">
                  <div className="card-header"><h3>类目趋势汇总</h3></div>
                  <div style={{ maxHeight: 560, overflowY: "auto" }}>
                    {(trendData.cat_rollup || []).length === 0 ? <div className="empty-state"><p>暂无类目趋势数据</p></div> : (
                      <table className="data-table">
                        <thead><tr>
                          <th>平台</th>
                          <SortableHeader label="分类" field="category" sort={catSort} onSort={toggleSort(setCatSort)} />
                          <SortableHeader label="份额" field="share_slope" sort={catSort} onSort={toggleSort(setCatSort)} align="right" />
                          <SortableHeader label="热度" field="avg_heat" sort={catSort} onSort={toggleSort(setCatSort)} align="right" />
                          <SortableHeader label="热度趋势" field="heat_slope" sort={catSort} onSort={toggleSort(setCatSort)} align="right" />
                          <SortableHeader label="数量趋势" field="count_slope" sort={catSort} onSort={toggleSort(setCatSort)} align="right" />
                        </tr></thead>
                        <tbody>
                          {sortBy(trendData.cat_rollup, catSort.key, catSort.dir).map((r, i) => (
                            <tr key={i}>
                              <td><span className={`tag ${r.platform}`}>{platformLabel(r.platform)}</span></td>
                              <td><span className="tag category">{r.category}</span></td>
                              <td style={{ textAlign: "right" }}><SlopeCell value={r.share_slope} /></td>
                              <td style={{ textAlign: "right" }} className="font-mono">{fmt(r.avg_heat, 0)}</td>
                              <td style={{ textAlign: "right" }}><SlopeCell value={r.heat_slope} /></td>
                              <td style={{ textAlign: "right" }}><SlopeCell value={r.count_slope} /></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                </div>
              )}

              {/* Opportunities subtab */}
              {subTab === "opportunities" && (
                <div className="card">
                  <div className="card-header"><h3>开书机会榜</h3><p style={{ fontSize: 12, color: "var(--text-tertiary)" }}>综合份额增长 + 热度增长 + 新书占比</p></div>
                  <div style={{ maxHeight: 560, overflowY: "auto" }}>
                    {(trendData.opportunities || []).length === 0 ? <div className="empty-state"><p>数据不足，无法计算机会分</p></div> : (
                      <table className="data-table">
                        <thead><tr>
                          <th>平台</th><th>分类</th>
                          <SortableHeader label="标签" field="tag" sort={oppSort} onSort={toggleSort(setOppSort)} />
                          <SortableHeader label="份额变化" field="share_delta" sort={oppSort} onSort={toggleSort(setOppSort)} align="right" />
                          <SortableHeader label="热度变化" field="heat_delta" sort={oppSort} onSort={toggleSort(setOppSort)} align="right" />
                          <SortableHeader label="新书比" field="new_entry_ratio" sort={oppSort} onSort={toggleSort(setOppSort)} align="right" />
                          <SortableHeader label="机会分" field="opportunity_score" sort={oppSort} onSort={toggleSort(setOppSort)} align="right" />
                        </tr></thead>
                        <tbody>
                          {sortBy(trendData.opportunities, oppSort.key, oppSort.dir).map((r, i) => (
                            <tr key={i}>
                              <td><span className={`tag ${r.platform}`}>{platformLabel(r.platform)}</span></td>
                              <td className="text-muted">{r.category}</td>
                              <td><span className="tag category">{r.tag}</span></td>
                              <td style={{ textAlign: "right" }}><DeltaCell value={r.share_delta} pct /></td>
                              <td style={{ textAlign: "right" }}><DeltaCell value={r.heat_delta} /></td>
                              <td style={{ textAlign: "right" }} className="font-mono">{fmt(r.new_entry_ratio, 2)}</td>
                              <td style={{ textAlign: "right", fontWeight: 700, fontFamily: "var(--font-mono)", color: "var(--text-primary)" }}>{fmt(r.opportunity_score, 2)}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                </div>
              )}

              {/* Cooccurrence subtab */}
              {subTab === "cooccurrence" && (
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
                  <div className="card">
                    <div className="card-header"><h3>标签共现（二元）</h3><span className="text-xs text-muted">{(trendData.pairs || []).length} 对</span></div>
                    <div style={{ maxHeight: 520, overflowY: "auto" }}>
                      {(trendData.pairs || []).length === 0 ? <div className="empty-state"><p>暂无共现数据</p></div> : (
                        <table className="data-table">
                          <thead><tr><th>标签 A</th><th>标签 B</th><SortableHeader label="共现数" field="count" sort={pairSort} onSort={toggleSort(setPairSort)} align="right" /></tr></thead>
                          <tbody>
                            {sortBy(trendData.pairs, pairSort.key, pairSort.dir).map((r, i) => (
                              <tr key={i}>
                                <td><span className="tag category">{r.tag_a}</span></td>
                                <td><span className="tag category">{r.tag_b}</span></td>
                                <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 600 }}>{r.count}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                    </div>
                  </div>
                  <div className="card">
                    <div className="card-header"><h3>标签共现（三元）</h3><span className="text-xs text-muted">{(trendData.triples || []).length} 组</span></div>
                    <div style={{ maxHeight: 520, overflowY: "auto" }}>
                      {(trendData.triples || []).length === 0 ? <div className="empty-state"><p>暂无共现数据</p></div> : (
                        <table className="data-table">
                          <thead><tr><th>A</th><th>B</th><th>C</th><th style={{ textAlign: "right" }}>共现数</th></tr></thead>
                          <tbody>
                            {sortBy(trendData.triples, "count", "desc").map((r, i) => (
                              <tr key={i}><td><span className="tag category">{r.tag_a}</span></td><td><span className="tag category">{r.tag_b}</span></td><td><span className="tag category">{r.tag_c}</span></td><td style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 600 }}>{r.count}</td></tr>
                            ))}
                          </tbody>
                        </table>
                      )}
                    </div>
                  </div>
                </div>
              )}

              {/* Cross-platform subtab */}
              {subTab === "cross" && (
                <div className="card">
                  <div className="card-header"><h3>跨平台分类对比</h3><p style={{ fontSize: 12, color: "var(--text-tertiary)" }}>仅展示两平台共有的分类</p></div>
                  <div style={{ maxHeight: 560, overflowY: "auto" }}>
                    {(trendData.cross_platform || []).filter(d => d.presence === "both").length === 0 ? <div className="empty-state"><p>暂无可对比数据</p></div> : (
                      <table className="data-table">
                        <thead><tr>
                          <SortableHeader label="分类" field="category" sort={crossSort} onSort={toggleSort(setCrossSort)} />
                          <SortableHeader label="起点份额" field="share_qidian" sort={crossSort} onSort={toggleSort(setCrossSort)} align="right" />
                          <SortableHeader label="番茄份额" field="share_fanqie" sort={crossSort} onSort={toggleSort(setCrossSort)} align="right" />
                          <SortableHeader label="份额差" field="share_diff" sort={crossSort} onSort={toggleSort(setCrossSort)} align="right" />
                          <SortableHeader label="热度差" field="heat_diff" sort={crossSort} onSort={toggleSort(setCrossSort)} align="right" />
                        </tr></thead>
                        <tbody>
                          {sortBy((trendData.cross_platform || []).filter(d => d.presence === "both"), crossSort.key, crossSort.dir).map((r, i) => (
                            <tr key={i}>
                              <td><span className="tag category">{r.category}</span></td>
                              <td style={{ textAlign: "right" }} className="font-mono">{fmt(r.share_qidian, 3)}</td>
                              <td style={{ textAlign: "right" }} className="font-mono">{fmt(r.share_fanqie, 3)}</td>
                              <td style={{ textAlign: "right" }}><DeltaCell value={r.share_diff} pct /></td>
                              <td style={{ textAlign: "right" }}><DeltaCell value={r.heat_diff} /></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    )}
                  </div>
                </div>
              )}
            </>
          )}

          {/* Empty result */}
          {!loadingTrend && trendData && trendData.empty && (
            <div className="empty-state"><div className="empty-icon">--</div><h4>暂无数据</h4><p>所选范围没有可分析数据，请调整参数后重试。</p></div>
          )}

          {/* Initial state */}
          {!loadingTrend && !trendData && !trendError && (
            <div className="empty-state"><div className="empty-icon">...</div><h4>准备就绪</h4><p>正在自动运行默认分析...</p></div>
          )}
        </>
      )}
    </div>
  );
}
