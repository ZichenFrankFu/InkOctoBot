import React, { useEffect, useState, useMemo, useCallback } from "react";
import { apiGet } from "../api/client";
import { useResizable } from "../hooks/useResizable";
import type { Novel, RankList, RankSnapshot, RankEntry } from "../api/types";

/* ── local types ── */
interface Overview {
  novel_count: number;
  rank_list_count: number;
  snapshot_count: number;
  chapter_count: number;
  platform_breakdown: { platform: string; count: number }[];
  categories: { main_category: string; count: number }[];
}

interface NovelRow {
  novel_uid: number;
  title?: string;
  author: string;
  platform: string;
  main_category?: string;
  total_words?: number;
  status?: string;
  tags?: string[];
}

interface NovelDetail {
  novel: Novel & Record<string, any>;
  titles: { title_id: number; title: string; is_primary: boolean }[];
  tags: { tag_id: number; tag_name: string }[];
  rank_history: { snapshot_date: string; rank_family: string; rank_sub_cat: string; rank: number }[];
  chapters: { chapter_num: number; chapter_title: string; word_count: number }[];
}

const platformLabel = (p: string) =>
  p === "qidian" ? "起点" : p === "fanqie" ? "番茄" : p || "未知";

export default function DatabasePage() {
  /* ── overview ── */
  const [overview, setOverview] = useState<Overview | null>(null);

  /* ── novel search ── */
  const [query, setQuery] = useState("");
  const [novels, setNovels] = useState<NovelRow[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const limit = 25;
  const [novelLoading, setNovelLoading] = useState(false);

  /* ── rank browser ── */
  const [rankLists, setRankLists] = useState<RankList[]>([]);
  const [selectedListId, setSelectedListId] = useState<number | null>(null);
  const [snapshots, setSnapshots] = useState<RankSnapshot[]>([]);
  const [selectedSnapId, setSelectedSnapId] = useState<number | null>(null);
  const [rankEntries, setRankEntries] = useState<RankEntry[]>([]);
  const [rankLoading, setRankLoading] = useState(false);

  /* ── side panel ── */
  const [panelOpen, setPanelOpen] = useState(false);
  const [panelData, setPanelData] = useState<NovelDetail | null>(null);
  const [panelLoading, setPanelLoading] = useState(false);

  /* ── resizable panel ── */
  const { size: panelWidth, handleProps } = useResizable({
    direction: "horizontal",
    initialSize: 520,
    minSize: 360,
    maxSize: 800,
  });

  /* ── initial loads ── */
  useEffect(() => {
    apiGet<Overview>("/api/data/overview").then(setOverview).catch(console.error);
    apiGet<RankList[]>("/api/data/rank-lists").then(setRankLists).catch(console.error);
    loadNovels(0);
  }, []);

  /* ── novel search ── */
  const loadNovels = useCallback(
    async (nextOffset: number) => {
      setNovelLoading(true);
      try {
        const qs = new URLSearchParams({ limit: String(limit), offset: String(nextOffset) });
        if (query) qs.set("q", query);
        const res = await apiGet<{ rows: NovelRow[]; total: number }>(
          `/api/data/novels?${qs.toString()}`,
        );
        setNovels(res.rows ?? []);
        setTotal(res.total ?? 0);
        setOffset(nextOffset);
      } catch (e) {
        console.error(e);
      } finally {
        setNovelLoading(false);
      }
    },
    [query],
  );

  const handleSearch = () => loadNovels(0);

  const pageInfo = useMemo(() => {
    const from = total === 0 ? 0 : offset + 1;
    const to = Math.min(offset + limit, total);
    return `${from}-${to} / ${total}`;
  }, [offset, total]);

  /* ── rank browser: load snapshots when list selected ── */
  useEffect(() => {
    if (selectedListId == null) {
      setSnapshots([]);
      setSelectedSnapId(null);
      setRankEntries([]);
      return;
    }
    setRankLoading(true);
    apiGet<RankSnapshot[]>(`/api/data/rank-snapshots?rank_list_id=${selectedListId}`)
      .then((r) => {
        const arr = Array.isArray(r) ? r : [];
        setSnapshots(arr);
        setSelectedSnapId(null);
        setRankEntries([]);
      })
      .catch(console.error)
      .finally(() => setRankLoading(false));
  }, [selectedListId]);

  /* ── rank browser: load entries when snapshot selected ── */
  useEffect(() => {
    if (selectedSnapId == null) {
      setRankEntries([]);
      return;
    }
    setRankLoading(true);
    apiGet<RankEntry[]>(`/api/data/rank-entries?snapshot_id=${selectedSnapId}`)
      .then((r) => setRankEntries(Array.isArray(r) ? r : []))
      .catch(console.error)
      .finally(() => setRankLoading(false));
  }, [selectedSnapId]);

  /* ── open novel detail ── */
  const openDetail = useCallback(async (uid: number) => {
    setPanelOpen(true);
    setPanelLoading(true);
    setPanelData(null);
    try {
      setPanelData(await apiGet<NovelDetail>(`/api/data/novels/${uid}`));
    } catch (e) {
      console.error(e);
    } finally {
      setPanelLoading(false);
    }
  }, []);

  return (
    <div className="page-container">
      {/* ══ Header ══ */}
      <div className="page-header" style={{ marginBottom: 20 }}>
        <h2>数据库浏览</h2>
        <p>浏览收录小说、标签与榜单数据</p>
      </div>

      {/* ══ Overview Stats ══ */}
      {overview && (
        <div className="stats-grid">
          {[
            { icon: "📚", color: "red", value: overview.novel_count, label: "小说总数" },
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
      )}

      {/* ══ Novel Search ══ */}
      <div className="card" style={{ marginBottom: 24 }}>
        <div className="card-header">
          <h3>小说列表</h3>
          <span className="text-xs text-muted">{pageInfo}</span>
        </div>
        <div className="card-body">
          {/* search bar */}
          <div style={{ display: "flex", gap: 8, marginBottom: 14 }}>
            <input
              className="input"
              placeholder="按书名或作者搜索..."
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSearch()}
              style={{ flex: 1 }}
            />
            <button className="btn-primary" onClick={handleSearch}>
              搜索
            </button>
          </div>

          {/* table */}
          <div style={{ maxHeight: 420, overflowY: "auto" }}>
            {novelLoading ? (
              <div className="loading">
                <div className="loading-spinner" />
                搜索中...
              </div>
            ) : novels.length === 0 ? (
              <div className="empty-state">
                <p>未找到匹配的小说</p>
              </div>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th style={{ width: 60 }}>UID</th>
                    <th>书名</th>
                    <th>作者</th>
                    <th>平台</th>
                    <th>分类</th>
                    <th style={{ textAlign: "right" }}>字数</th>
                    <th>状态</th>
                  </tr>
                </thead>
                <tbody>
                  {novels.map((n) => (
                    <tr
                      key={n.novel_uid}
                      className="clickable"
                      onClick={() => openDetail(n.novel_uid)}
                    >
                      <td className="font-mono text-muted">{n.novel_uid}</td>
                      <td style={{ fontWeight: 500, color: "var(--accent)" }}>
                        {n.title || "(未知)"}
                      </td>
                      <td className="text-muted">{n.author || "-"}</td>
                      <td>
                        <span className={`tag ${n.platform}`}>{platformLabel(n.platform)}</span>
                      </td>
                      <td>
                        {n.main_category && <span className="tag category">{n.main_category}</span>}
                      </td>
                      <td
                        style={{ textAlign: "right", fontFamily: "var(--font-mono)", fontSize: 12 }}
                      >
                        {n.total_words ? `${(n.total_words / 10000).toFixed(1)}万` : "-"}
                      </td>
                      <td>
                        {n.status && (
                          <span
                            className={`tag ${n.status === "completed" ? "status-completed" : "status-ongoing"}`}
                          >
                            {n.status === "completed" ? "已完本" : "连载中"}
                          </span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* pagination */}
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 12 }}>
            <button
              className="btn"
              disabled={offset === 0}
              onClick={() => loadNovels(Math.max(0, offset - limit))}
            >
              上一页
            </button>
            <button
              className="btn"
              disabled={offset + limit >= total}
              onClick={() => loadNovels(offset + limit)}
            >
              下一页
            </button>
            <span className="text-xs text-muted">{pageInfo}</span>
          </div>
        </div>
      </div>

      {/* ══ Rank Browser ══ */}
      <div className="card">
        <div className="card-header">
          <h3>榜单浏览</h3>
          <p style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
            选择榜单 → 选择快照日期 → 查看排名条目
          </p>
        </div>
        <div className="card-body">
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 16 }}>
            {/* rank list selector */}
            <div className="field">
              <label className="label">榜单类型</label>
              <select
                className="select"
                value={selectedListId ?? ""}
                onChange={(e) =>
                  setSelectedListId(e.target.value ? Number(e.target.value) : null)
                }
              >
                <option value="">-- 选择榜单 --</option>
                {rankLists.map((rl) => (
                  <option key={rl.rank_list_id} value={rl.rank_list_id}>
                    {platformLabel(rl.platform)} · {rl.rank_family}
                    {rl.rank_sub_cat ? ` · ${rl.rank_sub_cat}` : ""}
                  </option>
                ))}
              </select>
            </div>

            {/* snapshot selector */}
            <div className="field">
              <label className="label">快照日期</label>
              <select
                className="select"
                value={selectedSnapId ?? ""}
                onChange={(e) =>
                  setSelectedSnapId(e.target.value ? Number(e.target.value) : null)
                }
                disabled={snapshots.length === 0}
              >
                <option value="">-- 选择日期 --</option>
                {snapshots.map((s) => (
                  <option key={s.snapshot_id} value={s.snapshot_id}>
                    {s.snapshot_date} ({s.item_count} 条)
                  </option>
                ))}
              </select>
            </div>
          </div>

          {/* rank entries table */}
          <div style={{ maxHeight: 460, overflowY: "auto" }}>
            {rankLoading ? (
              <div className="loading">
                <div className="loading-spinner" />
                加载中...
              </div>
            ) : selectedSnapId == null ? (
              <div className="empty-state">
                <div className="empty-icon">📋</div>
                <h4>请选择榜单与日期</h4>
                <p>选择上方的榜单类型和快照日期来查看排名条目。</p>
              </div>
            ) : rankEntries.length === 0 ? (
              <div className="empty-state">
                <p>该快照暂无条目</p>
              </div>
            ) : (
              <table className="data-table">
                <thead>
                  <tr>
                    <th style={{ width: 56 }}>排名</th>
                    <th>书名</th>
                    <th>作者</th>
                    <th style={{ textAlign: "right" }}>推荐</th>
                    <th style={{ textAlign: "right" }}>阅读</th>
                  </tr>
                </thead>
                <tbody>
                  {rankEntries.map((e) => {
                    const title =
                      e.novel?.titles?.find((t: any) => t.is_primary)?.title ||
                      (e.novel as any)?.title ||
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
                        <td style={{ fontWeight: 500, color: "var(--accent)" }}>{title}</td>
                        <td className="text-muted">{e.novel?.author || "-"}</td>
                        <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>
                          {e.total_recommend != null ? e.total_recommend.toLocaleString() : "—"}
                        </td>
                        <td style={{ textAlign: "right", fontFamily: "var(--font-mono)" }}>
                          {e.reading_count != null ? e.reading_count.toLocaleString() : "—"}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>

      {/* ══ Side Panel ══ */}
      {panelOpen && (
        <>
          <div className="side-panel-overlay" onClick={() => setPanelOpen(false)} />
          <div className="side-panel" style={{ width: panelWidth }}>
            {/* resize handle on left edge */}
            <div
              className="panel-resize-h"
              {...handleProps}
              style={{
                position: "absolute",
                left: 0,
                top: 0,
                bottom: 0,
                width: 4,
                zIndex: 10,
              }}
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
                <DetailPanel detail={panelData} />
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

/* ── Detail Panel ── */
function DetailPanel({ detail }: { detail: NovelDetail }) {
  const { novel: n, titles, tags, rank_history: rh } = detail;
  const primaryTitle =
    titles.find((t) => t.is_primary)?.title || (titles.length > 0 ? titles[0].title : "(未知)");

  return (
    <>
      <div style={{ marginBottom: 20 }}>
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
    </>
  );
}
