import React, { useEffect, useState, useMemo, useCallback } from "react";
import { apiGet } from "../api/client";
import type { Novel, RankList, ReferenceWork, Volume } from "../api/types";

/* ── local response types ── */
interface Overview {
  novel_count: number;
  rank_list_count: number;
  snapshot_count: number;
  chapter_count: number;
  platform_breakdown: { platform: string; count: number }[];
  categories: { main_category: string; count: number }[];
}

interface HighFreqNovel {
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

interface HotTag {
  tag_name: string;
  novel_count: number;
}

interface NovelDetail {
  novel: Novel & Record<string, any>;
  titles: { title_id: number; title: string; is_primary: boolean }[];
  tags: { tag_id: number; tag_name: string }[];
  rank_history: { snapshot_date: string; rank_family: string; rank_sub_cat: string; rank: number }[];
  chapters: { chapter_num: number; chapter_title: string; word_count: number }[];
}

interface ProjectStats {
  id: string;
  name: string;
  genre?: string;
  totalWords: number;
  chapterCount: number;
  lastOpenedAt?: number;
}

type PlatformFilter = "" | "qidian" | "fanqie";

const platformLabel = (p: string) =>
  p === "qidian" ? "起点" : p === "fanqie" ? "番茄" : p || "未知";

const wc = (t: string) => (t ? t.replace(/[\s\p{P}]/gu, "").length : 0);

export default function DashboardPage({ projects, onNavigate }: { projects: { id: string; name: string; genre?: string }[]; onNavigate: (tab: string) => void }) {
  const [platform, setPlatform] = useState<PlatformFilter>("");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [highFreq, setHighFreq] = useState<HighFreqNovel[]>([]);
  const [hotTags, setHotTags] = useState<HotTag[]>([]);
  const [loading, setLoading] = useState(true);

  /* side panel */
  const [panelOpen, setPanelOpen] = useState(false);
  const [panelData, setPanelData] = useState<NovelDetail | null>(null);
  const [panelLoading, setPanelLoading] = useState(false);

  /* project stats */
  const [projectStats, setProjectStats] = useState<ProjectStats[]>([]);
  const [refSummary, setRefSummary] = useState<{ total: number; topGenres: string[]; avgRating: number }>({ total: 0, topGenres: [], avgRating: 0 });

  /* ── fetch project stats ── */
  useEffect(() => {
    // Load editor data for each project to get word count & chapter count
    const loadStats = async () => {
      const stats: ProjectStats[] = [];
      for (const proj of projects) {
        try {
          const data = await apiGet<{ volumes: Volume[] }>(`/api/data/editor?project_id=${proj.id}`);
          const vols = data.volumes || [];
          let totalWords = 0;
          let chapterCount = 0;
          for (const v of vols) {
            for (const ch of v.chapters) {
              chapterCount++;
              totalWords += ch.word_count || wc(ch.content || "");
            }
          }
          stats.push({ ...proj, totalWords, chapterCount });
        } catch {
          stats.push({ ...proj, totalWords: 0, chapterCount: 0 });
        }
      }
      setProjectStats(stats);
    };

    // Load reference library summary
    const loadRefSummary = async () => {
      try {
        const r = await apiGet<{ items: ReferenceWork[]; total: number }>("/api/references/works");
        const items = r.items || [];
        const genreMap: Record<string, number> = {};
        let ratingSum = 0;
        let ratingCount = 0;
        for (const w of items) {
          if (w.genre) genreMap[w.genre] = (genreMap[w.genre] || 0) + 1;
          if (w.user_rating) { ratingSum += w.user_rating; ratingCount++; }
        }
        const topGenres = Object.entries(genreMap)
          .sort((a, b) => b[1] - a[1])
          .slice(0, 5)
          .map(([g]) => g);
        setRefSummary({
          total: r.total || items.length,
          topGenres,
          avgRating: ratingCount > 0 ? ratingSum / ratingCount : 0,
        });
      } catch {
        // reference db might not exist yet
      }
    };

    if (projects.length > 0) loadStats();
    loadRefSummary();
  }, [projects]);

  /* ── fetch market data on platform change ── */
  useEffect(() => {
    setLoading(true);
    const qs = platform ? `?platform=${platform}` : "";
    Promise.all([
      apiGet<Overview>(`/api/db/overview${qs}`),
      apiGet<{ rows: HighFreqNovel[] }>(`/api/db/top_novels${qs}`),
      apiGet<{ rows: HotTag[] }>(`/api/db/tag_stats${qs}`),
    ])
      .then(([ov, hf, tg]) => {
        setOverview(ov);
        setHighFreq(Array.isArray(hf.rows) ? hf.rows : []);
        setHotTags(Array.isArray(tg.rows) ? tg.rows : []);
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [platform]);

  /* ── derived ── */
  const maxCategory = useMemo(
    () => Math.max(1, ...(overview?.categories?.map((c) => c.count) ?? [1])),
    [overview],
  );
  const maxTag = useMemo(
    () => Math.max(1, ...(hotTags.map((t) => t.novel_count) ?? [1])),
    [hotTags],
  );

  const totalWords = useMemo(() => projectStats.reduce((s, p) => s + p.totalWords, 0), [projectStats]);
  const totalChapters = useMemo(() => projectStats.reduce((s, p) => s + p.chapterCount, 0), [projectStats]);

  /* ── open novel detail ── */
  const openDetail = useCallback(async (uid: number) => {
    setPanelOpen(true);
    setPanelLoading(true);
    setPanelData(null);
    try {
      const d = await apiGet<NovelDetail>(`/api/db/novel/${uid}`);
      setPanelData(d);
    } catch (e) {
      console.error(e);
    } finally {
      setPanelLoading(false);
    }
  }, []);

  /* ── bar color palette ── */
  const barColors = ["red", "indigo", "gold", "jade", "purple"];
  const tagBarColors = ["indigo", "purple", "jade", "gold", "red"];

  return (
    <div className="page-container">
      {/* Header */}
      <div className="page-header" style={{ marginBottom: 20 }}>
        <div className="page-header-row">
          <div>
            <h2>首页</h2>
            <p>创作概览与市场数据速览</p>
          </div>
          <div className="tab-bar">
            {([["", "全部"], ["qidian", "起点"], ["fanqie", "番茄"]] as const).map(
              ([val, label]) => (
                <button
                  key={val}
                  className={`tab-item${platform === val ? " active" : ""}`}
                  onClick={() => setPlatform(val as PlatformFilter)}
                >
                  {label}
                </button>
              ),
            )}
          </div>
        </div>
      </div>

      {/* ══ 我的创作 ══ */}
      <div className="card" style={{ marginBottom: 24 }}>
        <div className="card-header">
          <h3>我的创作</h3>
        </div>
        <div className="card-body">
          {projects.length === 0 ? (
            <div className="empty-state">
              <p>暂无项目，去「项目管理」创建一个吧</p>
            </div>
          ) : (
            <>
              {/* Summary stats row */}
              <div className="stats-grid" style={{ marginBottom: 20 }}>
                <div className="stat-card">
                  <div className="stat-value">{totalWords.toLocaleString()}</div>
                  <div className="stat-label">创作总字数</div>
                </div>
                <div className="stat-card">
                  <div className="stat-value">{totalChapters}</div>
                  <div className="stat-label">总章节数</div>
                </div>
                <div className="stat-card">
                  <div className="stat-value">{projects.length}</div>
                  <div className="stat-label">进行中项目</div>
                </div>
                <div className="stat-card">
                  <div className="stat-value">{refSummary.total}</div>
                  <div className="stat-label">参考作品</div>
                </div>
              </div>

              {/* Recent projects */}
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 14, marginBottom: 16 }}>
                {projectStats.map((proj) => (
                  <div
                    key={proj.id}
                    className="card"
                    style={{ cursor: "pointer", border: "1px solid var(--border)", background: "var(--bg-surface-2)" }}
                    onClick={() => onNavigate("editor")}
                  >
                    <div style={{ padding: "16px 18px" }}>
                      <div style={{ fontSize: 15, fontWeight: 600, color: "var(--text-primary)", marginBottom: 6 }}>
                        {proj.name}
                      </div>
                      <div className="text-xs text-muted" style={{ marginBottom: 10 }}>
                        {proj.genre || "未设置类型"}
                      </div>
                      <div style={{ display: "flex", gap: 16, fontSize: 12 }}>
                        <span style={{ color: "var(--jade)" }}>
                          {proj.totalWords.toLocaleString()} 字
                        </span>
                        <span style={{ color: "var(--indigo)" }}>
                          {proj.chapterCount} 章
                        </span>
                      </div>
                      {/* AI todo placeholder */}
                      <div style={{ marginTop: 10, padding: "6px 10px", background: "var(--accent-subtle)", borderRadius: "var(--radius-sm)", fontSize: 11, color: "var(--accent)" }}>
                        AI 待办：继续创作下一章节
                      </div>
                    </div>
                  </div>
                ))}
              </div>

              {/* Reference library preferences */}
              {refSummary.total > 0 && (
                <div style={{ padding: "12px 16px", background: "var(--bg-surface-2)", borderRadius: "var(--radius-md)", border: "1px solid var(--border)" }}>
                  <div style={{ fontSize: 12, fontWeight: 600, color: "var(--text-secondary)", marginBottom: 6 }}>
                    参考作品库 · 审美偏好
                  </div>
                  <div className="flex gap-8 items-center" style={{ flexWrap: "wrap" }}>
                    {refSummary.topGenres.length > 0 && (
                      <div className="flex gap-4" style={{ flexWrap: "wrap" }}>
                        {refSummary.topGenres.map(g => (
                          <span key={g} className="tag category">{g}</span>
                        ))}
                      </div>
                    )}
                    {refSummary.avgRating > 0 && (
                      <span style={{ fontSize: 12, color: "var(--gold)" }}>
                        平均评分 {"★".repeat(Math.round(refSummary.avgRating))}{"☆".repeat(5 - Math.round(refSummary.avgRating))} ({refSummary.avgRating.toFixed(1)})
                      </span>
                    )}
                    <span className="text-xs text-muted">
                      共 {refSummary.total} 部作品
                    </span>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {loading ? (
        <div className="loading">
          <div className="loading-spinner" />
          加载中...
        </div>
      ) : !overview ? (
        <div className="empty-state">
          <div className="empty-icon">📭</div>
          <h4>暂无数据</h4>
          <p>数据库中尚无市场数据，请先运行数据采集。</p>
        </div>
      ) : (
        <>
          {/* Stats Grid */}
          <div className="stats-grid">
            {[
              { icon: "📚", color: "red", value: overview.novel_count, label: "收录小说" },
              { icon: "🏆", color: "gold", value: overview.rank_list_count, label: "榜单类型" },
              { icon: "📸", color: "jade", value: overview.snapshot_count, label: "榜单快照" },
              { icon: "📖", color: "indigo", value: overview.chapter_count, label: "采集章节" },
            ].map((s) => (
              <div className="stat-card" key={s.label}>
                <div className={`stat-icon ${s.color}`}>{s.icon}</div>
                <div className="stat-value">{s.value.toLocaleString()}</div>
                <div className="stat-label">{s.label}</div>
              </div>
            ))}
          </div>

          {/* Charts Row */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20, marginBottom: 24 }}>
            <div className="card">
              <div className="card-header"><h3>题材分布</h3></div>
              <div className="card-body">
                <div className="bar-chart">
                  {(overview.categories || []).slice(0, 12).map((c, idx) => (
                    <div className="bar-row" key={c.main_category || "_empty"}>
                      <div className="bar-label">{c.main_category || "未分类"}</div>
                      <div className="bar-track">
                        <div className={`bar-fill ${barColors[idx % barColors.length]}`} style={{ width: `${Math.max(4, (c.count / maxCategory) * 100)}%` }}>
                          {c.count}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
            <div className="card">
              <div className="card-header"><h3>热门标签</h3></div>
              <div className="card-body">
                <div className="bar-chart">
                  {hotTags.slice(0, 12).map((t, idx) => (
                    <div className="bar-row" key={t.tag_name}>
                      <div className="bar-label">{t.tag_name}</div>
                      <div className="bar-track">
                        <div className={`bar-fill ${tagBarColors[idx % tagBarColors.length]}`} style={{ width: `${Math.max(4, (t.novel_count / maxTag) * 100)}%` }}>
                          {t.novel_count}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>

          {/* Platform Breakdown */}
          {platform === "" && overview.platform_breakdown && overview.platform_breakdown.length > 0 && (
            <div className="card" style={{ marginBottom: 24 }}>
              <div className="card-header"><h3>平台对比</h3></div>
              <div className="card-body">
                <div style={{ display: "flex", gap: 16 }}>
                  {overview.platform_breakdown.map((pb) => (
                    <div key={pb.platform} style={{ flex: 1, padding: "20px 24px", background: "var(--bg-surface)", borderRadius: "var(--radius-md)", border: "1px solid var(--border)", textAlign: "center" }}>
                      <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginBottom: 6 }}>{platformLabel(pb.platform)}</div>
                      <div style={{ fontFamily: "var(--font-mono)", fontSize: 28, fontWeight: 700, color: "var(--text-primary)" }}>{pb.count.toLocaleString()}</div>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}

          {/* High Frequency Novels Table */}
          <div className="card">
            <div className="card-header">
              <div>
                <h3>高频上榜作品</h3>
                <p style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 2 }}>点击行查看作品详情</p>
              </div>
            </div>
            <div style={{ maxHeight: 480, overflowY: "auto" }}>
              {highFreq.length === 0 ? (
                <div className="empty-state"><p>暂无高频上榜数据</p></div>
              ) : (
                <table className="data-table">
                  <thead>
                    <tr>
                      <th style={{ width: 50 }}>#</th>
                      <th>书名</th>
                      <th>作者</th>
                      <th>平台</th>
                      <th>分类</th>
                      <th style={{ textAlign: "right" }}>上榜</th>
                      <th style={{ textAlign: "right" }}>最佳</th>
                      <th style={{ textAlign: "right" }}>均排</th>
                    </tr>
                  </thead>
                  <tbody>
                    {highFreq.map((n, i) => (
                      <tr key={n.novel_uid} className="clickable" onClick={() => openDetail(n.novel_uid)}>
                        <td><span className={`rank-badge ${i < 3 ? "top3" : i < 10 ? "top10" : "normal"}`}>{i + 1}</span></td>
                        <td style={{ fontWeight: 500, color: "var(--accent)" }}>{n.title || "(未知)"}</td>
                        <td className="text-muted">{n.author || "-"}</td>
                        <td><span className={`tag ${n.platform}`}>{platformLabel(n.platform)}</span></td>
                        <td>{n.main_category && <span className="tag category">{n.main_category}</span>}</td>
                        <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 600 }}>{n.appearances}</td>
                        <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>{n.best_rank}</td>
                        <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>{typeof n.avg_rank === "number" ? n.avg_rank.toFixed(1) : n.avg_rank}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
        </>
      )}

      {/* Side Panel */}
      {panelOpen && (
        <>
          <div className="side-panel-overlay" onClick={() => setPanelOpen(false)} />
          <div className="side-panel">
            <div className="side-panel-header">
              <h3>{panelLoading ? "加载中..." : "作品详情"}</h3>
              <button className="side-panel-close" onClick={() => setPanelOpen(false)}>✕</button>
            </div>
            <div className="side-panel-body">
              {panelLoading ? (
                <div className="loading"><div className="loading-spinner" /></div>
              ) : panelData ? (
                <NovelPanel detail={panelData} />
              ) : (
                <div className="text-muted">加载失败</div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

/* ── Novel Detail Panel Component ── */
function NovelPanel({ detail }: { detail: NovelDetail }) {
  const { novel: n, titles, tags, rank_history: rh, chapters: chs } = detail;
  const primaryTitle = titles.find((x) => x.is_primary)?.title || (titles.length > 0 ? titles[0].title : "(未知)");

  return (
    <>
      <div style={{ marginBottom: 22 }}>
        <h3 style={{ fontFamily: "var(--font-serif)", fontSize: 20, fontWeight: 700, color: "var(--text-primary)", marginBottom: 8 }}>{primaryTitle}</h3>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <span className={`tag ${n.platform}`}>{platformLabel(n.platform)}</span>
          {n.main_category && <span className="tag category">{n.main_category}</span>}
          {n.status && <span className={`tag ${n.status === "completed" ? "status-completed" : "status-ongoing"}`}>{n.status === "completed" ? "已完本" : "连载中"}</span>}
        </div>
      </div>
      <div className="detail-section">
        <h4>基本信息</h4>
        <div className="detail-grid">
          <span className="label">作者</span><span className="value">{n.author || "—"}</span>
          <span className="label">总字数</span><span className="value">{n.total_words ? `${(n.total_words / 10000).toFixed(1)} 万字` : "—"}</span>
          <span className="label">首次采集</span><span className="value">{n.created_date || "—"}</span>
          <span className="label">最近出现</span><span className="value">{n.last_seen_date || "—"}</span>
          {n.url && (<><span className="label">链接</span><span className="value"><a href={n.url} target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)", textDecoration: "underline", fontSize: 12 }}>查看原文 ↗</a></span></>)}
        </div>
      </div>
      {n.intro && (<div className="detail-section"><h4>简介</h4><p style={{ fontSize: 13, lineHeight: 1.7, color: "var(--text-secondary)", whiteSpace: "pre-wrap" }}>{n.intro}</p></div>)}
      {tags.length > 0 && (<div className="detail-section"><h4>标签</h4><div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>{tags.map((tg) => <span key={tg.tag_id} className="tag category">{tg.tag_name}</span>)}</div></div>)}
      {rh && rh.length > 0 && (
        <div className="detail-section">
          <h4>排名历史</h4>
          <div style={{ maxHeight: 220, overflowY: "auto" }}>
            <table className="data-table">
              <thead><tr><th>日期</th><th>榜单</th><th style={{ textAlign: "right" }}>排名</th></tr></thead>
              <tbody>
                {rh.slice(0, 30).map((h, i) => (
                  <tr key={i}>
                    <td className="font-mono" style={{ fontSize: 12 }}>{h.snapshot_date}</td>
                    <td style={{ fontSize: 12 }}>{h.rank_family}{h.rank_sub_cat && ` · ${h.rank_sub_cat}`}</td>
                    <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 600 }}>{h.rank}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {chs && chs.length > 0 && (
        <div className="detail-section">
          <h4>已采集章节（{chs.length} 章）</h4>
          <div style={{ fontSize: 12, color: "var(--text-tertiary)", lineHeight: 1.8 }}>
            {chs.map((ch) => (
              <div key={ch.chapter_num} style={{ padding: "4px 0", borderBottom: "1px solid var(--border-subtle)", display: "flex", justifyContent: "space-between" }}>
                <span><span className="font-mono" style={{ color: "var(--text-disabled)", marginRight: 8 }}>第{ch.chapter_num}章</span>{ch.chapter_title}</span>
                <span style={{ color: "var(--text-disabled)" }}>{ch.word_count ? `${ch.word_count}字` : ""}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
