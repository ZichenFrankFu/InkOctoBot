/**
 * InspirationOverviewPage — stats over the inspiration table.
 *
 * Layout:
 *  - Page header with title + refresh
 *  - Stat tiles (4 columns) using project's .stat-card style
 *  - Category breakdown as colored bars (visual count comparison)
 *  - Usage list as cards (cross-reference work × inspiration)
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiGet } from "../api/client";
import { swrHydrate, swrStore } from "../api/swr";
import { useToast } from "../components/shared/Toast";
import { tInspirationCategory, useLang } from "../i18n";


interface Inspiration {
  id: string;
  category: string;
  title: string;
  content: string;
  embedding_text_hash?: string;
  used_in_chapters_json?: string;
  updated_at?: string;
}

interface UsageEntry {
  inspiration: Inspiration;
  used_chapters: any[];
}

/** Color per category — drives the breakdown bars. Keys mirror
 *  CATEGORIES in InspirationLibrary. Unknown keys fall through to gray. */
const CATEGORY_COLOR: Record<string, string> = {
  scene:         "var(--accent)",
  plot_device:   "var(--gold)",
  character:     "var(--purple)",
  worldbuilding: "var(--jade)",
  other:         "var(--text-tertiary)",
};

export default function InspirationOverviewPage() {
  const { toast } = useToast();
  useLang();  // re-render on language change
  // 秒开: 同步水合上次列表快照，后台刷新（stale-while-revalidate）。
  const [items, setItems] = useState<Inspiration[]>(
    () => swrHydrate<Inspiration[]>("insp_overview_items") || [],
  );
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const r = await apiGet<{ items: Inspiration[] }>("/api/references/inspirations");
      setItems(r.items || []);
      swrStore("insp_overview_items", r.items || []);
    } catch (e: any) {
      toast(`加载失败: ${e.message}`, "error");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => { refresh(); }, [refresh]);

  const stats = useMemo(() => {
    const byCategory: Record<string, number> = {};
    let withEmbedding = 0;
    let usedSomewhere = 0;
    const usageEntries: UsageEntry[] = [];

    for (const it of items) {
      const c = it.category || "other";
      byCategory[c] = (byCategory[c] || 0) + 1;
      if (it.embedding_text_hash) withEmbedding++;
      try {
        const used = JSON.parse(it.used_in_chapters_json || "[]");
        if (Array.isArray(used) && used.length > 0) {
          usedSomewhere++;
          usageEntries.push({ inspiration: it, used_chapters: used });
        }
      } catch { /* ignore */ }
    }
    usageEntries.sort((a, b) => b.used_chapters.length - a.used_chapters.length);
    return {
      total: items.length,
      byCategory,
      withEmbedding,
      usedSomewhere,
      usageEntries,
    };
  }, [items]);

  const maxCategory = Math.max(1, ...Object.values(stats.byCategory));

  return (
    <div className="page" style={{ padding: "24px 28px 48px" }}>
      <div className="page-header" style={{ padding: 0, marginBottom: 20 }}>
        <div className="page-header-row">
          <div>
            <h2>灵感数据库总览</h2>
            <p>跨项目共享的灵感片段库 · 生成时按相关性自动召回</p>
          </div>
          <button className="btn" onClick={refresh} disabled={loading}>
            {loading ? "刷新中…" : "刷新"}
          </button>
        </div>
      </div>

      {/* Stat tiles — using the project's .stat-card pattern */}
      <div className="stats-grid">
        <StatCard
          label="灵感总数"
          value={stats.total}
          icon="✦"
          tone="red"
        />
        <StatCard
          label="已生成 embedding"
          value={`${stats.withEmbedding} / ${stats.total}`}
          icon="◇"
          tone="indigo"
        />
        <StatCard
          label="已被章节使用过"
          value={stats.usedSomewhere}
          icon="◉"
          tone="jade"
        />
        <StatCard
          label="分类数"
          value={Object.keys(stats.byCategory).length}
          icon="❖"
          tone="gold"
        />
      </div>

      {/* By-category breakdown — as horizontal bars */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="card-header">
          <div>
            <h3 style={{ margin: 0 }}>按类别分布</h3>
            <p style={{ margin: "2px 0 0" }}>{Object.keys(stats.byCategory).length} 个类别</p>
          </div>
        </div>
        <div className="card-body">
          {Object.keys(stats.byCategory).length === 0 ? (
            <EmptyHint>暂无灵感数据</EmptyHint>
          ) : (
            <div className="flex flex-col gap-8">
              {Object.entries(stats.byCategory)
                .sort((a, b) => b[1] - a[1])
                .map(([cat, n]) => {
                  const color = CATEGORY_COLOR[cat] || "var(--text-tertiary)";
                  const pct = (n / maxCategory) * 100;
                  return (
                    <div key={cat} className="flex items-center" style={{ gap: 12 }}>
                      <span style={{
                        flexShrink: 0, minWidth: 90, fontSize: 13,
                        fontWeight: 600, color: "var(--text-primary)",
                      }}>{tInspirationCategory(cat)}</span>
                      <div style={{
                        flex: 1, height: 8, background: "var(--bg-surface-2)",
                        borderRadius: 4, overflow: "hidden",
                      }}>
                        <div style={{
                          height: "100%", width: `${pct}%`,
                          background: color, borderRadius: 4,
                          transition: "width 0.3s var(--ease-out)",
                        }} />
                      </div>
                      <span style={{
                        flexShrink: 0, minWidth: 50, textAlign: "right",
                        fontFamily: "var(--font-mono)", fontSize: 13,
                        fontWeight: 700, color,
                      }}>{n}</span>
                    </div>
                  );
                })}
            </div>
          )}
        </div>
      </div>

      {/* Usage list — cards */}
      <div className="card">
        <div className="card-header">
          <div>
            <h3 style={{ margin: 0 }}>灵感在参考作品中被使用过</h3>
            <p style={{ margin: "2px 0 0" }}>
              {stats.usageEntries.length} 条灵感被引用过
              {stats.usageEntries.length > 30 && ` · 显示前 30 条`}
            </p>
          </div>
        </div>
        <div className="card-body">
          {stats.usageEntries.length === 0 ? (
            <EmptyHint>
              还没有任何灵感被章节引用过。在编辑器侧栏「关联灵感 / 事件」中选中灵感后会记入此字段。
            </EmptyHint>
          ) : (
            <div className="flex flex-col gap-8">
              {stats.usageEntries.slice(0, 30).map(u => (
                <UsageRow
                  key={u.inspiration.id}
                  inspiration={u.inspiration}
                  usedChapters={u.used_chapters}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}


function StatCard({
  label, value, icon, tone,
}: {
  label: string;
  value: number | string;
  icon: string;
  tone: "red" | "indigo" | "jade" | "gold" | "purple" | "cyan";
}) {
  return (
    <div className="stat-card">
      <div className={`stat-icon ${tone}`} style={{ color: "var(--text-secondary)" }}>
        {icon}
      </div>
      <div>
        <div className="stat-value">{value}</div>
        <div className="stat-label">{label}</div>
      </div>
    </div>
  );
}


function UsageRow({
  inspiration, usedChapters,
}: {
  inspiration: Inspiration;
  usedChapters: any[];
}) {
  const color = CATEGORY_COLOR[inspiration.category] || "var(--text-tertiary)";
  const title = inspiration.title
    || (inspiration.content.length > 40
        ? inspiration.content.slice(0, 40) + "…"
        : inspiration.content);
  const chapters = usedChapters.slice(0, 4).map(c =>
    typeof c === "string" ? c
      : (c.title || c.chapter_id || c.work_title || JSON.stringify(c))
          .toString().slice(0, 30),
  );
  return (
    <div style={{
      border: "1px solid var(--border)",
      borderLeft: `3px solid ${color}`,
      borderRadius: "var(--radius-sm)",
      background: "var(--bg-surface)",
      padding: "10px 14px",
      display: "flex", alignItems: "center", gap: 12, flexWrap: "wrap",
      transition: "border-color 0.15s",
    }}>
      <span style={{
        fontSize: 11, padding: "2px 8px", borderRadius: 4,
        color, border: `1px solid ${color}`,
        background: "var(--bg-surface-2)",
        flexShrink: 0, fontWeight: 600,
      }}>{tInspirationCategory(inspiration.category) || "—"}</span>
      <span style={{
        fontSize: 13, fontWeight: 600, color: "var(--text-primary)",
        flexShrink: 0, maxWidth: 280,
        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
      }} title={inspiration.content}>{title}</span>
      <span style={{
        fontFamily: "var(--font-mono)", fontSize: 12, fontWeight: 700,
        color: "var(--accent)", flexShrink: 0,
        padding: "2px 8px", borderRadius: 4,
        background: "var(--accent-subtle)",
      }}>{usedChapters.length} 次</span>
      <span className="text-xs" style={{
        color: "var(--text-tertiary)", flex: 1, minWidth: 200,
        overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
      }}>
        {chapters.join(" · ")}
        {usedChapters.length > 4 && ` …+${usedChapters.length - 4}`}
      </span>
    </div>
  );
}


function EmptyHint({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      padding: "30px 20px", textAlign: "center",
      background: "var(--bg-surface)",
      border: "1px dashed var(--border)",
      borderRadius: "var(--radius-sm)",
      fontSize: 12, lineHeight: 1.7,
      color: "var(--text-tertiary)",
    }}>{children}</div>
  );
}
