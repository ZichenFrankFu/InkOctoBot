import React, { useEffect, useState, useCallback, useMemo } from "react";
import { apiGet, apiPost, apiPut, apiDelete } from "../api/client";
import { useResizable } from "../hooks/useResizable";
import type { WorldBookEntry, WorldBookCategory } from "../api/types";

interface Props {
  projectId: string;
  projects: any[];
}

const BUILTIN_CATEGORIES: { key: string; label: string; icon: string }[] = [
  { key: "power_system", label: "力量体系", icon: "\u26A1" },
  { key: "factions", label: "势力", icon: "\uD83C\uDFF0" },
  { key: "geography", label: "地理", icon: "\uD83D\uDDFA\uFE0F" },
  { key: "social_rules", label: "社会规则/习俗", icon: "\uD83D\uDCDC" },
  { key: "history", label: "历史", icon: "\uD83D\uDCDA" },
  { key: "hard_rules", label: "硬规则", icon: "\uD83D\uDCCF" },
  { key: "other", label: "其他", icon: "\uD83D\uDCDD" },
];

function catLabel(cat: string, customCats: { key: string; label: string }[]): string {
  return BUILTIN_CATEGORIES.find(c => c.key === cat)?.label
    || customCats.find(c => c.key === cat)?.label
    || cat;
}
function catIcon(cat: string): string {
  return BUILTIN_CATEGORIES.find(c => c.key === cat)?.icon || "\uD83D\uDCDD";
}

