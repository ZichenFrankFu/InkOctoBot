import React, { useEffect, useState, useMemo, useCallback } from "react";
import { apiGet } from "../api/client";

type Platform = "" | "qidian" | "fanqie";
interface OverviewData { novel_count: number; rank_list_count: number; snapshot_count: number; chapter_count: number; recent_snapshots: any[]; platform_breakdown: { platform: string; count: number }[]; categories: { main_category: string; count: number }[]; rank_families: any[]; }
interface TopNovel { novel_uid: number; title: string; author: string; platform: string; main_category: string; appearances: number; best_rank: number; avg_rank: number; }
interface TagStat { tag_name: string; novel_count: number; }
interface NovelDetail { novel: any; titles: any[]; tags: any[]; rank_history: any[]; chapters: any[]; }

export default function DashboardPage() {
  const [platform, setPlatform] = useState<Platform>("");
  const [overview, setOverview] = useState<OverviewData | null>(null);
  const [topNovels, setTopNovels] = useState<TopNovel[]>([]);
  const [tagStats, setTagStats] = useState<TagStat[]>([]);
  const [loading, setLoading] = useState(true);
  const [showPanel, setShowPanel] = useState(false);
  const [novelDetail, setNovelDetail] = useState<NovelDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [readingChapter, setReadingChapter] = useState<any>(null);
  const [chapterContent, setChapterContent] = useState("");
  const [chapterLoading, setChapterLoading] = useState(false);

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

  const openNovelDetail = useCallback(async (uid: number) => {
    setShowPanel(true); setDetailLoading(true); setNovelDetail(null); setReadingChapter(null); setChapterContent("");
    try { setNovelDetail(await apiGet<NovelDetail>(`/api/db/novel/${uid}`)); } catch (e) { console.error(e); }
    finally { setDetailLoading(false); }
  }, []);
  const closePanel = () => { setShowPanel(false); setReadingChapter(null); setChapterContent(""); };
  const openChapter = async (ch: any, uid: number) => {
    setChapterLoading(true); setReadingChapter(ch);
    try { const r = await apiGet<any>(`/api/db/novel/${uid}/chapter/${ch.chapter_num}`); setChapterContent(r.chapter_content || "(无内容)"); }
    catch { setChapterContent("加载失败"); } finally { setChapterLoading(false); }
  };

  return (
    <div className="page-container">
      <div className="page-header"><h2>首页</h2><p>市场数据概览与创作进度</p></div>

      {/* Author Creation Placeholder */}
      <div className="card" style={{ marginBottom: 24 }}>
        <div className="card-header"><h3>📝 我的创作</h3><p>创作进度与 AI 辅助统计</p></div>
        <div className="card-body">
          <div className="stats-grid" style={{ marginBottom: 0 }}>
            {[{ icon: "✍️", cls: "gold", label: "累计创作字数" }, { icon: "📄", cls: "jade", label: "已完成章节" }, { icon: "🤖", cls: "indigo", label: "AI 辅助率" }, { icon: "💡", cls: "red", label: "待处理建议" }].map(s => (
              <div className="stat-card" style={{ opacity: 0.55 }} key={s.label}><div className={`stat-icon ${s.cls}`}>{s.icon}</div><div className="stat-value" style={{ color: "var(--ink-300)" }}>—</div><div className="stat-label">{s.label}</div></div>
            ))}
          </div>
          <div className="placeholder-banner"><span>🚧</span><div><strong>创作模块开发中</strong><p>此区域将展示你的创作进度、AI 辅助率统计、Editor Agent 优化建议等信息。</p></div></div>
        </div>
      </div>

      {/* Market Data */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
        <h3 className="font-serif" style={{ fontSize: 18, color: "var(--ink-800)" }}>📊 市场数据概览</h3>
        <div className="platform-tabs">
          <button className={`platform-tab${platform === "" ? " active" : ""}`} onClick={() => setPlatform("")}>全部</button>
          <button className={`platform-tab${platform === "qidian" ? " active" : ""}`} onClick={() => setPlatform("qidian")}>起点</button>
          <button className={`platform-tab${platform === "fanqie" ? " active" : ""}`} onClick={() => setPlatform("fanqie")}>番茄</button>
        </div>
      </div>

      {loading ? <div className="loading"><div className="loading-spinner" /> 加载中…</div> : !overview ? <div className="empty-state"><div className="empty-icon">📭</div><h4>暂无数据</h4></div> : (
        <>
          <div className="stats-grid">
            {[{ i: "📚", c: "red", v: overview.novel_count, l: "收录小说" }, { i: "🏆", c: "gold", v: overview.rank_list_count, l: "榜单类型" }, { i: "📸", c: "jade", v: overview.snapshot_count, l: "榜单快照" }, { i: "📖", c: "indigo", v: overview.chapter_count, l: "采集章节" }].map(s => (
              <div className="stat-card" key={s.l}><div className={`stat-icon ${s.c}`}>{s.i}</div><div className="stat-value">{s.v.toLocaleString()}</div><div className="stat-label">{s.l}</div></div>
            ))}
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20, marginBottom: 24 }}>
            <div className="card"><div className="card-header"><h3>题材分布</h3></div><div className="card-body"><div className="bar-chart">{overview.categories.slice(0, 12).map(c => <div className="bar-row" key={c.main_category || "nil"}><div className="bar-label">{c.main_category || "未分类"}</div><div className="bar-track"><div className="bar-fill red" style={{ width: `${Math.max(4, (c.count / maxCatCount) * 100)}%` }}>{c.count}</div></div></div>)}</div></div></div>
            <div className="card"><div className="card-header"><h3>热门标签</h3></div><div className="card-body"><div className="bar-chart">{tagStats.slice(0, 12).map(t => <div className="bar-row" key={t.tag_name}><div className="bar-label">{t.tag_name}</div><div className="bar-track"><div className="bar-fill indigo" style={{ width: `${Math.max(4, (t.novel_count / maxTagCount) * 100)}%` }}>{t.novel_count}</div></div></div>)}</div></div></div>
          </div>

          {platform === "" && overview.platform_breakdown.length > 0 && (
            <div className="card" style={{ marginBottom: 24 }}><div className="card-header"><h3>平台对比</h3></div><div className="card-body"><div style={{ display: "flex", gap: 24 }}>{overview.platform_breakdown.map(pb => <div key={pb.platform} style={{ flex: 1, padding: "16px 20px", background: "var(--paper-warm)", borderRadius: "var(--radius-sm)", textAlign: "center" }}><div style={{ fontSize: 12, color: "var(--ink-400)", marginBottom: 4 }}>{pl(pb.platform)}</div><div style={{ fontFamily: "var(--font-serif)", fontSize: 24, fontWeight: 700 }}>{pb.count.toLocaleString()}</div><div style={{ fontSize: 11, color: "var(--ink-400)" }}>部小说</div></div>)}</div></div></div>
          )}

          {/* Top Novels — CLICKABLE */}
          <div className="card"><div className="card-header"><h3>高频上榜作品</h3><p>点击书名查看详情</p></div>
            <div style={{ maxHeight: 440, overflowY: "auto" }}>
              <table className="data-table"><thead><tr><th style={{ width: 50 }}>#</th><th>书名</th><th>作者</th><th>平台</th><th>分类</th><th style={{ textAlign: "right" }}>上榜</th><th style={{ textAlign: "right" }}>最佳</th><th style={{ textAlign: "right" }}>均排</th></tr></thead>
                <tbody>{topNovels.map((n, i) => (
                  <tr key={n.novel_uid} className="clickable" onClick={() => openNovelDetail(n.novel_uid)}>
                    <td><span className={`rank-badge ${i < 3 ? "top3" : i < 10 ? "top10" : "normal"}`}>{i + 1}</span></td>
                    <td style={{ fontWeight: 500, color: "var(--accent)", cursor: "pointer" }}>{n.title || "(未知)"}</td>
                    <td className="text-muted">{n.author || "-"}</td>
                    <td><span className={`tag ${n.platform}`}>{pl(n.platform)}</span></td>
                    <td>{n.main_category && <span className="tag category">{n.main_category}</span>}</td>
                    <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 600 }}>{n.appearances}</td>
                    <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>{n.best_rank}</td>
                    <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>{n.avg_rank}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {/* Novel Detail Side Panel */}
      {showPanel && <>
        <div className="side-panel-overlay" onClick={closePanel} />
        <div className="side-panel">
          <div className="side-panel-header"><h3>{detailLoading ? "加载中…" : "作品详情"}</h3><button className="side-panel-close" onClick={closePanel}>✕</button></div>
          <div className="side-panel-body">
            {detailLoading ? <div className="loading"><div className="loading-spinner" /> 加载中…</div>
              : novelDetail ? (readingChapter
                ? <div>
                    <button onClick={() => { setReadingChapter(null); setChapterContent(""); }} style={{ background: "none", border: "none", color: "var(--accent)", cursor: "pointer", fontSize: 13, marginBottom: 12, fontFamily: "var(--font-sans)" }}>← 返回详情</button>
                    <h4 style={{ fontFamily: "var(--font-serif)", fontSize: 16, marginBottom: 12 }}>{readingChapter.chapter_title}</h4>
                    {chapterLoading ? <div className="loading"><div className="loading-spinner" /></div> : <div style={{ fontFamily: "var(--font-serif)", fontSize: 15, lineHeight: 2, color: "var(--ink-700)", whiteSpace: "pre-wrap" }}>{chapterContent}</div>}
                  </div>
                : <NovelPanel detail={novelDetail} onChapter={(ch) => openChapter(ch, novelDetail.novel.novel_uid)} />
              ) : <div className="text-muted">加载失败</div>
            }
          </div>
        </div>
      </>}
    </div>
  );
}

function NovelPanel({ detail, onChapter }: { detail: NovelDetail; onChapter: (ch: any) => void }) {
  const { novel, titles, tags, rank_history, chapters } = detail;
  const t = titles.find((x: any) => x.is_primary)?.title || novel.author || "(未知)";
  const pl = (p: string) => p === "qidian" ? "起点" : p === "fanqie" ? "番茄" : p;
  return <>
    <div style={{ marginBottom: 20 }}>
      <h3 style={{ fontFamily: "var(--font-serif)", fontSize: 20, fontWeight: 700, marginBottom: 6 }}>{t}</h3>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <span className={`tag ${novel.platform}`}>{pl(novel.platform)}</span>
        {novel.main_category && <span className="tag category">{novel.main_category}</span>}
        {novel.status && <span className={`tag ${novel.status === "completed" ? "status-completed" : "status-ongoing"}`}>{novel.status === "completed" ? "已完本" : "连载中"}</span>}
      </div>
    </div>
    <div className="detail-section"><h4>基本信息</h4>
      <div className="detail-grid">
        <span className="label">作者</span><span className="value">{novel.author || "—"}</span>
        <span className="label">总字数</span><span className="value">{novel.total_words ? `${(novel.total_words / 10000).toFixed(1)} 万字` : "—"}</span>
        <span className="label">首次采集</span><span className="value">{novel.created_date || "—"}</span>
        <span className="label">最近出现</span><span className="value">{novel.last_seen_date || "—"}</span>
        {novel.url && <><span className="label">链接</span><span className="value"><a href={novel.url} target="_blank" rel="noopener noreferrer" style={{ color: "var(--accent)", textDecoration: "underline", fontSize: 12 }}>查看原文 ↗</a></span></>}
      </div>
    </div>
    {novel.intro && <div className="detail-section"><h4>简介</h4><p style={{ fontSize: 13, lineHeight: 1.7, color: "var(--ink-600)", whiteSpace: "pre-wrap" }}>{novel.intro}</p></div>}
    {tags.length > 0 && <div className="detail-section"><h4>标签</h4><div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>{tags.map((tg: any) => <span key={tg.tag_id} className="tag category">{tg.tag_name}</span>)}</div></div>}
    {rank_history.length > 0 && <div className="detail-section"><h4>排名历史（最近 20 条）</h4><div style={{ maxHeight: 240, overflowY: "auto" }}><table className="data-table"><thead><tr><th>日期</th><th>榜单</th><th style={{ textAlign: "right" }}>排名</th></tr></thead><tbody>{rank_history.slice(0, 20).map((h: any, i: number) => <tr key={i}><td className="font-mono" style={{ fontSize: 12 }}>{h.snapshot_date}</td><td style={{ fontSize: 12 }}>{h.rank_family}{h.rank_sub_cat && ` · ${h.rank_sub_cat}`}</td><td style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 600 }}>{h.rank}</td></tr>)}</tbody></table></div></div>}
    {chapters.length > 0 && <div className="detail-section"><h4>开篇章节</h4>{chapters.map((ch: any) => <div key={ch.chapter_num} onClick={() => onChapter(ch)} style={{ padding: "8px 12px", borderRadius: "var(--radius-sm)", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "space-between", borderBottom: "1px solid var(--ink-50)", transition: "background 0.15s" }} onMouseEnter={e => (e.currentTarget.style.background = "var(--paper-warm)")} onMouseLeave={e => (e.currentTarget.style.background = "transparent")}><span style={{ fontSize: 13 }}><span className="font-mono" style={{ color: "var(--ink-400)", marginRight: 8 }}>第{ch.chapter_num}章</span>{ch.chapter_title}</span><span style={{ fontSize: 11, color: "var(--ink-300)" }}>{ch.word_count ? `${ch.word_count}字` : ""}</span></div>)}</div>}
  </>;
}