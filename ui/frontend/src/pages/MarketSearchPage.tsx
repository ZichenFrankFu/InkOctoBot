/**
 * MarketSearchPage — full-text search over the crawler DB.
 *
 * Search by title / author / intro substring → click a hit → see
 * all rank snapshots that novel ever appeared in, plus all crawled
 * opening chapters with content on demand.
 *
 * Wires three endpoints:
 *   GET /api/db/search_novels?q=...&platform=...
 *   GET /api/db/novel/{novel_uid}      (titles + tags + rank_history + chapters meta)
 *   GET /api/db/chapter/{chapter_id}   (single chapter content, lazy-loaded)
 */
import React, { useCallback, useEffect, useState } from "react";
import { apiGet, apiPut, apiDelete } from "../api/client";
import { tPlatform, useLang } from "../i18n";
import { useToast } from "../components/shared/Toast";

interface SearchHit {
  novel_uid: number;
  title: string;
  platform: string;
  author?: string;
  main_category?: string;
  status?: string;
  total_words?: number;
  last_seen_date?: string;
}

interface RankHistoryRow {
  snapshot_id: number;
  rank: number;
  rank_family: string;
  rank_sub_cat?: string;
  platform: string;
  snapshot_date: string;
  total_recommend?: number;
  reading_count?: number;
}

interface ChapterMeta {
  chapter_id: number;
  chapter_num: number;
  chapter_title: string;
  word_count?: number;
  publish_date?: string;
}

interface NovelDetail {
  novel: { novel_uid: number; author?: string; intro?: string;
           main_category?: string; status?: string; total_words?: number;
           platform?: string };
  titles: { title: string; is_primary: number; last_seen_date?: string }[];
  tags: { tag_name: string }[];
  rank_history: RankHistoryRow[];
  chapters: ChapterMeta[];
}

