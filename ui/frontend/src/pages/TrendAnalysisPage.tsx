import React, { useState, useCallback } from "react";
import { apiPost } from "../api/client";

/* ── subtab type ── */
type SubTab = "tags" | "categories" | "opportunities" | "cooccurrence" | "cross";

/* ── response shape from /api/analysis/trends ── */
interface TrendResult {
  start_date: string;
  end_date: string;
  platform: string;
  window_days: number;
  top_k: number;
  empty: boolean;
  error?: string;
  message?: string;
  tag_trends: TagTrend[];
  category_trends: CategoryTrend[];
  opportunities: Opportunity[];
  tag_pairs: TagPair[];
  tag_triples: TagTriple[];
  cross_platform: CrossPlatform[];
}

interface TagTrend {
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

interface CategoryTrend {
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

interface TagPair {
  tag_a: string;
  tag_b: string;
  count: number;
}

interface TagTriple {
  tag_a: string;
  tag_b: string;
  tag_c: string;
  count: number;
}

interface CrossPlatform {
  category: string;
  presence: string;
  share_qidian?: number;
  share_fanqie?: number;
  share_diff?: number;
  heat_diff?: number;
  rank_diff?: number;
}

/* ── sort state ── */
type SortDir = "asc" | "desc";
interface SortConfig {
  key: string;
  dir: SortDir;
}

const platformLabel = (p: string) =>
  p === "qidian" ? "起点" : p === "fanqie" ? "番茄" : p === "both" ? "全部" : p || "未知";

const fmt = (v: number | null | undefined, d: number) =>
  v == null || isNaN(v) ? "—" : v.toFixed(d);

/* ── slope display ── */
function SlopeCell({ value }: { value: number | null | undefined }) {
  if (value == null || isNaN(value)) {
    return <span className="text-muted font-mono">—</span>;
  }
  const color =
    value > 0.001 ? "var(--jade)" : value < -0.001 ? "var(--accent)" : "var(--text-tertiary)";
  const arrow = value > 0.001 ? "↑" : value < -0.001 ? "↓" : "→";
  return (
    <span className="font-mono" style={{ color, fontSize: 12 }}>
      {arrow} {Math.abs(value).toFixed(4)}
    </span>
  );
}

/* ── delta display ── */
function DeltaCell({ value, pct }: { value: number | null | undefined; pct?: boolean }) {
  if (value == null || isNaN(value)) {
    return <span className="text-muted font-mono">—</span>;
  }
  const color = value > 0 ? "var(--jade)" : value < 0 ? "var(--accent)" : "var(--text-tertiary)";
  const sign = value > 0 ? "+" : "";
  return (
    <span className="font-mono" style={{ color, fontSize: 12, fontWeight: 600 }}>
      {pct ? `${sign}${(value * 100).toFixed(1)}%` : `${sign}${value.toFixed(0)}`}
    </span>
  );
}

/* ── stage badge ── */
function StageBadge({ stage }: { stage?: string }) {
  const map: Record<string, { bg: string; color: string; label: string }> = {
    safe: { bg: "var(--jade-subtle)", color: "var(--jade)", label: "稳定" },
    chance: { bg: "var(--gold-subtle)", color: "var(--gold)", label: "机会" },
    rising: { bg: "var(--indigo-subtle)", color: "var(--indigo)", label: "上升" },
    declining: { bg: "var(--accent-subtle)", color: "var(--accent)", label: "下降" },
    stable: { bg: "var(--bg-surface-2)", color: "var(--text-secondary)", label: "平稳" },
  };
  const s = map[stage || ""] || { bg: "var(--bg-surface-2)", color: "var(--text-disabled)", label: "—" };
  return (
    <span className="tag" style={{ background: s.bg, color: s.color }}>
      {s.label}
    </span>
  );
}

/* ── sortable header ── */
function SortableHeader({
  label,
  field,
  sort,
  onSort,
  align,
}: {
  label: string;
  field: string;
  sort: SortConfig;
  onSort: (field: string) => void;
  align?: "left" | "right";
}) {
  const isActive = sort.key === field;
  return (
    <th
      style={{ textAlign: align || "left", cursor: "pointer", userSelect: "none" }}
      onClick={() => onSort(field)}
    >
      {label}{" "}
      {isActive ? (
        <span style={{ color: "var(--accent)" }}>{sort.dir === "asc" ? "↑" : "↓"}</span>
      ) : (
        <span style={{ color: "var(--text-disabled)" }}>⇅</span>
      )}
    </th>
  );
}

/* ── generic sort helper ── */
function sortBy<T>(arr: T[], key: string, dir: SortDir): T[] {
  return [...arr].sort((a, b) => {
    const va = (a as any)[key] ?? 0;
    const vb = (b as any)[key] ?? 0;
    if (typeof va === "string") return dir === "asc" ? va.localeCompare(vb) : vb.localeCompare(va);
    return dir === "asc" ? va - vb : vb - va;
  });
}

export default function TrendAnalysisPage() {
  /* controls */
  const [platform, setPlatform] = useState("both");
  const [windowDays, setWindowDays] = useState(30);
  const [topK, setTopK] = useState(20);

  /* data */
  const [data, setData] = useState<TrendResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  /* tab */
  const [subTab, setSubTab] = useState<SubTab>("tags");

  /* sort states per tab */
  const [tagSort, setTagSort] = useState<SortConfig>({ key: "avg_heat", dir: "desc" });
  const [catSort, setCatSort] = useState<SortConfig>({ key: "avg_heat", dir: "desc" });
  const [oppSort, setOppSort] = useState<SortConfig>({ key: "opportunity_score", dir: "desc" });
  const [pairSort, setPairSort] = useState<SortConfig>({ key: "count", dir: "desc" });
  const [crossSort, setCrossSort] = useState<SortConfig>({ key: "share_diff", dir: "desc" });

  const toggleSort = (setter: React.Dispatch<React.SetStateAction<SortConfig>>) => (field: string) => {
    setter((prev) =>
      prev.key === field ? { key: field, dir: prev.dir === "asc" ? "desc" : "asc" } : { key: field, dir: "desc" },
    );
  };

  /* ── run analysis ── */
  const runAnalysis = useCallback(() => {
    setLoading(true);
    setError("");
    apiPost<TrendResult>("/api/analysis/trends", {
      platform,
      window_days: windowDays,
      top_k: topK,
    })
      .then((res) => {
        if (res.error) {
          setError(res.message || res.error);
          setData(null);
        } else {
          setData(res);
        }
      })
      .catch((e) => {
        setError(String(e));
        setData(null);
      })
      .finally(() => setLoading(false));
  }, [platform, windowDays, topK]);

  /* subtabs config */
  const tabs: { key: SubTab; label: string; show?: boolean }[] = [
    { key: "tags", label: "标签趋势" },
    { key: "categories", label: "类目趋势" },
    { key: "opportunities", label: "机会分析" },
    { key: "cooccurrence", label: "标签共现" },
    { key: "cross", label: "跨平台对比", show: platform === "both" },
  ];

  return (
    <div className="page-container">
      {/* ══ Header ══ */}
      <div className="page-header" style={{ marginBottom: 20 }}>
        <h2>趋势分析</h2>
        <p>题材趋势、热度变化与开书机会挖掘</p>
      </div>

      {/* ══ Control Panel ══ */}
      <div className="card" style={{ marginBottom: 24 }}>
        <div className="card-body">
          <div style={{ display: "flex", gap: 20, alignItems: "flex-end", flexWrap: "wrap" }}>
            <div className="field">
              <label className="label">平台</label>
              <select
                className="select"
                value={platform}
                onChange={(e) => setPlatform(e.target.value)}
              >
                <option value="both">全部平台</option>
                <option value="qidian">起点</option>
                <option value="fanqie">番茄</option>
              </select>
            </div>

            <div className="field">
              <label className="label">时间窗口</label>
              <select
                className="select"
                value={windowDays}
                onChange={(e) => setWindowDays(Number(e.target.value))}
              >
                {[7, 14, 30, 60, 90].map((d) => (
                  <option key={d} value={d}>
                    {d} 天
                  </option>
                ))}
              </select>
            </div>

            <div className="field">
              <label className="label">Top K</label>
              <select
                className="select"
                value={topK}
                onChange={(e) => setTopK(Number(e.target.value))}
              >
                {[10, 15, 20, 30, 50].map((k) => (
                  <option key={k} value={k}>
                    {k}
                  </option>
                ))}
              </select>
            </div>

            <button className="btn-primary" onClick={runAnalysis} disabled={loading}>
              {loading ? "分析中..." : "运行分析"}
            </button>
          </div>
        </div>
      </div>

      {/* ══ Error ══ */}
      {error && (
        <div
          className="card"
          style={{ marginBottom: 20, borderLeft: "3px solid var(--accent)" }}
        >
          <div className="card-body">
            <div style={{ fontSize: 14, fontWeight: 600, color: "var(--accent)", marginBottom: 6 }}>
              分析出错
            </div>
            <div
              style={{
                fontSize: 13,
                color: "var(--text-secondary)",
                whiteSpace: "pre-wrap",
                lineHeight: 1.6,
              }}
            >
              {error}
            </div>
            <div style={{ marginTop: 10, fontSize: 12, color: "var(--text-tertiary)" }}>
              提示：请确认数据库中有足够的快照数据。
            </div>
          </div>
        </div>
      )}

      {/* ══ Loading ══ */}
      {loading && (
        <div className="loading">
          <div className="loading-spinner" />
          正在运行趋势分析...
        </div>
      )}

      {/* ══ Results ══ */}
      {!loading && data && !data.empty && (
        <>
          {/* date range indicator */}
          <div
            style={{
              fontSize: 12,
              color: "var(--text-tertiary)",
              marginBottom: 16,
              display: "flex",
              gap: 16,
            }}
          >
            <span>
              {data.start_date} ~ {data.end_date}
            </span>
            <span>{platformLabel(data.platform)}</span>
            <span>{data.window_days} 天窗口</span>
          </div>

          {/* Tab bar */}
          <div className="tab-bar" style={{ marginBottom: 24 }}>
            {tabs
              .filter((t) => t.show !== false)
              .map((t) => (
                <button
                  key={t.key}
                  className={`tab-item${subTab === t.key ? " active" : ""}`}
                  onClick={() => setSubTab(t.key)}
                >
                  {t.label}
                </button>
              ))}
          </div>

          {/* ── Tags subtab ── */}
          {subTab === "tags" && (
            <div className="card">
              <div className="card-header">
                <h3>标签趋势汇总</h3>
                <span className="text-xs text-muted">
                  {(data.tag_trends || []).length} 条记录
                </span>
              </div>
              <div style={{ maxHeight: 560, overflowY: "auto" }}>
                {(data.tag_trends || []).length === 0 ? (
                  <div className="empty-state">
                    <p>暂无标签趋势数据</p>
                  </div>
                ) : (
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>平台</th>
                        <th>榜单</th>
                        <SortableHeader label="标签" field="tag" sort={tagSort} onSort={toggleSort(setTagSort)} />
                        <th>阶段</th>
                        <SortableHeader label="份额" field="latest_share" sort={tagSort} onSort={toggleSort(setTagSort)} align="right" />
                        <SortableHeader label="热度" field="avg_heat" sort={tagSort} onSort={toggleSort(setTagSort)} align="right" />
                        <SortableHeader label="均排名" field="avg_rank" sort={tagSort} onSort={toggleSort(setTagSort)} align="right" />
                        <SortableHeader label="热度趋势" field="heat_slope" sort={tagSort} onSort={toggleSort(setTagSort)} align="right" />
                        <SortableHeader label="份额趋势" field="share_slope" sort={tagSort} onSort={toggleSort(setTagSort)} align="right" />
                      </tr>
                    </thead>
                    <tbody>
                      {sortBy(data.tag_trends, tagSort.key, tagSort.dir).map((r, i) => (
                        <tr key={i}>
                          <td>
                            <span className={`tag ${r.platform}`}>{platformLabel(r.platform)}</span>
                          </td>
                          <td className="text-xs text-muted">
                            {r.rank_family || ""}
                            {r.rank_sub_cat ? ` · ${r.rank_sub_cat}` : ""}
                          </td>
                          <td style={{ fontWeight: 500 }}>{r.tag}</td>
                          <td>
                            <StageBadge stage={r.stage} />
                          </td>
                          <td style={{ textAlign: "right" }} className="font-mono">
                            {fmt(r.latest_share, 3)}
                          </td>
                          <td style={{ textAlign: "right" }} className="font-mono">
                            {fmt(r.avg_heat, 0)}
                          </td>
                          <td style={{ textAlign: "right" }} className="font-mono">
                            {fmt(r.avg_rank, 1)}
                          </td>
                          <td style={{ textAlign: "right" }}>
                            <SlopeCell value={r.heat_slope} />
                          </td>
                          <td style={{ textAlign: "right" }}>
                            <SlopeCell value={r.share_slope} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          )}

          {/* ── Categories subtab ── */}
          {subTab === "categories" && (
            <div className="card">
              <div className="card-header">
                <h3>类目趋势汇总</h3>
              </div>
              <div style={{ maxHeight: 560, overflowY: "auto" }}>
                {(data.category_trends || []).length === 0 ? (
                  <div className="empty-state">
                    <p>暂无类目趋势数据</p>
                  </div>
                ) : (
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>平台</th>
                        <SortableHeader label="分类" field="category" sort={catSort} onSort={toggleSort(setCatSort)} />
                        <SortableHeader label="份额" field="share_slope" sort={catSort} onSort={toggleSort(setCatSort)} align="right" />
                        <SortableHeader label="热度" field="avg_heat" sort={catSort} onSort={toggleSort(setCatSort)} align="right" />
                        <SortableHeader label="均排名" field="avg_rank" sort={catSort} onSort={toggleSort(setCatSort)} align="right" />
                        <SortableHeader label="热度趋势" field="heat_slope" sort={catSort} onSort={toggleSort(setCatSort)} align="right" />
                        <SortableHeader label="数量趋势" field="count_slope" sort={catSort} onSort={toggleSort(setCatSort)} align="right" />
                      </tr>
                    </thead>
                    <tbody>
                      {sortBy(data.category_trends, catSort.key, catSort.dir).map((r, i) => (
                        <tr key={i}>
                          <td>
                            <span className={`tag ${r.platform}`}>{platformLabel(r.platform)}</span>
                          </td>
                          <td style={{ fontWeight: 500 }}>{r.category}</td>
                          <td style={{ textAlign: "right" }}>
                            <SlopeCell value={r.share_slope} />
                          </td>
                          <td style={{ textAlign: "right" }} className="font-mono">
                            {fmt(r.avg_heat, 0)}
                          </td>
                          <td style={{ textAlign: "right" }} className="font-mono">
                            {fmt(r.avg_rank, 1)}
                          </td>
                          <td style={{ textAlign: "right" }}>
                            <SlopeCell value={r.heat_slope} />
                          </td>
                          <td style={{ textAlign: "right" }}>
                            <SlopeCell value={r.count_slope} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          )}

          {/* ── Opportunities subtab ── */}
          {subTab === "opportunities" && (
            <div className="card">
              <div className="card-header">
                <h3>开书机会榜</h3>
                <p style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
                  综合份额增长 + 热度增长 + 新书占比
                </p>
              </div>
              <div style={{ maxHeight: 560, overflowY: "auto" }}>
                {(data.opportunities || []).length === 0 ? (
                  <div className="empty-state">
                    <p>数据不足，无法计算机会分</p>
                  </div>
                ) : (
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>平台</th>
                        <th>分类</th>
                        <SortableHeader label="标签" field="tag" sort={oppSort} onSort={toggleSort(setOppSort)} />
                        <SortableHeader label="份额变化" field="share_delta" sort={oppSort} onSort={toggleSort(setOppSort)} align="right" />
                        <SortableHeader label="热度变化" field="heat_delta" sort={oppSort} onSort={toggleSort(setOppSort)} align="right" />
                        <SortableHeader label="新书比" field="new_entry_ratio" sort={oppSort} onSort={toggleSort(setOppSort)} align="right" />
                        <SortableHeader label="机会分" field="opportunity_score" sort={oppSort} onSort={toggleSort(setOppSort)} align="right" />
                      </tr>
                    </thead>
                    <tbody>
                      {sortBy(data.opportunities, oppSort.key, oppSort.dir).map((r, i) => (
                        <tr key={i}>
                          <td>
                            <span className={`tag ${r.platform}`}>{platformLabel(r.platform)}</span>
                          </td>
                          <td className="text-muted">{r.category}</td>
                          <td style={{ fontWeight: 500 }}>{r.tag}</td>
                          <td style={{ textAlign: "right" }}>
                            <DeltaCell value={r.share_delta} pct />
                          </td>
                          <td style={{ textAlign: "right" }}>
                            <DeltaCell value={r.heat_delta} />
                          </td>
                          <td style={{ textAlign: "right" }} className="font-mono">
                            {fmt(r.new_entry_ratio, 2)}
                          </td>
                          <td
                            style={{
                              textAlign: "right",
                              fontWeight: 700,
                              fontFamily: "var(--font-mono)",
                              color: "var(--text-primary)",
                            }}
                          >
                            {fmt(r.opportunity_score, 2)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          )}

          {/* ── Cooccurrence subtab ── */}
          {subTab === "cooccurrence" && (
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
              {/* pairs */}
              <div className="card">
                <div className="card-header">
                  <h3>标签共现（二元）</h3>
                  <span className="text-xs text-muted">
                    {(data.tag_pairs || []).length} 对
                  </span>
                </div>
                <div style={{ maxHeight: 520, overflowY: "auto" }}>
                  {(data.tag_pairs || []).length === 0 ? (
                    <div className="empty-state">
                      <p>暂无共现数据</p>
                    </div>
                  ) : (
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>标签 A</th>
                          <th>标签 B</th>
                          <SortableHeader label="共现数" field="count" sort={pairSort} onSort={toggleSort(setPairSort)} align="right" />
                        </tr>
                      </thead>
                      <tbody>
                        {sortBy(data.tag_pairs, pairSort.key, pairSort.dir).map((r, i) => (
                          <tr key={i}>
                            <td>
                              <span className="tag category">{r.tag_a}</span>
                            </td>
                            <td>
                              <span className="tag category">{r.tag_b}</span>
                            </td>
                            <td
                              style={{
                                textAlign: "right",
                                fontFamily: "var(--font-mono)",
                                fontWeight: 600,
                              }}
                            >
                              {r.count}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              </div>

              {/* triples */}
              <div className="card">
                <div className="card-header">
                  <h3>标签共现（三元）</h3>
                  <span className="text-xs text-muted">
                    {(data.tag_triples || []).length} 组
                  </span>
                </div>
                <div style={{ maxHeight: 520, overflowY: "auto" }}>
                  {(data.tag_triples || []).length === 0 ? (
                    <div className="empty-state">
                      <p>暂无共现数据</p>
                    </div>
                  ) : (
                    <table className="data-table">
                      <thead>
                        <tr>
                          <th>A</th>
                          <th>B</th>
                          <th>C</th>
                          <th style={{ textAlign: "right" }}>共现数</th>
                        </tr>
                      </thead>
                      <tbody>
                        {sortBy(data.tag_triples, "count", "desc").map((r, i) => (
                          <tr key={i}>
                            <td>
                              <span className="tag category">{r.tag_a}</span>
                            </td>
                            <td>
                              <span className="tag category">{r.tag_b}</span>
                            </td>
                            <td>
                              <span className="tag category">{r.tag_c}</span>
                            </td>
                            <td
                              style={{
                                textAlign: "right",
                                fontFamily: "var(--font-mono)",
                                fontWeight: 600,
                              }}
                            >
                              {r.count}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* ── Cross-platform subtab ── */}
          {subTab === "cross" && (
            <div className="card">
              <div className="card-header">
                <h3>跨平台分类对比</h3>
                <p style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
                  仅展示两平台共有的分类
                </p>
              </div>
              <div style={{ maxHeight: 560, overflowY: "auto" }}>
                {(data.cross_platform || []).filter((d) => d.presence === "both").length === 0 ? (
                  <div className="empty-state">
                    <p>暂无可对比数据</p>
                  </div>
                ) : (
                  <table className="data-table">
                    <thead>
                      <tr>
                        <SortableHeader label="分类" field="category" sort={crossSort} onSort={toggleSort(setCrossSort)} />
                        <SortableHeader label="起点份额" field="share_qidian" sort={crossSort} onSort={toggleSort(setCrossSort)} align="right" />
                        <SortableHeader label="番茄份额" field="share_fanqie" sort={crossSort} onSort={toggleSort(setCrossSort)} align="right" />
                        <SortableHeader label="份额差" field="share_diff" sort={crossSort} onSort={toggleSort(setCrossSort)} align="right" />
                        <SortableHeader label="热度差" field="heat_diff" sort={crossSort} onSort={toggleSort(setCrossSort)} align="right" />
                        <SortableHeader label="排名差" field="rank_diff" sort={crossSort} onSort={toggleSort(setCrossSort)} align="right" />
                      </tr>
                    </thead>
                    <tbody>
                      {sortBy(
                        (data.cross_platform || []).filter((d) => d.presence === "both"),
                        crossSort.key,
                        crossSort.dir,
                      ).map((r, i) => (
                        <tr key={i}>
                          <td style={{ fontWeight: 500 }}>{r.category}</td>
                          <td style={{ textAlign: "right" }} className="font-mono">
                            {fmt(r.share_qidian, 3)}
                          </td>
                          <td style={{ textAlign: "right" }} className="font-mono">
                            {fmt(r.share_fanqie, 3)}
                          </td>
                          <td style={{ textAlign: "right" }}>
                            <DeltaCell value={r.share_diff} pct />
                          </td>
                          <td style={{ textAlign: "right" }}>
                            <DeltaCell value={r.heat_diff} />
                          </td>
                          <td style={{ textAlign: "right" }} className="font-mono">
                            {fmt(r.rank_diff, 1)}
                          </td>
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

      {/* ══ Empty result ══ */}
      {!loading && data && data.empty && (
        <div className="empty-state">
          <div className="empty-icon">📭</div>
          <h4>暂无数据</h4>
          <p>所选范围没有可分析数据，请调整参数后重试。</p>
        </div>
      )}

      {/* ══ Initial state ══ */}
      {!loading && !data && !error && (
        <div className="empty-state">
          <div className="empty-icon">📊</div>
          <h4>点击 "运行分析" 开始</h4>
          <p>选择平台、时间窗口和 Top K 参数后点击按钮。</p>
        </div>
      )}
    </div>
  );
}
