import React, { useEffect, useState, useCallback, useMemo } from "react";
import { apiGet } from "../api/client";
import { useResizable } from "../hooks/useResizable";
import type { RankList, RankSnapshot, RankEntry, Novel } from "../api/types";

/* ── local types ── */
interface NovelDetail {
  novel: Novel & Record<string, any>;
  titles: { title_id: number; title: string; is_primary: boolean }[];
  tags: { tag_id: number; tag_name: string }[];
  rank_history: { snapshot_date: string; rank_family: string; rank_sub_cat: string; rank: number }[];
  chapters: { chapter_num: number; chapter_title: string; word_count: number }[];
}

type Step = "lists" | "snapshots" | "entries";
type PlatformFilter = "" | "qidian" | "fanqie";

const platformLabel = (p: string) =>
  p === "qidian" ? "起点" : p === "fanqie" ? "番茄" : p || "未知";

export default function RankingsPage() {
  const [platform, setPlatform] = useState<PlatformFilter>("");
  const [step, setStep] = useState<Step>("lists");
  const [loading, setLoading] = useState(false);

  /* data */
  const [rankLists, setRankLists] = useState<RankList[]>([]);
  const [snapshots, setSnapshots] = useState<RankSnapshot[]>([]);
  const [entries, setEntries] = useState<RankEntry[]>([]);

  /* selections */
  const [selectedList, setSelectedList] = useState<RankList | null>(null);
  const [selectedSnapshot, setSelectedSnapshot] = useState<RankSnapshot | null>(null);

  /* side panel */
  const [panelOpen, setPanelOpen] = useState(false);
  const [panelData, setPanelData] = useState<NovelDetail | null>(null);
  const [panelLoading, setPanelLoading] = useState(false);

  /* resizable panel */
  const { size: panelWidth, handleProps } = useResizable({
    direction: "horizontal",
    initialSize: 520,
    minSize: 360,
    maxSize: 800,
  });

  /* ── Step 1: load rank lists ── */
  useEffect(() => {
    setLoading(true);
    setStep("lists");
    setSelectedList(null);
    setSelectedSnapshot(null);
    setSnapshots([]);
    setEntries([]);
    const qs = platform ? `?platform=${platform}` : "";
    apiGet<{ rows: RankList[] }>(`/api/db/rank_lists${qs}`)
      .then((r) => setRankLists(Array.isArray(r.rows) ? r.rows : []))
      .catch(console.error)
      .finally(() => setLoading(false));
  }, [platform]);

  /* ── Step 2: load snapshots for a rank list ── */
  const selectList = useCallback(async (list: RankList) => {
    setSelectedList(list);
    setSelectedSnapshot(null);
    setEntries([]);
    setStep("snapshots");
    setLoading(true);
    try {
      const r = await apiGet<{ rows: RankSnapshot[] }>(
        `/api/db/snapshots?rank_list_id=${list.rank_list_id}`,
      );
      setSnapshots(Array.isArray(r.rows) ? r.rows : []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  /* ── Step 3: load entries for a snapshot ── */
  const selectSnapshot = useCallback(async (snap: RankSnapshot) => {
    setSelectedSnapshot(snap);
    setStep("entries");
    setLoading(true);
    try {
      const r = await apiGet<{ rows: RankEntry[] }>(`/api/db/entries?snapshot_id=${snap.snapshot_id}`);
      setEntries(Array.isArray(r.rows) ? r.rows : []);
    } catch (e) {
      console.error(e);
    } finally {
      setLoading(false);
    }
  }, []);

  /* ── novel detail ── */
  const openDetail = useCallback(async (uid: number) => {
    setPanelOpen(true);
    setPanelLoading(true);
    setPanelData(null);
    try {
      setPanelData(await apiGet<NovelDetail>(`/api/db/novel/${uid}`));
    } catch (e) {
      console.error(e);
    } finally {
      setPanelLoading(false);
    }
  }, []);

  /* ── breadcrumb navigation ── */
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

  /* ── group rank lists by platform + family ── */
  const groupedLists = useMemo(() => {
    const g: Record<string, RankList[]> = {};
    for (const rl of rankLists) {
      const key = `${rl.platform}|${rl.rank_family}`;
      if (!g[key]) g[key] = [];
      g[key].push(rl);
    }
    return g;
  }, [rankLists]);

  return (
    <div className="page-container">
      {/* ══ Header ══ */}
      <div className="page-header" style={{ marginBottom: 20 }}>
        <div className="page-header-row">
          <div>
            <h2>榜单浏览</h2>
            <p>逐级浏览各平台榜单数据</p>
          </div>
          <div className="tab-bar">
            {([["", "全部平台"], ["qidian", "起点"], ["fanqie", "番茄"]] as const).map(
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

      {/* ══ Breadcrumb ══ */}
      <div className="breadcrumb">
        <div className={`breadcrumb-item${step === "lists" ? " active" : ""}`}>
          {step === "lists" ? (
            <span>选择榜单</span>
          ) : (
            <button onClick={goToLists}>榜单列表</button>
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
              <span>{selectedSnapshot.snapshot_date}</span>
            </div>
          </>
        )}
      </div>

      {/* ══ Content ══ */}
      {loading ? (
        <div className="loading">
          <div className="loading-spinner" />
          加载中...
        </div>
      ) : (
        <>
          {/* Step 1: rank list cards */}
          {step === "lists" &&
            (rankLists.length === 0 ? (
              <div className="empty-state">
                <div className="empty-icon">📭</div>
                <h4>暂无榜单数据</h4>
                <p>请先运行数据采集后再来查看。</p>
              </div>
            ) : (
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fill, minmax(340px, 1fr))",
                  gap: 16,
                }}
              >
                {Object.entries(groupedLists).map(([key, lists]) => {
                  const [plat, family] = key.split("|");
                  return (
                    <div className="card" key={key}>
                      <div className="card-header">
                        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                          <span className={`tag ${plat}`}>{platformLabel(plat)}</span>
                          <h3>{family}</h3>
                        </div>
                        <p style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
                          {lists.length} 个子榜
                        </p>
                      </div>
                      <div className="card-body" style={{ padding: "6px 10px" }}>
                        {lists.map((rl) => (
                          <div
                            key={rl.rank_list_id}
                            onClick={() => selectList(rl)}
                            style={{
                              padding: "10px 12px",
                              borderRadius: "var(--radius-sm)",
                              cursor: "pointer",
                              display: "flex",
                              alignItems: "center",
                              justifyContent: "space-between",
                              transition: "background 0.15s",
                            }}
                            onMouseEnter={(e) =>
                              (e.currentTarget.style.background = "var(--bg-surface-hover)")
                            }
                            onMouseLeave={(e) =>
                              (e.currentTarget.style.background = "transparent")
                            }
                          >
                            <span style={{ fontSize: 13, color: "var(--text-secondary)" }}>
                              {rl.rank_sub_cat || family}
                            </span>
                            <span style={{ fontSize: 16, color: "var(--text-disabled)" }}>›</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            ))}

          {/* Step 2: snapshot list */}
          {step === "snapshots" && (
            <div className="card">
              <div className="card-header">
                <h3>选择快照日期</h3>
                <span className="text-xs text-muted">共 {snapshots.length} 个快照</span>
              </div>
              <div style={{ maxHeight: 560, overflowY: "auto" }}>
                {snapshots.length === 0 ? (
                  <div className="empty-state">
                    <p>暂无快照</p>
                  </div>
                ) : (
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th>日期</th>
                        <th style={{ textAlign: "right" }}>收录数</th>
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
                          <td className="font-mono" style={{ fontWeight: 500 }}>
                            {s.snapshot_date}
                          </td>
                          <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>
                            {s.item_count ?? "—"}
                          </td>
                          <td style={{ textAlign: "center", color: "var(--text-disabled)" }}>›</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          )}

          {/* Step 3: rank entries */}
          {step === "entries" && (
            <div className="card">
              <div className="card-header">
                <div>
                  <h3>
                    {selectedList?.rank_family}
                    {selectedList?.rank_sub_cat && ` · ${selectedList.rank_sub_cat}`}
                  </h3>
                  <p style={{ fontSize: 12, color: "var(--text-tertiary)", marginTop: 2 }}>
                    {selectedSnapshot?.snapshot_date} · {entries.length} 部作品
                  </p>
                </div>
                {selectedList && (
                  <span className={`tag ${selectedList.platform}`}>
                    {platformLabel(selectedList.platform)}
                  </span>
                )}
              </div>
              <div style={{ maxHeight: 600, overflowY: "auto" }}>
                {entries.length === 0 ? (
                  <div className="empty-state">
                    <p>暂无条目</p>
                  </div>
                ) : (
                  <table className="data-table">
                    <thead>
                      <tr>
                        <th style={{ width: 56 }}>排名</th>
                        <th>书名</th>
                        <th>作者</th>
                        <th>分类</th>
                        <th style={{ textAlign: "right" }}>指标</th>
                        <th style={{ textAlign: "right" }}>总字数</th>
                      </tr>
                    </thead>
                    <tbody>
                      {entries.map((e) => {
                        const nov = e.novel as any;
                        const title =
                          nov?.titles?.find((t: any) => t.is_primary)?.title ||
                          nov?.title ||
                          `(uid: ${e.novel_uid})`;
                        return (
                          <tr
                            key={`${e.snapshot_id}-${e.novel_uid}`}
                            className="clickable"
                            onClick={() => openDetail(e.novel_uid)}
                          >
                            <td>
                              <span
                                className={`rank-badge ${e.rank <= 3 ? "top3" : e.rank <= 10 ? "top10" : "normal"}`}
                              >
                                {e.rank}
                              </span>
                            </td>
                            <td
                              style={{ fontWeight: 500, maxWidth: 280, color: "var(--accent)" }}
                              className="truncate"
                            >
                              {title}
                            </td>
                            <td className="text-muted">{nov?.author || "—"}</td>
                            <td>
                              {nov?.main_category && (
                                <span className="tag category">{nov.main_category}</span>
                              )}
                            </td>
                            <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>
                              {e.total_recommend != null
                                ? e.total_recommend.toLocaleString()
                                : e.reading_count != null
                                  ? e.reading_count.toLocaleString()
                                  : "—"}
                            </td>
                            <td
                              style={{
                                textAlign: "right",
                                fontFamily: "var(--font-mono)",
                                color: "var(--text-tertiary)",
                              }}
                            >
                              {nov?.total_words
                                ? `${(nov.total_words / 10000).toFixed(1)}万`
                                : "—"}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                )}
              </div>
            </div>
          )}
        </>
      )}

      {/* ══ Side Panel ══ */}
      {panelOpen && (
        <>
          <div className="side-panel-overlay" onClick={() => setPanelOpen(false)} />
          <div className="side-panel" style={{ width: panelWidth }}>
            <div
              className="panel-resize-h"
              {...handleProps}
              style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: 4, zIndex: 10 }}
            />
            <div className="side-panel-header">
              <h3>{panelLoading ? "加载中..." : "作品详情"}</h3>
              <button className="side-panel-close" onClick={() => setPanelOpen(false)}>
                ✕
              </button>
            </div>
            <div className="side-panel-body">
              {panelLoading ? (
                <div className="loading">
                  <div className="loading-spinner" />
                </div>
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

/* ── Novel Detail Panel ── */
function NovelPanel({ detail }: { detail: NovelDetail }) {
  const { novel: n, titles, tags, rank_history: rh, chapters: chs } = detail;
  const primaryTitle =
    titles.find((t) => t.is_primary)?.title || (titles.length > 0 ? titles[0].title : "(未知)");

  return (
    <>
      <div style={{ marginBottom: 22 }}>
        <h3
          style={{
            fontFamily: "var(--font-serif)",
            fontSize: 20,
            fontWeight: 700,
            color: "var(--text-primary)",
            marginBottom: 8,
          }}
        >
          {primaryTitle}
        </h3>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <span className={`tag ${n.platform}`}>{platformLabel(n.platform)}</span>
          {n.main_category && <span className="tag category">{n.main_category}</span>}
          {n.status && (
            <span
              className={`tag ${n.status === "completed" ? "status-completed" : "status-ongoing"}`}
            >
              {n.status === "completed" ? "已完本" : "连载中"}
            </span>
          )}
        </div>
      </div>

      <div className="detail-section">
        <h4>基本信息</h4>
        <div className="detail-grid">
          <span className="label">作者</span>
          <span className="value">{n.author || "—"}</span>
          <span className="label">总字数</span>
          <span className="value">
            {n.total_words ? `${(n.total_words / 10000).toFixed(1)} 万字` : "—"}
          </span>
          <span className="label">首次采集</span>
          <span className="value">{n.created_date || "—"}</span>
          <span className="label">最近出现</span>
          <span className="value">{n.last_seen_date || "—"}</span>
          {n.url && (
            <>
              <span className="label">链接</span>
              <span className="value">
                <a
                  href={n.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  style={{ color: "var(--accent)", textDecoration: "underline", fontSize: 12 }}
                >
                  查看原文 ↗
                </a>
              </span>
            </>
          )}
        </div>
      </div>

      {n.intro && (
        <div className="detail-section">
          <h4>简介</h4>
          <p style={{ fontSize: 13, lineHeight: 1.7, color: "var(--text-secondary)", whiteSpace: "pre-wrap" }}>
            {n.intro}
          </p>
        </div>
      )}

      {tags.length > 0 && (
        <div className="detail-section">
          <h4>标签</h4>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
            {tags.map((tg) => (
              <span key={tg.tag_id} className="tag category">
                {tg.tag_name}
              </span>
            ))}
          </div>
        </div>
      )}

      {rh && rh.length > 0 && (
        <div className="detail-section">
          <h4>排名历史</h4>
          <div style={{ maxHeight: 220, overflowY: "auto" }}>
            <table className="data-table">
              <thead>
                <tr>
                  <th>日期</th>
                  <th>榜单</th>
                  <th style={{ textAlign: "right" }}>排名</th>
                </tr>
              </thead>
              <tbody>
                {rh.slice(0, 30).map((h, i) => (
                  <tr key={i}>
                    <td className="font-mono" style={{ fontSize: 12 }}>
                      {h.snapshot_date}
                    </td>
                    <td style={{ fontSize: 12 }}>
                      {h.rank_family}
                      {h.rank_sub_cat && ` · ${h.rank_sub_cat}`}
                    </td>
                    <td style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontWeight: 600 }}>
                      {h.rank}
                    </td>
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
              <div
                key={ch.chapter_num}
                style={{
                  padding: "4px 0",
                  borderBottom: "1px solid var(--border-subtle)",
                  display: "flex",
                  justifyContent: "space-between",
                }}
              >
                <span>
                  <span className="font-mono" style={{ color: "var(--text-disabled)", marginRight: 8 }}>
                    第{ch.chapter_num}章
                  </span>
                  {ch.chapter_title}
                </span>
                <span style={{ color: "var(--text-disabled)" }}>
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
