/* 灵感库 — a personal store of free-text idea snippets (scenes, plot
 * devices, character designs, …).
 *
 * Rendered as a tab inside the 灵感搜索 page. Full CRUD against the
 * /api/references/inspirations endpoints; each entry carries a「搜索
 * 参考作品」action that runs the cross-work fuzzy search using the
 * inspiration's text as the query (handled by the parent page). */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiGet, apiPost, apiPut, apiDelete } from "../api/client";
import { swrHydrate, swrStore } from "../api/swr";
import { useToast } from "./shared/Toast";
import { useDialog } from "./shared/Dialog";
import { tInspirationCategory } from "../i18n";

interface Inspiration {
  id: string;
  category: string;
  title: string;
  content: string;
  created_at?: string;
  updated_at?: string;
}

const CATEGORIES: { key: string; label: string; color: string }[] = [
  { key: "scene",         label: "场景",     color: "var(--accent)" },
  { key: "plot_device",   label: "桥段",     color: "var(--gold)" },
  { key: "character",     label: "人物设计", color: "var(--purple)" },
  { key: "worldbuilding", label: "设定",     color: "var(--jade)" },
  { key: "other",         label: "其他",     color: "var(--text-tertiary)" },
];

const catLabel = (k: string) => tInspirationCategory(k);
const catColor = (k: string) =>
  CATEGORIES.find(c => c.key === k)?.color || "var(--text-tertiary)";

