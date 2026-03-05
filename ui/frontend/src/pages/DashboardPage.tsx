import React, { useEffect, useState, useMemo } from "react";
import { apiGet } from "../api/client";

type Platform = "" | "qidian" | "fanqie";

interface OverviewData {
  novel_count: number;
  rank_list_count: number;
  snapshot_count: number;
  chapter_count: number;
  recent_snapshots: any[];
  platform_breakdown: { platform: string; count: number }[];
  categories: { main_category: string; count: number }[];
  rank_families: { rank_family: string; platform: string; snapshot_count: number }[];
}

interface TopNovel {
  novel_uid: number;
  title: string;
  author: string;
  platform: string;
  main_category: string;
  status: string;
  total_words: number;
  appearances: number;
  best_rank: number;
  avg_rank: number;
  last_seen: string;
}

interface TagStat {
  tag_name: string;
  novel_count: number;
}

export default function DashboardPage() {
  const [platform, setPlatform] = useState<Platform>("");
  const [overview, setOverview] = useState<OverviewData | null>(null);
  const [topNovels, setTopNovels] = useState<TopNovel[]>([]);
  const [tagStats, setTagStats] = useState<TagStat[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    const p = platform || undefined;
    Promise.all([
      apiGet<OverviewData>(`/api/db/overview${p ? `?platform=${p}` : ""}`),
      apiGet<{ rows: TopNovel[] }>(`/api/db/top_novels?limit=20${p ? `&platform=${p}` : ""}`),
      apiGet<{ rows: TagStat[] }>(`/api/db/tag_stats?limit=20${p ? `&platform=${p}` : ""}`),
    ])
      .then(([ov, tn, ts]) => {
        setOverview(ov);
        setTopNovels(tn.rows);
        setTagStats(ts.rows);
      })
      .catch((e) => console.error("Dashboard load error:", e))
      .finally(() => setLoading(false));
  }, [platform]);

  const maxCatCount = useMemo(
    () => Math.max(1, ...(overview?.categories.map((c) => c.count) || [1])),
    [overview]
  );

  const maxTagCount = useMemo(
    () => Math.max(1, ...(tagStats.map((t) => t.novel_count) || [1])),
    [tagStats]
  );

  const platformLabel = (p: string) =>
    p === "qidian" ? "起点" : p === "fanqie" ? "番茄" : p;

  return (
    <div className="page-container">
      <div className="page-header">
        <h2>数据概览</h2>
        <p>网文市场趋势一览 — 了解当前热门题材与上榜作品</p>
      </div>

      {/* Platform Tabs */}
      <div className="platform-tabs">
        <button
          className={`platform-tab${platform === "" ? " active" : ""}`}
          onClick={() => setPlatform("")}
        >
          全部平台
        </button>
        <button
          className={`platform-tab${platform === "qidian" ? " active" : ""}`}
          onClick={() => setPlatform("qidian")}
        >
          起点中文网
        </button>
        <button
          className={`platform-tab${platform === "fanqie" ? " active" : ""}`}
          onClick={() => setPlatform("fanqie")}
        >
          番茄小说
        </button>
      </div>

      {loading ? (
        <div className="loading">
          <div className="loading-spinner" />
          加载中…
        </div>
      ) : !overview ? (
        <div className="empty-state">
          <div className="empty-icon">📭</div>
          <h4>暂无数据</h4>
          <p>数据库中还没有采集到任何数据，请先运行爬虫采集</p>
        </div>
      ) : (
        <>
          {/* ── Stat Cards ── */}
          <div className="stats-grid">
            <StatCard
              icon="📚"
              iconClass="red"
              value={overview.novel_count.toLocaleString()}
              label="收录小说"
            />
            <StatCard
              icon="🏆"
              iconClass="gold"
              value={overview.rank_list_count.toLocaleString()}
              label="榜单类型"
            />
            <StatCard
              icon="📸"
              iconClass="jade"
              value={overview.snapshot_count.toLocaleString()}
              label="榜单快照"
            />
            <StatCard
              icon="📖"
              iconClass="indigo"
              value={overview.chapter_count.toLocaleString()}
              label="采集章节"
            />
          </div>

          {/* ── Two column layout ── */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20, marginBottom: 24 }}>
            {/* Category Distribution */}
            <div className="card">
              <div className="card-header">
                <h3>题材分布</h3>
                <p>按主分类统计的小说数量</p>
              </div>
              <div className="card-body">
                {overview.categories.length === 0 ? (
                  <div className="text-sm text-muted" style={{ padding: "16px 0" }}>
                    暂无分类数据
                  </div>
                ) : (
                  <div className="bar-chart">
                    {overview.categories.slice(0, 12).map((c) => (
                      <div className="bar-row" key={c.main_category || "(未分类)"}>
                        <div className="bar-label">{c.main_category || "未分类"}</div>
                        <div className="bar-track">
                          <div
                            className="bar-fill red"
                            style={{ width: `${Math.max(4, (c.count / maxCatCount) * 100)}%` }}
                          >
                            {c.count}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>

            {/* Tag Cloud */}
            <div className="card">
              <div className="card-header">
                <h3>热门标签</h3>
                <p>出现频率最高的细分标签</p>
              </div>
              <div className="card-body">
                {tagStats.length === 0 ? (
                  <div className="text-sm text-muted" style={{ padding: "16px 0" }}>
                    暂无标签数据
                  </div>
                ) : (
                  <div className="bar-chart">
                    {tagStats.slice(0, 12).map((t) => (
                      <div className="bar-row" key={t.tag_name}>
                        <div className="bar-label">{t.tag_name}</div>
                        <div className="bar-track">
                          <div
                            className="bar-fill indigo"
                            style={{ width: `${Math.max(4, (t.novel_count / maxTagCount) * 100)}%` }}
                          >
                            {t.novel_count}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* ── Platform breakdown (only in "all" mode) ── */}
          {platform === "" && overview.platform_breakdown.length > 0 && (
            <div className="card" style={{ marginBottom: 24 }}>
              <div className="card-header">
                <h3>平台对比</h3>
                <p>各平台收录小说数量</p>
              </div>
              <div className="card-body">
                <div style={{ display: "flex", gap: 24 }}>
                  {overview.platform_breakdown.map((pb) => (
                    <div
                      key={pb.platform}
                      style={{
                        flex: 1,
                        padding: "16px 20px",
                        background: "var(--paper-warm)",
                        borderRadius: "var(--radius-sm)",
                        textAlign: "center",
                      }}
                    >
                      <div style={{ fontSize: 12, color: "var(--ink-400)", marginBottom: 4 }}>
                        {platformLabel(pb.platform)}
                      </div>
                      <div
                        style={{
                          fontFamily: "var(--font-serif)",
                          fontSize: 24,
                          fontWeight: 700,
                          color: "var(--ink-900)",
                        }}
                      >
                        {pb.count.toLocaleString()}
                      </div>
                      <div style={{ fontSize: 11, color: "var(--ink-400)" }}>部小说</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* ── Top Novels Table ── */}
          <div className="card">
            <div className="card-header">
              <h3>高频上榜作品</h3>
              <p>按上榜次数排序 — 最稳定的热门作品</p>
            </div>
            <div
              style={{
                maxHeight: 520,
                overflowY: "auto",
              }}
            >
              {topNovels.length === 0 ? (
                <div className="empty-state">
                  <p>暂无排名数据</p>
                </div>
              ) : (
                <table className="data-table">
                  <thead>
                    <tr>
                      <th style={{ width: 50 }}>#</th>
                      <th>书名</th>
                      <th>作者</th>
                      <th>平台</th>
                      <th>分类</th>
                      <th style={{ textAlign: "right" }}>上榜次数</th>
                      <th style={{ textAlign: "right" }}>最佳排名</th>
                      <th style={{ textAlign: "right" }}>平均排名</th>
                    </tr>
                  </thead>
                  <tbody>
                    {topNovels.map((n, i) => (
                      <tr key={n.novel_uid}>
                        <td>
                          <span
                            className={`rank-badge ${
                              i < 3 ? "top3" : i < 10 ? "top10" : "normal"
                            }`}
                          >
                            {i + 1}
                          </span>
                        </td>
                        <td style={{ fontWeight: 500 }}>{n.title || "(未知)"}</td>
                        <td className="text-muted">{n.author || "-"}</td>
                        <td>
                          <span className={`tag ${n.platform}`}>
                            {platformLabel(n.platform)}
                          </span>
                        </td>
                        <td>
                          {n.main_category && (
                            <span className="tag category">{n.main_category}</span>
                          )}
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
                        <td
                          style={{
                            textAlign: "right",
                            fontFamily: "var(--font-mono)",
                          }}
                        >
                          {n.best_rank}
                        </td>
                        <td
                          style={{
                            textAlign: "right",
                            fontFamily: "var(--font-mono)",
                          }}
                        >
                          {n.avg_rank}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>

          {/* ── Recent Activity ── */}
          <div className="card" style={{ marginTop: 24 }}>
            <div className="card-header">
              <h3>最近采集记录</h3>
              <p>最近的榜单快照</p>
            </div>
            <div style={{ maxHeight: 320, overflowY: "auto" }}>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>日期</th>
                    <th>平台</th>
                    <th>榜单</th>
                    <th>子类</th>
                    <th style={{ textAlign: "right" }}>收录数</th>
                  </tr>
                </thead>
                <tbody>
                  {overview.recent_snapshots.map((s) => (
                    <tr key={s.snapshot_id}>
                      <td className="font-mono">{s.snapshot_date}</td>
                      <td>
                        <span className={`tag ${s.platform}`}>
                          {platformLabel(s.platform)}
                        </span>
                      </td>
                      <td>{s.rank_family}</td>
                      <td className="text-muted">{s.rank_sub_cat || "—"}</td>
                      <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>
                        {s.item_count ?? "-"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function StatCard(props: {
  icon: string;
  iconClass: string;
  value: string;
  label: string;
}) {
  return (
    <div className="stat-card">
      <div className={`stat-icon ${props.iconClass}`}>{props.icon}</div>
      <div className="stat-value">{props.value}</div>
      <div className="stat-label">{props.label}</div>
    </div>
  );
}
