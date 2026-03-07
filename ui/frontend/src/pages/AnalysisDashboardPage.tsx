import React, { useEffect, useState, useMemo } from "react";
import { apiGet } from "../api/client";

/* ── response types ── */
interface Summary {
  novel_count?: number;
  rank_entry_count?: number;
  snapshot_count?: number;
  tag_count?: number;
  earliest?: string;
  latest?: string;
}

interface TrendPoint {
  snapshot_date: string;
  entry_count: number;
  novel_count: number;
}

interface PlatformDist {
  platform: string;
  count: number;
}

interface HotTag {
  tag_name: string;
  count: number;
}

interface StableNovel {
  novel_uid: number;
  novel_name: string;
  author: string;
  platform: string;
  avg_rank: number;
  appearances: number;
}

const platformLabel = (p: string) =>
  p === "qidian" ? "起点" : p === "fanqie" ? "番茄" : p || "未知";

export default function AnalysisDashboardPage() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [timeline, setTimeline] = useState<TrendPoint[]>([]);
  const [platDist, setPlatDist] = useState<PlatformDist[]>([]);
  const [hotTags, setHotTags] = useState<HotTag[]>([]);
  const [stableNovels, setStableNovels] = useState<StableNovel[]>([]);
  const [loading, setLoading] = useState(true);
  const [activeDate, setActiveDate] = useState<string>("");

  useEffect(() => {
    setLoading(true);
    Promise.all([
      apiGet<Summary>("/api/analysis/summary"),
      apiGet<TrendPoint[]>("/api/analysis/trend-timeline"),
      apiGet<PlatformDist[]>("/api/analysis/platform-distribution"),
      apiGet<HotTag[]>("/api/analysis/hot-tags"),
      apiGet<StableNovel[]>("/api/analysis/stable-ranked"),
    ])
      .then(([sum, tl, pd, ht, sn]) => {
        setSummary(sum);
        const tlArr = Array.isArray(tl) ? tl : [];
        setTimeline(tlArr);
        setPlatDist(Array.isArray(pd) ? pd : []);
        setHotTags(Array.isArray(ht) ? ht : []);
        setStableNovels(Array.isArray(sn) ? sn : []);
        if (tlArr.length > 0) setActiveDate(tlArr[tlArr.length - 1].snapshot_date);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  /* derived */
  const trendMax = useMemo(
    () => Math.max(1, ...timeline.map((x) => x.entry_count)),
    [timeline],
  );
  const tagMax = useMemo(() => Math.max(1, ...hotTags.map((x) => x.count)), [hotTags]);
  const platMax = useMemo(() => Math.max(1, ...platDist.map((x) => x.count)), [platDist]);
  const activeTrend = timeline.find((x) => x.snapshot_date === activeDate) ?? null;

  const barColors = ["red", "indigo", "gold", "jade", "purple"];

  if (loading) {
    return (
      <div className="page-container">
        <div className="loading">
          <div className="loading-spinner" />
          加载分析数据...
        </div>
      </div>
    );
  }

  return (
    <div className="page-container">
      {/* ══ Header ══ */}
      <div className="page-header" style={{ marginBottom: 20 }}>
        <h2>分析面板</h2>
        <p>市场规模、平台分布、标签热度与稳定上榜作品</p>
      </div>

      {/* ══ Summary Stats ══ */}
      <div className="stats-grid">
        {[
          { icon: "📚", color: "red", value: summary?.novel_count, label: "小说总数" },
          { icon: "📊", color: "gold", value: summary?.rank_entry_count, label: "榜单记录" },
          { icon: "📸", color: "jade", value: summary?.snapshot_count, label: "快照天数" },
          { icon: "🏷", color: "indigo", value: summary?.tag_count, label: "标签数量" },
          { icon: "📅", color: "purple", value: summary?.earliest, label: "最早日期", isText: true },
          { icon: "📅", color: "cyan", value: summary?.latest, label: "最新日期", isText: true },
        ].map((s) => (
          <div className="stat-card" key={s.label}>
            <div className={`stat-icon ${s.color}`}>{s.icon}</div>
            <div
              className="stat-value"
              style={(s as any).isText ? { fontSize: 18 } : undefined}
            >
              {s.value != null
                ? typeof s.value === "number"
                  ? s.value.toLocaleString()
                  : s.value
                : "—"}
            </div>
            <div className="stat-label">{s.label}</div>
          </div>
        ))}
      </div>

      {/* ══ Trend Timeline + Date Detail ══ */}
      <div style={{ display: "grid", gridTemplateColumns: "1.3fr 1fr", gap: 20, marginBottom: 24 }}>
        {/* Trend timeline bar chart */}
        <div className="card">
          <div className="card-header">
            <h3>榜单活跃度时间线</h3>
            <span className="text-xs text-muted">{timeline.length} 天</span>
          </div>
          <div className="card-body" style={{ maxHeight: 380, overflowY: "auto" }}>
            {timeline.length === 0 ? (
              <div className="empty-state">
                <p>暂无时间线数据</p>
              </div>
            ) : (
              <div className="bar-chart">
                {timeline.map((row) => (
                  <div
                    className="bar-row"
                    key={row.snapshot_date}
                    onClick={() => setActiveDate(row.snapshot_date)}
                    style={{
                      cursor: "pointer",
                      padding: "2px 0",
                      borderRadius: "var(--radius-xs)",
                      background:
                        row.snapshot_date === activeDate ? "var(--accent-subtle)" : "transparent",
                    }}
                  >
                    <div className="bar-label" style={{ fontSize: 11 }}>
                      {row.snapshot_date}
                    </div>
                    <div className="bar-track">
                      <div
                        className="bar-fill red"
                        style={{
                          width: `${Math.max(4, (row.entry_count / trendMax) * 100)}%`,
                        }}
                      >
                        {row.entry_count}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Date detail + platform distribution */}
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          {/* Active date detail */}
          <div className="card">
            <div className="card-header">
              <h3>日期详情</h3>
            </div>
            <div className="card-body">
              {activeTrend ? (
                <div className="detail-grid">
                  <span className="label">日期</span>
                  <span className="value font-mono">{activeTrend.snapshot_date}</span>
                  <span className="label">榜单条目</span>
                  <span className="value font-mono">{activeTrend.entry_count.toLocaleString()}</span>
                  <span className="label">上榜作品</span>
                  <span className="value font-mono">{activeTrend.novel_count.toLocaleString()}</span>
                </div>
              ) : (
                <div className="text-muted" style={{ padding: 12, textAlign: "center", fontSize: 13 }}>
                  点击左侧时间线查看详情
                </div>
              )}
            </div>
          </div>

          {/* Platform distribution */}
          <div className="card" style={{ flex: 1 }}>
            <div className="card-header">
              <h3>平台分布</h3>
            </div>
            <div className="card-body">
              {platDist.length === 0 ? (
                <div className="empty-state" style={{ padding: 16 }}>
                  <p>暂无平台数据</p>
                </div>
              ) : (
                <div className="bar-chart">
                  {platDist.map((p, idx) => (
                    <div className="bar-row" key={p.platform}>
                      <div className="bar-label">{platformLabel(p.platform)}</div>
                      <div className="bar-track">
                        <div
                          className={`bar-fill ${barColors[idx % barColors.length]}`}
                          style={{ width: `${Math.max(4, (p.count / platMax) * 100)}%` }}
                        >
                          {p.count.toLocaleString()}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* ══ Hot Tags + Stable Novels ══ */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr 1.2fr", gap: 20 }}>
        {/* Hot tags top 12 */}
        <div className="card">
          <div className="card-header">
            <h3>热门标签 Top 12</h3>
          </div>
          <div className="card-body">
            {hotTags.length === 0 ? (
              <div className="empty-state">
                <p>暂无标签数据</p>
              </div>
            ) : (
              <div className="bar-chart">
                {hotTags.slice(0, 12).map((t, idx) => (
                  <div className="bar-row" key={t.tag_name}>
                    <div className="bar-label">{t.tag_name}</div>
                    <div className="bar-track">
                      <div
                        className={`bar-fill ${barColors[idx % barColors.length]}`}
                        style={{ width: `${Math.max(4, (t.count / tagMax) * 100)}%` }}
                      >
                        {t.count}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Stably ranked novels */}
        <div className="card">
          <div className="card-header">
            <h3>稳定上榜作品</h3>
            <span className="text-xs text-muted">{stableNovels.length} 部</span>
          </div>
          <div style={{ maxHeight: 420, overflowY: "auto" }}>
            {stableNovels.length === 0 ? (
              <div className="empty-state">
                <p>暂无数据</p>
              </div>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th>书名</th>
                    <th>作者</th>
                    <th>平台</th>
                    <th style={{ textAlign: "right" }}>平均排名</th>
                    <th style={{ textAlign: "right" }}>上榜次数</th>
                  </tr>
                </thead>
                <tbody>
                  {stableNovels.map((n) => (
                    <tr key={n.novel_uid}>
                      <td style={{ fontWeight: 500, color: "var(--text-primary)" }}>
                        {n.novel_name || "-"}
                      </td>
                      <td className="text-muted">{n.author || "-"}</td>
                      <td>
                        <span className={`tag ${n.platform}`}>{platformLabel(n.platform)}</span>
                      </td>
                      <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>
                        {Number(n.avg_rank || 0).toFixed(1)}
                      </td>
                      <td
                        style={{
                          textAlign: "right",
                          fontFamily: "var(--font-mono)",
                          fontWeight: 600,
                        }}
                      >
                        {n.appearances}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
