/**
 * InspirationOverviewPage — new top-level page in the 灵感数据库 group.
 *
 * Surfaces basic stats over the user's inspiration table (count by
 * category, age distribution, embedding coverage). Plus quick-actions
 * to jump into the library or the cross-work search.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiGet } from "../api/client";
import { useToast } from "../components/shared/Toast";


interface Inspiration {
  id: string;
  category: string;
  title: string;
  content: string;
  embedding_text_hash?: string;
  used_in_chapters_json?: string;
  updated_at?: string;
}


interface Props {
  onNavigate?: (tab: string) => void;
}


export default function InspirationOverviewPage({ onNavigate }: Props) {
  const { toast } = useToast();
  const [items, setItems] = useState<Inspiration[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const r = await apiGet<{ items: Inspiration[] }>("/api/references/inspirations");
      setItems(r.items || []);
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
    for (const it of items) {
      const c = it.category || "other";
      byCategory[c] = (byCategory[c] || 0) + 1;
      if (it.embedding_text_hash) withEmbedding++;
      try {
        const used = JSON.parse(it.used_in_chapters_json || "[]");
        if (Array.isArray(used) && used.length > 0) usedSomewhere++;
      } catch { /* ignore */ }
    }
    return {
      total: items.length,
      byCategory,
      withEmbedding,
      usedSomewhere,
    };
  }, [items]);

  return (
    <div className="page" style={{ padding: 16 }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <div>
          <h2 style={{ margin: 0 }}>💡 灵感数据库总览</h2>
          <p style={{ color: "var(--text-tertiary)", fontSize: 12, margin: "4px 0 0" }}>
            跨项目共享的灵感片段库。在章节生成时会按相关性自动召回。
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
                  {cat}: <strong>{n}</strong>
                </div>
              ))}
          </div>
        )}
      </div>

      {/* Quick actions */}
      <div className="card" style={{ padding: 14 }}>
        <h3 style={{ marginTop: 0, fontSize: 14 }}>快速入口</h3>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn primary" onClick={() => onNavigate?.("inspiration-library")}>
            🗂 打开灵感库
          </button>
          <button className="btn" onClick={() => onNavigate?.("inspiration-search")}>
            🔍 跨作品灵感搜索
          </button>
        </div>
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