export default function WorldBookPage({ projectId, projects }: Props) {
  const [items, setItems] = useState<WorldBookEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [filterCat, setFilterCat] = useState<string>("");
  const [editing, setEditing] = useState<WorldBookEntry | null>(null);
  const [dirty, setDirty] = useState(false);
  const [checking, setChecking] = useState(false);
  const [checkResult, setCheckResult] = useState<string | null>(null);

  // Custom categories
  const CUSTOM_CAT_KEY = `inkocto_wb_custom_cats_${projectId}`;
  const [customCategories, setCustomCategories] = useState<{ key: string; label: string }[]>(() => {
    try { const raw = localStorage.getItem(CUSTOM_CAT_KEY); return raw ? JSON.parse(raw) : []; } catch { return []; }
  });
  const [showAddCat, setShowAddCat] = useState(false);
  const [newCatName, setNewCatName] = useState("");

  const addCustomCategory = () => {
    const name = newCatName.trim();
    if (!name) return;
    const key = `custom_${name.toLowerCase().replace(/[^a-z0-9\u4e00-\u9fff]/g, "_")}`;
    if (customCategories.some(c => c.key === key) || BUILTIN_CATEGORIES.some(c => c.key === key)) return;
    const updated = [...customCategories, { key, label: name }];
    setCustomCategories(updated);
    localStorage.setItem(CUSTOM_CAT_KEY, JSON.stringify(updated));
    setNewCatName("");
    setShowAddCat(false);
  };

  const removeCustomCategory = (key: string) => {
    const updated = customCategories.filter(c => c.key !== key);
    setCustomCategories(updated);
    localStorage.setItem(CUSTOM_CAT_KEY, JSON.stringify(updated));
  };

  const allCategories = useMemo(() => [
    ...BUILTIN_CATEGORIES,
    ...customCategories.map(c => ({ ...c, icon: "\uD83C\uDFF7\uFE0F" })),
  ], [customCategories]);

  const leftPanel = useResizable({ direction: "horizontal", initialSize: 320, minSize: 240, maxSize: 450 });

  const projName = projects.find((p: any) => p.id === projectId)?.name || "未选择项目";

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await apiGet<{ items: WorldBookEntry[] }>(`/api/data/worldbook?project_id=${projectId}`);
      const newItems = r.items || [];
      setItems(newItems);
      // Sync editing with fresh data after save
      setEditing(prev => {
        if (prev) {
          return newItems.find(i => i.id === prev.id) || prev;
        }
        return prev;
      });
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load]);

  const filtered = useMemo(
    () => (filterCat ? items.filter(i => i.category === filterCat) : items),
    [items, filterCat]
  );

  const catCounts = useMemo(() => {
    const m: Record<string, number> = {};
    items.forEach(i => {
      m[i.category] = (m[i.category] || 0) + 1;
    });
    return m;
  }, [items]);

  const create = async () => {
    try {
      const entry = await apiPost<WorldBookEntry>(`/api/data/worldbook`, {
        category: (filterCat as WorldBookCategory) || "power_system",
        title: "新条目",
        content: "",
        tags: [],
        project_id: projectId,
      });
      setItems([...items, entry]);
      setEditing(entry);
      setDirty(false);
    } catch (e) {
      console.error(e);
    }
  };

  const save = async () => {
    if (!editing) return;
    try {
      const updated = await apiPut<WorldBookEntry>(`/api/data/worldbook/${editing.id}`, editing);
      setDirty(false);
      setItems(prev => prev.map(item => item.id === editing.id ? { ...editing, ...updated } : item));
      setEditing({ ...editing, ...updated });
    } catch (e) {
      console.error(e);
    }
  };

  const remove = async (id: string) => {
    if (!confirm("确定删除该条目？")) return;
    try {
      await apiDelete(`/api/data/worldbook/${id}`);
      if (editing?.id === id) setEditing(null);
      load();
    } catch (e) {
      console.error(e);
    }
  };

  const u = (key: string, val: any) => {
    if (!editing) return;
    setEditing({ ...editing, [key]: val } as WorldBookEntry);
    setDirty(true);
  };

  const runConsistencyCheck = async () => {
    setChecking(true);
    setCheckResult(null);
    try {
      const entries = items.map((e: any) => `[${catLabel(e.category, customCategories)}] ${e.title}: ${e.content || ""}`).join("\n");
      const resp = await apiPost<{ result: string; issues?: string[] }>("/api/worldbook/consistency-check", {
        project_id: projectId,
        entries_text: entries,
      });
      if (resp.issues && resp.issues.length > 0) {
        setCheckResult("发现以下潜在矛盾：\n" + resp.issues.map((s: string, i: number) => `${i + 1}. ${s}`).join("\n"));
      } else {
        setCheckResult(resp.result || "未发现明显矛盾，世界观设定一致性良好。");
      }
    } catch (e: any) {
      setCheckResult("一致性检查失败：" + (e?.message || "请检查模型连接设置。"));
    }
    setChecking(false);
  };

  return (
    <div className="page-full">
      <div className="panel-layout">
        {/* ======== LEFT PANEL: Category Filter + Entry List ======== */}
        <div className="panel" style={{ width: leftPanel.size, flexShrink: 0, background: "var(--bg-surface)", borderRight: "1px solid var(--border)" }}>
          <div className="panel-header" style={{ flexDirection: "column", alignItems: "stretch", gap: 8 }}>
            <div className="flex items-center justify-between">
              <h3>世界书</h3>
              <button className="btn-primary" style={{ padding: "5px 12px", fontSize: 12 }} onClick={create}>
                + 新建条目
              </button>
            </div>
            <div className="text-xs text-muted">{projName}</div>
          </div>

          {/* Category filters */}
          <div style={{ padding: "10px 12px", borderBottom: "1px solid var(--border)", display: "flex", gap: 4, flexWrap: "wrap" }}>
            <button
              className={filterCat === "" ? "btn-primary" : "btn"}
              style={{ padding: "4px 10px", fontSize: 11, borderRadius: 12 }}
              onClick={() => setFilterCat("")}
            >
              全部 ({items.length})
            </button>
            {allCategories.map(c => (
              <button
                key={c.key}
                className={filterCat === c.key ? "btn-primary" : "btn"}
                style={{ padding: "4px 10px", fontSize: 11, borderRadius: 12 }}
                onClick={() => setFilterCat(filterCat === c.key ? "" : c.key)}
              >
                {c.icon} {c.label} ({catCounts[c.key] || 0})
              </button>
            ))}
            <button
              className="btn"
              style={{ padding: "4px 8px", fontSize: 11, borderRadius: 12, borderStyle: "dashed" }}
              onClick={() => setShowAddCat(!showAddCat)}
            >
              + 添加分类
            </button>
          </div>

          {/* Add custom category */}
          {showAddCat && (
            <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)", display: "flex", gap: 6 }}>
              <input className="input" value={newCatName} onChange={e => setNewCatName(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter") addCustomCategory(); }}
                placeholder="输入新分类名称..." style={{ flex: 1, fontSize: 11, padding: "4px 8px" }} />
              <button className="btn-primary" style={{ fontSize: 11, padding: "4px 10px" }} onClick={addCustomCategory} disabled={!newCatName.trim()}>添加</button>
            </div>
          )}

          {/* Custom categories management */}
          {customCategories.length > 0 && showAddCat && (
            <div style={{ padding: "4px 12px 8px", borderBottom: "1px solid var(--border)", fontSize: 10, color: "var(--text-tertiary)" }}>
              自定义分类：
              {customCategories.map(c => (
                <span key={c.key} style={{ marginLeft: 4, padding: "1px 6px", background: "var(--bg-surface-2)", borderRadius: 8, cursor: "pointer" }}
                  onClick={() => { if (confirm(`删除自定义分类「${c.label}」？`)) removeCustomCategory(c.key); }}>
                  {c.label} &times;
                </span>
              ))}
            </div>
          )}

          {/* Consistency check button */}
          <div style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
            <button
              className="btn w-full"
              onClick={runConsistencyCheck}
              disabled={checking || items.length === 0}
              style={{ justifyContent: "center" }}
            >
              {checking ? "检查中..." : "一致性检查"}
            </button>
          </div>

          {/* Entry list */}
          <div className="panel-body">
            {loading ? (
              <div className="loading"><div className="loading-spinner" /></div>
            ) : filtered.length === 0 ? (
              <div className="empty-state" style={{ padding: 32 }}>
                <p>{filterCat ? "该分类下暂无条目" : "暂无条目"}</p>
              </div>
            ) : (
              filtered.map(entry => (
                <div
                  key={entry.id}
                  className={`report-list-item ${editing?.id === entry.id ? "active" : ""}`}
                  onClick={() => { setEditing(entry); setDirty(false); }}
                >
                  <span className="report-icon">{catIcon(entry.category)}</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="report-name" style={{ fontWeight: 600, color: "var(--text-primary)" }}>
                      {entry.title}
                    </div>
                    <div className="text-xs text-muted truncate">
                      {catLabel(entry.category, customCategories)}
                      {entry.content ? ` \u00B7 ${entry.content.length > 40 ? entry.content.slice(0, 40) + "..." : entry.content}` : " \u00B7 (空)"}
                    </div>
                  </div>
                  <button
                    className="btn-icon"
                    style={{ fontSize: 14 }}
                    onClick={e => { e.stopPropagation(); remove(entry.id); }}
                  >
                    &times;
                  </button>
                </div>
              ))
            )}
          </div>
        </div>

        {/* Resize handle */}
        <div className="panel-resize-h" {...leftPanel.handleProps} />

        {/* ======== RIGHT PANEL: Entry Editor ======== */}
        <div className="panel flex-1" style={{ background: "var(--bg-app)", overflowY: "auto" }}>
          {/* Consistency check result */}
          {checkResult && (
            <div style={{
              padding: "12px 16px",
              margin: "16px 32px 0",
              background: "var(--bg-surface-2)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-md)",
              fontSize: 13,
              color: "var(--text-secondary)",
              lineHeight: 1.7,
              whiteSpace: "pre-wrap",
            }}>
              <div className="flex items-center justify-between mb-8">
                <span className="label" style={{ marginBottom: 0 }}>一致性检查结果</span>
                <button className="btn-icon" onClick={() => setCheckResult(null)} style={{ fontSize: 12 }}>&times;</button>
              </div>
              {checkResult}
            </div>
          )}

          {!editing ? (
            <div className="empty-state" style={{ paddingTop: 120 }}>
              <h4>选择或创建一个条目</h4>
              <p>在左侧列表中选择条目进行编辑</p>
            </div>
          ) : (
            <div style={{ maxWidth: 720, margin: "0 auto", padding: "28px 32px 48px" }}>
              {/* Header */}
              <div className="flex items-center justify-between mb-24">
                <h2 className="font-serif" style={{ fontSize: 22, fontWeight: 700 }}>
                  {editing.title}
                </h2>
                <button
                  className="btn-primary"
                  onClick={save}
                  disabled={!dirty}
                  style={{ opacity: dirty ? 1 : 0.5 }}
                >
                  {dirty ? "保存" : "已保存"}
                </button>
              </div>

              {/* Entry info */}
              <div className="card mb-20">
                <div className="card-header"><h3>条目信息</h3></div>
                <div className="card-body">
                  <div className="field mb-12">
                    <label className="label">标题</label>
                    <input
                      className="input"
                      value={editing.title}
                      onChange={e => u("title", e.target.value)}
                      style={{ fontWeight: 600 }}
                    />
                  </div>
                  <div className="field mb-12">
                    <label className="label">分类</label>
                    <div className="flex gap-6" style={{ flexWrap: "wrap" }}>
                      {allCategories.map(c => (
                        <button
                          key={c.key}
                          className={editing.category === c.key ? "btn-primary" : "btn"}
                          style={{ padding: "6px 14px", fontSize: 12, borderRadius: 20 }}
                          onClick={() => u("category", c.key)}
                        >
                          {c.icon} {c.label}
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="field">
                    <label className="label">标签（逗号分隔）</label>
                    <input
                      className="input"
                      value={(editing.tags || []).join(", ")}
                      onChange={e => u("tags", e.target.value.split(",").map(s => s.trim()).filter(Boolean))}
                      placeholder="例：核心设定, 力量等级"
                    />
                  </div>
                </div>
              </div>

              {/* Content */}
              <div className="card">
                <div className="card-header"><h3>内容</h3></div>
                <div className="card-body">
                  <textarea
                    className="input"
                    value={editing.content}
                    onChange={e => u("content", e.target.value)}
                    rows={16}
                    placeholder="在此输入世界观设定..."
                    style={{ fontFamily: "var(--font-serif)", lineHeight: 1.8, fontSize: 14 }}
                  />
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
