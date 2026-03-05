import React, { useEffect, useState, useMemo } from "react";
import { apiGet } from "../api/client";

type SubTab = "tags" | "categories" | "opportunities" | "cooccurrence" | "cross";

interface AnalysisResult {
  start_date: string;
  end_date: string;
  platform: string;
  lookback: string;
  top_k: number;
  empty: boolean;
  tag_rollup: any[];
  cat_rollup: any[];
  opportunities: any[];
  new_entry: any[];
  pairs: any[];
  triples: any[];
  cross_platform: any[];
}

export default function TrendAnalysisPage() {
  const [platform, setPlatform] = useState("both");
  const [lookback, setLookback] = useState("all");
  const [topK, setTopK] = useState(20);
  const [data, setData] = useState<AnalysisResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [subTab, setSubTab] = useState<SubTab>("tags");
  const [dateRange, setDateRange] = useState<{ min_date: string; max_date: string } | null>(null);

  // Load date range on mount
  useEffect(() => {
    apiGet<{ min_date: string; max_date: string }>("/api/analysis/date_range")
      .then(setDateRange).catch(() => {});
  }, []);

  function runAnalysis() {
    setLoading(true);
    setError("");
    apiGet<AnalysisResult>(`/api/analysis/run?platform=${platform}&lookback=${lookback}&top_k=${topK}`)
      .then(setData)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }

  // Auto-run on mount
  useEffect(() => { runAnalysis(); }, []);

  const pl = (p: string) => p === "qidian" ? "起点" : p === "fanqie" ? "番茄" : p;

  return (
    <div className="page-container">
      <div className="page-header">
        <h2>趋势分析</h2>
        <p>基于榜单数据的题材趋势、热度变化与开书机会分析</p>
      </div>

      {/* ── Controls ── */}
      <div className="card" style={{ marginBottom: 24 }}>
        <div className="card-body">
          <div style={{ display: "flex", gap: 20, alignItems: "flex-end", flexWrap: "wrap" }}>
            <div className="control-group">
              <label className="control-label">平台</label>
              <select className="control-select" value={platform} onChange={e => setPlatform(e.target.value)}>
                <option value="both">全部平台</option>
                <option value="qidian">起点中文网</option>
                <option value="fanqie">番茄小说</option>
              </select>
            </div>
            <div className="control-group">
              <label className="control-label">时间窗口</label>
              <select className="control-select" value={lookback} onChange={e => setLookback(e.target.value)}>
                <option value="week">最近一周</option>
                <option value="month">最近一月</option>
                <option value="quarter">最近一季</option>
                <option value="year">最近一年</option>
                <option value="all">全部数据</option>
              </select>
            </div>
            <div className="control-group">
              <label className="control-label">Top K</label>
              <select className="control-select" value={topK} onChange={e => setTopK(Number(e.target.value))}>
                {[10, 15, 20, 30, 50].map(k => <option key={k} value={k}>{k}</option>)}
              </select>
            </div>
            <button className="btn-primary" onClick={runAnalysis} disabled={loading}>
              {loading ? "分析中…" : "运行分析"}
            </button>
            {dateRange && dateRange.min_date && (
              <span className="text-xs text-muted" style={{ marginLeft: 8 }}>
                数据范围: {dateRange.min_date} ~ {dateRange.max_date}
              </span>
            )}
          </div>
        </div>
      </div>

      {error && <div className="card" style={{ marginBottom: 16, borderLeft: "3px solid var(--accent)" }}><div className="card-body text-sm" style={{ color: "var(--accent)" }}>分析出错: {error}</div></div>}

      {loading ? (
        <div className="loading"><div className="loading-spinner" /> 正在运行分析 Pipeline…</div>
      ) : !data ? null : data.empty ? (
        <div className="empty-state"><div className="empty-icon">📭</div><h4>暂无数据</h4><p>所选时间窗口和平台没有可分析的数据</p></div>
      ) : (
        <>
          {/* ── Window info ── */}
          <div style={{ display: "flex", gap: 8, marginBottom: 20, fontSize: 12, color: "var(--ink-400)" }}>
            <span>📅 {data.start_date} ~ {data.end_date}</span>
            <span>·</span>
            <span>平台: {data.platform === "both" ? "全部" : pl(data.platform)}</span>
          </div>

          {/* ── Sub-tabs ── */}
          <div className="platform-tabs" style={{ marginBottom: 24 }}>
            <button className={`platform-tab${subTab === "tags" ? " active" : ""}`} onClick={() => setSubTab("tags")}>标签趋势</button>
            <button className={`platform-tab${subTab === "categories" ? " active" : ""}`} onClick={() => setSubTab("categories")}>分类趋势</button>
            <button className={`platform-tab${subTab === "opportunities" ? " active" : ""}`} onClick={() => setSubTab("opportunities")}>开书机会</button>
            <button className={`platform-tab${subTab === "cooccurrence" ? " active" : ""}`} onClick={() => setSubTab("cooccurrence")}>标签共现</button>
            {data.platform === "both" && (
              <button className={`platform-tab${subTab === "cross" ? " active" : ""}`} onClick={() => setSubTab("cross")}>跨平台对比</button>
            )}
          </div>

          {subTab === "tags" && <TagRollupView data={data.tag_rollup} pl={pl} />}
          {subTab === "categories" && <CatRollupView data={data.cat_rollup} pl={pl} />}
          {subTab === "opportunities" && <OpportunitiesView opportunities={data.opportunities} newEntry={data.new_entry} pl={pl} />}
          {subTab === "cooccurrence" && <CooccurrenceView pairs={data.pairs} triples={data.triples} />}
          {subTab === "cross" && <CrossPlatformView data={data.cross_platform} />}
        </>
      )}
    </div>
  );
}

