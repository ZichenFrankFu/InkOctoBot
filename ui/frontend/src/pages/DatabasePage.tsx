import React, { useEffect, useMemo, useState } from "react";
import { apiGet } from "../api/client";

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
  const [overview, setOverview] = useState<{ tables: string[]; row_counts: Record<string, number> }>({ tables: [], row_counts: {} });
  const [keyword, setKeyword] = useState("");
  const [offset, setOffset] = useState(0);
  const limit = 20;
  const [novels, setNovels] = useState<any[]>([]);
  const [total, setTotal] = useState(0);

  const [rankLists, setRankLists] = useState<any[]>([]);
  const [snapshots, setSnapshots] = useState<any[]>([]);
  const [entries, setEntries] = useState<any[]>([]);

  useEffect(() => {
    apiGet<{ tables: string[]; row_counts: Record<string, number> }>("/api/db/overview").then(setOverview);
    apiGet<{ rows: any[] }>("/api/db/rank_lists").then((res) => setRankLists(res.rows));
  }, []);

  async function loadNovels(nextOffset: number = 0) {
    const res = await apiGet<{ rows: any[]; total: number }>(`/api/db/novels?limit=${limit}&offset=${nextOffset}&keyword=${encodeURIComponent(keyword)}`);
    setNovels(res.rows);
    setTotal(res.total);
    setOffset(nextOffset);
  }

  async function openSnapshots(rank_list_id: number) {
    const res = await apiGet<{ rows: any[] }>(`/api/db/snapshots?rank_list_id=${rank_list_id}`);
    setSnapshots(res.rows);
    setEntries([]);
  }

  async function openEntries(snapshot_id: number) {
    const res = await apiGet<{ rows: any[] }>(`/api/db/entries?snapshot_id=${snapshot_id}&limit=100`);
    setEntries(res.rows);
  }

  useEffect(() => {
    loadNovels(0);
  }, []);

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
        <div style={{ display: "grid", gridTemplateColumns: "repeat(4, minmax(0, 1fr))", gap: 10 }}>
          {overview.tables.map((t) => (
            <div key={t} style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 10, background: "var(--bg-surface)" }}>
              <div style={{ color: "var(--text-secondary)", fontSize: 12 }}>{t}</div>
              <div style={{ fontWeight: 700, marginTop: 4 }}>{overview.row_counts[t] ?? "-"}</div>
            </div>
          ))}
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
        <h3 style={{ marginTop: 0, marginBottom: 8 }}>榜单钻取（rank_lists → snapshots → entries）</h3>
        <div style={{ display: "grid", gridTemplateColumns: "0.9fr 0.9fr 1.2fr", gap: 10 }}>
          <div style={{ border: "1px solid var(--border)", borderRadius: 8, height: 300, overflow: "auto" }}>
            {rankLists.map((r) => (
              <div key={r.rank_list_id} style={{ padding: 8, borderTop: "1px solid var(--border)", cursor: "pointer" }} onClick={() => openSnapshots(r.rank_list_id)}>
                #{r.rank_list_id} {r.platform} {r.rank_family} {r.rank_sub_cat}
              </div>
            ))}
          </div>
          <div style={{ border: "1px solid var(--border)", borderRadius: 8, height: 300, overflow: "auto" }}>
            {snapshots.map((s) => (
              <div key={s.snapshot_id} style={{ padding: 8, borderTop: "1px solid var(--border)", cursor: "pointer" }} onClick={() => openEntries(s.snapshot_id)}>
                id={s.snapshot_id} date={s.snapshot_date} count={s.item_count}
              </div>
            ))}
          </div>
          <pre style={{ margin: 0, border: "1px solid var(--border)", borderRadius: 8, height: 300, overflow: "auto", background: "var(--bg-surface)", padding: 10 }}>
            {entries.length ? JSON.stringify(entries.slice(0, 60), null, 2) : "(选择一个 snapshot 查看 rank_entries)"}
          </pre>
        </div>
      </section>
    </div>
  );
}
