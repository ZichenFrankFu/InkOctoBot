import React, { useEffect, useMemo, useState } from "react";
import { apiGet } from "../api/client";

type DashboardData = {
  summary: {
    novel_count?: number;
    rank_entry_count?: number;
    snapshot_count?: number;
    tag_count?: number;
    earliest?: string;
    latest?: string;
  };
  platform_distribution: { platform: string; c: number }[];
  trend: { snapshot_date: string; entry_count: number; novel_count: number }[];
  top_tags: { tag_name: string; c: number }[];
  top_novels: { novel_uid: number; novel_name: string; author: string; avg_rank: number; appearances: number }[];
};

const card: React.CSSProperties = {
  background: "var(--bg-card)",
  border: "1px solid var(--border)",
  borderRadius: 14,
  padding: 14,
};

function Bar({ value, max }: { value: number; max: number }) {
  const pct = max > 0 ? Math.max(4, Math.round((value / max) * 100)) : 4;
  return <div style={{ height: 8, borderRadius: 999, width: `${pct}%`, background: "var(--accent)" }} />;
}

export default function AnalysisDashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [activeDate, setActiveDate] = useState<string>("");

  useEffect(() => {
    apiGet<DashboardData>("/api/analysis/dashboard").then((res) => {
      setData(res);
      if (res.trend.length) setActiveDate(res.trend[res.trend.length - 1].snapshot_date);
    });
  }, []);

  const trendMax = useMemo(() => Math.max(1, ...(data?.trend.map((x) => x.entry_count) || [1])), [data]);
  const tagMax = useMemo(() => Math.max(1, ...(data?.top_tags.map((x) => x.c) || [1])), [data]);
  const platformMax = useMemo(() => Math.max(1, ...(data?.platform_distribution.map((x) => x.c) || [1])), [data]);

  const activeTrend = data?.trend.find((x) => x.snapshot_date === activeDate) || null;

  return (
    <div style={{ color: "var(--text-primary)" }}>
      <h2 style={{ marginTop: 0, marginBottom: 4 }}>📈 分析面板</h2>
      <p style={{ color: "var(--text-secondary)", marginBottom: 16 }}>集中展示市场规模、平台分布、标签热度与稳定上榜作品，支持点选日期查看细节。</p>

      <section style={{ display: "grid", gridTemplateColumns: "repeat(6, minmax(0, 1fr))", gap: 10, marginBottom: 14 }}>
        <div style={card}><div style={{ fontSize: 12, color: "var(--text-secondary)" }}>小说总数</div><div style={{ fontSize: 22, fontWeight: 700 }}>{data?.summary.novel_count ?? "-"}</div></div>
        <div style={card}><div style={{ fontSize: 12, color: "var(--text-secondary)" }}>榜单记录</div><div style={{ fontSize: 22, fontWeight: 700 }}>{data?.summary.rank_entry_count ?? "-"}</div></div>
        <div style={card}><div style={{ fontSize: 12, color: "var(--text-secondary)" }}>快照天数</div><div style={{ fontSize: 22, fontWeight: 700 }}>{data?.summary.snapshot_count ?? "-"}</div></div>
        <div style={card}><div style={{ fontSize: 12, color: "var(--text-secondary)" }}>标签数量</div><div style={{ fontSize: 22, fontWeight: 700 }}>{data?.summary.tag_count ?? "-"}</div></div>
        <div style={card}><div style={{ fontSize: 12, color: "var(--text-secondary)" }}>最早日期</div><div style={{ fontSize: 18, fontWeight: 700 }}>{data?.summary.earliest ?? "-"}</div></div>
        <div style={card}><div style={{ fontSize: 12, color: "var(--text-secondary)" }}>最新日期</div><div style={{ fontSize: 18, fontWeight: 700 }}>{data?.summary.latest ?? "-"}</div></div>
      </section>

      <section style={{ display: "grid", gridTemplateColumns: "1.2fr 1fr", gap: 14, marginBottom: 14 }}>
        <div style={card}>
          <h3 style={{ marginTop: 0, marginBottom: 10 }}>近30天榜单活跃度</h3>
          <div style={{ display: "grid", gap: 8, maxHeight: 320, overflow: "auto" }}>
            {(data?.trend || []).map((row) => (
              <button key={row.snapshot_date} onClick={() => setActiveDate(row.snapshot_date)} style={{ border: "1px solid var(--border)", borderRadius: 8, background: row.snapshot_date === activeDate ? "var(--accent-subtle)" : "var(--bg-surface)", padding: 8, cursor: "pointer", textAlign: "left" }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
                  <span>{row.snapshot_date}</span><span style={{ color: "var(--text-secondary)", fontSize: 12 }}>{row.entry_count} 条</span>
                </div>
                <Bar value={row.entry_count} max={trendMax} />
              </button>
            ))}
          </div>
        </div>

        <div style={card}>
          <h3 style={{ marginTop: 0, marginBottom: 10 }}>日期细节</h3>
          <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 10, background: "var(--bg-surface)" }}>
            <div>日期：{activeTrend?.snapshot_date || "-"}</div>
            <div>榜单条目：{activeTrend?.entry_count ?? 0}</div>
            <div>上榜作品数：{activeTrend?.novel_count ?? 0}</div>
          </div>
          <h4 style={{ marginBottom: 8 }}>平台分布</h4>
          <div style={{ display: "grid", gap: 8 }}>
            {(data?.platform_distribution || []).map((p) => (
              <div key={p.platform} style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 8, background: "var(--bg-surface)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}><span>{p.platform}</span><span>{p.c}</span></div>
                <Bar value={p.c} max={platformMax} />
              </div>
            ))}
          </div>
        </div>
      </section>

      <section style={{ display: "grid", gridTemplateColumns: "1fr 1.2fr", gap: 14 }}>
        <div style={card}>
          <h3 style={{ marginTop: 0, marginBottom: 10 }}>热门标签 Top 12</h3>
          <div style={{ display: "grid", gap: 8 }}>
            {(data?.top_tags || []).map((t) => (
              <div key={t.tag_name} style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 8, background: "var(--bg-surface)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}><span>{t.tag_name}</span><span>{t.c}</span></div>
                <Bar value={t.c} max={tagMax} />
              </div>
            ))}
          </div>
        </div>

        <div style={card}>
          <h3 style={{ marginTop: 0, marginBottom: 10 }}>稳定上榜作品</h3>
          <div style={{ overflow: "auto", maxHeight: 360, border: "1px solid var(--border)", borderRadius: 8 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
              <thead style={{ position: "sticky", top: 0, background: "var(--bg-surface)" }}>
                <tr>
                  <th align="left" style={{ padding: 8 }}>书名</th>
                  <th align="left" style={{ padding: 8 }}>作者</th>
                  <th align="left" style={{ padding: 8 }}>平均排名</th>
                  <th align="left" style={{ padding: 8 }}>上榜次数</th>
                </tr>
              </thead>
              <tbody>
                {(data?.top_novels || []).map((n) => (
                  <tr key={n.novel_uid}>
                    <td style={{ borderTop: "1px solid var(--border)", padding: 8 }}>{n.novel_name || "-"}</td>
                    <td style={{ borderTop: "1px solid var(--border)", padding: 8 }}>{n.author || "-"}</td>
                    <td style={{ borderTop: "1px solid var(--border)", padding: 8 }}>{Number(n.avg_rank || 0).toFixed(2)}</td>
                    <td style={{ borderTop: "1px solid var(--border)", padding: 8 }}>{n.appearances}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </div>
  );
}
