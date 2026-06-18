import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiGet, apiDelete } from "../api/client";
import { swrHydrate, swrStore } from "../api/swr";
import type { ReferenceWork, MediaType, SkillInfo } from "../api/types";
import { splitGenres } from "../utils/genre";
import { useToast } from "../components/shared/Toast";
import { useDialog } from "../components/shared/Dialog";
import ReferenceSearchPage from "./ReferenceSearchPage";
import CompareWorksPanel from "../components/CompareWorksPanel";
import CommonPatternLearningPanel from "../components/reference/CommonPatternLearningPanel";

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

/** Parse the pure-setting raw_entries list out of `quick_input_text`.
 *  Returns [] for legacy plain-text values. */
function parseRawEntries(raw: string | undefined | null): { title: string; content: string }[] {
  if (!raw) return [];
  try {
    const v = JSON.parse(raw);
    if (Array.isArray(v)) {
      return v
        .filter((x: any) => x && typeof x === "object")
        .map((x: any) => ({
          title: String(x.title || ""),
          content: String(x.content || ""),
        }))
        .filter(e => e.title || e.content);
    }
  } catch { /* legacy plain string */ }
  return [];
}

/** Count source / extracted items for a pure-setting (setting_collection) work. */
interface PureSettingCounts {
  entries: number;
  settings: number;
  characters: number;
  features: number;
}

function readPureSettingCounts(w: ReferenceWork): PureSettingCounts {
  const entries = parseRawEntries(w.quick_input_text);
  const settings = pj(w.settings_json);
  const characters = pj(w.static_characters_json);
  const features = pj(w.setting_features_json);
  return {
    entries: entries.length,
    settings: Array.isArray(settings) ? settings.length : 0,
    characters: Array.isArray(characters) ? characters.length : 0,
    features: Array.isArray(features) ? features.length : 0,
  };
}

function pureSettingHasContent(w: ReferenceWork): boolean {
  const c = readPureSettingCounts(w);
  return c.entries > 0 || c.settings > 0 || c.characters > 0;
}

interface FeatureProgress {
  total: number;
  events: number;
  characters: number;
  settings: number;
  style: number;
}

/** Per-feature chapter coverage from the unified-extraction ledger
 *  (style_fingerprint_json._chunks): a committed chunk's chapter span
 *  is the length of its per-chapter signal list. */
function readFeatureProgress(w: ReferenceWork): FeatureProgress {
  const fp = pj(w.style_fingerprint_json) as any;
  const chunks = (fp && fp._chunks) || {};
  const acc = { events: 0, characters: 0, settings: 0, style: 0 };
  for (const key of Object.keys(chunks)) {
    const e = chunks[key] || {};
    const span = Array.isArray(e.fp?.chapter_signals) ? e.fp.chapter_signals.length : 0;
    const done = e.done || {};
    if (done.events) acc.events += span;
    if (done.characters) acc.characters += span;
    if (done.settings) acc.settings += span;
    if (done.style) acc.style += span;
  }
  const sigTotal = Array.isArray(fp?.chapter_signals) ? fp.chapter_signals.length : 0;
  return { total: readChapterMetrics(w).chapters || sigTotal, ...acc };
}

type RefTab = "overview" | "search" | "compare_learn" | "index";

interface Props {
  onNavigate?: (tab: string) => void;
  onSelectWork?: (refId: string) => void;
  /** Open directly on a specific tab. */
  initialTab?: RefTab;
}

