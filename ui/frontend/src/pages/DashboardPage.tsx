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
  rank_families: any[];
}
interface TopNovel { novel_uid: number; title: string; author: string; platform: string; main_category: string; appearances: number; best_rank: number; avg_rank: number; }
interface TagStat { tag_name: string; novel_count: number; }

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
      apiGet<{ rows: TopNovel[] }>(`/api/db/top_novels?limit=15${p ? `&platform=${p}` : ""}`),
      apiGet<{ rows: TagStat[] }>(`/api/db/tag_stats?limit=15${p ? `&platform=${p}` : ""}`),
    ]).then(([ov, tn, ts]) => { setOverview(ov); setTopNovels(tn.rows); setTagStats(ts.rows); })
      .catch(console.error).finally(() => setLoading(false));
  }, [platform]);

  const maxCatCount = useMemo(() => Math.max(1, ...(overview?.categories.map(c => c.count) || [1])), [overview]);
  const maxTagCount = useMemo(() => Math.max(1, ...(tagStats.map(t => t.novel_count) || [1])), [tagStats]);
  const pl = (p: string) => p === "qidian" ? "起点" : p === "fanqie" ? "番茄" : p;

  return (
    <div className="page-container">
      <div className="page-header">
        <h2>首页</h2>
        <p>市场数据概览与创作进度</p>
      </div>

      {/* ── Author Creation Stats (Placeholder) ── */}
      <div className="card" style={{ marginBottom: 24 }}>
        <div className="card-header">
          <h3>📝 我的创作</h3>
          <p>创作进度与 AI 辅助统计</p>
        </div>
        <div className="card-body">
          <div className="stats-grid" style={{ marginBottom: 0 }}>
            <div className="stat-card" style={{ opacity: 0.55 }}>
              <div className="stat-icon gold">✍️</div>
              <div className="stat-value" style={{ color: "var(--ink-300)" }}>—</div>
              <div className="stat-label">累计创作字数</div>
            </div>
            <div className="stat-card" style={{ opacity: 0.55 }}>
              <div className="stat-icon jade">📄</div>
              <div className="stat-value" style={{ color: "var(--ink-300)" }}>—</div>
              <div className="stat-label">已完成章节</div>
            </div>
            <div className="stat-card" style={{ opacity: 0.55 }}>
              <div className="stat-icon indigo">🤖</div>
              <div className="stat-value" style={{ color: "var(--ink-300)" }}>—%</div>
              <div className="stat-label">AI 辅助率</div>
            </div>
            <div className="stat-card" style={{ opacity: 0.55 }}>
              <div className="stat-icon red">💡</div>
              <div className="stat-value" style={{ color: "var(--ink-300)" }}>—</div>
              <div className="stat-label">待处理建议</div>
            </div>
          </div>
          <div className="placeholder-banner">
            <span>🚧</span>
            <div>
              <strong>创作模块开发中</strong>
              <p>此区域将展示你的创作进度、AI 辅助率统计、Editor Agent 优化建议等信息。</p>
            </div>
          </div>
        </div>
      </div>

      {/* ── Market Data Section ── */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
        <h3 className="font-serif" style={{ fontSize: 18, color: "var(--ink-800)" }}>📊 市场数据概览</h3>
        <div className="platform-tabs">
          <button className={`platform-tab${platform === "" ? " active" : ""}`} onClick={() => setPlatform("")}>全部</button>
          <button className={`platform-tab${platform === "qidian" ? " active" : ""}`} onClick={() => setPlatform("qidian")}>起点</button>
          <button className={`platform-tab${platform === "fanqie" ? " active" : ""}`} onClick={() => setPlatform("fanqie")}>番茄</button>
        </div>
      </div>

      {loading ? (
        <div className="loading"><div className="loading-spinner" /> 加载中…</div>
      ) : !overview ? (
        <div className="empty-state"><div className="empty-icon">📭</div><h4>暂无数据</h4><p>数据库中还没有采集到任何数据，请先运行爬虫采集</p></div>
      ) : (
        <>
          <div className="stats-grid">
            <StatCard icon="📚" cls="red" value={overview.novel_count.toLocaleString()} label="收录小说" />
            <StatCard icon="🏆" cls="gold" value={overview.rank_list_count.toLocaleString()} label="榜单类型" />
            <StatCard icon="📸" cls="jade" value={overview.snapshot_count.toLocaleString()} label="榜单快照" />
            <StatCard icon="📖" cls="indigo" value={overview.chapter_count.toLocaleString()} label="采集章节" />
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20, marginBottom: 24 }}>
            <div className="card">
              <div className="card-header"><h3>题材分布</h3><p>按主分类统计</p></div>
              <div className="card-body">
                {overview.categories.length === 0 ? <div className="text-sm text-muted" style={{ padding: "16px 0" }}>暂无数据</div> : (
                  <div className="bar-chart">
                    {overview.categories.slice(0, 12).map(c => (
                      <div className="bar-row" key={c.main_category || "nil"}>
                        <div className="bar-label">{c.main_category || "未分类"}</div>
                        <div className="bar-track"><div className="bar-fill red" style={{ width: `${Math.max(4, (c.count / maxCatCount) * 100)}%` }}>{c.count}</div></div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
            <div className="card">
              <div className="card-header"><h3>热门标签</h3><p>高频细分标签</p></div>
              <div className="card-body">
                {tagStats.length === 0 ? <div className="text-sm text-muted" style={{ padding: "16px 0" }}>暂无数据</div> : (
                  <div className="bar-chart">
                    {tagStats.slice(0, 12).map(t => (
                      <div className="bar-row" key={t.tag_name}>
                        <div className="bar-label">{t.tag_name}</div>
                        <div className="bar-track"><div className="bar-fill indigo" style={{ width: `${Math.max(4, (t.novel_count / maxTagCount) * 100)}%` }}>{t.novel_count}</div></div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>

          {platform === "" && overview.platform_breakdown.length > 0 && (
            <div className="card" style={{ marginBottom: 24 }}>
              <div className="card-header"><h3>平台对比</h3></div>
              <div className="card-body">
                <div style={{ display: "flex", gap: 24 }}>
                  {overview.platform_breakdown.map(pb => (
                    <div key={pb.platform} style={{ flex: 1, padding: "16px 20px", background: "var(--paper-warm)", borderRadius: "var(--radius-sm)", textAlign: "center" }}>
                      <div style={{ fontSize: 12, color: "var(--ink-400)", marginBottom: 4 }}>{pl(pb.platform)}</div>
                      <div style={{ fontFamily: "var(--font-serif)", fontSize: 24, fontWeight: 700 }}>{pb.count.toLocaleString()}</div>
                      <div style={{ fontSize: 11, color: "var(--ink-400)" }}>部小说</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          <div className="card">
            <div className="card-header"><h3>高频上榜作品</h3><p>按上榜次数排序</p></div>
            <div style={{ maxHeight: 440, overflowY: "auto" }}>
              {topNovels.length === 0 ? <div className="empty-state"><p>暂无数据</p></div> : (
                <table className="data-table">
                  <thead><tr><th style={{ width: 50 }}>#</th><th>书名</th><th>作者</th><th>平台</th><th>分类</th><th style={{ textAlign: "right" }}>上榜</th><th style={{ textAlign: "right" }}>最佳</th><th style={{ textAlign: "right" }}>均排</th></tr></thead>
                  <tbody>
                    {topNovels.map((n, i) => (
                      <tr key={n.novel_uid}>
                        <td><span className={`rank-badge ${i < 3 ? "top3" : i < 10 ? "top10" : "normal"}`}>{i + 1}</span></td>
                        <td style={{ fontWeight: 500 }}>{n.title || "(未知)"}</td>
                        <td className="text-muted">{n.author || "-"}</td>
                        <td><span className={`tag ${n.platform}`}>{pl(n.platform)}</span></td>
                        <td>{n.main_category && <span className="tag category">{n.main_category}</span>}</td>
                        <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 600 }}>{n.appearances}</td>
                        <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>{n.best_rank}</td>
                        <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>{n.avg_rank}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function StatCard(props: { icon: string; cls: string; value: string; label: string }) {
  return (
    <div className="stat-card">
      <div className={`stat-icon ${props.cls}`}>{props.icon}</div>
      <div className="stat-value">{props.value}</div>
      <div className="stat-label">{props.label}</div>
    </div>
  );
}
