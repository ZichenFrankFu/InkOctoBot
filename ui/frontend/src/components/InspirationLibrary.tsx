/* 灵感库 — a personal store of free-text idea snippets (scenes, plot
 * devices, character designs, …).
 *
 * Rendered as a tab inside the 灵感搜索 page. Full CRUD against the
 * /api/references/inspirations endpoints; each entry carries a「搜索
 * 参考作品」action that runs the cross-work fuzzy search using the
 * inspiration's text as the query (handled by the parent page). */
import React, { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost, apiPut, apiDelete } from "../api/client";
import { useToast } from "./shared/Toast";
import { useDialog } from "./shared/Dialog";

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
const catLabel = (k: string) => CATEGORIES.find(c => c.key === k)?.label || k;
const catColor = (k: string) => CATEGORIES.find(c => c.key === k)?.color || "var(--text-tertiary)";

export default function InspirationLibrary({ onSearchWorks }: {
  /** Run the cross-work fuzzy search with the given text as the query. */
  onSearchWorks: (query: string) => void;
}) {
  const { toast } = useToast();
  const { confirm } = useDialog();
  const [items, setItems] = useState<Inspiration[]>([]);
  const [loading, setLoading] = useState(false);
  const [catFilter, setCatFilter] = useState("");
  // draft being added/edited; id === "" means a new inspiration.
  const [draft, setDraft] = useState<Inspiration | null>(null);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await apiGet<{ items: Inspiration[] }>("/api/references/inspirations");
      setItems(r.items || []);
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

  const shown = items.filter(it => !catFilter || it.category === catFilter);

  return (
    <>
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-body">
          <div className="flex gap-8 items-center" style={{ flexWrap: "wrap" }}>
            <div className="flex gap-4" style={{ flexWrap: "wrap" }}>
              <button className={catFilter === "" ? "btn-primary" : "btn"}
                      style={{ fontSize: 11, padding: "2px 10px" }}
                      onClick={() => setCatFilter("")}>全部</button>
              {CATEGORIES.map(c => (
                <button key={c.key}
                        className={catFilter === c.key ? "btn-primary" : "btn"}
                        style={{ fontSize: 11, padding: "2px 10px" }}
                        onClick={() => setCatFilter(c.key)}>{c.label}</button>
              ))}
            </div>
            <div style={{ flex: 1 }} />
            <button className="btn-primary"
                    onClick={() => setDraft({ id: "", category: "scene", title: "", content: "" })}>
              + 添加灵感
            </button>
          </div>
          <div className="text-xs text-muted" style={{ marginTop: 8, lineHeight: 1.55 }}>
            把想到的场景、桥段、人物设计等记到灵感库；在任意一条灵感上点「搜索参考作品」即可用它的内容跨作品模糊检索相似的片段。
          </div>
        </div>
      </div>

      {draft && (
        <div className="card" style={{ marginBottom: 14, border: "1px solid var(--accent)" }}>
          <div className="card-body flex flex-col gap-8">
            <div className="flex items-center gap-8">
              <span className="text-xs text-muted" style={{ minWidth: 36 }}>类别</span>
              <select className="select" value={draft.category}
                      onChange={e => setDraft({ ...draft, category: e.target.value })}
                      style={{ fontSize: 12 }}>
                {CATEGORIES.map(c => <option key={c.key} value={c.key}>{c.label}</option>)}
              </select>
            </div>
            <div className="flex items-center gap-8">
              <span className="text-xs text-muted" style={{ minWidth: 36 }}>标题</span>
              <input className="input" value={draft.title}
                     placeholder="一句话概括（可选）"
                     onChange={e => setDraft({ ...draft, title: e.target.value })}
                     style={{ flex: 1, fontSize: 12 }} />
            </div>
            <textarea className="input" value={draft.content}
                      placeholder="详细描述这个灵感：一个场景、一段桥段、一个人物设计…"
                      rows={5}
                      onChange={e => setDraft({ ...draft, content: e.target.value })}
                      style={{ fontSize: 13, lineHeight: 1.7, resize: "vertical" }} />
            <div className="flex gap-6" style={{ justifyContent: "flex-end" }}>
              <button className="btn" onClick={() => setDraft(null)} disabled={saving}>取消</button>
              <button className="btn-primary" onClick={save} disabled={saving}>
                {saving ? "保存中..." : (draft.id ? "保存" : "添加")}
              </button>
            </div>
          </div>
        </div>
      )}

      {loading ? (
        <div className="empty-state" style={{ paddingTop: 40 }}><p>加载中...</p></div>
      ) : shown.length === 0 ? (
        <div className="empty-state" style={{ paddingTop: 40 }}>
          <p>{catFilter
            ? "该类别下还没有灵感。"
            : "灵感库还是空的。点击「添加灵感」记录第一个想法。"}</p>
        </div>
      ) : (
        <div className="flex flex-col gap-10">
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

function InspirationCard({ it, onEdit, onDelete, onSearchWorks }: {
  it: Inspiration;
  onEdit: () => void;
  onDelete: () => void;
  onSearchWorks: () => void;
}) {
  return (
    <div className="card">
      <div className="card-body">
        <div className="flex items-center gap-8" style={{ marginBottom: 6, flexWrap: "wrap" }}>
          <span className="tag" style={{
            fontSize: 10, padding: "1px 8px",
            color: catColor(it.category), background: "var(--bg-surface-2)",
            border: `1px solid ${catColor(it.category)}`,
          }}>{catLabel(it.category)}</span>
          {it.title && (
            <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>{it.title}</span>
          )}
          <div style={{ flex: 1 }} />
          <button className="btn-primary" style={{ fontSize: 11, padding: "3px 10px" }}
                  onClick={onSearchWorks}
                  title="用这条灵感的内容跨参考作品模糊搜索相似片段">搜索参考作品</button>
          <button className="btn-ghost" style={{ fontSize: 11, padding: "2px 8px" }}
                  onClick={onEdit}>编辑</button>
          <button className="btn-ghost" style={{ fontSize: 11, padding: "2px 8px", color: "var(--error)" }}
                  onClick={onDelete}>删除</button>
        </div>
        <div style={{
          fontSize: 13, lineHeight: 1.7,
          color: "var(--text-secondary)", whiteSpace: "pre-wrap",
        }}>{it.content}</div>
      </div>
    </div>
  );
}
