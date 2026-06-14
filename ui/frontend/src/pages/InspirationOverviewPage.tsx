/**
 * InspirationOverviewPage — stats over the inspiration table.
 *
 * Latest IA revision:
 *  - 快速入口 removed (the user gets to the library via the nav)
 *  - Added "灵感在参考作品中被使用过" — shows which 参考作品
 *    actually use which inspiration. The inspiration table already
 *    stores ``used_in_chapters_json``; we surface a top-N view here.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiGet } from "../api/client";
import { swrHydrate, swrStore } from "../api/swr";
import { useToast } from "../components/shared/Toast";
import { t, tInspirationCategory, useLang } from "../i18n";


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

  return (
    <div className="page" style={{ padding: 16 }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <div>
          <h2 style={{ margin: 0 }}>灵感数据库总览</h2>
          <p style={{ color: "var(--text-tertiary)", fontSize: 12, margin: "4px 0 0" }}>
            跨项目共享的灵感片段库。生成时按相关性自动召回。
          </p>
        </div>
        <button className="btn" onClick={refresh} disabled={loading}>刷新</button>
      </header>

      {/* Stat tiles */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12, marginBottom: 20 }}>
        <StatTile label="灵感总数" value={stats.total} />
        <StatTile label="已生成 embedding" value={`${stats.withEmbedding} / ${stats.total}`} />
        <StatTile label="已被章节使用过" value={`${stats.usedSomewhere}`} />
        <StatTile label="分类数" value={Object.keys(stats.byCategory).length} />
      </div>

      {/* By-category breakdown */}
      <div className="card" style={{ padding: 14, marginBottom: 16 }}>
        <h3 style={{ marginTop: 0, fontSize: 14 }}>按类别分布</h3>
        {Object.keys(stats.byCategory).length === 0 ? (
          <p style={{ color: "var(--text-tertiary)", fontSize: 12 }}>暂无灵感数据</p>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
            {Object.entries(stats.byCategory)
              .sort((a, b) => b[1] - a[1])
              .map(([cat, n]) => (
                <div key={cat} style={{
                  padding: "6px 12px", borderRadius: 12,
                  background: "var(--bg-surface-2)", fontSize: 12,
                }}>
                  {tInspirationCategory(cat)}: <strong>{n}</strong>
                </div>
              ))}
          </div>
        )}
      </div>

      {/* Usage in reference works / chapters */}
      <div className="card" style={{ padding: 14 }}>
        <h3 style={{ marginTop: 0, fontSize: 14 }}>
          灵感在参考作品中被使用过 ({stats.usageEntries.length})
        </h3>
        {stats.usageEntries.length === 0 ? (
          <p style={{ color: "var(--text-tertiary)", fontSize: 12 }}>
            还没有任何灵感被章节引用过。灵感会在编辑器侧栏「关联灵感 / 事件」选中后记入此字段。
          </p>
        ) : (
          <table style={{ width: "100%", fontSize: 12 }}>
            <thead>
              <tr style={{ color: "var(--text-tertiary)", textAlign: "left" }}>
                <th style={{ padding: "6px" }}>灵感</th>
                <th style={{ padding: "6px" }}>类别</th>
                <th style={{ padding: "6px" }}>引用次数</th>
                <th style={{ padding: "6px" }}>引用的章节 / 参考作品</th>
              </tr>
            </thead>
            <tbody>
              {stats.usageEntries.slice(0, 30).map(u => (
                <tr key={u.inspiration.id} style={{ borderTop: "1px solid var(--border)" }}>
                  <td style={{ padding: "6px", maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                      title={u.inspiration.content}>
                    {u.inspiration.title || u.inspiration.content.slice(0, 30) + "..."}
                  </td>
                  <td style={{ padding: "6px" }}>{u.inspiration.category ? tInspirationCategory(u.inspiration.category) : "—"}</td>
                  <td style={{ padding: "6px" }}><strong>{u.used_chapters.length}</strong></td>
                  <td style={{ padding: "6px", fontSize: 11, color: "var(--text-tertiary)" }}>
                    {u.used_chapters.slice(0, 4).map(c =>
                      typeof c === "string" ? c
                        : (c.title || c.chapter_id || c.work_title || JSON.stringify(c)).toString().slice(0, 30)
                    ).join("、")}
                    {u.used_chapters.length > 4 && ` ...+${u.used_chapters.length - 4}`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}


function StatTile({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="card" style={{ padding: 14, textAlign: "center" }}>
      <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 600, marginTop: 4 }}>{value}</div>
    </div>
  );
}