export default function ReferenceOverviewPage({ onNavigate, initialTab }: Props) {
  // 顶部 tab：总览 / 作品搜索 / 作品对比 & 共通点学习 / 索引管理。
  // 数据库工具 subtab 与 ReferenceSearchPage 内部 tab strip 都已撤销，
  // 这四个全部平铺在参考总览的顶层。
  const [subTab, setSubTab] = useState<RefTab>(initialTab || "overview");
  // 秒开: 同步水合上次作品列表，后台刷新（stale-while-revalidate）。
  const [works, setWorks] = useState<ReferenceWork[]>(
    () => swrHydrate<ReferenceWork[]>("ref_overview_works") || [],
  );
  const [loading, setLoading] = useState(works.length === 0);
  const [genreFilter, setGenreFilter] = useState<string>("");
  const [genreOpen, setGenreOpen] = useState<boolean>(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await apiGet<{ items: ReferenceWork[]; total: number }>(
        "/api/references/works?limit=500"
      );
      setWorks(r.items || []);
      swrStore("ref_overview_works", r.items || []);
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
    for (const w of works) {
      byMedia[w.media_type] = (byMedia[w.media_type] || 0) + 1;
      // Split multi-tag genre strings so each tag contributes independently.
      for (const g of splitGenres(w.genre)) {
        byGenre[g] = (byGenre[g] || 0) + 1;
      }
      byStatus[w.preprocessing_status] = (byStatus[w.preprocessing_status] || 0) + 1;
      // Pure-setting works don't have full text — count them as "uploaded"
      // when they have any 原始文本 / 设定 / 角色 content.
      if (w.structure_type === "setting_collection") {
        if (pureSettingHasContent(w)) withFullText++;
      } else if (w.has_full_text) {
        withFullText++;
      }
      if (w.plot_outline_json) withPlot++;
      const chars = pj(w.extracted_characters_json);
      if (Array.isArray(chars) && chars.length) withCharacters++;
    }
    return {
      total: works.length,
      byMedia, byGenre, byStatus,
      withFullText, withPlot, withCharacters,
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

  return (
    // Wrap as a plain block so the outer .main-content (which already
    // has overflow-y: auto) handles scrolling. Using `.page-full` here
    // imposes height: 100% + flex column which clipped long content at
    // the viewport edge regardless of inline overflow overrides.
    <div style={{ padding: "16px 20px", maxWidth: 1400, margin: "0 auto" }}>
      <div className="page-header" style={{ paddingBottom: 8 }}>
        <div className="page-header-row">
          <div>
            <h2>参考总览</h2>
            <p>参考作品数据库的全局视图 · 共 {stats.total} 部作品</p>
          </div>
          <div className="flex gap-8">
            <button className="btn" onClick={load}>刷新</button>
          </div>
        </div>
      </div>

      {/* Tab strip: 总览 | 作品搜索 | 作品对比 & 共通点学习 | 索引管理 */}
      <div style={{
        display: "flex", gap: 0,
        borderBottom: "1px solid var(--border)",
        marginBottom: 14,
      }}>
        {([
          { key: "overview"      as const, label: "总览" },
          { key: "search"        as const, label: "作品搜索" },
          { key: "compare_learn" as const, label: "参考学习" },
          { key: "index"         as const, label: "索引管理" },
        ]).map(opt => (
          <button
            key={opt.key}
            onClick={() => setSubTab(opt.key)}
            style={{
              padding: "10px 20px",
              fontSize: 13,
              fontWeight: subTab === opt.key ? 700 : 400,
              color: subTab === opt.key ? "var(--accent)" : "var(--text-secondary)",
              background: "none",
              border: "none",
              borderBottom: subTab === opt.key ? "2px solid var(--accent)" : "2px solid transparent",
              cursor: "pointer",
              marginBottom: -1,
            }}
          >{opt.label}</button>
        ))}
      </div>

      {subTab === "search" ? (
        <ReferenceSearchPage embedded hideTabs initialTab="search" onNavigate={onNavigate} />
      ) : subTab === "index" ? (
        <ReferenceSearchPage embedded hideTabs initialTab="index" onNavigate={onNavigate} />
      ) : subTab === "compare_learn" ? (
        <ReferenceLearningTab works={works} />
      ) : loading && stats.total === 0 ? (
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

          {/* Genre bar chart — dashboard style; collapsible */}
          {Object.keys(stats.byGenre).length > 0 && (
            <div className="card" style={{ marginBottom: 18 }}>
              <div
                className="card-header"
                style={{ display: "flex", alignItems: "center", justifyContent: "space-between", cursor: "pointer", userSelect: "none" }}
                onClick={() => setGenreOpen(o => !o)}
                title={genreOpen ? "点击收起" : "点击展开"}
              >
                <h3 className="flex items-center gap-8">
                  <span style={{ transition: "transform 0.15s", transform: genreOpen ? "rotate(0deg)" : "rotate(-90deg)", display: "inline-block", fontSize: 10, color: "var(--text-tertiary)" }}>&#x25BC;</span>
                  题材分布
                  <span className="text-xs text-muted" style={{ fontWeight: 400, marginLeft: 6 }}>
                    {Object.keys(stats.byGenre).length} 个标签
                  </span>
                </h3>
                {genreFilter && (
                  <div className="flex items-center gap-8" onClick={e => e.stopPropagation()}>
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
              {genreOpen && (
                <div className="card-body">
                  <GenreBarChart
                    byGenre={stats.byGenre}
                    selected={genreFilter}
                    onSelect={g => setGenreFilter(g === genreFilter ? "" : g)}
                  />
                </div>
              )}
            </div>
          )}

          {/* Per-work attribute grid */}
          <div className="card">
            <div className="card-header">
              <h3>
                作品列表
                {genreFilter && (
                  <span className="text-xs text-muted" style={{ marginLeft: 8, fontWeight: 400 }}>
                    （筛选: {genreFilter} · {filteredWorks.length} / {works.length}）
                  </span>
                )}
              </h3>
            </div>
            <div className="card-body">
              <WorkAttributeGrid works={filteredWorks} onOpen={openDetail} />
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

// Match the dashboard's 题材分布 visual style (.bar-chart / .bar-row / .bar-fill).
const _BAR_COLORS = ["red", "indigo", "gold", "jade", "purple"];

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
    <div className="bar-chart">
      {rows.map(([g, n], idx) => {
        const isSel = g === selected;
        const width = Math.max(4, (n / max) * 100);
        const colorClass = isSel ? "red" : _BAR_COLORS[idx % _BAR_COLORS.length];
        return (
          <div
            className="bar-row"
            key={g}
            onClick={() => onSelect(g)}
            style={{
              cursor: "pointer",
              padding: "2px 4px",
              borderRadius: 3,
              background: isSel ? "var(--accent-subtle)" : "transparent",
            }}
            title={isSel ? "点击清除筛选" : `点击按「${g}」筛选作品`}
          >
            <div
              className="bar-label"
              style={{ color: isSel ? "var(--accent)" : "var(--text-secondary)", fontWeight: isSel ? 600 : 400 }}
            >{g}</div>
            <div className="bar-track">
              <div className={`bar-fill ${colorClass}`} style={{ width: `${width}%` }}>
                {n}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

function WorkAttributeGrid({ works, onOpen }: {
  works: ReferenceWork[];
  onOpen: (refId: string) => void;
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
        <WorkCard key={w.ref_id} w={w} onOpen={() => onOpen(w.ref_id)} />
      ))}
    </div>
  );
}

/** Per-chapter coverage bar for one extracted feature. */
function FeatureBar({ label, done, total }: { label: string; done: number; total: number }) {
  const ratio = total > 0 ? Math.min(1, done / total) : 0;
  return (
    <div className="flex items-center gap-8">
      <span className="text-xs text-muted" style={{ minWidth: 30 }}>{label}</span>
      <div style={{
        flex: 1, height: 7, background: "var(--bg-surface-2)",
        borderRadius: 4, overflow: "hidden",
      }}>
        <div style={{
          height: "100%", width: `${Math.round(ratio * 100)}%`,
          background: ratio >= 1 ? "var(--jade)" : "var(--accent)",
          borderRadius: 4, transition: "width 0.3s",
        }} />
      </div>
      <span className="text-xs" style={{
        fontFamily: "var(--font-mono)", minWidth: 56,
        textAlign: "right", color: "var(--text-tertiary)",
      }}>{total > 0 ? `${Math.min(done, total)}/${total} 章` : "—"}</span>
    </div>
  );
}

function WorkCard({ w, onOpen }: {
  w: ReferenceWork;
  onOpen: () => void;
}) {
  const isPureSetting = w.structure_type === "setting_collection";
  const genres = splitGenres(w.genre);
  const serialKey = (w.serial_status || "unknown") as keyof typeof SERIAL_LABEL;
  const serial = SERIAL_LABEL[serialKey];

  return (
    <div style={{
      padding: 12,
      background: "var(--bg-surface)",
      border: "1px solid var(--border)",
      borderRadius: "var(--radius-sm)",
      borderLeft: `3px solid ${isPureSetting ? "var(--gold)" : mediaColor(w.media_type)}`,
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
            {isPureSetting && (
              <span style={{
                fontSize: 10, padding: "1px 6px", borderRadius: 3,
                color: "var(--gold)", border: "1px solid var(--gold)",
                background: "var(--bg-surface-2)", fontWeight: 600,
              }}>纯设定</span>
            )}
            {w.creator && <span className="text-muted">· {w.creator}</span>}
            {w.user_rating ? <span style={{ color: "var(--gold)" }}>{stars(w.user_rating)}</span> : null}
          </div>
        </div>
        {/* Serial status — narrative works only; 未知 is dropped. */}
        {!isPureSetting && serialKey !== "unknown" && serial && (
          <span className="tag" title={`连载状态：${serial.label}`} style={{
            fontSize: 10, padding: "1px 7px",
            color: serial.color,
            background: "var(--bg-surface-2)",
            border: `1px solid ${serial.color}`,
            whiteSpace: "nowrap",
          }}>{serial.label}</span>
        )}
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

      {isPureSetting ? (
        <PureSettingBody w={w} />
      ) : (
        <NarrativeBody w={w} />
      )}

      {/* Actions */}
      <div className="flex gap-6" style={{ marginTop: "auto" }}>
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

/** Body block for narrative reference works (with chapters / extraction progress). */
function NarrativeBody({ w }: { w: ReferenceWork }) {
  const ch = readChapterMetrics(w);
  const hasText = Boolean(w.has_full_text);
  const fp = readFeatureProgress(w);
  const isEpisode = EPISODE_MEDIA.has(w.media_type);
  return (
    <>
      {/* Chapter / volume / word counts */}
      <div className="text-xs text-muted" style={{ lineHeight: 1.5 }}>
        {ch.chapters > 0 ? (
          <>
            {ch.volumes > 0 && <span>{ch.volumes} 卷 · </span>}
            <span>{ch.chapters} {isEpisode ? "集" : "章"}</span>
            {ch.charCount > 0 && <span> · {fmtChars(ch.charCount)}</span>}
          </>
        ) : !hasText ? (
          <span style={{ color: "var(--text-tertiary)", fontStyle: "italic" }}>未上传正文</span>
        ) : null}
      </div>

      <div className="flex flex-col gap-5">
        <div className="flex items-center gap-8">
          <span className="text-xs text-muted" style={{ minWidth: 30 }}>正文</span>
          <span className="tag" style={{
            fontSize: 9.5, padding: "1px 8px",
            background: hasText ? "var(--accent-subtle)" : "var(--bg-surface-2)",
            color: hasText ? "var(--jade)" : "var(--text-tertiary)",
            border: `1px solid ${hasText ? "var(--jade)" : "var(--border)"}`,
          }}>{hasText ? "已上传" : "未上传"}</span>
        </div>
        {fp.total > 0 && (
          <FeatureBar
            label="提取进度"
            done={Math.min(fp.events, fp.characters, fp.settings, fp.style)}
            total={fp.total}
          />
        )}
      </div>
    </>
  );
}

/** Body block for pure-setting (setting_collection) works.
 *  Shows entry count (原始文本) + extraction progress as three counts
 *  (设定 / 角色 / 设定特征). No upload-status badge — these works don't
 *  have a「正文」to upload. */
function PureSettingBody({ w }: { w: ReferenceWork }) {
  const c = readPureSettingCounts(w);
  return (
    <div className="flex flex-col gap-5">
      <div className="flex items-center gap-8">
        <span className="text-xs text-muted" style={{ minWidth: 36 }}>条目</span>
        <span style={{
          fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 700,
          color: c.entries > 0 ? "var(--jade)" : "var(--text-tertiary)",
        }}>{c.entries}</span>
      </div>
      <div className="flex items-center gap-8" style={{ flexWrap: "wrap" }}>
        <span className="text-xs text-muted" style={{ minWidth: 36 }}>提取</span>
        <PureSettingChip label="设定" value={c.settings} color="var(--accent)" />
        <PureSettingChip label="角色" value={c.characters} color="var(--indigo)" />
        <PureSettingChip label="特征" value={c.features} color="var(--purple)" />
      </div>
    </div>
  );
}

function PureSettingChip({ label, value, color }: {
  label: string; value: number; color: string;
}) {
  const has = value > 0;
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      fontSize: 11, padding: "1px 8px", borderRadius: 4,
      color: has ? color : "var(--text-tertiary)",
      border: `1px solid ${has ? color : "var(--border)"}`,
      background: has ? "var(--bg-surface-2)" : "transparent",
      fontWeight: 600,
    }}>
      {label}
      <span style={{
        fontFamily: "var(--font-mono)", fontWeight: 700,
      }}>{value}</span>
    </span>
  );
}

/* ──────────────── 参考学习 tab ───────────────── */

/** Unified work-selection picker for the 参考学习 tab. Uses the
 *  checkbox + border-color-on-select pattern that's standard across
 *  the rest of the app (see LevelMultiSelect in 索引管理). The
 *  selection is shared by both the 作品对比 and 共通点学习 flows. */
function ReferenceLearningTab({ works }: { works: ReferenceWork[] }) {
  const { toast } = useToast();
  const { confirm } = useDialog();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [search, setSearch] = useState("");
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // 自学习成果 — fetched from /api/skills/learned which the backend
  // already merges (file-based learned_skills + DB self_learned). This
  // is the same endpoint the 智能体 page consumes, so the two stay in
  // sync (delete in one place clears the other on refresh).
  const reloadSkills = useCallback(async () => {
    try {
      const r = await apiGet<{ skills: SkillInfo[] }>("/api/skills/learned");
      setSkills(r.skills || []);
    } catch { setSkills([]); }
  }, []);
  useEffect(() => { reloadSkills(); }, [reloadSkills]);

  const filtered = works.filter(w => {
    if (!search.trim()) return true;
    const q = search.toLowerCase();
    return (w.title || "").toLowerCase().includes(q)
        || (w.creator || "").toLowerCase().includes(q);
  });

  const toggle = (refId: string) => {
    setSelected(prev => {
      const next = new Set(prev);
      if (next.has(refId)) next.delete(refId);
      else if (next.size < 8) next.add(refId);
      else { toast("最多 8 部作品", "info"); return prev; }
      return next;
    });
  };

  const selectedWorks = useMemo(() => works
    .filter(w => selected.has(w.ref_id))
    .map(w => ({ ref_id: w.ref_id, title: w.title, creator: w.creator })),
    [works, selected]);

  const deleteSkill = async (s: SkillInfo) => {
    if (!(await confirm({
      message: `确认删除自学习技能「${s.display_name || s.name}」？`,
      destructive: true,
    }))) return;
    try {
      await apiDelete(`/api/skills/${encodeURIComponent(s.name)}`);
      toast("已删除", "success");
      await reloadSkills();
    } catch (e: any) {
      toast(e?.message || "删除失败", "error");
    }
  };

  return (
    <div className="flex flex-col" style={{ gap: 18 }}>
      {/* 共享作品选择 */}
      <div className="card">
        <div className="card-header">
          <div>
            <h3 style={{ margin: 0 }}>选择参考作品</h3>
            <p style={{ margin: "2px 0 0" }}>
              {selected.size} / {Math.min(works.length, 8)} 已选（最多 8 部，最少 2 部）
            </p>
          </div>
          {selected.size > 0 && (
            <button className="btn" onClick={() => setSelected(new Set())}>清除选择</button>
          )}
        </div>
        <div className="card-body">
          <input
            className="input"
            placeholder="搜索标题 / 作者..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{ marginBottom: 12 }}
          />
          {filtered.length === 0 ? (
            <div className="text-xs text-muted text-center" style={{ padding: 20 }}>
              无匹配作品
            </div>
          ) : (
            <div style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))",
              gap: 8,
              maxHeight: 320,
              overflowY: "auto",
              padding: 2,
            }}>
              {filtered.map(w => {
                const on = selected.has(w.ref_id);
                return (
                  <label
                    key={w.ref_id}
                    onClick={(e) => {
                      e.preventDefault();
                      toggle(w.ref_id);
                    }}
                    style={{
                      display: "flex", alignItems: "center", gap: 8,
                      padding: "8px 12px",
                      border: `1px solid ${on ? "var(--accent)" : "var(--border)"}`,
                      borderRadius: "var(--radius-sm)",
                      background: on ? "var(--bg-surface-2)" : "var(--bg-surface)",
                      cursor: "pointer",
                      transition: "border-color 0.15s, background 0.15s",
                    }}>
                    <input
                      type="checkbox"
                      checked={on}
                      onChange={() => toggle(w.ref_id)}
                      onClick={(e) => e.stopPropagation()}
                      style={{ width: 14, height: 14, flexShrink: 0 }}
                    />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="truncate" style={{
                        fontSize: 13, fontWeight: 600,
                        color: on ? "var(--accent)" : "var(--text-primary)",
                      }}>{w.title}</div>
                      {w.creator && (
                        <div className="text-xs text-muted truncate">{w.creator}</div>
                      )}
                    </div>
                  </label>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* 共通点学习（不带自己的作品选择/成果列表） */}
      <div className="card">
        <div className="card-header">
          <h3 style={{ margin: 0 }}>共通点学习</h3>
        </div>
        <div className="card-body">
          <CommonPatternLearningPanel
            works={works.map(w => ({
              ref_id: w.ref_id, title: w.title, creator: w.creator,
            }))}
            selectedRefIds={selected}
            hideWorkPicker
            hideSelfLearnedList
            onLearned={reloadSkills}
          />
        </div>
      </div>

      {/* 作品对比 → 草稿技能（不带自己的作品选择） */}
      <div className="card">
        <div className="card-header">
          <h3 style={{ margin: 0 }}>作品对比 → 草稿技能</h3>
        </div>
        <div className="card-body">
          <CompareWorksPanel
            selectedWorks={selectedWorks}
            hideWorkPicker
            onSaved={reloadSkills}
          />
        </div>
      </div>

      {/* 统一的自学习成果列表（与「智能体」页面共用同一份数据） */}
      <div className="card">
        <div className="card-header">
          <div>
            <h3 style={{ margin: 0 }}>自学习成果</h3>
            <p style={{ margin: "2px 0 0" }}>
              {skills.length} 个技能 · 与「智能体 → 自学习成果」共用同一份数据
            </p>
          </div>
          <button className="btn" onClick={reloadSkills}>刷新</button>
        </div>
        <div className="card-body">
          {skills.length === 0 ? (
            <div className="text-xs text-muted text-center" style={{ padding: 20 }}>
              暂无自学习技能。选择 2-8 部作品并触发上方任一学习按钮即可生成。
            </div>
          ) : (
            <div className="flex flex-col" style={{ gap: 8 }}>
              {skills.map(s => (
                <div key={s.name} style={{
                  border: "1px solid var(--border)",
                  borderLeft: "3px solid var(--purple)",
                  borderRadius: "var(--radius-sm)",
                  background: "var(--bg-surface)",
                  padding: "10px 14px",
                }}>
                  <div className="flex items-center" style={{ gap: 10, flexWrap: "wrap" }}>
                    <span style={{
                      fontSize: 13, fontWeight: 700,
                      color: "var(--text-primary)",
                    }}>{s.display_name || s.name}</span>
                    <span className="text-xs text-muted" style={{ flex: 1, minWidth: 120 }}>
                      {s.description}
                    </span>
                    <button className="btn"
                            style={{ fontSize: 11, padding: "3px 10px" }}
                            onClick={() => setExpandedId(expandedId === s.name ? null : s.name)}>
                      {expandedId === s.name ? "收起" : "查看"}
                    </button>
                    <button className="btn"
                            style={{ fontSize: 11, padding: "3px 10px", color: "var(--error)" }}
                            onClick={() => deleteSkill(s)}>
                      删除
                    </button>
                  </div>
                  {expandedId === s.name && (
                    <pre style={{
                      marginTop: 8, fontSize: 11, lineHeight: 1.6,
                      whiteSpace: "pre-wrap",
                      color: "var(--text-secondary)",
                      background: "var(--bg-card)",
                      border: "1px solid var(--border)",
                      borderRadius: 4, padding: "8px 12px",
                      maxHeight: 280, overflow: "auto",
                    }}>{s.skill_md || ""}</pre>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