export default function InspirationLibrary({ onSearchWorks }: {
  /** Run the cross-work fuzzy search with the given text as the query. */
  onSearchWorks: (query: string) => void;
}) {
  const { toast } = useToast();
  const { confirm } = useDialog();
  // 秒开: 同步水合上次列表，后台刷新（stale-while-revalidate）。
  const [items, setItems] = useState<Inspiration[]>(
    () => swrHydrate<Inspiration[]>("insp_library_items") || [],
  );
  const [loading, setLoading] = useState(false);
  const [catFilter, setCatFilter] = useState("");
  // Text search — local fuzzy match against title + content.
  const [textFilter, setTextFilter] = useState("");
  // draft being added/edited; id === "" means a new inspiration.
  const [draft, setDraft] = useState<Inspiration | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await apiGet<{ items: Inspiration[] }>("/api/references/inspirations");
      setItems(r.items || []);
      swrStore("insp_library_items", r.items || []);
    } catch (e: any) {
      toast(e?.message || "加载灵感库失败", "error");
    } finally { setLoading(false); }
  }, [toast]);

  useEffect(() => { load(); }, [load]);

  const save = async () => {
    if (!draft) return;
    const content = draft.content.trim();
    if (!content) { toast("灵感内容不能为空", "error"); return; }
    setSaving(true);
    try {
      const payload = { category: draft.category, title: draft.title.trim(), content };
      if (draft.id) {
        await apiPut(`/api/references/inspirations/${draft.id}`, payload);
        toast("已更新", "success");
      } else {
        await apiPost("/api/references/inspirations", payload);
        toast("已添加", "success");
      }
      setDraft(null);
      await load();
    } catch (e: any) {
      toast(e?.message || "保存失败", "error");
    } finally { setSaving(false); }
  };

  const remove = async (insp: Inspiration) => {
    const label = insp.title || insp.content.slice(0, 20);
    if (!(await confirm({ message: `确认删除灵感「${label}」？此操作不可撤销。`, destructive: true }))) return;
    try {
      await apiDelete(`/api/references/inspirations/${insp.id}`);
      toast("已删除", "success");
      setItems(prev => prev.filter(x => x.id !== insp.id));
    } catch (e: any) {
      toast(e?.message || "删除失败", "error");
    }
  };

  const q = textFilter.trim().toLowerCase();
  const shown = useMemo(() => items.filter(it => {
    if (catFilter && it.category !== catFilter) return false;
    if (!q) return true;
    return (it.title || "").toLowerCase().includes(q)
        || (it.content || "").toLowerCase().includes(q);
  }), [items, catFilter, q]);

  // 类别计数 — 用于在 chip 上显示数字
  const catCounts = useMemo(() => {
    const m: Record<string, number> = {};
    for (const it of items) m[it.category] = (m[it.category] || 0) + 1;
    return m;
  }, [items]);

  return (
    <>
      {/* Toolbar */}
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-body">
          <div className="flex items-center" style={{
            gap: 10, marginBottom: 12, flexWrap: "wrap",
          }}>
            <input
              className="input"
              type="text"
              placeholder="搜索灵感（标题或正文）..."
              value={textFilter}
              onChange={e => setTextFilter(e.target.value)}
              style={{ flex: 1, minWidth: 240 }}
            />
            {textFilter && (
              <button className="btn"
                      onClick={() => setTextFilter("")}>清除</button>
            )}
            <button className="btn-primary"
                    onClick={() => setDraft({
                      id: "", category: "scene", title: "", content: "",
                    })}>
              + 添加灵感
            </button>
          </div>

          {/* Category chips */}
          <div className="flex items-center" style={{ gap: 6, flexWrap: "wrap" }}>
            <CategoryChip
              label="全部" count={items.length}
              color="var(--text-secondary)"
              active={catFilter === ""}
              onClick={() => setCatFilter("")}
            />
            {CATEGORIES.map(c => (
              <CategoryChip key={c.key}
                label={tInspirationCategory(c.key)}
                count={catCounts[c.key] || 0}
                color={c.color}
                active={catFilter === c.key}
                onClick={() => setCatFilter(c.key)}
              />
            ))}
            <div style={{ flex: 1 }} />
            <span className="text-xs" style={{
              fontFamily: "var(--font-mono)", color: "var(--text-tertiary)",
            }}>{shown.length} / {items.length}</span>
          </div>
        </div>
      </div>

      {/* Inline add/edit form */}
      {draft && (
        <div className="card" style={{
          marginBottom: 14, border: "1px solid var(--accent)",
        }}>
          <div className="card-header">
            <h3 style={{ margin: 0 }}>
              {draft.id ? "编辑灵感" : "添加灵感"}
            </h3>
            <button className="btn-icon" onClick={() => setDraft(null)}
                    disabled={saving} title="取消">×</button>
          </div>
          <div className="card-body flex flex-col" style={{ gap: 12 }}>
            <div>
              <label className="label" style={{
                fontSize: 11, fontWeight: 600, marginBottom: 4,
                color: "var(--text-tertiary)",
                textTransform: "uppercase", letterSpacing: 0.5,
              }}>类别</label>
              <select className="select" value={draft.category}
                      onChange={e => setDraft({ ...draft, category: e.target.value })}>
                {CATEGORIES.map(c => (
                  <option key={c.key} value={c.key}>
                    {tInspirationCategory(c.key)}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="label" style={{
                fontSize: 11, fontWeight: 600, marginBottom: 4,
                color: "var(--text-tertiary)",
                textTransform: "uppercase", letterSpacing: 0.5,
              }}>标题（可选）</label>
              <input className="input" value={draft.title}
                     placeholder="一句话概括"
                     onChange={e => setDraft({ ...draft, title: e.target.value })} />
            </div>
            <div>
              <label className="label" style={{
                fontSize: 11, fontWeight: 600, marginBottom: 4,
                color: "var(--text-tertiary)",
                textTransform: "uppercase", letterSpacing: 0.5,
              }}>正文</label>
              <textarea className="input" value={draft.content}
                        placeholder="详细描述这个灵感：一个场景、一段桥段、一个人物设计…"
                        rows={6}
                        onChange={e => setDraft({ ...draft, content: e.target.value })}
                        style={{ lineHeight: 1.7, resize: "vertical" }} />
            </div>
            <div className="flex" style={{ gap: 8, justifyContent: "flex-end" }}>
              <button className="btn" onClick={() => setDraft(null)} disabled={saving}>
                取消
              </button>
              <button className="btn-primary" onClick={save} disabled={saving}>
                {saving ? "保存中..." : (draft.id ? "保存" : "添加")}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* List */}
      {loading && items.length === 0 ? (
        <div className="empty-state" style={{ paddingTop: 40 }}>
          <p>加载中...</p>
        </div>
      ) : shown.length === 0 ? (
        <EmptyHero
          title={catFilter ? "该类别下还没有灵感" : "灵感库还是空的"}
          message="点击右上角「+ 添加灵感」记录第一个想法。"
          actions={(
            <button className="btn-primary"
                    onClick={() => setDraft({
                      id: "", category: catFilter || "scene",
                      title: "", content: "",
                    })}>
              + 添加灵感
            </button>
          )}
        />
      ) : (
        <div className="flex flex-col" style={{ gap: 10 }}>
          {shown.map(it => (
            <InspirationCard key={it.id} it={it}
              onEdit={() => setDraft({ ...it })}
              onDelete={() => remove(it)}
              onSearchWorks={() => onSearchWorks(
                (it.title ? it.title + "。" : "") + it.content)} />
          ))}
        </div>
      )}
    </>
  );
}

function CategoryChip({
  label, count, color, active, onClick,
}: {
  label: string;
  count: number;
  color: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "4px 10px", fontSize: 12, fontWeight: 600,
        background: active ? color : "transparent",
        color: active ? "white" : color,
        border: `1px solid ${color}`,
        borderRadius: 12,
        cursor: "pointer",
        transition: "all 0.15s",
        display: "inline-flex", alignItems: "center", gap: 6,
      }}>
      {label}
      <span style={{
        fontFamily: "var(--font-mono)",
        fontSize: 10, fontWeight: 700,
        opacity: 0.85,
      }}>{count}</span>
    </button>
  );
}

function InspirationCard({ it, onEdit, onDelete, onSearchWorks }: {
  it: Inspiration;
  onEdit: () => void;
  onDelete: () => void;
  onSearchWorks: () => void;
}) {
  const color = catColor(it.category);
  return (
    <div className="card" style={{
      borderLeft: `3px solid ${color}`,
    }}>
      <div className="card-body" style={{ padding: "14px 16px" }}>
        <div className="flex items-center" style={{
          gap: 10, marginBottom: 8, flexWrap: "wrap",
        }}>
          <span style={{
            fontSize: 11, padding: "2px 8px", borderRadius: 4,
            color, background: "var(--bg-surface-2)",
            border: `1px solid ${color}`,
            flexShrink: 0, fontWeight: 600,
          }}>{catLabel(it.category)}</span>
          {it.title && (
            <span style={{
              fontSize: 14, fontWeight: 700,
              color: "var(--text-primary)",
              fontFamily: "var(--font-serif)",
            }}>{it.title}</span>
          )}
          <div style={{ flex: 1 }} />
          <button className="btn"
                  onClick={onSearchWorks}
                  style={{ fontSize: 12, padding: "4px 12px" }}
                  title="用这条灵感的内容跨参考作品模糊搜索相似片段">
            搜索参考作品
          </button>
          <button className="btn-ghost"
                  onClick={onEdit}
                  style={{ fontSize: 12 }}>编辑</button>
          <button className="btn-icon"
                  onClick={onDelete}
                  title="删除"
                  style={{
                    width: 28, height: 28, fontSize: 16,
                    color: "var(--text-tertiary)",
                  }}>×</button>
        </div>
        <div style={{
          fontSize: 13, lineHeight: 1.75,
          color: "var(--text-secondary)",
          whiteSpace: "pre-wrap",
        }}>{it.content}</div>
      </div>
    </div>
  );
}

function EmptyHero({
  title, message, actions,
}: {
  title: string;
  message: string;
  actions?: React.ReactNode;
}) {
  return (
    <div style={{
      padding: "40px 20px", textAlign: "center",
      background: "var(--bg-surface)",
      border: "1px dashed var(--border)",
      borderRadius: "var(--radius-sm)",
    }}>
      <div style={{
        fontSize: 14, fontWeight: 600,
        color: "var(--text-secondary)", marginBottom: 6,
      }}>{title}</div>
      <div className="text-xs" style={{
        color: "var(--text-tertiary)", lineHeight: 1.7,
        maxWidth: 420, margin: "0 auto 16px",
      }}>{message}</div>
      {actions}
    </div>
  );
}
