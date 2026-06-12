import React, { useCallback, useEffect, useState } from "react";
import { apiGet } from "../api/client";
import InspirationLibrary from "../components/InspirationLibrary";

// 灵感管理页面 (spec 6.3 页面1)：事实行（灵感总数 / 已生成 embedding 数 /
// 已被章节使用数）+ 类别分布 block + 灵感 list 的增删改查。
// 相似检索在独立的「灵感搜索」页（spec 6.3 页面2）。

interface Stats {
  total: number; embedded: number; used: number;
  by_category: Record<string, number>;
}

const CATEGORY_LABELS: Record<string, string> = {
  scene: "场景", plot_device: "桥段", character: "人物设计",
  setting: "设定", other: "其他",
};

export default function InspirationManagePage({ onNavigate }: {
  onNavigate?: (tab: string) => void;
}) {
  const [stats, setStats] = useState<Stats | null>(null);

  const reloadStats = useCallback(async () => {
    try {
      const r = await apiGet<Stats>("/api/references/inspirations/stats");
      setStats(r);
    } catch { setStats(null); }
  }, []);

  useEffect(() => { reloadStats(); }, [reloadStats]);

  const factBlock = (label: string, value: number | string) => (
    <div style={{
      flex: 1, minWidth: 140, background: "var(--bg-surface)",
      border: "1px solid var(--border-subtle)", borderRadius: 8,
      padding: "12px 16px",
    }}>
      <div style={{ fontSize: 22, fontWeight: 700, color: "var(--text-primary)" }}>{value}</div>
      <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 2 }}>{label}</div>
    </div>
  );

  const catEntries = Object.entries(stats?.by_category || {})
    .sort((a, b) => b[1] - a[1]);
  const catMax = Math.max(1, ...catEntries.map(([, n]) => n));

  return (
    <div style={{ padding: 16, maxWidth: 1100, margin: "0 auto" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
        <div style={{ fontSize: 16, fontWeight: 700, color: "var(--text-primary)" }}>灵感管理</div>
        <button className="btn" style={{ fontSize: 11, padding: "3px 12px", marginLeft: "auto" }}
          onClick={() => onNavigate?.("references-search")}>
          去灵感相似检索
        </button>
      </div>

      {/* 事实行 */}
      <div style={{ display: "flex", gap: 10, marginBottom: 12, flexWrap: "wrap" }}>
        {factBlock("灵感总数", stats?.total ?? "-")}
        {factBlock("已生成 embedding", stats?.embedded ?? "-")}
        {factBlock("已被章节使用", stats?.used ?? "-")}
      </div>

      {/* 类别分布 */}
      <div style={{
        background: "var(--bg-surface)", border: "1px solid var(--border-subtle)",
        borderRadius: 8, padding: "12px 16px", marginBottom: 14,
      }}>
        <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>类别分布</div>
        {catEntries.length === 0 && (
          <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>暂无灵感条目。</div>
        )}
        {catEntries.map(([cat, n]) => (
          <div key={cat} style={{ display: "flex", alignItems: "center", gap: 8, padding: "2px 0", fontSize: 11 }}>
            <span style={{ width: 70, color: "var(--text-secondary)" }}>
              {CATEGORY_LABELS[cat] || cat}
            </span>
            <div style={{ flex: 1, height: 8, background: "var(--bg-secondary)", borderRadius: 4 }}>
              <div style={{
                width: `${(n / catMax) * 100}%`, height: "100%",
                background: "var(--accent)", borderRadius: 4,
              }} />
            </div>
            <span style={{ width: 32, textAlign: "right", color: "var(--text-tertiary)" }}>{n}</span>
          </div>
        ))}
      </div>

      {/* 灵感 list（添加 / 搜索 / 编辑 / 删除） */}
      <div style={{
        background: "var(--bg-surface)", border: "1px solid var(--border-subtle)",
        borderRadius: 8, padding: "12px 16px",
      }}>
        <InspirationLibrary onSearchWorks={(text) => {
          try { sessionStorage.setItem("inspiration_search_query", text); } catch {}
          onNavigate?.("references-search");
        }} />
      </div>
    </div>
  );
}
