import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiGet } from "../api/client";
import type { ReferenceWork, MediaType } from "../api/types";

const MEDIA_TYPES: { value: MediaType; label: string; color: string }[] = [
  { value: "web_novel", label: "网文", color: "var(--accent)" },
  { value: "literature", label: "文学", color: "var(--jade)" },
  { value: "poetry", label: "诗歌", color: "var(--purple)" },
  { value: "film", label: "电影", color: "var(--gold)" },
  { value: "anime", label: "动漫", color: "#f472b6" },
  { value: "tv_series", label: "电视剧", color: "var(--indigo)" },
  { value: "other", label: "其他", color: "var(--text-tertiary)" },
];
const mediaLabel = (mt: string) => MEDIA_TYPES.find(m => m.value === mt)?.label || mt;
const mediaColor = (mt: string) => MEDIA_TYPES.find(m => m.value === mt)?.color || "var(--text-tertiary)";

function pj(s: string | null | undefined): any {
  if (!s) return null;
  try { return JSON.parse(s); } catch { return null; }
}

function stars(n: number | null | undefined): string {
  if (!n) return "—";
  return "★".repeat(n) + "☆".repeat(5 - n);
}

interface Props {
  onNavigate?: (tab: string) => void;
  onSelectWork?: (refId: string) => void;
}

