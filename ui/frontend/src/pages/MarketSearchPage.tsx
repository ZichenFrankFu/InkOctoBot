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
import { apiGet } from "../api/client";

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
  const [query, setQuery] = useState("");
  const [platform, setPlatform] = useState<string>("");
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchErr, setSearchErr] = useState("");

  const [selected, setSelected] = useState<SearchHit | null>(null);
  const [detail, setDetail] = useState<NovelDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);

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
      setHits(r.items || []);
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
    <div style={{ padding: 16, height: "100%", display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <h2 style={{ margin: "0 0 4px", fontSize: 18 }}>市场数据库搜索</h2>
      <p style={{ color: "var(--text-secondary)", fontSize: 12, marginTop: 0 }}>
        全文搜索爬虫数据库（标题 / 作者 / 简介）→ 选中作品后查看其历史榜单 snapshot 与首章内容。
      </p>

      <div style={{ display: "flex", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") doSearch(); }}
          placeholder="输入标题 / 作者 / 关键词（如：诡秘 / 乌贼 / 修仙）"
          style={{ flex: 1, padding: 6, fontSize: 14 }}
        />
        <select value={platform} onChange={(e) => setPlatform(e.target.value)} style={{ padding: 6 }}>
          <option value="">所有平台</option>
          <option value="qidian">起点</option>
          <option value="fanqie">番茄</option>
          <option value="zongheng">纵横</option>
          <option value="17k">17K</option>
        </select>
        <button onClick={doSearch} disabled={searching || !query.trim()} style={{ padding: "6px 16px" }}>
          {searching ? "搜索中…" : "搜索"}
        </button>
      </div>

      {searchErr && (
        <div style={{ color: "tomato", fontSize: 12, marginBottom: 8 }}>{searchErr}</div>
      )}

      <div style={{ flex: 1, display: "grid", gridTemplateColumns: "320px 1fr", gap: 12, overflow: "hidden" }}>
        {/* Hits list */}
        <div style={{ border: "1px solid var(--border)", borderRadius: 4, overflow: "auto" }}>
          <div style={{ padding: "6px 12px", background: "var(--surface-1)", fontSize: 12, fontWeight: "bold", borderBottom: "1px solid var(--border)" }}>
            搜索结果 ({hits.length})
          </div>
          {hits.length === 0 && (
            <div style={{ padding: 16, color: "var(--text-secondary)", fontSize: 12 }}>
              {searching ? "搜索中…" : "输入关键词后回车，或选择平台筛选后再点搜索"}
            </div>
          )}
          {hits.map((h) => (
            <div
              key={h.novel_uid}
              onClick={() => selectNovel(h)}
              style={{
                padding: 8, borderBottom: "1px solid var(--border)",
                cursor: "pointer",
                background: selected?.novel_uid === h.novel_uid ? "var(--surface-2)" : "transparent",
              }}
            >
              <div style={{ fontWeight: "bold", fontSize: 13 }}>{h.title}</div>
              <div style={{ fontSize: 11, color: "var(--text-secondary)", marginTop: 2 }}>
                {h.platform}{h.author ? ` · ${h.author}` : ""}
                {h.main_category ? ` · ${h.main_category}` : ""}
              </div>
              <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                {h.total_words ? `${(h.total_words / 10000).toFixed(1)} 万字` : ""}
                {h.status ? ` · ${h.status}` : ""}
              </div>
            </div>
          ))}
        </div>

        {/* Detail */}
        <div style={{ border: "1px solid var(--border)", borderRadius: 4, overflow: "auto", padding: 12 }}>
          {!selected && (
            <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>
              从左侧选择一部作品查看其历史榜单与首章内容
            </div>
          )}
          {loadingDetail && <div>加载中…</div>}
          {detail && !loadingDetail && (
            <>
              <h3 style={{ margin: 0 }}>{detail.titles.find((t) => t.is_primary)?.title || selected?.title}</h3>
              <div style={{ marginTop: 4, fontSize: 12, color: "var(--text-secondary)" }}>
                {detail.novel.platform}{detail.novel.author ? ` · ${detail.novel.author}` : ""}
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
                        <th style={{ textAlign: "right", padding: "2px 6px" }}>推荐</th>
                        <th style={{ textAlign: "right", padding: "2px 6px" }}>阅读</th>
                      </tr>
                    </thead>
                    <tbody>
                      {detail.rank_history.map((r, i) => (
                        <tr key={i} style={{ borderBottom: "1px solid var(--border)" }}>
                          <td style={{ padding: "2px 6px" }}>{r.snapshot_date}</td>
                          <td style={{ padding: "2px 6px" }}>{r.platform}</td>
                          <td style={{ padding: "2px 6px" }}>
                            {r.rank_family}{r.rank_sub_cat ? ` · ${r.rank_sub_cat}` : ""}
                          </td>
                          <td style={{ padding: "2px 6px", textAlign: "right", fontWeight: "bold" }}>{r.rank}</td>
                          <td style={{ padding: "2px 6px", textAlign: "right" }}>
                            {r.total_recommend?.toLocaleString() || "-"}
                          </td>
                          <td style={{ padding: "2px 6px", textAlign: "right" }}>
                            {r.reading_count?.toLocaleString() || "-"}
                          </td>
                        </tr>
                      ))}
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
      </div>
    </div>
  );
}
