import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiGet, apiPost } from "../api/client";
import { useToast } from "../components/shared/Toast";
import type { ReferenceWork, MediaType } from "../api/types";
import { splitGenres } from "../utils/genre";

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

const SERIAL_LABEL: Record<string, { label: string; color: string }> = {
  ongoing:   { label: "连载中", color: "var(--gold)" },
  completed: { label: "已完结", color: "var(--jade)" },
  hiatus:    { label: "停更",   color: "var(--text-tertiary)" },
  unknown:   { label: "未知",   color: "var(--text-tertiary)" },
};

const EPISODE_MEDIA = new Set<string>(["film", "tv_series", "anime"]);

function pj(s: string | null | undefined): any {
  if (!s) return null;
  try { return JSON.parse(s); } catch { return null; }
}

function stars(n: number | null | undefined): string {
  if (!n) return "—";
  return "★".repeat(n) + "☆".repeat(5 - n);
}

function pct(n: number, total: number): number {
  return total === 0 ? 0 : Math.round((n / total) * 100);
}

function fmtChars(n: number): string {
  if (!n) return "—";
  if (n >= 10000) return `${(n / 10000).toFixed(1)} 万字`;
  return `${n.toLocaleString()} 字`;
}

interface ChapterMetrics {
  chapters: number;
  volumes: number;
  charCount: number;
  type: "volumes" | "chunks" | "none";
}

function readChapterMetrics(w: ReferenceWork): ChapterMetrics {
  const seg = pj(w.segments_json) as any;
  const plan: any[] = seg?.plan || [];
  if (!plan.length) {
    return { chapters: 0, volumes: 0, charCount: 0, type: "none" };
  }
  const type = (seg.type === "volumes" ? "volumes" : "chunks") as "volumes" | "chunks";
  let endMax = 0, chars = 0;
  for (const p of plan) {
    if (typeof p.end_chapter === "number") endMax = Math.max(endMax, p.end_chapter);
    if (typeof p.char_count === "number") chars += p.char_count;
  }
  return {
    chapters: endMax,
    volumes: type === "volumes" ? plan.length : 0,
    charCount: chars,
    type,
  };
}

interface CompletenessFlags {
  text: boolean;
  plot: boolean;
  chars: boolean;
  settings: boolean;
  style: boolean;
}

function readCompleteness(w: ReferenceWork): CompletenessFlags {
  return {
    text: Boolean(w.has_full_text),
    plot: Boolean(w.plot_outline_json) && (() => {
      const p = pj(w.plot_outline_json);
      return Boolean(p && ((p.epochs && p.epochs.length) || p.logline));
    })(),
    chars: (() => {
      const c = pj(w.extracted_characters_json);
      return Array.isArray(c) && c.length > 0;
    })(),
    settings: (() => {
      const s = pj(w.settings_json);
      return Array.isArray(s) && s.length > 0;
    })(),
    style: Boolean(w.style_fingerprint_json) && (() => {
      const fp = pj(w.style_fingerprint_json);
      return Boolean(fp && Object.keys(fp).length > 0);
    })(),
  };
}

function completenessScore(c: CompletenessFlags): number {
  return (Number(c.text) + Number(c.plot) + Number(c.chars) + Number(c.settings) + Number(c.style)) / 5;
}

interface Capability {
  enabled: boolean;
  provider: string;
  model: string;
  reason: string;
}

interface Props {
  onNavigate?: (tab: string) => void;
  onSelectWork?: (refId: string) => void;
}

