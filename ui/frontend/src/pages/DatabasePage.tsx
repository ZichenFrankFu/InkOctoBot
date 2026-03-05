import React, { useEffect, useMemo, useState } from "react";
import { apiGet } from "../api/client";

type Overview = {
  tables: string[];
  row_counts: Record<string, number>;
  date_range?: { earliest: string | null; latest: string | null; snapshot_count: number };
};

type RankList = { rank_list_id: number; platform: string; rank_family: string; rank_sub_cat: string };

type RankNovel = {
  platform: string;
  rank_family: string;
  rank_sub_cat: string;
  snapshot_date: string;
  rank: number;
  novel_uid: number;
  novel_name: string;
  author: string;
  intro: string;
  word_count: number;
  rating: number;
  tags: string;
};

const card: React.CSSProperties = {
  background: "var(--bg-card)",
  border: "1px solid var(--border)",
  borderRadius: 12,
  padding: 14,
};

const input: React.CSSProperties = {
  padding: "8px 10px",
  borderRadius: 8,
  border: "1px solid var(--border)",
  background: "var(--bg-input)",
  color: "var(--text-primary)",
  fontFamily: "inherit",
};

const btn: React.CSSProperties = {
  padding: "8px 10px",
  borderRadius: 8,
  border: "1px solid var(--border)",
  background: "var(--bg-surface)",
  color: "var(--text-primary)",
  cursor: "pointer",
  fontFamily: "inherit",
};

