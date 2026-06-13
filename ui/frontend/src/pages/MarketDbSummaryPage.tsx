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
import { swrHydrate, swrStore } from "../api/swr";
import { useToast } from "../components/shared/Toast";
import { tPlatform, useLang } from "../i18n";


interface OverviewResp {
  novel_count: number;
  rank_list_count: number;
  snapshot_count: number;
  chapter_count: number;
  recent_snapshots: Array<{
    snapshot_id: number;
    rank_list_id: number;
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


interface RankSnapshotTarget {
  rank_list_id: number; snapshot_id: number; snapshot_date?: string;
  item_count?: number; platform?: string; rank_family?: string; rank_sub_cat?: string;
}

export default function MarketDbSummaryPage({ onOpenSnapshot }: { onOpenSnapshot?: (t: RankSnapshotTarget) => void } = {}) {
  const { toast } = useToast();
  useLang();  // re-render on language change
  // 秒开: 同步水合上次概览快照，后台刷新（stale-while-revalidate）。
  const [data, setData] = useState<OverviewResp | null>(
    () => swrHydrate<OverviewResp>("market_overview_all"),
  );
  const [loading, setLoading] = useState(false);
  const [platform, setPlatform] = useState("");

  const load = useCallback(async () => {
    const swrKey = `market_overview_${platform || "all"}`;
    const cached = swrHydrate<OverviewResp>(swrKey);
    if (cached) setData(cached);
    setLoading(true);
    try {
      const qs = platform ? `?platform=${encodeURIComponent(platform)}` : "";
      const r = await apiGet<OverviewResp>(`/api/db/overview${qs}`);
      setData(r);
      swrStore(swrKey, r);
    } catch (e: any) {
      toast(`加载失败: ${e.message}`, "error");
    } finally {
      setLoading(false);
    }
  }, [platform, toast]);

  useEffect(() => { load(); }, [load]);

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
            <PlatformDistributionChart breakdown={data!.platform_breakdown} />
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
                <th style={{ width: 28 }}></th>
              </tr>
            </thead>
            <tbody>
              {data!.recent_snapshots.map((s, i) => {
                const clickable = !!onOpenSnapshot && s.rank_list_id != null && s.snapshot_id != null;
                return (
                  <tr
                    key={`${s.snapshot_id ?? s.snapshot_date}-${i}`}
                    onClick={clickable ? () => onOpenSnapshot!({
                      rank_list_id: s.rank_list_id, snapshot_id: s.snapshot_id,
                      snapshot_date: s.snapshot_date, item_count: s.item_count,
                      platform: s.platform, rank_family: s.rank_family,
                      rank_sub_cat: s.rank_sub_cat,
                    }) : undefined}
                    style={{
                      borderTop: "1px solid var(--border)",
                      cursor: clickable ? "pointer" : "default",
                    }}
                    onMouseEnter={clickable ? (e) => (e.currentTarget.style.background = "var(--bg-surface-hover, var(--bg-surface-2))") : undefined}
                    onMouseLeave={clickable ? (e) => (e.currentTarget.style.background = "transparent") : undefined}
                    title={clickable ? "查看该快照详情" : undefined}
                  >
                    <td style={{ padding: "4px 6px" }}>{s.snapshot_date}</td>
                    <td style={{ padding: "4px 6px" }}>{tPlatform(s.platform)}</td>
                    <td style={{ padding: "4px 6px" }}>
                      {s.rank_family}
                      {s.rank_sub_cat && s.rank_sub_cat !== s.rank_family ? ` · ${s.rank_sub_cat}` : ""}
                    </td>
                    <td style={{ padding: "4px 6px", textAlign: "right" }}>{s.item_count}</td>
                    <td style={{ padding: "4px 6px", textAlign: "center", color: "var(--text-disabled)" }}>
                      {clickable ? "›" : ""}
                    </td>
                  </tr>
                );
              })}
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


/**
 * PlatformDistributionChart — a richer view when there are only a few
 * platforms (typically 起点 / 番茄). Renders:
 *   - a hero stat row with total + per-platform share %
 *   - a 100% stacked ratio bar so the relative split reads at a glance
 *   - per-platform breakdown cards with count, share, and an accent dot
 */
function PlatformDistributionChart({
  breakdown,
}: { breakdown: Array<{ platform: string; count: number }> }) {
  const total = breakdown.reduce((s, p) => s + (p.count || 0), 0) || 1;
  // Color palette keyed by platform; falls back through an accent cycle
  // for platforms we don't know about.
  const FALLBACK_COLORS = ["var(--accent)", "var(--jade)", "var(--gold)", "var(--indigo)", "var(--cyan)"];
  const PLATFORM_COLORS: Record<string, string> = {
    qidian:   "var(--accent)",
    fanqie:   "var(--gold)",
    zongheng: "var(--jade)",
    "17k":    "var(--indigo)",
    jjwxc:    "var(--cyan)",
  };
  const colorFor = (key: string, i: number) =>
    PLATFORM_COLORS[key] || FALLBACK_COLORS[i % FALLBACK_COLORS.length];

  const sorted = [...breakdown].sort((a, b) => b.count - a.count);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {/* Hero — total */}
      <div style={{
        padding: "8px 0 4px",
        display: "flex", alignItems: "baseline", gap: 10,
        borderBottom: "1px solid var(--border)",
      }}>
        <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>合计</span>
        <span style={{ fontSize: 26, fontWeight: 700, fontFamily: "var(--font-mono)" }}>
          {total.toLocaleString()}
        </span>
        <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
          部作品 · {sorted.length} 个平台
        </span>
      </div>

      {/* 100% stacked ratio bar */}
      <div style={{
        height: 28, display: "flex", borderRadius: 6, overflow: "hidden",
        boxShadow: "inset 0 0 0 1px var(--border)",
      }}>
        {sorted.map((p, i) => {
          const pct = (p.count / total) * 100;
          if (pct < 0.01) return null;
          return (
            <div
              key={p.platform}
              title={`${tPlatform(p.platform)} · ${p.count.toLocaleString()} · ${pct.toFixed(1)}%`}
              style={{
                width: `${pct}%`,
                background: colorFor(p.platform, i),
                position: "relative",
                display: "flex", alignItems: "center", justifyContent: "center",
                color: "white", fontSize: 11, fontWeight: 600,
                transition: "width 0.4s ease",
              }}
            >
              {pct > 8 ? `${pct.toFixed(1)}%` : ""}
            </div>
          );
        })}
      </div>

      {/* Per-platform tiles */}
      <div style={{
        display: "grid",
        gridTemplateColumns: `repeat(${Math.min(sorted.length, 4)}, 1fr)`,
        gap: 10,
      }}>
        {sorted.map((p, i) => {
          const accent = colorFor(p.platform, i);
          const pct = (p.count / total) * 100;
          return (
            <div
              key={p.platform}
              style={{
                padding: 12,
                background: "var(--bg-surface-2)",
                borderRadius: 6,
                borderLeft: `3px solid ${accent}`,
              }}
            >
              <div style={{
                display: "flex", alignItems: "center", gap: 6,
                fontSize: 12, color: "var(--text-secondary)",
              }}>
                <span style={{
                  width: 8, height: 8, borderRadius: "50%", background: accent,
                  display: "inline-block",
                }} />
                {tPlatform(p.platform)}
              </div>
              <div style={{
                fontSize: 20, fontWeight: 700, fontFamily: "var(--font-mono)",
                marginTop: 4, color: "var(--text-primary)",
              }}>
                {p.count.toLocaleString()}
              </div>
              <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 2 }}>
                占比 {pct.toFixed(1)}%
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
