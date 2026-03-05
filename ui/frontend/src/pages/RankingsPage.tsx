import React, { useEffect, useState, useCallback } from "react";
import { apiGet } from "../api/client";

type Platform = "" | "qidian" | "fanqie";

interface RankList {
  rank_list_id: number;
  platform: string;
  rank_family: string;
  rank_sub_cat: string;
  source_url: string;
}

interface Snapshot {
  snapshot_id: number;
  rank_list_id: number;
  snapshot_date: string;
  item_count: number;
  platform: string;
  rank_family: string;
  rank_sub_cat: string;
}

interface Entry {
  snapshot_id: number;
  novel_uid: number;
  rank: number;
  total_recommend: number | null;
  reading_count: number | null;
  title: string;
  author: string;
  platform: string;
  main_category: string;
  status: string;
  total_words: number;
  url: string;
}

interface NovelDetail {
  novel: any;
  titles: any[];
  tags: any[];
  rank_history: any[];
  chapters: any[];
}

type DrillStep = "lists" | "snapshots" | "entries";

export default function RankingsPage() {
  const [platform, setPlatform] = useState<Platform>("");
  const [step, setStep] = useState<DrillStep>("lists");

  // Data
  const [rankLists, setRankLists] = useState<RankList[]>([]);
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [loading, setLoading] = useState(false);

  // Selection
  const [selectedList, setSelectedList] = useState<RankList | null>(null);
  const [selectedSnapshot, setSelectedSnapshot] = useState<Snapshot | null>(null);

  // Novel detail panel
  const [novelDetail, setNovelDetail] = useState<NovelDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [showPanel, setShowPanel] = useState(false);

  const platformLabel = (p: string) =>
    p === "qidian" ? "起点" : p === "fanqie" ? "番茄" : p;

  // Load rank lists
  useEffect(() => {
    setLoading(true);
    const url = `/api/db/rank_lists${platform ? `?platform=${platform}` : ""}`;
    apiGet<{ rows: RankList[] }>(url)
      .then((res) => setRankLists(res.rows))
      .catch(console.error)
      .finally(() => setLoading(false));
    // Reset drill state on platform change
    setStep("lists");
    setSelectedList(null);
    setSelectedSnapshot(null);
    setSnapshots([]);
    setEntries([]);
  }, [platform]);

  // Select a rank list → load snapshots
  const selectList = useCallback(async (list: RankList) => {
    setSelectedList(list);
    setSelectedSnapshot(null);
    setEntries([]);
    setStep("snapshots");
    setLoading(true);
    try {
      const res = await apiGet<{ rows: Snapshot[] }>(
        `/api/db/snapshots?rank_list_id=${list.rank_list_id}`
      );
      setSnapshots(res.rows);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  // Select a snapshot → load entries
  const selectSnapshot = useCallback(async (snap: Snapshot) => {
    setSelectedSnapshot(snap);
    setStep("entries");
    setLoading(true);
    try {
      const res = await apiGet<{ rows: Entry[] }>(
        `/api/db/entries?snapshot_id=${snap.snapshot_id}&limit=200`
      );
      setEntries(res.rows);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  // Open novel detail panel
  const openNovelDetail = useCallback(async (novel_uid: number) => {
    setShowPanel(true);
    setDetailLoading(true);
    setNovelDetail(null);
    try {
      const res = await apiGet<NovelDetail>(`/api/db/novel/${novel_uid}`);
      setNovelDetail(res);
    } catch (e) {
      console.error(e);
    } finally {
      setDetailLoading(false);
    }
  }, []);

  // Navigate back
  const goToLists = () => {
    setStep("lists");
    setSelectedList(null);
    setSelectedSnapshot(null);
    setSnapshots([]);
    setEntries([]);
  };

  const goToSnapshots = () => {
    setStep("snapshots");
    setSelectedSnapshot(null);
    setEntries([]);
  };

  // Group rank lists by family for better display
  const groupedLists = React.useMemo(() => {
    const groups: Record<string, RankList[]> = {};
    for (const rl of rankLists) {
      const key = `${rl.platform}|${rl.rank_family}`;
      if (!groups[key]) groups[key] = [];
      groups[key].push(rl);
    }
    return groups;
  }, [rankLists]);

  return (
    <div className="page-container">
      <div className="page-header">
        <h2>榜单浏览</h2>
        <p>逐级浏览各平台榜单数据 — 榜单 → 日期 → 排名详情</p>
      </div>

      {/* Platform Tabs */}
      <div className="platform-tabs">
        <button className={`platform-tab${platform === "" ? " active" : ""}`} onClick={() => setPlatform("")}>全部平台</button>
        <button className={`platform-tab${platform === "qidian" ? " active" : ""}`} onClick={() => setPlatform("qidian")}>起点中文网</button>
        <button className={`platform-tab${platform === "fanqie" ? " active" : ""}`} onClick={() => setPlatform("fanqie")}>番茄小说</button>
      </div>

      {/* Breadcrumb */}
      <div className="breadcrumb">
        <div className={`breadcrumb-item${step === "lists" ? " active" : ""}`}>
          {step === "lists" ? (
            <span>📋 选择榜单</span>
          ) : (
            <button onClick={goToLists}>📋 榜单列表</button>
          )}
        </div>
        {(step === "snapshots" || step === "entries") && selectedList && (
          <>
            <span className="breadcrumb-sep">›</span>
            <div className={`breadcrumb-item${step === "snapshots" ? " active" : ""}`}>
              {step === "snapshots" ? (
                <span>
                  {platformLabel(selectedList.platform)} · {selectedList.rank_family}
                  {selectedList.rank_sub_cat && ` · ${selectedList.rank_sub_cat}`}
                </span>
              ) : (
                <button onClick={goToSnapshots}>
                  {selectedList.rank_family}
                  {selectedList.rank_sub_cat && ` · ${selectedList.rank_sub_cat}`}
                </button>
              )}
            </div>
          </>
        )}
        {step === "entries" && selectedSnapshot && (
          <>
            <span className="breadcrumb-sep">›</span>
            <div className="breadcrumb-item active">
              <span>📅 {selectedSnapshot.snapshot_date}</span>
            </div>
          </>
        )}
      </div>

      {loading ? (
        <div className="loading">
          <div className="loading-spinner" />
          加载中…
        </div>
      ) : (
        <>
          {/* ── Step 1: Rank Lists ── */}
          {step === "lists" && (
            <>
              {rankLists.length === 0 ? (
                <div className="empty-state">
                  <div className="empty-icon">📭</div>
                  <h4>暂无榜单数据</h4>
                  <p>数据库中还没有采集到任何榜单</p>
                </div>
              ) : (
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))", gap: 16 }}>
                  {Object.entries(groupedLists).map(([key, lists]) => {
                    const [plat, family] = key.split("|");
                    return (
                      <div className="card" key={key}>
                        <div className="card-header">
                          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                            <span className={`tag ${plat}`}>{platformLabel(plat)}</span>
                            <h3>{family}</h3>
                          </div>
                          <p>{lists.length === 1 ? "1 个子榜" : `${lists.length} 个子榜`}</p>
                        </div>
                        <div className="card-body" style={{ padding: "8px 12px" }}>
                          {lists.map((rl) => (
                            <div
                              key={rl.rank_list_id}
                              onClick={() => selectList(rl)}
                              style={{
                                padding: "10px 12px",
                                borderRadius: "var(--radius-sm)",
                                cursor: "pointer",
                                transition: "background 0.15s",
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "space-between",
                              }}
                              onMouseEnter={(e) => (e.currentTarget.style.background = "var(--paper-warm)")}
                              onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
                            >
                              <span style={{ fontSize: 13 }}>
                                {rl.rank_sub_cat || family}
                              </span>
                              <span style={{ fontSize: 18, color: "var(--ink-300)" }}>›</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </>
          )}

          {/* ── Step 2: Snapshots ── */}
          {step === "snapshots" && (
            <div className="card">
              <div className="card-header">
                <h3>选择日期查看榜单</h3>
                <p>共 {snapshots.length} 个快照</p>
              </div>
              <div style={{ maxHeight: 560, overflowY: "auto" }}>
                {snapshots.length === 0 ? (
                  <div className="empty-state">
                    <p>该榜单暂无快照数据</p>
                  </div>
                ) : (
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>日期</th>
                        <th>榜单</th>
                        <th style={{ textAlign: "right" }}>收录作品数</th>
                        <th style={{ width: 48 }}></th>
                      </tr>
                    </thead>
                    <tbody>
                      {snapshots.map((s) => (
                        <tr
                          key={s.snapshot_id}
                          className="clickable"
                          onClick={() => selectSnapshot(s)}
                        >
                          <td style={{ fontFamily: "var(--font-mono)", fontWeight: 500 }}>
                            {s.snapshot_date}
                          </td>
                          <td>
                            {s.rank_family}
                            {s.rank_sub_cat && (
                              <span className="text-muted"> · {s.rank_sub_cat}</span>
                            )}
                          </td>
                          <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>
                            {s.item_count ?? "—"}
                          </td>
                          <td style={{ textAlign: "center", color: "var(--ink-300)", fontSize: 16 }}>
                            ›
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          )}

          {/* ── Step 3: Entries ── */}
          {step === "entries" && (
            <div className="card">
              <div className="card-header">
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                  <div>
                    <h3>
                      {selectedList?.rank_family}
                      {selectedList?.rank_sub_cat && ` · ${selectedList.rank_sub_cat}`}
                    </h3>
                    <p>{selectedSnapshot?.snapshot_date} · 共 {entries.length} 部作品</p>
                  </div>
                  {selectedList && (
                    <span className={`tag ${selectedList.platform}`}>
                      {platformLabel(selectedList.platform)}
                    </span>
                  )}
                </div>
              </div>
              <div style={{ maxHeight: 600, overflowY: "auto" }}>
                {entries.length === 0 ? (
                  <div className="empty-state">
                    <p>该快照暂无条目数据</p>
                  </div>
                ) : (
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th style={{ width: 56 }}>排名</th>
                        <th>书名</th>
                        <th>作者</th>
                        <th>分类</th>
                        {selectedList?.platform === "qidian" && (
                          <th style={{ textAlign: "right" }}>推荐票</th>
                        )}
                        {selectedList?.platform === "fanqie" && (
                          <th style={{ textAlign: "right" }}>在读数</th>
                        )}
                        {!selectedList?.platform && (
                          <th style={{ textAlign: "right" }}>指标</th>
                        )}
                        <th style={{ textAlign: "right" }}>总字数</th>
                      </tr>
                    </thead>
                    <tbody>
                      {entries.map((e) => (
                        <tr
                          key={`${e.snapshot_id}-${e.novel_uid}`}
                          className="clickable"
                          onClick={() => openNovelDetail(e.novel_uid)}
                        >
                          <td>
                            <span
                              className={`rank-badge ${
                                e.rank <= 3 ? "top3" : e.rank <= 10 ? "top10" : "normal"
                              }`}
                            >
                              {e.rank}
                            </span>
                          </td>
                          <td style={{ fontWeight: 500, maxWidth: 280 }} className="truncate">
                            {e.title || `(novel_uid: ${e.novel_uid})`}
                          </td>
                          <td className="text-muted">{e.author || "—"}</td>
                          <td>
                            {e.main_category && (
                              <span className="tag category">{e.main_category}</span>
                            )}
                          </td>
                          <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>
                            {e.total_recommend != null
                              ? e.total_recommend.toLocaleString()
                              : e.reading_count != null
                              ? e.reading_count.toLocaleString()
                              : "—"}
                          </td>
                          <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", color: "var(--ink-400)" }}>
                            {e.total_words
                              ? `${(e.total_words / 10000).toFixed(1)}万`
                              : "—"}
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

      {/* ── Novel Detail Side Panel ── */}
      {showPanel && (
        <>
          <div className="side-panel-overlay" onClick={() => setShowPanel(false)} />
          <div className="side-panel">
            <div className="side-panel-header">
              <h3>{novelDetail?.novel ? "作品详情" : "加载中…"}</h3>
              <button className="side-panel-close" onClick={() => setShowPanel(false)}>
                ✕
              </button>
            </div>
            <div className="side-panel-body">
              {detailLoading ? (
                <div className="loading">
                  <div className="loading-spinner" />
                  加载中…
                </div>
              ) : novelDetail ? (
                <NovelDetailPanel detail={novelDetail} />
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

/* ─────────────────────────────────
   Novel Detail Panel Content
   ───────────────────────────────── */

function NovelDetailPanel({ detail }: { detail: NovelDetail }) {
  const { novel, titles, tags, rank_history, chapters } = detail;

  const primaryTitle = titles.find((t: any) => t.is_primary)?.title || novel.author || "(未知)";

  const platformLabel = (p: string) =>
    p === "qidian" ? "起点" : p === "fanqie" ? "番茄" : p;

  // Reading chapter state
  const [readingChapter, setReadingChapter] = useState<any>(null);
  const [chapterContent, setChapterContent] = useState<string>("");
  const [chapterLoading, setChapterLoading] = useState(false);

  const openChapter = async (ch: any) => {
    setChapterLoading(true);
    setReadingChapter(ch);
    try {
      const res = await apiGet<any>(
        `/api/db/novel/${novel.novel_uid}/chapter/${ch.chapter_num}`
      );
      setChapterContent(res.chapter_content || "(无内容)");
    } catch {
      setChapterContent("加载失败");
    } finally {
      setChapterLoading(false);
    }
  };

  if (readingChapter) {
    return (
      <div>
        <button
          onClick={() => { setReadingChapter(null); setChapterContent(""); }}
          style={{
            background: "none",
            border: "none",
            color: "var(--accent)",
            cursor: "pointer",
            fontSize: 13,
            marginBottom: 12,
            fontFamily: "var(--font-sans)",
          }}
        >
          ← 返回详情
        </button>
        <h4 style={{ fontFamily: "var(--font-serif)", fontSize: 16, marginBottom: 12 }}>
          {readingChapter.chapter_title}
        </h4>
        {chapterLoading ? (
          <div className="loading"><div className="loading-spinner" /> 加载章节…</div>
        ) : (
          <div
            style={{
              fontFamily: "var(--font-serif)",
              fontSize: 15,
              lineHeight: 2,
              color: "var(--ink-700)",
              whiteSpace: "pre-wrap",
            }}
          >
            {chapterContent}
          </div>
        )}
      </div>
    );
  }

  return (
    <>
      {/* Title */}
      <div style={{ marginBottom: 20 }}>
        <h3
          style={{
            fontFamily: "var(--font-serif)",
            fontSize: 20,
            fontWeight: 700,
            color: "var(--ink-900)",
            marginBottom: 6,
          }}
        >
          {primaryTitle}
        </h3>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <span className={`tag ${novel.platform}`}>{platformLabel(novel.platform)}</span>
          {novel.main_category && <span className="tag category">{novel.main_category}</span>}
          {novel.status && (
            <span
              className={`tag ${
                novel.status === "completed" ? "status-completed" : "status-ongoing"
              }`}
            >
              {novel.status === "completed" ? "已完本" : "连载中"}
            </span>
          )}
        </div>
      </div>

      {/* Info grid */}
      <div className="detail-section">
        <h4>基本信息</h4>
        <div className="detail-grid">
          <span className="label">作者</span>
          <span className="value">{novel.author || "—"}</span>
          <span className="label">总字数</span>
          <span className="value">
            {novel.total_words
              ? `${(novel.total_words / 10000).toFixed(1)} 万字`
              : "—"}
          </span>
          <span className="label">首次采集</span>
          <span className="value">{novel.created_date || "—"}</span>
          <span className="label">最近出现</span>
          <span className="value">{novel.last_seen_date || "—"}</span>
          {novel.url && (
            <>
              <span className="label">链接</span>
              <span className="value">
                <a
                  href={novel.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ color: "var(--accent)", textDecoration: "underline", textUnderlineOffset: 2, fontSize: 12 }}
                >
                  查看原文 ↗
                </a>
              </span>
            </>
          )}
        </div>
      </div>

      {/* Intro */}
      {novel.intro && (
        <div className="detail-section">
          <h4>简介</h4>
          <p
            style={{
              fontSize: 13,
              lineHeight: 1.7,
              color: "var(--ink-600)",
              whiteSpace: "pre-wrap",
            }}
          >
            {novel.intro}
          </p>
        </div>
      )}

      {/* Tags */}
      {tags.length > 0 && (
        <div className="detail-section">
          <h4>标签</h4>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {tags.map((t: any) => (
              <span key={t.tag_id} className="tag category">
                {t.tag_name}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Rank history */}
      {rank_history.length > 0 && (
        <div className="detail-section">
          <h4>排名历史（最近 20 条）</h4>
          <div style={{ maxHeight: 240, overflowY: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>日期</th>
                  <th>榜单</th>
                  <th style={{ textAlign: "right" }}>排名</th>
                </tr>
              </thead>
              <tbody>
                {rank_history.slice(0, 20).map((h: any, i: number) => (
                  <tr key={i}>
                    <td className="font-mono" style={{ fontSize: 12 }}>
                      {h.snapshot_date}
                    </td>
                    <td style={{ fontSize: 12 }}>
                      {h.rank_family}
                      {h.rank_sub_cat && ` · ${h.rank_sub_cat}`}
                    </td>
                    <td
                      style={{
                        textAlign: "right",
                        fontFamily: "var(--font-mono)",
                        fontWeight: 600,
                      }}
                    >
                      {h.rank}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Chapters */}
      {chapters.length > 0 && (
        <div className="detail-section">
          <h4>开篇章节</h4>
          <div>
            {chapters.map((ch: any) => (
              <div
                key={ch.chapter_num}
                onClick={() => openChapter(ch)}
                style={{
                  padding: "8px 12px",
                  borderRadius: "var(--radius-sm)",
                  cursor: "pointer",
                  transition: "background 0.15s",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "space-between",
                  borderBottom: "1px solid var(--ink-50)",
                }}
                onMouseEnter={(e) => (e.currentTarget.style.background = "var(--paper-warm)")}
                onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}
              >
                <span style={{ fontSize: 13 }}>
                  <span className="font-mono" style={{ color: "var(--ink-400)", marginRight: 8 }}>
                    第{ch.chapter_num}章
                  </span>
                  {ch.chapter_title}
                </span>
                <span style={{ fontSize: 11, color: "var(--ink-300)" }}>
                  {ch.word_count ? `${ch.word_count}字` : ""}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}