/* ── Tag Rollup View ── */
function TagRollupView({ data, pl }: { data: any[]; pl: (p: string) => string }) {
  const [sortBy, setSortBy] = useState("avg_heat");
  const sorted = useMemo(() => [...data].sort((a, b) => (b[sortBy] ?? 0) - (a[sortBy] ?? 0)), [data, sortBy]);

  return (
    <div className="card">
      <div className="card-header">
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div><h3>标签趋势汇总 (tag_u)</h3><p>按时间窗口聚合的标签级指标，含趋势斜率与阶段判断</p></div>
          <select className="control-select" value={sortBy} onChange={e => setSortBy(e.target.value)} style={{ width: 160 }}>
            <option value="avg_heat">按平均热度</option>
            <option value="mean_share">按市场份额</option>
            <option value="heat_slope">按热度趋势</option>
            <option value="share_slope">按份额趋势</option>
            <option value="mean_rank">按平均排名</option>
          </select>
        </div>
      </div>
      <div style={{ maxHeight: 560, overflowY: "auto" }}>
        <table className="data-table">
          <thead><tr>
            <th>平台</th><th>榜单</th><th>标签</th><th>阶段</th>
            <th style={{ textAlign: "right" }}>份额</th>
            <th style={{ textAlign: "right" }}>热度</th>
            <th style={{ textAlign: "right" }}>均排名</th>
            <th style={{ textAlign: "right" }}>热度趋势</th>
            <th style={{ textAlign: "right" }}>份额趋势</th>
          </tr></thead>
          <tbody>
            {sorted.map((r, i) => (
              <tr key={i}>
                <td><span className={`tag ${r.platform}`}>{pl(r.platform)}</span></td>
                <td className="text-xs text-muted">{r.rank_family}{r.rank_sub_cat ? ` · ${r.rank_sub_cat}` : ""}</td>
                <td style={{ fontWeight: 500 }}>{r.tag_u}</td>
                <td><StageTag stage={r.stage} /></td>
                <td style={{ textAlign: "right" }} className="font-mono">{fmt(r.mean_share, 3)}</td>
                <td style={{ textAlign: "right" }} className="font-mono">{fmt(r.avg_heat, 0)}</td>
                <td style={{ textAlign: "right" }} className="font-mono">{fmt(r.mean_rank, 1)}</td>
                <td style={{ textAlign: "right" }}><SlopeIndicator value={r.heat_slope} /></td>
                <td style={{ textAlign: "right" }}><SlopeIndicator value={r.share_slope} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ── Category Rollup View ── */
function CatRollupView({ data, pl }: { data: any[]; pl: (p: string) => string }) {
  const sorted = useMemo(() => [...data].sort((a, b) => (b.avg_heat ?? 0) - (a.avg_heat ?? 0)), [data]);
  return (
    <div className="card">
      <div className="card-header"><h3>分类趋势汇总 (cat_u)</h3><p>按主分类聚合，适合跨平台宏观对比</p></div>
      <div style={{ maxHeight: 560, overflowY: "auto" }}>
        <table className="data-table">
          <thead><tr>
            <th>平台</th><th>分类</th>
            <th style={{ textAlign: "right" }}>份额</th>
            <th style={{ textAlign: "right" }}>热度</th>
            <th style={{ textAlign: "right" }}>均排名</th>
            <th style={{ textAlign: "right" }}>热度趋势</th>
            <th style={{ textAlign: "right" }}>份额趋势</th>
          </tr></thead>
          <tbody>
            {sorted.map((r, i) => (
              <tr key={i}>
                <td><span className={`tag ${r.platform}`}>{pl(r.platform)}</span></td>
                <td style={{ fontWeight: 500 }}>{r.cat_u}</td>
                <td style={{ textAlign: "right" }} className="font-mono">{fmt(r.mean_share, 3)}</td>
                <td style={{ textAlign: "right" }} className="font-mono">{fmt(r.avg_heat, 0)}</td>
                <td style={{ textAlign: "right" }} className="font-mono">{fmt(r.mean_rank, 1)}</td>
                <td style={{ textAlign: "right" }}><SlopeIndicator value={r.heat_slope} /></td>
                <td style={{ textAlign: "right" }}><SlopeIndicator value={r.share_slope} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/* ── Opportunities View ── */
function OpportunitiesView({ opportunities, newEntry, pl }: { opportunities: any[]; newEntry: any[]; pl: (p: string) => string }) {
  return (
    <>
      <div className="card" style={{ marginBottom: 20 }}>
        <div className="card-header"><h3>🎯 开书机会榜</h3><p>综合份额增长 + 热度增长 + 新书占比，得分越高表示越适合现在入场</p></div>
        <div style={{ maxHeight: 480, overflowY: "auto" }}>
          {opportunities.length === 0 ? <div className="empty-state"><p>数据不足，无法计算</p></div> : (
            <table className="data-table">
              <thead><tr>
                <th>平台</th><th>分类</th><th>标签</th>
                <th style={{ textAlign: "right" }}>份额变化</th>
                <th style={{ textAlign: "right" }}>热度变化</th>
                <th style={{ textAlign: "right" }}>新书比</th>
                <th style={{ textAlign: "right" }}>机会分</th>
              </tr></thead>
              <tbody>
                {opportunities.map((r, i) => (
                  <tr key={i}>
                    <td><span className={`tag ${r.platform}`}>{pl(r.platform)}</span></td>
                    <td className="text-muted">{r.cat_u}</td>
                    <td style={{ fontWeight: 500 }}>{r.tag_u}</td>
                    <td style={{ textAlign: "right" }}><DeltaValue value={r.share_delta} pct /></td>
                    <td style={{ textAlign: "right" }}><DeltaValue value={r.heat_delta} /></td>
                    <td style={{ textAlign: "right" }} className="font-mono">{fmt(r.new_entry_ratio, 2)}</td>
                    <td style={{ textAlign: "right", fontWeight: 700, fontFamily: "var(--font-mono)" }}>
                      {fmt(r.opportunity_score, 2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-header"><h3>📊 新入榜占比</h3><p>各标签的新书/老书比例</p></div>
        <div style={{ maxHeight: 400, overflowY: "auto" }}>
          {newEntry.length === 0 ? <div className="empty-state"><p>暂无数据</p></div> : (
            <table className="data-table">
              <thead><tr>
                <th>平台</th><th>标签</th>
                <th style={{ textAlign: "right" }}>总书数</th>
                <th style={{ textAlign: "right" }}>新书数</th>
                <th style={{ textAlign: "right" }}>新书比</th>
              </tr></thead>
              <tbody>
                {newEntry.map((r, i) => (
                  <tr key={i}>
                    <td><span className={`tag ${r.platform}`}>{pl(r.platform)}</span></td>
                    <td style={{ fontWeight: 500 }}>{r.tag_u}</td>
                    <td style={{ textAlign: "right" }} className="font-mono">{r.total_books}</td>
                    <td style={{ textAlign: "right" }} className="font-mono">{r.new_books}</td>
                    <td style={{ textAlign: "right" }}><DeltaValue value={r.new_entry_ratio} pct /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </>
  );
}

/* ── Co-occurrence View ── */
function CooccurrenceView({ pairs, triples }: { pairs: any[]; triples: any[] }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20 }}>
      <div className="card">
        <div className="card-header"><h3>标签共现（二元组）</h3><p>经常一起出现在同一本书上的标签对</p></div>
        <div style={{ maxHeight: 480, overflowY: "auto" }}>
          {pairs.length === 0 ? <div className="empty-state"><p>暂无数据</p></div> : (
            <table className="data-table">
              <thead><tr><th>标签 A</th><th>标签 B</th><th style={{ textAlign: "right" }}>共现数</th></tr></thead>
              <tbody>
                {pairs.map((r, i) => (
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
        <div className="card-header"><h3>标签共现（三元组）</h3><p>三个标签的常见组合</p></div>
        <div style={{ maxHeight: 480, overflowY: "auto" }}>
          {triples.length === 0 ? <div className="empty-state"><p>暂无数据</p></div> : (
            <table className="data-table">
              <thead><tr><th>标签 A</th><th>标签 B</th><th>标签 C</th><th style={{ textAlign: "right" }}>共现数</th></tr></thead>
              <tbody>
                {triples.map((r, i) => (
                  <tr key={i}>
                    <td><span className="tag category">{r.tag_a}</span></td>
                    <td><span className="tag category">{r.tag_b}</span></td>
                    <td><span className="tag category">{r.tag_c}</span></td>
                    <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 600 }}>{r.count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  );
}

/* ── Cross-Platform View ── */
function CrossPlatformView({ data }: { data: any[] }) {
  const both = useMemo(() => data.filter(d => d.presence === "both").sort((a, b) => Math.abs(b.share_diff ?? 0) - Math.abs(a.share_diff ?? 0)), [data]);
  return (
    <div className="card">
      <div className="card-header"><h3>跨平台分类差异</h3><p>同一分类在起点 vs 番茄的份额/热度/排名差异（仅展示两平台共有的分类）</p></div>
      <div style={{ maxHeight: 560, overflowY: "auto" }}>
        {both.length === 0 ? <div className="empty-state"><p>暂无可对比的数据</p></div> : (
          <table className="data-table">
            <thead><tr>
              <th>分类</th>
              <th style={{ textAlign: "right" }}>起点份额</th>
              <th style={{ textAlign: "right" }}>番茄份额</th>
              <th style={{ textAlign: "right" }}>份额差</th>
              <th style={{ textAlign: "right" }}>热度差</th>
              <th style={{ textAlign: "right" }}>排名差</th>
            </tr></thead>
            <tbody>
              {both.map((r, i) => (
                <tr key={i}>
                  <td style={{ fontWeight: 500 }}>{r.cat_u}</td>
                  <td style={{ textAlign: "right" }} className="font-mono">{fmt(r.share_qidian, 3)}</td>
                  <td style={{ textAlign: "right" }} className="font-mono">{fmt(r.share_fanqie, 3)}</td>
                  <td style={{ textAlign: "right" }}><DeltaValue value={r.share_diff} pct /></td>
                  <td style={{ textAlign: "right" }}><DeltaValue value={r.heat_diff} /></td>
                  <td style={{ textAlign: "right" }} className="font-mono">{fmt(r.rank_diff, 1)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

/* ── Shared small components ── */

function StageTag({ stage }: { stage: string }) {
  const map: Record<string, { bg: string; color: string; label: string }> = {
    safe: { bg: "var(--jade-light)", color: "var(--jade)", label: "稳定" },
    chance: { bg: "var(--gold-light)", color: "#92700c", label: "机会" },
    stable: { bg: "var(--ink-50)", color: "var(--ink-500)", label: "平稳" },
    unknown: { bg: "var(--ink-50)", color: "var(--ink-400)", label: "—" },
  };
  const s = map[stage] || map.unknown;
  return <span className="tag" style={{ background: s.bg, color: s.color }}>{s.label}</span>;
}

function SlopeIndicator({ value }: { value: number | null }) {
  if (value == null || isNaN(value)) return <span className="text-muted font-mono">—</span>;
  const color = value > 0.001 ? "var(--jade)" : value < -0.001 ? "var(--accent)" : "var(--ink-400)";
  const arrow = value > 0.001 ? "↑" : value < -0.001 ? "↓" : "→";
  return <span className="font-mono" style={{ color, fontSize: 12 }}>{arrow} {Math.abs(value).toFixed(4)}</span>;
}

function DeltaValue({ value, pct }: { value: number | null; pct?: boolean }) {
  if (value == null || isNaN(value)) return <span className="text-muted font-mono">—</span>;
  const color = value > 0 ? "var(--jade)" : value < 0 ? "var(--accent)" : "var(--ink-400)";
  const sign = value > 0 ? "+" : "";
  const display = pct ? `${sign}${(value * 100).toFixed(1)}%` : `${sign}${value.toFixed(0)}`;
  return <span className="font-mono" style={{ color, fontSize: 12, fontWeight: 600 }}>{display}</span>;
}

function fmt(v: number | null, decimals: number): string {
  if (v == null || isNaN(v)) return "—";
  return v.toFixed(decimals);
}
