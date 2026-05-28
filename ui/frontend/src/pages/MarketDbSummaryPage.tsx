/**
 * MarketDbSummaryPage — DB-level summary visualisation.
 *
 * Stat tiles + platform / category bar charts + recent snapshots.
 * CRUD for individual works lives in MarketSearchPage now (the search
 * page is the natural surface for finding the row you want to fix).
 * Chapter / word-count drill-down belongs to 市场特征提取.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiGet } from "../api/client";
import { useToast } from "../components/shared/Toast";
import { tPlatform, useLang } from "../i18n";


interface OverviewResp {
  novel_count: number;
  rank_list_count: number;
  snapshot_count: number;
  chapter_count: number;
  recent_snapshots: Array<{
    snapshot_date: string;
    item_count: number;
    platform: string;
    rank_family: string;
    rank_sub_cat?: string;
  }>;
  platform_breakdown: Array<{ platform: string; count: number }>;
  categories: Array<{ main_category: string; count: number }>;
  rank_families: Array<{ rank_family: string; platform: string; snapshot_count: number }>;
}


export default function MarketDbSummaryPage() {
  const { toast } = useToast();
  useLang();  // re-render on language change
  const [data, setData] = useState<OverviewResp | null>(null);
  const [loading, setLoading] = useState(false);
  const [platform, setPlatform] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const qs = platform ? `?platform=${encodeURIComponent(platform)}` : "";
      const r = await apiGet<OverviewResp>(`/api/db/overview${qs}`);
      setData(r);
    } catch (e: any) {
      toast(`加载失败: ${e.message}`, "error");
    } finally {
      setLoading(false);
    }
  }, [platform, toast]);

  useEffect(() => { load(); }, [load]);

  const maxPlatform = useMemo(() => Math.max(1, ...(data?.platform_breakdown.map(p => p.count) || [1])), [data]);
  const maxCategory = useMemo(() => Math.max(1, ...(data?.categories.map(c => c.count) || [1])), [data]);

  if (!data && loading) {
    return <div className="loading" style={{ paddingTop: 80 }}><div className="loading-spinner" />加载中...</div>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, padding: 4 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12 }}>
        <StatTile label="作品总数"     value={data?.novel_count ?? 0}     accent="var(--accent)" />
        <StatTile label="榜单总数"     value={data?.rank_list_count ?? 0} accent="var(--gold)" />
        <StatTile label="快照数"       value={data?.snapshot_count ?? 0}  accent="var(--cyan)" />
        <StatTile label="已采集开篇章节" value={data?.chapter_count ?? 0}   accent="var(--jade)" />
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>平台筛选:</span>
        <button className={platform === "" ? "btn-primary" : "btn"}
                style={{ fontSize: 11, padding: "3px 10px" }}
                onClick={() => setPlatform("")}>全部</button>
        {data?.platform_breakdown.map(p => (
          <button key={p.platform}
                  className={platform === p.platform ? "btn-primary" : "btn"}
                  style={{ fontSize: 11, padding: "3px 10px" }}
                  onClick={() => setPlatform(p.platform)}>{tPlatform(p.platform)}</button>
        ))}
        <div style={{ flex: 1 }} />
        <button className="btn" onClick={load} disabled={loading}>{loading ? "刷新中..." : "刷新"}</button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        <div className="card" style={{ padding: 14 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>平台作品分布</h3>
          {(data?.platform_breakdown || []).length === 0 ? (
            <p style={{ color: "var(--text-tertiary)", fontSize: 12 }}>无数据</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {data!.platform_breakdown.map(p => (
                <div key={p.platform} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ width: 80, fontSize: 12, color: "var(--text-secondary)" }}>{tPlatform(p.platform)}</span>
                  <div style={{ flex: 1, height: 18, background: "var(--bg-surface-2)", borderRadius: 4, overflow: "hidden" }}>
                    <div style={{
                      width: `${(p.count / maxPlatform) * 100}%`,
                      height: "100%",
                      background: "linear-gradient(90deg, var(--accent), var(--cyan))",
                      transition: "width 0.4s ease",
                    }} />
                  </div>
                  <span className="font-mono" style={{ fontSize: 11, width: 60, textAlign: "right" }}>{p.count.toLocaleString()}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="card" style={{ padding: 14 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>主类目分布（前 15 项）</h3>
          {(data?.categories || []).length === 0 ? (
            <p style={{ color: "var(--text-tertiary)", fontSize: 12 }}>无数据</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: 280, overflowY: "auto" }}>
              {data!.categories.map(c => (
                <div key={c.main_category || "(空)"} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ width: 80, fontSize: 11, color: "var(--text-secondary)" }} title={c.main_category}>
                    {c.main_category || "(空)"}
                  </span>
                  <div style={{ flex: 1, height: 14, background: "var(--bg-surface-2)", borderRadius: 3, overflow: "hidden" }}>
                    <div style={{
                      width: `${(c.count / maxCategory) * 100}%`,
                      height: "100%",
                      background: "var(--gold)",
                    }} />
                  </div>
                  <span className="font-mono" style={{ fontSize: 10, width: 50, textAlign: "right" }}>{c.count.toLocaleString()}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="card" style={{ padding: 14 }}>
        <h3 style={{ marginTop: 0, fontSize: 14 }}>最近快照</h3>
        {(data?.recent_snapshots || []).length === 0 ? (
          <p style={{ color: "var(--text-tertiary)", fontSize: 12 }}>无数据</p>
        ) : (
          <table style={{ width: "100%", fontSize: 12 }}>
            <thead>
              <tr style={{ color: "var(--text-tertiary)", textAlign: "left" }}>
                <th style={{ padding: "4px 6px" }}>日期</th>
                <th style={{ padding: "4px 6px" }}>平台</th>
                <th style={{ padding: "4px 6px" }}>榜单</th>
                <th style={{ padding: "4px 6px", textAlign: "right" }}>条目数</th>
              </tr>
            </thead>
            <tbody>
              {data!.recent_snapshots.map((s, i) => (
                <tr key={`${s.snapshot_date}-${i}`} style={{ borderTop: "1px solid var(--border)" }}>
                  <td style={{ padding: "4px 6px" }}>{s.snapshot_date}</td>
                  <td style={{ padding: "4px 6px" }}>{tPlatform(s.platform)}</td>
                  <td style={{ padding: "4px 6px" }}>
                    {s.rank_family}
                    {s.rank_sub_cat && s.rank_sub_cat !== s.rank_family ? ` · ${s.rank_sub_cat}` : ""}
                  </td>
                  <td style={{ padding: "4px 6px", textAlign: "right" }}>{s.item_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}


function StatTile({ label, value, accent }: { label: string; value: number | string; accent: string }) {
  return (
    <div className="card" style={{ padding: 14, textAlign: "center", borderTop: `3px solid ${accent}` }}>
      <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 700, marginTop: 4 }}>
        {typeof value === "number" ? value.toLocaleString() : value}
      </div>
    </div>
  );
}