export default function ReferenceOverviewPage({ onNavigate }: Props) {
  const [works, setWorks] = useState<ReferenceWork[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      // Pull a large page; the API caps at 500
      const r = await apiGet<{ items: ReferenceWork[]; total: number }>(
        "/api/references/works?limit=500"
      );
      setWorks(r.items || []);
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const stats = useMemo(() => {
    const byMedia: Record<string, number> = {};
    const byGenre: Record<string, number> = {};
    const byStatus: Record<string, number> = { not_applicable: 0, pending: 0, processing: 0, done: 0, error: 0 };
    let withFullText = 0, withPlot = 0, withCharacters = 0;
    let totalRatings = 0, ratedCount = 0;
    for (const w of works) {
      byMedia[w.media_type] = (byMedia[w.media_type] || 0) + 1;
      const g = (w.genre || "").trim();
      if (g) byGenre[g] = (byGenre[g] || 0) + 1;
      byStatus[w.preprocessing_status] = (byStatus[w.preprocessing_status] || 0) + 1;
      if (w.has_full_text) withFullText++;
      if (w.plot_outline_json) withPlot++;
      const chars = pj(w.extracted_characters_json);
      if (Array.isArray(chars) && chars.length) withCharacters++;
      if (w.user_rating) { totalRatings += w.user_rating; ratedCount++; }
    }
    return {
      total: works.length,
      byMedia, byGenre, byStatus,
      withFullText, withPlot, withCharacters,
      avgRating: ratedCount > 0 ? totalRatings / ratedCount : null,
      ratedCount,
    };
  }, [works]);

  const recent = useMemo(() =>
    [...works].sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || "")).slice(0, 6),
    [works]
  );
  const topRated = useMemo(() =>
    works.filter(w => w.user_rating).sort((a, b) => (b.user_rating || 0) - (a.user_rating || 0)).slice(0, 6),
    [works]
  );

  const open = () => onNavigate?.("references");

  return (
    <div className="page-full">
      <div className="page-header" style={{ paddingBottom: 12 }}>
        <div className="page-header-row">
          <div>
            <h2>数据库概览</h2>
            <p>参考作品数据库的全局视图 · 共 {stats.total} 部作品</p>
          </div>
          <div className="flex gap-8">
            <button className="btn" onClick={load}>刷新</button>
            <button className="btn-primary" onClick={open}>进入参考作品库</button>
          </div>
        </div>
      </div>

      {loading && stats.total === 0 ? (
        <div className="empty-state" style={{ paddingTop: 60 }}><p>加载中...</p></div>
      ) : stats.total === 0 ? (
        <div className="empty-state" style={{ paddingTop: 60 }}>
          <h4>参考库空空如也</h4>
          <p>前往参考作品库添加你喜欢的作品作为创作参考。</p>
          <button className="btn-primary" style={{ marginTop: 16 }} onClick={open}>
            进入参考作品库
          </button>
        </div>
      ) : (
        <>
          {/* Top KPI row */}
          <div style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(170px, 1fr))",
            gap: 10,
            marginBottom: 18,
          }}>
            <KpiTile label="作品总数" value={stats.total} accent="var(--accent)" />
            <KpiTile label="已上传正文" value={stats.withFullText}
              hint={`${pct(stats.withFullText, stats.total)}%`} accent="var(--jade)" />
            <KpiTile label="已完成分析" value={stats.byStatus.done || 0}
              hint={`${pct(stats.byStatus.done || 0, stats.total)}%`} accent="var(--jade)" />
            <KpiTile label="已生成大纲" value={stats.withPlot}
              hint={`${pct(stats.withPlot, stats.total)}%`} accent="var(--purple)" />
            <KpiTile label="已提取角色" value={stats.withCharacters}
              hint={`${pct(stats.withCharacters, stats.total)}%`} accent="var(--indigo)" />
            <KpiTile label="平均评分"
              value={stats.avgRating == null ? "—" : stats.avgRating.toFixed(1)}
              hint={`${stats.ratedCount} 部已评分`} accent="var(--gold)" />
          </div>

          {/* Two-column block: media + status; genre cloud */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 18 }}>
            {/* By media type */}
            <div className="card">
              <div className="card-header"><h3>按类型分布</h3></div>
              <div className="card-body">
                <StackedBar
                  total={stats.total}
                  segments={MEDIA_TYPES
                    .filter(m => (stats.byMedia[m.value] || 0) > 0)
                    .map(m => ({ key: m.value, label: m.label, value: stats.byMedia[m.value] || 0, color: m.color }))}
                />
              </div>
            </div>

            {/* By processing status */}
            <div className="card">
              <div className="card-header"><h3>处理状态</h3></div>
              <div className="card-body">
                <StackedBar
                  total={stats.total}
                  segments={[
                    { key: "done", label: "已完成", value: stats.byStatus.done || 0, color: "var(--jade)" },
                    { key: "processing", label: "处理中", value: stats.byStatus.processing || 0, color: "var(--gold)" },
                    { key: "pending", label: "待处理", value: stats.byStatus.pending || 0, color: "var(--accent)" },
                    { key: "error", label: "出错", value: stats.byStatus.error || 0, color: "var(--error)" },
                    { key: "not_applicable", label: "手动", value: stats.byStatus.not_applicable || 0, color: "var(--text-tertiary)" },
                  ].filter(s => s.value > 0)}
                />
              </div>
            </div>
          </div>

          {/* Genre distribution */}
          {Object.keys(stats.byGenre).length > 0 && (
            <div className="card" style={{ marginBottom: 18 }}>
              <div className="card-header"><h3>题材分布</h3></div>
              <div className="card-body">
                <div className="flex gap-6" style={{ flexWrap: "wrap" }}>
                  {Object.entries(stats.byGenre)
                    .sort((a, b) => b[1] - a[1])
                    .slice(0, 30)
                    .map(([g, n]) => (
                      <span key={g} className="tag" style={{
                        fontSize: 11 + Math.min(6, Math.floor(n / 2)),
                        padding: `3px ${8 + Math.min(8, n)}px`,
                        color: "var(--text-primary)",
                        background: "var(--bg-surface)",
                        border: "1px solid var(--border)",
                      }}>
                        {g} <span className="text-muted" style={{ marginLeft: 4, fontSize: 11 }}>{n}</span>
                      </span>
                    ))}
                </div>
              </div>
            </div>
          )}

          {/* Recent + top-rated lists */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            <WorkListCard title="最近更新" works={recent} onNavigate={open} />
            <WorkListCard title="高评分作品" works={topRated} onNavigate={open}
              emptyHint="尚无已评分的作品。" showRating />
          </div>
        </>
      )}
    </div>
  );
}

/* ───── Helpers ───── */

function pct(n: number, total: number): number {
  return total === 0 ? 0 : Math.round((n / total) * 100);
}