export default function ReferenceOverviewPage({ onNavigate }: Props) {
  const { toast } = useToast();
  const [works, setWorks] = useState<ReferenceWork[]>([]);
  const [loading, setLoading] = useState(true);
  const [capability, setCapability] = useState<Capability | null>(null);
  const [genreFilter, setGenreFilter] = useState<string>("");
  const [completingIds, setCompletingIds] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await apiGet<{ items: ReferenceWork[]; total: number }>(
        "/api/references/works?limit=500"
      );
      setWorks(r.items || []);
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  }, []);

  const loadCapability = useCallback(async () => {
    try {
      const c = await apiGet<Capability>("/api/references/web_search/capability");
      setCapability(c);
    } catch {
      setCapability({ enabled: false, provider: "", model: "", reason: "无法获取联网能力状态" });
    }
  }, []);

  useEffect(() => { load(); loadCapability(); }, [load, loadCapability]);

  const stats = useMemo(() => {
    const byMedia: Record<string, number> = {};
    const byGenre: Record<string, number> = {};
    const byStatus: Record<string, number> = { not_applicable: 0, pending: 0, processing: 0, done: 0, error: 0 };
    let withFullText = 0, withPlot = 0, withCharacters = 0;
    let totalRatings = 0, ratedCount = 0;
    for (const w of works) {
      byMedia[w.media_type] = (byMedia[w.media_type] || 0) + 1;
      // Split multi-tag genre strings so each tag contributes independently.
      for (const g of splitGenres(w.genre)) {
        byGenre[g] = (byGenre[g] || 0) + 1;
      }
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

  const filteredWorks = useMemo(() => {
    if (!genreFilter) return works;
    return works.filter(w => splitGenres(w.genre).includes(genreFilter));
  }, [works, genreFilter]);

  const open = () => onNavigate?.("references");

  const openDetail = (refId: string) => {
    try { sessionStorage.setItem("ref_open_ref_id", refId); } catch { /* noop */ }
    onNavigate?.("references");
  };

  const runAiComplete = async (refId: string) => {
    setCompletingIds(prev => new Set(prev).add(refId));
    try {
      const r = await apiPost<any>(
        `/api/references/works/${refId}/ai_complete`, {},
        { timeoutMs: 90_000 },
      );
      if (!r.updated_keys || r.updated_keys.length === 0) {
        toast(r.message || "未更新任何字段", "info");
      } else {
        toast(`已补全：${r.updated_keys.join(" · ")}`, "success");
      }
      // patch the work in-place
      if (r.work) {
        setWorks(prev => prev.map(w => w.ref_id === refId ? r.work as ReferenceWork : w));
      }
    } catch (e: any) {
      toast(e?.message || "联网补全失败", "error");
    } finally {
      setCompletingIds(prev => {
        const next = new Set(prev);
        next.delete(refId);
        return next;
      });
    }
  };

  return (
    <div className="page-full">
      <div className="page-header" style={{ paddingBottom: 12 }}>
        <div className="page-header-row">
          <div>
            <h2>数据库概览</h2>
            <p>参考作品数据库的全局视图 · 共 {stats.total} 部作品</p>
          </div>
          <div className="flex gap-8">
            <button className="btn" onClick={() => { load(); loadCapability(); }}>刷新</button>
            <button className="btn-primary" onClick={open}>进入参考作品详情</button>
          </div>
        </div>
      </div>

      {loading && stats.total === 0 ? (
        <div className="empty-state" style={{ paddingTop: 60 }}><p>加载中...</p></div>
      ) : stats.total === 0 ? (
        <div className="empty-state" style={{ paddingTop: 60 }}>
          <h4>参考库空空如也</h4>
          <p>前往参考作品详情添加你喜欢的作品作为创作参考。</p>
          <button className="btn-primary" style={{ marginTop: 16 }} onClick={open}>
            进入参考作品详情
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

          {/* Two stacked-bar cards */}
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 18 }}>
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

          {/* Genre bar chart (clickable to filter) */}
          {Object.keys(stats.byGenre).length > 0 && (
            <div className="card" style={{ marginBottom: 18 }}>
              <div className="card-header" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
                <h3>题材分布</h3>
                {genreFilter && (
                  <div className="flex items-center gap-8">
                    <span className="text-xs text-muted">
                      已筛选: <span style={{ color: "var(--accent)", fontWeight: 600 }}>{genreFilter}</span>
                    </span>
                    <button
                      className="btn"
                      style={{ fontSize: 11, padding: "2px 10px" }}
                      onClick={() => setGenreFilter("")}
                    >清除</button>
                  </div>
                )}
              </div>
              <div className="card-body">
                <GenreBarChart
                  byGenre={stats.byGenre}
                  selected={genreFilter}
                  onSelect={g => setGenreFilter(g === genreFilter ? "" : g)}
                />
              </div>
            </div>
          )}

          {/* Per-work attribute grid */}
          <div className="card">
            <div className="card-header" style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
              <h3>
                作品列表
                {genreFilter && (
                  <span className="text-xs text-muted" style={{ marginLeft: 8, fontWeight: 400 }}>
                    （筛选: {genreFilter} · {filteredWorks.length} / {works.length}）
                  </span>
                )}
              </h3>
              <CapabilityBadge capability={capability} />
            </div>
            <div className="card-body">
              <WorkAttributeGrid
                works={filteredWorks}
                capability={capability}
                completingIds={completingIds}
                onOpen={openDetail}
                onAiComplete={runAiComplete}
              />
            </div>
          </div>
        </>
      )}
    </div>
  );
}