export default function DatabasePage() {
  const [overview, setOverview] = useState<Overview>({ tables: [], row_counts: {} });
  const [keyword, setKeyword] = useState("");
  const [offset, setOffset] = useState(0);
  const [novels, setNovels] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const limit = 20;

  const [platforms, setPlatforms] = useState<string[]>([]);
  const [rankLists, setRankLists] = useState<RankList[]>([]);
  const [dates, setDates] = useState<string[]>([]);

  const [selectedDate, setSelectedDate] = useState("");
  const [selectedPlatform, setSelectedPlatform] = useState("");
  const [selectedRankListId, setSelectedRankListId] = useState("");
  const [rankRows, setRankRows] = useState<RankNovel[]>([]);

  useEffect(() => {
    apiGet<Overview>("/api/db/overview").then(setOverview);
    apiGet<{ platforms: string[]; rank_lists: RankList[]; snapshot_dates: string[] }>("/api/db/rank_filter_options").then((res) => {
      setPlatforms(res.platforms);
      setRankLists(res.rank_lists);
      setDates(res.snapshot_dates);
      if (res.snapshot_dates.length) setSelectedDate(res.snapshot_dates[0]);
      if (res.platforms.length) setSelectedPlatform(res.platforms[0]);
    });
  }, []);

  useEffect(() => {
    loadNovels(0);
  }, []);

  async function loadNovels(nextOffset = 0) {
    const res = await apiGet<{ rows: any[]; total: number }>(`/api/db/novels?limit=${limit}&offset=${nextOffset}&keyword=${encodeURIComponent(keyword)}`);
    setNovels(res.rows);
    setTotal(res.total);
    setOffset(nextOffset);
  }

  const filteredRankLists = useMemo(() => {
    if (!selectedPlatform) return rankLists;
    return rankLists.filter((r) => r.platform === selectedPlatform);
  }, [rankLists, selectedPlatform]);

  useEffect(() => {
    if (!filteredRankLists.length) {
      setSelectedRankListId("");
      return;
    }
    if (!filteredRankLists.some((x) => String(x.rank_list_id) === selectedRankListId)) {
      setSelectedRankListId(String(filteredRankLists[0].rank_list_id));
    }
  }, [filteredRankLists, selectedRankListId]);

  async function loadRankRows() {
    if (!selectedDate) return;
    const qs = new URLSearchParams({ snapshot_date: selectedDate, limit: "120" });
    if (selectedPlatform) qs.set("platform", selectedPlatform);
    if (selectedRankListId) qs.set("rank_list_id", selectedRankListId);
    const res = await apiGet<{ rows: RankNovel[] }>(`/api/db/rank_novels?${qs.toString()}`);
    setRankRows(res.rows);
  }

  useEffect(() => {
    if (selectedDate) loadRankRows();
  }, [selectedDate, selectedPlatform, selectedRankListId]);

  const pageInfo = useMemo(() => {
    const from = total === 0 ? 0 : offset + 1;
    const to = Math.min(offset + limit, total);
    return `${from}-${to} / ${total}`;
  }, [offset, total]);

  return (
    <div style={{ color: "var(--text-primary)" }}>
      <h2 style={{ marginTop: 0, marginBottom: 12 }}>数据库（novels.db）</h2>

      <section style={{ ...card, marginBottom: 14 }}>
        <h3 style={{ marginTop: 0, marginBottom: 8 }}>数据总览</h3>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 10, marginBottom: 10 }}>
          {overview.tables.map((t) => (
            <div key={t} style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 10, background: "var(--bg-surface)" }}>
              <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>{t}</div>
              <div style={{ fontWeight: 700, marginTop: 4 }}>{overview.row_counts[t] ?? "-"}</div>
            </div>
          ))}
        </div>
        <div style={{ display: "flex", gap: 10, color: "var(--text-secondary)", fontSize: 13 }}>
          <span>最早爬取日期：{overview.date_range?.earliest || "-"}</span>
          <span>最新爬取日期：{overview.date_range?.latest || "-"}</span>
          <span>快照总数：{overview.date_range?.snapshot_count ?? 0}</span>
        </div>
      </section>

      <section style={{ ...card, marginBottom: 14 }}>
        <h3 style={{ marginTop: 0, marginBottom: 8 }}>小说列表（novels + tags）</h3>
        <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
          <input style={{ ...input, flex: 1 }} value={keyword} placeholder="按书名/作者搜索" onChange={(e) => setKeyword(e.target.value)} />
          <button style={btn} onClick={() => loadNovels(0)}>搜索</button>
        </div>
        <div style={{ overflow: "auto", border: "1px solid var(--border)", borderRadius: 8 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead style={{ position: "sticky", top: 0, background: "var(--bg-surface)" }}>
              <tr>
                <th align="left" style={{ padding: 8 }}>uid</th>
                <th align="left" style={{ padding: 8 }}>书名</th>
                <th align="left" style={{ padding: 8 }}>作者</th>
                <th align="left" style={{ padding: 8 }}>平台</th>
                <th align="left" style={{ padding: 8 }}>字数</th>
                <th align="left" style={{ padding: 8 }}>评分</th>
                <th align="left" style={{ padding: 8 }}>标签</th>
              </tr>
            </thead>
            <tbody>
              {novels.map((n) => (
                <tr key={n.novel_uid}>
                  <td style={{ borderTop: "1px solid var(--border)", padding: 8 }}>{n.novel_uid}</td>
                  <td style={{ borderTop: "1px solid var(--border)", padding: 8 }}>{n.novel_name || "-"}</td>
                  <td style={{ borderTop: "1px solid var(--border)", padding: 8 }}>{n.author || "-"}</td>
                  <td style={{ borderTop: "1px solid var(--border)", padding: 8 }}>{n.platform || "-"}</td>
                  <td style={{ borderTop: "1px solid var(--border)", padding: 8 }}>{n.word_count ?? "-"}</td>
                  <td style={{ borderTop: "1px solid var(--border)", padding: 8 }}>{n.rating ?? "-"}</td>
                  <td style={{ borderTop: "1px solid var(--border)", padding: 8 }}>{n.tags || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8 }}>
          <button style={btn} disabled={offset === 0} onClick={() => loadNovels(Math.max(0, offset - limit))}>上一页</button>
          <button style={btn} disabled={offset + limit >= total} onClick={() => loadNovels(offset + limit)}>下一页</button>
          <span style={{ color: "var(--text-secondary)", fontSize: 12 }}>{pageInfo}</span>
        </div>
      </section>

      <section style={card}>
        <h3 style={{ marginTop: 0, marginBottom: 8 }}>榜单浏览</h3>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 10, marginBottom: 10 }}>
          <select style={input} value={selectedDate} onChange={(e) => setSelectedDate(e.target.value)}>
            {dates.map((d) => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
          <select style={input} value={selectedPlatform} onChange={(e) => setSelectedPlatform(e.target.value)}>
            {platforms.map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
          <select style={input} value={selectedRankListId} onChange={(e) => setSelectedRankListId(e.target.value)}>
            {filteredRankLists.map((r) => (
              <option key={r.rank_list_id} value={r.rank_list_id}>
                {r.rank_family} {r.rank_sub_cat ? `· ${r.rank_sub_cat}` : ""}
              </option>
            ))}
          </select>
        </div>
        <div style={{ overflow: "auto", border: "1px solid var(--border)", borderRadius: 8, maxHeight: 420 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead style={{ position: "sticky", top: 0, background: "var(--bg-surface)" }}>
              <tr>
                <th align="left" style={{ padding: 8 }}>排名</th>
                <th align="left" style={{ padding: 8 }}>书名</th>
                <th align="left" style={{ padding: 8 }}>作者</th>
                <th align="left" style={{ padding: 8 }}>简介</th>
                <th align="left" style={{ padding: 8 }}>字数</th>
                <th align="left" style={{ padding: 8 }}>评分</th>
                <th align="left" style={{ padding: 8 }}>标签</th>
              </tr>
            </thead>
            <tbody>
              {rankRows.map((n) => (
                <tr key={`${n.snapshot_date}-${n.novel_uid}-${n.rank}`}>
                  <td style={{ borderTop: "1px solid var(--border)", padding: 8 }}>{n.rank}</td>
                  <td style={{ borderTop: "1px solid var(--border)", padding: 8 }}>{n.novel_name || "-"}</td>
                  <td style={{ borderTop: "1px solid var(--border)", padding: 8 }}>{n.author || "-"}</td>
                  <td style={{ borderTop: "1px solid var(--border)", padding: 8, maxWidth: 360 }}>{n.intro || "-"}</td>
                  <td style={{ borderTop: "1px solid var(--border)", padding: 8 }}>{n.word_count ?? 0}</td>
                  <td style={{ borderTop: "1px solid var(--border)", padding: 8 }}>{n.rating ?? 0}</td>
                  <td style={{ borderTop: "1px solid var(--border)", padding: 8 }}>{n.tags || "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