function KpiTile({ label, value, hint, accent }: { label: string; value: number | string; hint?: string; accent?: string }) {
  return (
    <div style={{
      padding: "14px 16px",
      background: "var(--bg-surface)",
      border: "1px solid var(--border)",
      borderRadius: "var(--radius-sm)",
      borderLeft: `3px solid ${accent || "var(--accent)"}`,
    }}>
      <div className="text-xs text-muted" style={{ marginBottom: 6 }}>{label}</div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span style={{ fontSize: 24, fontWeight: 700, color: "var(--text-primary)", lineHeight: 1 }}>{value}</span>
        {hint && <span className="text-xs text-muted">{hint}</span>}
      </div>
    </div>
  );
}

function StackedBar({ total, segments }: { total: number; segments: { key: string; label: string; value: number; color: string }[] }) {
  return (
    <>
      <div style={{
        display: "flex", height: 18, borderRadius: 4, overflow: "hidden",
        background: "var(--bg-surface-2)", marginBottom: 10,
      }}>
        {segments.map(s => (
          <div key={s.key} title={`${s.label} · ${s.value}`}
            style={{ width: `${(s.value / Math.max(total, 1)) * 100}%`, background: s.color }} />
        ))}
      </div>
      <div className="flex gap-12" style={{ flexWrap: "wrap", fontSize: 12 }}>
        {segments.map(s => (
          <div key={s.key} className="flex items-center gap-6">
            <span style={{ width: 10, height: 10, borderRadius: 2, background: s.color, display: "inline-block" }} />
            <span style={{ color: "var(--text-secondary)" }}>{s.label}</span>
            <span style={{ fontWeight: 600 }}>{s.value}</span>
            <span className="text-xs text-muted">({pct(s.value, total)}%)</span>
          </div>
        ))}
      </div>
    </>
  );
}

function WorkListCard({ title, works, onNavigate, emptyHint, showRating }: {
  title: string; works: ReferenceWork[]; onNavigate: () => void;
  emptyHint?: string; showRating?: boolean;
}) {
  return (
    <div className="card">
      <div className="card-header"><h3>{title}</h3></div>
      <div className="card-body" style={{ padding: 0 }}>
        {works.length === 0 ? (
          <div className="text-xs text-muted text-center" style={{ padding: 18 }}>
            {emptyHint || "暂无数据"}
          </div>
        ) : (
          <div className="flex flex-col">
            {works.map(w => {
              const plot = pj(w.plot_outline_json);
              const preview: string = plot?.logline || (() => {
                for (const ep of (plot?.epochs || [])) {
                  for (const per of (ep.periods || [])) {
                    for (const ev of (per.events || [])) {
                      if (ev.description) return ev.description;
                    }
                  }
                }
                return "";
              })();
              return (
                <div
                  key={w.ref_id}
                  onClick={onNavigate}
                  style={{
                    padding: "10px 16px",
                    cursor: "pointer",
                    borderTop: "1px solid var(--border)",
                  }}
                  onMouseEnter={e => (e.currentTarget.style.background = "var(--bg-surface-hover)")}
                  onMouseLeave={e => (e.currentTarget.style.background = "transparent")}
                >
                  <div className="flex items-center justify-between" style={{ marginBottom: 4 }}>
                    <span className="truncate" style={{ fontWeight: 600, fontSize: 13 }}>{w.title}</span>
                    <div className="flex items-center gap-6 text-xs">
                      {showRating && w.user_rating ? (
                        <span style={{ color: "var(--gold)" }}>{stars(w.user_rating)}</span>
                      ) : null}
                      <span style={{ color: mediaColor(w.media_type) }}>{mediaLabel(w.media_type)}</span>
                    </div>
                  </div>
                  {preview && (
                    <div style={{
                      fontSize: 12, lineHeight: 1.5,
                      color: "var(--text-secondary)",
                      display: "-webkit-box",
                      WebkitBoxOrient: "vertical",
                      WebkitLineClamp: 2,
                      overflow: "hidden",
                    }}>{preview}</div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