/* ───── Helpers ───── */

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

function GenreBarChart({ byGenre, selected, onSelect }: {
  byGenre: Record<string, number>;
  selected: string;
  onSelect: (g: string) => void;
}) {
  const rows = Object.entries(byGenre)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 20);
  const max = rows[0]?.[1] || 1;
  return (
    <div className="flex flex-col gap-4">
      {rows.map(([g, n]) => {
        const isSel = g === selected;
        const width = (n / max) * 100;
        return (
          <button
            key={g}
            onClick={() => onSelect(g)}
            className="btn-ghost"
            style={{
              display: "grid",
              gridTemplateColumns: "100px 1fr 36px",
              alignItems: "center",
              gap: 10,
              padding: "4px 6px",
              borderRadius: 3,
              background: isSel ? "var(--accent-subtle)" : "transparent",
              cursor: "pointer",
              width: "100%",
              textAlign: "left",
            }}
          >
            <span
              style={{
                fontSize: 12,
                fontWeight: isSel ? 700 : 500,
                color: isSel ? "var(--accent)" : "var(--text-primary)",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
              }}
              title={g}
            >{g}</span>
            <div style={{ height: 10, borderRadius: 3, background: "var(--bg-surface-2)", overflow: "hidden" }}>
              <div style={{
                height: "100%",
                width: `${width}%`,
                background: isSel ? "var(--accent)" : "var(--purple)",
                borderRadius: 3,
                transition: "width 0.2s",
              }} />
            </div>
            <span style={{
              fontSize: 12, fontFamily: "var(--font-mono)",
              fontWeight: 600,
              color: isSel ? "var(--accent)" : "var(--text-secondary)",
              textAlign: "right",
            }}>{n}</span>
          </button>
        );
      })}
    </div>
  );
}

function CapabilityBadge({ capability }: { capability: Capability | null }) {
  if (!capability) return null;
  const enabled = capability.enabled;
  return (
    <span
      title={capability.reason}
      className="tag"
      style={{
        fontSize: 11, padding: "2px 8px",
        background: enabled ? "var(--accent-subtle)" : "var(--bg-surface-2)",
        color: enabled ? "var(--accent)" : "var(--text-tertiary)",
        border: `1px solid ${enabled ? "var(--accent)" : "var(--border)"}`,
        cursor: "help",
      }}
    >
      AI 联网补全：{enabled ? "可用" : "未配置"}
    </span>
  );
}

function WorkAttributeGrid({ works, capability, completingIds, onOpen, onAiComplete }: {
  works: ReferenceWork[];
  capability: Capability | null;
  completingIds: Set<string>;
  onOpen: (refId: string) => void;
  onAiComplete: (refId: string) => void;
}) {
  if (works.length === 0) {
    return (
      <div className="text-xs text-muted text-center" style={{ padding: 24 }}>
        当前筛选无匹配作品。
      </div>
    );
  }
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "repeat(auto-fill, minmax(320px, 1fr))",
      gap: 12,
    }}>
      {works.map(w => (
        <WorkCard
          key={w.ref_id}
          w={w}
          capability={capability}
          completing={completingIds.has(w.ref_id)}
          onOpen={() => onOpen(w.ref_id)}
          onAiComplete={() => onAiComplete(w.ref_id)}
        />
      ))}
    </div>
  );
}

