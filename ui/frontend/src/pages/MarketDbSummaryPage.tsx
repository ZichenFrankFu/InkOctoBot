/**
 * MarketDbSummaryPage — the visualisation that backs the 市场总览
 * landing tab.
 *
 * Shows only DB-level summary stats — book/list/snapshot counts, per-
 * platform breakdown, top categories, ranking families — plus a
 * "数据修正" panel for manual CRUD on novels (so the user can fix a
 * wrong total_words, switch a status, or delete a duplicate row).
 * Chapter / word-count drill-down lives in 市场特征提取 instead.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiGet, apiPost, apiPut, apiDelete } from "../api/client";
import { useToast } from "../components/shared/Toast";


interface OverviewResp {
  novel_count: number;
  rank_list_count: number;
  snapshot_count: number;
  chapter_count: number;
  recent_snapshots: Array<{
    snapshot_date: string;
    item_count: number;
    platform: string;
    rank_family: string;
    rank_sub_cat?: string;
  }>;
  platform_breakdown: Array<{ platform: string; count: number }>;
  categories: Array<{ main_category: string; count: number }>;
  rank_families: Array<{ rank_family: string; platform: string; snapshot_count: number }>;
}


interface NovelRow {
  novel_uid: number;
  platform: string;
  author?: string;
  main_category?: string;
  status?: string;
  total_words?: number;
  title?: string;
  url?: string;
  intro?: string;
  last_seen_date?: string;
}


export default function MarketDbSummaryPage() {
  const { toast } = useToast();
  const [data, setData] = useState<OverviewResp | null>(null);
  const [loading, setLoading] = useState(false);
  const [platform, setPlatform] = useState("");

  // Curation surface
  const [curationOpen, setCurationOpen] = useState(false);
  const [curationQuery, setCurationQuery] = useState("");
  const [curationHits, setCurationHits] = useState<NovelRow[]>([]);
  const [editing, setEditing] = useState<NovelRow | null>(null);
  const [createOpen, setCreateOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const qs = platform ? `?platform=${encodeURIComponent(platform)}` : "";
      const r = await apiGet<OverviewResp>(`/api/db/overview${qs}`);
      setData(r);
    } catch (e: any) {
      toast(`加载失败: ${e.message}`, "error");
    } finally {
      setLoading(false);
    }
  }, [platform, toast]);

  useEffect(() => { load(); }, [load]);

  const runCurationSearch = useCallback(async () => {
    const q = curationQuery.trim();
    if (!q) { setCurationHits([]); return; }
    try {
      const params = new URLSearchParams({ q });
      if (platform) params.set("platform", platform);
      const r = await apiGet<{ items: NovelRow[] }>(`/api/db/search_novels?${params}`);
      setCurationHits(r.items || []);
    } catch (e: any) {
      toast(`搜索失败: ${e.message}`, "error");
    }
  }, [curationQuery, platform, toast]);

  const saveEdit = useCallback(async () => {
    if (!editing) return;
    try {
      await apiPut(`/api/db/novel/${editing.novel_uid}`, {
        author: editing.author ?? "",
        intro: editing.intro ?? "",
        main_category: editing.main_category ?? "",
        status: editing.status ?? "ongoing",
        total_words: editing.total_words ?? 0,
        url: editing.url ?? "",
      });
      toast("已保存", "success");
      setEditing(null);
      runCurationSearch();
      load();
    } catch (e: any) {
      toast(`保存失败: ${e.message}`, "error");
    }
  }, [editing, runCurationSearch, load, toast]);

  const deleteNovel = useCallback(async (n: NovelRow) => {
    if (!window.confirm(`确认删除「${n.title || n.novel_uid}」？\n会同时清掉它在 rank_entries / chapters / titles / tag_map 中的所有引用，不可恢复。`)) return;
    try {
      await apiDelete(`/api/db/novel/${n.novel_uid}`);
      toast("已删除", "success");
      runCurationSearch();
      load();
    } catch (e: any) {
      toast(`删除失败: ${e.message}`, "error");
    }
  }, [runCurationSearch, load, toast]);

  // Derived: max counts for bar normalisation
  const maxPlatform = useMemo(() => Math.max(1, ...(data?.platform_breakdown.map(p => p.count) || [1])), [data]);
  const maxCategory = useMemo(() => Math.max(1, ...(data?.categories.map(c => c.count) || [1])), [data]);

  if (!data && loading) {
    return <div className="loading" style={{ paddingTop: 80 }}><div className="loading-spinner" />加载中...</div>;
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, padding: 4 }}>
      {/* Top-row stat tiles */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 12 }}>
        <StatTile label="作品总数"    value={data?.novel_count ?? 0}    accent="var(--accent)" />
        <StatTile label="榜单总数"    value={data?.rank_list_count ?? 0} accent="var(--gold)" />
        <StatTile label="snapshot 数" value={data?.snapshot_count ?? 0}  accent="var(--cyan)" />
        <StatTile label="已采集开篇章节" value={data?.chapter_count ?? 0}  accent="var(--jade)" />
      </div>

      {/* Platform filter */}
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>平台筛选:</span>
        <button className={platform === "" ? "btn-primary" : "btn"}
                style={{ fontSize: 11, padding: "3px 10px" }}
                onClick={() => setPlatform("")}>全部</button>
        {data?.platform_breakdown.map(p => (
          <button key={p.platform}
                  className={platform === p.platform ? "btn-primary" : "btn"}
                  style={{ fontSize: 11, padding: "3px 10px" }}
                  onClick={() => setPlatform(p.platform)}>{p.platform}</button>
        ))}
        <div style={{ flex: 1 }} />
        <button className="btn" onClick={load} disabled={loading}>{loading ? "刷新中..." : "刷新"}</button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
        {/* Platform distribution */}
        <div className="card" style={{ padding: 14 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>平台作品分布</h3>
          {(data?.platform_breakdown || []).length === 0 ? (
            <p style={{ color: "var(--text-tertiary)", fontSize: 12 }}>无数据</p>
          ) : (
            <div className="bar-chart" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {data!.platform_breakdown.map(p => (
                <div key={p.platform} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ width: 70, fontSize: 12, color: "var(--text-secondary)" }}>{p.platform}</span>
                  <div style={{ flex: 1, height: 18, background: "var(--bg-surface-2)", borderRadius: 4, overflow: "hidden" }}>
                    <div style={{
                      width: `${(p.count / maxPlatform) * 100}%`,
                      height: "100%",
                      background: "linear-gradient(90deg, var(--accent), var(--cyan))",
                      transition: "width 0.4s ease",
                    }} />
                  </div>
                  <span className="font-mono" style={{ fontSize: 11, width: 60, textAlign: "right" }}>{p.count.toLocaleString()}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Categories */}
        <div className="card" style={{ padding: 14 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>主类目分布 (top 15)</h3>
          {(data?.categories || []).length === 0 ? (
            <p style={{ color: "var(--text-tertiary)", fontSize: 12 }}>无数据</p>
          ) : (
            <div className="bar-chart" style={{ display: "flex", flexDirection: "column", gap: 4, maxHeight: 280, overflowY: "auto" }}>
              {data!.categories.map(c => (
                <div key={c.main_category || "(空)"} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ width: 80, fontSize: 11, color: "var(--text-secondary)" }} title={c.main_category}>
                    {c.main_category || "(空)"}
                  </span>
                  <div style={{ flex: 1, height: 14, background: "var(--bg-surface-2)", borderRadius: 3, overflow: "hidden" }}>
                    <div style={{
                      width: `${(c.count / maxCategory) * 100}%`,
                      height: "100%",
                      background: "var(--gold)",
                    }} />
                  </div>
                  <span className="font-mono" style={{ fontSize: 10, width: 50, textAlign: "right" }}>{c.count.toLocaleString()}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Recent snapshots */}
      <div className="card" style={{ padding: 14 }}>
        <h3 style={{ marginTop: 0, fontSize: 14 }}>最近 snapshot</h3>
        {(data?.recent_snapshots || []).length === 0 ? (
          <p style={{ color: "var(--text-tertiary)", fontSize: 12 }}>无数据</p>
        ) : (
          <table style={{ width: "100%", fontSize: 12 }}>
            <thead>
              <tr style={{ color: "var(--text-tertiary)", textAlign: "left" }}>
                <th style={{ padding: "4px 6px" }}>日期</th>
                <th style={{ padding: "4px 6px" }}>平台</th>
                <th style={{ padding: "4px 6px" }}>榜单</th>
                <th style={{ padding: "4px 6px", textAlign: "right" }}>条目数</th>
              </tr>
            </thead>
            <tbody>
              {data!.recent_snapshots.map((s, i) => (
                <tr key={`${s.snapshot_date}-${i}`} style={{ borderTop: "1px solid var(--border)" }}>
                  <td style={{ padding: "4px 6px" }}>{s.snapshot_date}</td>
                  <td style={{ padding: "4px 6px" }}>{s.platform}</td>
                  <td style={{ padding: "4px 6px" }}>{s.rank_family}{s.rank_sub_cat ? ` · ${s.rank_sub_cat}` : ""}</td>
                  <td style={{ padding: "4px 6px", textAlign: "right" }}>{s.item_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Curation panel (CRUD) */}
      <div className="card" style={{ padding: 14 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <div>
            <h3 style={{ margin: 0, fontSize: 14 }}>数据修正</h3>
            <p style={{ margin: "4px 0 0", fontSize: 11, color: "var(--text-tertiary)" }}>
              修复作品字段（字数、状态、类别、简介、url 等），或删掉错误条目，或手动新建一行。
            </p>
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn-primary" onClick={() => setCreateOpen(true)}>+ 新建作品</button>
            <button className="btn" onClick={() => setCurationOpen(o => !o)}>
              {curationOpen ? "收起" : "展开搜索修正"}
            </button>
          </div>
        </div>

        {curationOpen && (
          <div style={{ marginTop: 12 }}>
            <div style={{ display: "flex", gap: 8 }}>
              <input
                className="input"
                style={{ flex: 1 }}
                placeholder="按标题 / 作者搜索..."
                value={curationQuery}
                onChange={e => setCurationQuery(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter") runCurationSearch(); }}
              />
              <button className="btn-primary" onClick={runCurationSearch} disabled={!curationQuery.trim()}>搜索</button>
            </div>
            {curationHits.length > 0 && (
              <table style={{ width: "100%", fontSize: 12, marginTop: 10 }}>
                <thead>
                  <tr style={{ color: "var(--text-tertiary)", textAlign: "left" }}>
                    <th style={{ padding: "4px 6px" }}>书名</th>
                    <th style={{ padding: "4px 6px" }}>平台</th>
                    <th style={{ padding: "4px 6px" }}>作者</th>
                    <th style={{ padding: "4px 6px" }}>类别</th>
                    <th style={{ padding: "4px 6px", textAlign: "right" }}>字数</th>
                    <th style={{ padding: "4px 6px" }}>状态</th>
                    <th style={{ padding: "4px 6px" }}></th>
                  </tr>
                </thead>
                <tbody>
                  {curationHits.map(n => (
                    <tr key={n.novel_uid} style={{ borderTop: "1px solid var(--border)" }}>
                      <td style={{ padding: "4px 6px" }}><strong>{n.title || n.novel_uid}</strong></td>
                      <td style={{ padding: "4px 6px" }}>{n.platform}</td>
                      <td style={{ padding: "4px 6px" }}>{n.author || "—"}</td>
                      <td style={{ padding: "4px 6px" }}>{n.main_category || "—"}</td>
                      <td style={{ padding: "4px 6px", textAlign: "right" }}>{n.total_words?.toLocaleString() ?? "—"}</td>
                      <td style={{ padding: "4px 6px" }}>{n.status || "—"}</td>
                      <td style={{ padding: "4px 6px", textAlign: "right" }}>
                        <button className="btn" style={{ fontSize: 11, padding: "2px 8px", marginRight: 4 }}
                                onClick={() => setEditing({ ...n })}>编辑</button>
                        <button className="btn" style={{ fontSize: 11, padding: "2px 8px", color: "var(--danger)" }}
                                onClick={() => deleteNovel(n)}>删除</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}
      </div>

      {editing && (
        <EditNovelModal
          novel={editing}
          onChange={n => setEditing(n)}
          onSave={saveEdit}
          onClose={() => setEditing(null)}
        />
      )}

      {createOpen && (
        <CreateNovelModal
          onClose={() => setCreateOpen(false)}
          onCreated={() => { setCreateOpen(false); load(); }}
          defaultPlatform={platform}
        />
      )}
    </div>
  );
}


function StatTile({ label, value, accent }: { label: string; value: number | string; accent: string }) {
  return (
    <div className="card" style={{ padding: 14, textAlign: "center", borderTop: `3px solid ${accent}` }}>
      <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{label}</div>
      <div style={{ fontSize: 26, fontWeight: 700, marginTop: 4 }}>
        {typeof value === "number" ? value.toLocaleString() : value}
      </div>
    </div>
  );
}


function EditNovelModal({
  novel, onChange, onSave, onClose,
}: { novel: NovelRow; onChange: (n: NovelRow) => void; onSave: () => void; onClose: () => void }) {
  return (
    <Modal title={`编辑作品: ${novel.title || novel.novel_uid}`} onClose={onClose}>
      <Field label="作者">
        <input className="input" value={novel.author || ""}
               onChange={e => onChange({ ...novel, author: e.target.value })} />
      </Field>
      <Field label="主类目">
        <input className="input" value={novel.main_category || ""}
               onChange={e => onChange({ ...novel, main_category: e.target.value })} />
      </Field>
      <Field label="字数">
        <input className="input" type="number" value={novel.total_words ?? 0}
               onChange={e => onChange({ ...novel, total_words: parseInt(e.target.value) || 0 })} />
      </Field>
      <Field label="状态">
        <select className="select" value={novel.status || "ongoing"}
                onChange={e => onChange({ ...novel, status: e.target.value })}>
          <option value="ongoing">ongoing (连载中)</option>
          <option value="completed">completed (已完结)</option>
        </select>
      </Field>
      <Field label="URL">
        <input className="input" value={novel.url || ""}
               onChange={e => onChange({ ...novel, url: e.target.value })} />
      </Field>
      <Field label="简介">
        <textarea className="input" rows={5} value={novel.intro || ""}
                  onChange={e => onChange({ ...novel, intro: e.target.value })} />
      </Field>
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 12 }}>
        <button className="btn" onClick={onClose}>取消</button>
        <button className="btn-primary" onClick={onSave}>保存</button>
      </div>
    </Modal>
  );
}


function CreateNovelModal({
  onClose, onCreated, defaultPlatform,
}: { onClose: () => void; onCreated: () => void; defaultPlatform: string }) {
  const { toast } = useToast();
  const [form, setForm] = useState({
    platform: defaultPlatform || "qidian",
    platform_novel_id: "",
    title: "",
    author: "",
    main_category: "",
    status: "ongoing",
    total_words: 0,
    url: "",
    intro: "",
  });
  const [saving, setSaving] = useState(false);
  const submit = async () => {
    if (!form.platform.trim() || !form.title.trim()) {
      toast("平台 + 书名必填", "error");
      return;
    }
    setSaving(true);
    try {
      await apiPost("/api/db/novel", form);
      toast("已创建", "success");
      onCreated();
    } catch (e: any) {
      toast(`创建失败: ${e.message}`, "error");
    } finally {
      setSaving(false);
    }
  };
  return (
    <Modal title="新建作品" onClose={onClose}>
      <Field label="平台">
        <input className="input" value={form.platform}
               onChange={e => setForm({ ...form, platform: e.target.value })} />
      </Field>
      <Field label="书名">
        <input className="input" value={form.title}
               onChange={e => setForm({ ...form, title: e.target.value })} />
      </Field>
      <Field label="平台内部 id (可选)">
        <input className="input" value={form.platform_novel_id}
               onChange={e => setForm({ ...form, platform_novel_id: e.target.value })} />
      </Field>
      <Field label="作者">
        <input className="input" value={form.author}
               onChange={e => setForm({ ...form, author: e.target.value })} />
      </Field>
      <Field label="主类目">
        <input className="input" value={form.main_category}
               onChange={e => setForm({ ...form, main_category: e.target.value })} />
      </Field>
      <Field label="字数">
        <input className="input" type="number" value={form.total_words}
               onChange={e => setForm({ ...form, total_words: parseInt(e.target.value) || 0 })} />
      </Field>
      <Field label="状态">
        <select className="select" value={form.status}
                onChange={e => setForm({ ...form, status: e.target.value })}>
          <option value="ongoing">ongoing</option>
          <option value="completed">completed</option>
        </select>
      </Field>
      <Field label="URL">
        <input className="input" value={form.url}
               onChange={e => setForm({ ...form, url: e.target.value })} />
      </Field>
      <Field label="简介">
        <textarea className="input" rows={4} value={form.intro}
                  onChange={e => setForm({ ...form, intro: e.target.value })} />
      </Field>
      <div style={{ display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 12 }}>
        <button className="btn" onClick={onClose}>取消</button>
        <button className="btn-primary" onClick={submit} disabled={saving}>{saving ? "创建中..." : "创建"}</button>
      </div>
    </Modal>
  );
}


function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div onClick={onClose} style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.45)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1100,
    }}>
      <div className="card" onClick={e => e.stopPropagation()} style={{
        width: "min(560px, 95vw)", maxHeight: "90vh", overflow: "auto",
        background: "var(--bg-surface)", padding: 16,
      }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>{title}</h3>
          <button className="btn-sm" onClick={onClose}>x</button>
        </div>
        {children}
      </div>
    </div>
  );
}


function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <label style={{ fontSize: 12, color: "var(--text-secondary)", display: "block", marginBottom: 4 }}>{label}</label>
      {children}
    </div>
  );
}