export default function MarketSearchPage() {
  const { toast } = useToast();
  useLang();
  const [query, setQuery] = useState("");
  const [platform, setPlatform] = useState<string>("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchErr, setSearchErr] = useState("");

  const [selected, setSelected] = useState<SearchHit | null>(null);
  const [detail, setDetail] = useState<NovelDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

  // Edit modal — populated when the user clicks "编辑" on the detail.
  const [editing, setEditing] = useState<{
    novel_uid: number; author: string; main_category: string;
    status: string; total_words: number; url: string; intro: string;
    title: string;
  } | null>(null);

  // Lazy-loaded chapter content cache
  const [chapterContent, setChapterContent] = useState<Record<number, string>>({});
  const [loadingChapter, setLoadingChapter] = useState<Record<number, boolean>>({});

  const doSearch = useCallback(async () => {
    const q = query.trim();
    if (!q) {
      setHits([]);
      return;
    }
    setSearching(true);
    setSearchErr("");
    try {
      const params = new URLSearchParams({ q });
      if (platform) params.set("platform", platform);
      const r = await apiGet<{ items: SearchHit[] }>(
        `/api/db/search_novels?${params}`,
      );
      // Sort by relevance: exact-title > substring match in title >
      // substring in author > everything else. Within each band, keep
      // backend's tie order. Highest-confidence hits surface at top.
      const ql = q.toLowerCase();
      const rank = (h: SearchHit): number => {
        const title = (h.title || "").toLowerCase();
        const author = (h.author || "").toLowerCase();
        if (title === ql) return 0;
        if (title.startsWith(ql)) return 1;
        if (title.includes(ql)) return 2;
        if (author === ql) return 3;
        if (author.includes(ql)) return 4;
        return 5;
      };
      const items = (r.items || []).slice();
      items.sort((a, b) => rank(a) - rank(b));
      setHits(items);
      // Reset the detail view — user clicks to expand the next hit.
      setSelected(null);
      setDetail(null);
    } catch (e: any) {
      setSearchErr(e.message || String(e));
    } finally {
      setSearching(false);
    }
  }, [query, platform]);

  const selectNovel = useCallback(async (hit: SearchHit) => {
    setSelected(hit);
    setDetail(null);
    setChapterContent({});
    setLoadingDetail(true);
    try {
      const d = await apiGet<NovelDetail>(`/api/db/novel/${hit.novel_uid}`);
      setDetail(d);
    } catch (e: any) {
      setSearchErr(`加载详情失败: ${e.message || e}`);
    } finally {
      setLoadingDetail(false);
    }
  }, []);

  const loadChapter = useCallback(async (chapter_id: number) => {
    if (chapterContent[chapter_id]) return;
    setLoadingChapter((prev) => ({ ...prev, [chapter_id]: true }));
    try {
      const c = await apiGet<{ chapter_content?: string }>(
        `/api/db/chapter/${chapter_id}`,
      );
      setChapterContent((prev) => ({
        ...prev, [chapter_id]: c.chapter_content || "（无内容）",
      }));
    } catch (e: any) {
      setChapterContent((prev) => ({
        ...prev, [chapter_id]: `加载失败: ${e.message || e}`,
      }));
    } finally {
      setLoadingChapter((prev) => ({ ...prev, [chapter_id]: false }));
    }
  }, [chapterContent]);

  return (
    <div className="page-container" style={{ padding: "16px 20px", maxWidth: 1400, margin: "0 auto", height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      {/* Header — same shape as other pages (page-header / page-header-row) */}
      <div className="page-header" style={{ paddingBottom: 12 }}>
        <div className="page-header-row">
          <div>
            <h2>市场作品搜索</h2>
            <p>按标题 / 作者 / 简介关键词搜索市场数据库，选中后查看历史榜单 snapshot 与首章内容</p>
          </div>
        </div>
      </div>

      {/* Search bar — promoted into a card matching other pages */}
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-body">
          <div className="flex gap-8 items-center" style={{ flexWrap: "wrap" }}>
            <input
              className="input"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") doSearch(); }}
              placeholder="输入标题 / 作者 / 关键词（如：诡秘 / 乌贼 / 修仙）"
              style={{ flex: 1, minWidth: 280 }}
            />
            <label className="flex items-center gap-4" style={{ fontSize: 12, color: "var(--text-secondary)" }}>
              <span>平台</span>
              <select
                className="input"
                value={platform}
                onChange={(e) => setPlatform(e.target.value)}
                style={{ width: 110 }}
              >
                <option value="">全部平台</option>
                <option value="qidian">起点</option>
                <option value="fanqie">番茄</option>
                <option value="zongheng">纵横</option>
                <option value="17k">17K</option>
              </select>
            </label>
            <button
              className="btn-primary"
              onClick={doSearch}
              disabled={searching || !query.trim()}
            >
              {searching ? "搜索中..." : "搜索"}
            </button>
          </div>
          <div className="text-xs text-muted" style={{ marginTop: 8, lineHeight: 1.55 }}>
            点击搜索结果中的任意作品，查看其历史 rank snapshot 与已爬取的首章正文。
          </div>
        </div>
      </div>

      {searchErr && (
        <div className="card" style={{ marginBottom: 8, borderColor: "var(--danger)" }}>
          <div className="card-body" style={{ color: "var(--danger)", fontSize: 12 }}>{searchErr}</div>
        </div>
      )}

      {/* The detail column collapses to 0 until the user picks a hit.
          Hits list expands to the full width when nothing's selected
          so initial search results breathe. */}
      <div style={{
        flex: 1, display: "grid",
        gridTemplateColumns: selected ? "360px 1fr" : "1fr",
        gap: 14, overflow: "hidden", minHeight: 0,
      }}>
        {/* Hits list — wrapped in a card to match other pages */}
        <div className="card" style={{ padding: 0, overflow: "hidden", display: "flex", flexDirection: "column" }}>
          <div className="card-header" style={{ padding: "8px 14px", fontSize: 12, fontWeight: 600 }}>
            搜索结果 ({hits.length})
          </div>
          <div style={{ flex: 1, overflowY: "auto" }}>
          {hits.length === 0 && (
            <div style={{ padding: 20, color: "var(--text-tertiary)", fontSize: 12, textAlign: "center" }}>
              {searching ? "搜索中..." : "输入关键词后回车，或选择平台筛选后再点搜索"}
            </div>
          )}
          {hits.map((h) => (
            <div
              key={h.novel_uid}
              onClick={() => selectNovel(h)}
              style={{
                padding: "10px 14px", borderBottom: "1px solid var(--border)",
                cursor: "pointer",
                background: selected?.novel_uid === h.novel_uid
                  ? "var(--accent-subtle, var(--bg-surface-2))"
                  : "transparent",
                borderLeft: selected?.novel_uid === h.novel_uid
                  ? "3px solid var(--accent)"
                  : "3px solid transparent",
                transition: "background 0.12s",
              }}
              onMouseEnter={e => {
                if (selected?.novel_uid !== h.novel_uid) {
                  (e.currentTarget as HTMLDivElement).style.background = "var(--bg-surface-2)";
                }
              }}
              onMouseLeave={e => {
                if (selected?.novel_uid !== h.novel_uid) {
                  (e.currentTarget as HTMLDivElement).style.background = "transparent";
                }
              }}
            >
              <div style={{ fontWeight: 600, fontSize: 13, color: "var(--text-primary)" }}>{h.title}</div>
              <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 4 }}>
                {h.platform}{h.author ? ` · ${h.author}` : ""}
                {h.main_category ? ` · ${h.main_category}` : ""}
              </div>
              {(h.total_words || h.status) && (
                <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 2 }}>
                  {h.total_words ? `${(h.total_words / 10000).toFixed(1)} 万字` : ""}
                  {h.status ? ` · ${h.status}` : ""}
                </div>
              )}
            </div>
          ))}
          </div>
        </div>

        {/* Detail — only render when a hit is selected; otherwise the
            list takes the full width. */}
        {selected && (
        <div className="card" style={{ padding: 16, overflow: "auto" }}>
          {loadingDetail && <div>加载中…</div>}
          {detail && !loadingDetail && (
            <>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
                <h3 style={{ margin: 0 }}>{detail.titles.find((t) => t.is_primary)?.title || selected?.title}</h3>
                <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                  <button
                    className="btn"
                    style={{ fontSize: 11, padding: "3px 10px" }}
                    onClick={() => {
                      const title = detail.titles.find(t => t.is_primary)?.title || selected?.title || "";
                      setEditing({
                        novel_uid: detail.novel.novel_uid,
                        author: detail.novel.author || "",
                        main_category: detail.novel.main_category || "",
                        status: detail.novel.status || "ongoing",
                        total_words: detail.novel.total_words || 0,
                        url: (detail.novel as any).url || "",
                        intro: detail.novel.intro || "",
                        title,
                      });
                    }}
                  >编辑</button>
                  <button
                    className="btn"
                    style={{ fontSize: 11, padding: "3px 10px", color: "var(--danger)" }}
                    onClick={async () => {
                      const title = detail.titles.find(t => t.is_primary)?.title || "this work";
                      if (!window.confirm(`确认删除「${title}」？\n会同时清掉它在 rank_entries / chapters / titles / tag_map 中的所有引用，不可恢复。`)) return;
                      try {
                        await apiDelete(`/api/db/novel/${detail.novel.novel_uid}`);
                        toast("已删除", "success");
                        setHits(prev => prev.filter(h => h.novel_uid !== detail.novel.novel_uid));
                        setSelected(null);
                        setDetail(null);
                      } catch (e: any) {
                        toast(`删除失败: ${e.message}`, "error");
                      }
                    }}
                  >删除</button>
                </div>
              </div>
              <div style={{ marginTop: 4, fontSize: 12, color: "var(--text-secondary)" }}>
                {tPlatform(detail.novel.platform || "")}{detail.novel.author ? ` · ${detail.novel.author}` : ""}
                {detail.novel.main_category ? ` · ${detail.novel.main_category}` : ""}
                {detail.novel.total_words ? ` · ${(detail.novel.total_words / 10000).toFixed(1)} 万字` : ""}
              </div>

              {detail.tags.length > 0 && (
                <div style={{ marginTop: 8 }}>
                  {detail.tags.map((t, i) => (
                    <span key={i} style={{ display: "inline-block", padding: "2px 8px", margin: 2, background: "var(--surface-1)", borderRadius: 10, fontSize: 11 }}>
                      {t.tag_name}
                    </span>
                  ))}
                </div>
              )}

              {detail.novel.intro && (
                <details style={{ marginTop: 8 }}>
                  <summary style={{ cursor: "pointer", fontSize: 12 }}>简介</summary>
                  <div style={{ padding: 8, background: "var(--surface-1)", fontSize: 12, marginTop: 4 }}>
                    {detail.novel.intro}
                  </div>
                </details>
              )}

              {detail.titles.length > 1 && (
                <details style={{ marginTop: 8 }}>
                  <summary style={{ cursor: "pointer", fontSize: 12 }}>历史标题 ({detail.titles.length})</summary>
                  <ul style={{ marginTop: 4, fontSize: 11 }}>
                    {detail.titles.map((t, i) => (
                      <li key={i}>
                        {t.title}
                        {t.is_primary ? " (主)" : ""}
                        {t.last_seen_date ? ` — ${t.last_seen_date}` : ""}
                      </li>
                    ))}
                  </ul>
                </details>
              )}

              {/* Rank snapshots */}
              <h4 style={{ marginTop: 16, marginBottom: 4 }}>
                历史榜单 Snapshot ({detail.rank_history.length})
              </h4>
              {detail.rank_history.length === 0 ? (
                <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                  无榜单记录
                </div>
              ) : (
                <div style={{ overflowX: "auto" }}>
                  <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
                    <thead>
                      <tr style={{ background: "var(--surface-1)" }}>
                        <th style={{ textAlign: "left", padding: "2px 6px" }}>日期</th>
                        <th style={{ textAlign: "left", padding: "2px 6px" }}>平台</th>
                        <th style={{ textAlign: "left", padding: "2px 6px" }}>榜单</th>
                        <th style={{ textAlign: "right", padding: "2px 6px" }}>排名</th>
                        <th style={{ textAlign: "right", padding: "2px 6px" }}>热度</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.rank_history.map((r, i) => {
                        // Per-platform single hotness metric — 起点
                        // uses 推荐票, 番茄 uses 阅读量; show whichever
                        // applies to this row instead of both columns.
                        const isQidian = r.platform === "qidian";
                        const isFanqie = r.platform === "fanqie";
                        const metric = isQidian
                          ? { label: "推荐", value: r.total_recommend }
                          : isFanqie
                            ? { label: "阅读", value: r.reading_count }
                            : { label: "热度", value: r.total_recommend ?? r.reading_count };
                        return (
                          <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                            <td style={{ padding: "2px 6px" }}>{r.snapshot_date}</td>
                            <td style={{ padding: "2px 6px" }}>{r.platform}</td>
                            <td style={{ padding: "2px 6px" }}>
                              {r.rank_family}{r.rank_sub_cat ? ` · ${r.rank_sub_cat}` : ""}
                            </td>
                            <td style={{ padding: "2px 6px", textAlign: "right", fontWeight: "bold" }}>{r.rank}</td>
                            <td style={{ padding: "2px 6px", textAlign: "right" }}
                                title={metric.label}>
                              {metric.value?.toLocaleString() || "-"}
                              <span style={{ fontSize: 10, color: "var(--text-tertiary)", marginLeft: 4 }}>
                                {metric.label}
                              </span>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}

              {/* Chapters */}
              <h4 style={{ marginTop: 16, marginBottom: 4 }}>
                已抓取章节 ({detail.chapters.length})
              </h4>
              {detail.chapters.length === 0 ? (
                <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                  无章节内容
                </div>
              ) : (
                detail.chapters.map((ch) => (
                  <details
                    key={ch.chapter_id}
                    onToggle={(e) => {
                      if ((e.target as HTMLDetailsElement).open) {
                        loadChapter(ch.chapter_id);
                      }
                    }}
                    style={{ marginBottom: 4, padding: 4, borderBottom: "1px solid var(--border)" }}
                  >
                    <summary style={{ cursor: "pointer", fontSize: 12 }}>
                      第 {ch.chapter_num} 章 · {ch.chapter_title}
                      {ch.word_count ? <span style={{ color: "var(--text-secondary)", marginLeft: 8 }}>({ch.word_count} 字)</span> : null}
                    </summary>
                    <div style={{ padding: 8, background: "var(--surface-1)", fontSize: 12, marginTop: 4, maxHeight: 400, overflow: "auto", whiteSpace: "pre-wrap" }}>
                      {loadingChapter[ch.chapter_id] ? "加载中…" : (chapterContent[ch.chapter_id] || "（点击展开后加载）")}
                    </div>
                  </details>
                ))
              )}
            </>
          )}
        </div>
        )}
      </div>

      {editing && (
        <div onClick={() => setEditing(null)} style={{
          position: "fixed", inset: 0, background: "rgba(0,0,0,0.45)",
          display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1100,
        }}>
          <div className="card" onClick={e => e.stopPropagation()} style={{
            width: "min(560px, 95vw)", maxHeight: "90vh", overflow: "auto",
            background: "var(--bg-surface)", padding: 16,
          }}>
            <h3 style={{ marginTop: 0 }}>编辑作品: {editing.title || editing.novel_uid}</h3>
            <Field label="作者">
              <input className="input" value={editing.author}
                     onChange={e => setEditing({ ...editing, author: e.target.value })} />
            </Field>
            <Field label="主类目">
              <input className="input" value={editing.main_category}
                     onChange={e => setEditing({ ...editing, main_category: e.target.value })} />
            </Field>
            <Field label="字数">
              <input className="input" type="number" value={editing.total_words}
                     onChange={e => setEditing({ ...editing, total_words: parseInt(e.target.value) || 0 })} />
            </Field>
            <Field label="状态">
              <select className="select" value={editing.status}
                      onChange={e => setEditing({ ...editing, status: e.target.value })}>
                <option value="ongoing">ongoing</option>
                <option value="completed">completed</option>
              </select>
            </Field>
            <Field label="URL">
              <input className="input" value={editing.url}
                     onChange={e => setEditing({ ...editing, url: e.target.value })} />
            </Field>
            <Field label="简介">
              <textarea className="input" rows={5} value={editing.intro}
                        onChange={e => setEditing({ ...editing, intro: e.target.value })} />
            </Field>
            <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 12 }}>
              <button className="btn" onClick={() => setEditing(null)}>取消</button>
              <button className="btn-primary" onClick={async () => {
                try {
                  await apiPut(`/api/db/novel/${editing.novel_uid}`, {
                    author: editing.author,
                    intro: editing.intro,
                    main_category: editing.main_category,
                    status: editing.status,
                    total_words: editing.total_words,
                    url: editing.url,
                  });
                  toast("已保存", "success");
                  // Refresh the detail so the user sees the changes.
                  if (selected) {
                    const d = await apiGet<NovelDetail>(`/api/db/novel/${selected.novel_uid}`);
                    setDetail(d);
                  }
                  setEditing(null);
                } catch (e: any) {
                  toast(`保存失败: ${e.message}`, "error");
                }
              }}>保存</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}


function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <label style={{ fontSize: 12, color: "var(--text-secondary)", display: "block", marginBottom: 4 }}>{label}</label>
      {children}
    </div>
  );
}