function WorkCard({ w, capability, completing, onOpen, onAiComplete }: {
  w: ReferenceWork;
  capability: Capability | null;
  completing: boolean;
  onOpen: () => void;
  onAiComplete: () => void;
}) {
  const ch = readChapterMetrics(w);
  const c = readCompleteness(w);
  const score = completenessScore(c);
  const isEpisode = EPISODE_MEDIA.has(w.media_type);
  const genres = splitGenres(w.genre);
  const serialKey = (w.serial_status || "unknown") as keyof typeof SERIAL_LABEL;
  const serial = SERIAL_LABEL[serialKey] || SERIAL_LABEL.unknown;
  const canAi = !!capability?.enabled && !completing;
  const aiTooltip = capability?.enabled
    ? `通过 ${capability.provider}/${capability.model} 联网补全空字段`
    : (capability?.reason || "未配置联网模型");

  return (
    <div style={{
      padding: 12,
      background: "var(--bg-surface)",
      border: "1px solid var(--border)",
      borderRadius: "var(--radius-sm)",
      borderLeft: `3px solid ${mediaColor(w.media_type)}`,
      display: "flex", flexDirection: "column", gap: 8,
    }}>
      {/* Title row */}
      <div className="flex items-start" style={{ gap: 8 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="truncate" style={{ fontSize: 14, fontWeight: 700, color: "var(--text-primary)" }}>
            {w.title}
          </div>
          <div className="flex gap-6 items-center" style={{ flexWrap: "wrap", marginTop: 3, fontSize: 11 }}>
            <span style={{ color: mediaColor(w.media_type), fontWeight: 600 }}>{mediaLabel(w.media_type)}</span>
            {w.creator && <span className="text-muted">· {w.creator}</span>}
            {w.user_rating ? <span style={{ color: "var(--gold)" }}>{stars(w.user_rating)}</span> : null}
          </div>
        </div>
        <span className="tag" title={`连载状态：${serial.label}`} style={{
          fontSize: 10, padding: "1px 7px",
          color: serial.color,
          background: "var(--bg-surface-2)",
          border: `1px solid ${serial.color}`,
          whiteSpace: "nowrap",
        }}>{serial.label}</span>
      </div>

      {/* Genre chips */}
      {genres.length > 0 && (
        <div className="flex gap-4" style={{ flexWrap: "wrap" }}>
          {genres.map(g => (
            <span key={g} className="tag" style={{
              fontSize: 10, padding: "1px 6px",
              background: "var(--bg-surface-2)",
              color: "var(--text-secondary)",
              border: "1px solid var(--border)",
            }}>{g}</span>
          ))}
        </div>
      )}

      {/* Chapter / volume / word counts */}
      <div className="text-xs text-muted" style={{ lineHeight: 1.5 }}>
        {ch.chapters > 0 ? (
          <>
            {ch.volumes > 0 && <span>{ch.volumes} 卷 · </span>}
            <span>{ch.chapters} {isEpisode ? "集" : "章"}</span>
            {ch.charCount > 0 && <span> · {fmtChars(ch.charCount)}</span>}
          </>
        ) : c.text ? (
          <span>已上传正文（尚未分段）</span>
        ) : (
          <span style={{ color: "var(--text-tertiary)", fontStyle: "italic" }}>未上传正文</span>
        )}
      </div>

      {/* Completeness pills */}
      <div>
        <div className="flex items-center justify-between" style={{ marginBottom: 4 }}>
          <span className="text-xs text-muted">数据完整度</span>
          <span className="text-xs" style={{
            fontFamily: "var(--font-mono)",
            color: score >= 0.6 ? "var(--jade)" : score >= 0.3 ? "var(--gold)" : "var(--text-tertiary)",
          }}>{Math.round(score * 100)}%</span>
        </div>
        <div className="flex gap-4">
          {[
            { k: "text", label: "正文", on: c.text },
            { k: "plot", label: "大纲", on: c.plot },
            { k: "chars", label: "角色", on: c.chars },
            { k: "settings", label: "设定", on: c.settings },
            { k: "style", label: "特征", on: c.style },
          ].map(p => (
            <span key={p.k} className="tag" title={`${p.label}${p.on ? "（已生成）" : "（缺）"}`} style={{
              fontSize: 9.5, padding: "1px 6px", flex: 1, textAlign: "center",
              background: p.on ? "var(--accent-subtle)" : "var(--bg-surface-2)",
              color: p.on ? "var(--jade)" : "var(--text-tertiary)",
              border: `1px solid ${p.on ? "var(--jade)" : "var(--border)"}`,
            }}>{p.label}</span>
          ))}
        </div>
      </div>

      {/* Actions */}
      <div className="flex gap-6" style={{ marginTop: "auto" }}>
        <button
          className="btn"
          style={{ fontSize: 11, padding: "4px 10px", flex: 1, opacity: canAi ? 1 : 0.5 }}
          onClick={onAiComplete}
          disabled={!canAi}
          title={aiTooltip}
        >
          {completing ? "联网中…" : "AI 补全"}
        </button>
        <button
          className="btn-primary"
          style={{ fontSize: 11, padding: "4px 10px", flex: 1 }}
          onClick={onOpen}
        >
          打开详情
        </button>
      </div>
    </div>
  );
}
