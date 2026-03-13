import React, { useEffect, useState, useCallback, useRef } from "react";
import { apiGet, apiPost, apiPut, apiDelete } from "../api/client";
import { useResizable } from "../hooks/useResizable";
import { useToast } from "../components/shared/Toast";
import type { ReferenceWork, ReferenceEntry, MediaType } from "../api/types";

const MEDIA_TYPES: { value: MediaType; label: string; color: string }[] = [
  { value: "web_novel", label: "网文", color: "var(--accent)" },
  { value: "literature", label: "文学", color: "var(--jade)" },
  { value: "poetry", label: "诗歌", color: "var(--purple)" },
  { value: "film", label: "电影", color: "var(--gold)" },
  { value: "anime", label: "动漫", color: "#f472b6" },
  { value: "tv_series", label: "电视剧", color: "var(--indigo)" },
  { value: "other", label: "其他", color: "var(--text-tertiary)" },
];

const ENTRY_TYPES = [
  "scene", "character", "worldbuilding", "dialogue", "technique",
  "atmosphere", "plot_structure", "emotional_beat", "hook", "style_sample", "other",
];

function mediaLabel(mt: string) {
  return MEDIA_TYPES.find(m => m.value === mt)?.label || mt;
}
function mediaColor(mt: string) {
  return MEDIA_TYPES.find(m => m.value === mt)?.color || "var(--text-tertiary)";
}
function pj(s: string | null | undefined): any {
  if (!s) return null;
  try { return JSON.parse(s); } catch { return null; }
}
function stars(n: number | null | undefined): string {
  if (!n) return "\u2014";
  return "\u2605".repeat(n) + "\u2606".repeat(5 - n);
}

/* ---- Clickable star rating component ---- */
function StarRating({ value, onChange }: { value: number; onChange: (n: number) => void }) {
  const [hover, setHover] = useState(0);
  return (
    <div className="flex gap-4" style={{ display: "inline-flex" }}>
      {[1, 2, 3, 4, 5].map(n => (
        <button
          key={n}
          className="btn-ghost"
          style={{
            fontSize: 20,
            padding: "2px 4px",
            color: n <= (hover || value) ? "var(--gold)" : "var(--text-disabled)",
            cursor: "pointer",
            lineHeight: 1,
          }}
          onMouseEnter={() => setHover(n)}
          onMouseLeave={() => setHover(0)}
          onClick={() => onChange(value === n ? 0 : n)}
        >
          &#x2605;
        </button>
      ))}
    </div>
  );
}

export default function ReferenceLibraryPage() {
  const { toast } = useToast();
  const [works, setWorks] = useState<ReferenceWork[]>([]);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState("");
  const [filterMedia, setFilterMedia] = useState("");
  const [sel, setSel] = useState<ReferenceWork | null>(null);
  const [entries, setEntries] = useState<ReferenceEntry[]>([]);
  const [loading, setLoading] = useState(false);

  // Add work modal
  const [showAddWork, setShowAddWork] = useState(false);
  const [nTitle, setNTitle] = useState("");
  const [nCreator, setNCreator] = useState("");
  const [nMedia, setNMedia] = useState<MediaType>("web_novel");
  const [nGenre, setNGenre] = useState("");
  const [nWhy, setNWhy] = useState("");
  const [nRating, setNRating] = useState(0);

  // Upload novel text modal
  const [showUpload, setShowUpload] = useState(false);
  const [uTitle, setUTitle] = useState("");
  const [uCreator, setUCreator] = useState("");
  const [uGenre, setUGenre] = useState("");
  const [uMedia, setUMedia] = useState<MediaType>("web_novel");
  const [uFile, setUFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Add entry inline form
  const [showAddEntry, setShowAddEntry] = useState(false);
  const [eType, setEType] = useState("scene");
  const [eTitle, setETitle] = useState("");
  const [eCont, setECont] = useState("");
  const [ePos, setEPos] = useState("");
  const [eNotes, setENotes] = useState("");

  // Batch management
  const [batchMode, setBatchMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const leftPanel = useResizable({ direction: "horizontal", initialSize: 360, minSize: 260, maxSize: 560 });

  /* ---- Data loading ---- */
  const load = useCallback(async () => {
    setLoading(true);
    const params = new URLSearchParams();
    if (search) params.set("search", search);
    if (filterMedia) params.set("media_type", filterMedia);
    try {
      const r = await apiGet<{ items: ReferenceWork[]; total: number }>(`/api/references/works?${params}`);
      setWorks(r.items || []);
      setTotal(r.total || 0);
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  }, [search, filterMedia]);

  useEffect(() => {
    load();
  }, [load]);

  async function selectWork(w: ReferenceWork) {
    setSel(w);
    try {
      const r = await apiGet<{ items: ReferenceEntry[] }>(`/api/references/entries/${w.ref_id}`);
      setEntries(r.items || []);
    } catch {
      setEntries([]);
    }
  }

  async function saveAnalysisField(fieldKey: string, data: any) {
    if (!sel) return;
    try {
      const updated = await apiPut<ReferenceWork>(`/api/references/works/${sel.ref_id}/analysis`, { field: fieldKey, data });
      setSel(updated);
    } catch (e: any) {
      toast(e?.message || "操作失败", "error");
    }
  }

  async function addWork() {
    if (!nTitle.trim()) return;
    try {
      await apiPost("/api/references/works", {
        title: nTitle.trim(),
        creator: nCreator.trim() || undefined,
        media_type: nMedia,
        genre: nGenre.trim() || undefined,
        user_why_i_like: nWhy.trim() || undefined,
        user_rating: nRating || undefined,
        source: "manual",
      });
      setShowAddWork(false);
      setNTitle(""); setNCreator(""); setNGenre(""); setNWhy(""); setNRating(0);
      load();
    } catch (e: any) {
      toast(e.message || "操作失败", "error");
    }
  }

  async function uploadNovelText() {
    if (!uFile) return;
    
    // Upload for existing work
    if (sel) {
      setUploading(true);
      try {
        const fd = new FormData();
        fd.append("file", uFile);
        const resp = await fetch(`/api/references/works/${sel.ref_id}/upload`, { method: "POST", body: fd });
        if (!resp.ok) {
          const err = await resp.text();
          throw new Error(err || resp.statusText);
        }
        setShowUpload(false);
        setUFile(null);
        if (fileInputRef.current) fileInputRef.current.value = "";
        
        const updated = await resp.json();
        setSel(updated);
        setWorks(prev => prev.map(w => w.ref_id === updated.ref_id ? updated : w));
      } catch (e: any) {
        toast(e.message || "操作失败", "error");
      }
      setUploading(false);
      return;
    }

    // Upload new work from scratch
    if (!uTitle.trim()) return;
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("title", uTitle.trim());
      if (uCreator.trim()) fd.append("creator", uCreator.trim());
      if (uGenre.trim()) fd.append("genre", uGenre.trim());
      fd.append("media_type", uMedia);
      fd.append("file", uFile);
      const resp = await fetch("/api/references/works/upload", { method: "POST", body: fd });
      if (!resp.ok) {
        const err = await resp.text();
        throw new Error(err || resp.statusText);
      }
      setShowUpload(false);
      setUTitle(""); setUCreator(""); setUGenre(""); setUFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      load();
    } catch (e: any) {
      toast(e.message || "操作失败", "error");
    }
    setUploading(false);
  }

  async function delWork(id: string) {
    if (!confirm("确定删除这部参考作品及其所有条目？")) return;
    try {
      await apiDelete(`/api/references/works/${id}`);
      setSel(null);
      setEntries([]);
      load();
    } catch (e: any) {
      toast(e.message || "操作失败", "error");
    }
  }

  async function batchDelete() {
    if (!selectedIds.size) return;
    if (!confirm(`确定删除选中的 ${selectedIds.size} 部作品？`)) return;
    for (const id of selectedIds) {
      try {
        await apiDelete(`/api/references/works/${id}`);
      } catch (e: any) {
        toast(e.message || "操作失败", "error");
      }
    }
    if (sel && selectedIds.has(sel.ref_id)) {
      setSel(null);
      setEntries([]);
    }
    setSelectedIds(new Set());
    setBatchMode(false);
    load();
  }

  function toggleSelected(id: string) {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function updateRating(rating: number) {
    if (!sel) return;
    try {
      await apiPut(`/api/references/works/${sel.ref_id}`, { user_rating: rating || null });
      const updated = { ...sel, user_rating: rating || null } as ReferenceWork;
      setSel(updated);
      setWorks(prev => prev.map(w => w.ref_id === sel.ref_id ? updated : w));
    } catch (e: any) {
      toast(e.message || "操作失败", "error");
    }
  }

  async function runPreprocess(id: string) {
    try {
      const r = await apiPost<any>(`/api/references/preprocess/${id}`, {});
      toast(`特征提取完成 (${r.chapters || 0} 章, ${r.errors?.length || 0} 错误)`, "success");
    } catch (e: any) {
      toast(e.message || "操作失败", "error");
    }
    load();
    if (sel?.ref_id === id) {
      try {
        const w = await apiGet<ReferenceWork>(`/api/references/works/${id}`);
        setSel(w);
      } catch {}
    }
  }

  async function addEntry() {
    if (!sel || !eCont.trim()) return;
    try {
      await apiPost("/api/references/entries", {
        ref_id: sel.ref_id,
        entry_type: eType,
        title: eTitle.trim(),
        content: eCont.trim(),
        position_label: ePos.trim() || undefined,
        user_notes: eNotes.trim() || undefined,
      });
      setShowAddEntry(false);
      setETitle(""); setECont(""); setEPos(""); setENotes("");
      selectWork(sel);
    } catch (e: any) {
      toast(e.message || "操作失败", "error");
    }
  }

  async function delEntry(eid: string) {
    try {
      await apiDelete(`/api/references/entries/${eid}`);
      if (sel) selectWork(sel);
    } catch (e: any) {
      toast(e.message || "操作失败", "error");
    }
  }

  const statusBadge = (s: string) => {
    const map: Record<string, { cls: string; label: string }> = {
      done: { cls: "status-ongoing", label: "已分析" },
      pending: { cls: "qidian", label: "待处理" },
      processing: { cls: "cyan", label: "处理中" },
      error: { cls: "accent", label: "出错" },
      not_applicable: { cls: "category", label: "手动" },
    };
    const m = map[s] || map.not_applicable;
    return <span className={`tag ${m.cls}`}>{m.label}</span>;
  };

  return (
    <div className="page-full">
      {/* Header */}
      <div className="page-header" style={{ paddingBottom: 8 }}>
        <div className="page-header-row">
          <div>
            <h2>参考作品库</h2>
            <p>录入你喜欢的作品 &mdash; 网文、文学、电影、动漫均可 &middot; 记录审美倾向 &middot; 自动提取风格特征</p>
          </div>
          <div className="flex gap-8">
            <button className="btn-primary" onClick={() => setShowAddWork(true)}>+ 添加作品</button>
          </div>
        </div>
      </div>

      {/* Toolbar */}
      <div className="flex gap-8 items-center" style={{ padding: "0 0 14px", flexWrap: "wrap" }}>
        <input
          className="input"
          placeholder="搜索标题 / 创作者..."
          value={search}
          onChange={e => setSearch(e.target.value)}
          style={{ width: 220 }}
        />
        <select className="select" value={filterMedia} onChange={e => setFilterMedia(e.target.value)}>
          <option value="">全部类型</option>
          {MEDIA_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
        </select>
        <button
          className={batchMode ? "btn-primary" : "btn"}
          style={{ fontSize: 12, padding: "4px 12px" }}
          onClick={() => { setBatchMode(!batchMode); setSelectedIds(new Set()); }}
        >
          批量管理
        </button>
        {batchMode && (
          <>
            <button
              className="btn"
              style={{ fontSize: 12, padding: "4px 12px" }}
              onClick={() => {
                if (selectedIds.size === works.length) {
                  setSelectedIds(new Set());
                } else {
                  setSelectedIds(new Set(works.map(w => w.ref_id)));
                }
              }}
            >
              {selectedIds.size === works.length ? "取消全选" : "全选"}
            </button>
            {selectedIds.size > 0 && (
              <button
                className="btn"
                style={{ fontSize: 12, padding: "4px 12px", color: "var(--error)" }}
                onClick={batchDelete}
              >
                删除选中 ({selectedIds.size})
              </button>
            )}
          </>
        )}
        <span className="text-xs text-muted" style={{ marginLeft: "auto" }}>
          {loading ? "加载中..." : `${total} 部作品`}
        </span>
      </div>

      {/* Main two-panel layout */}
      <div className="panel-layout" style={{ borderTop: "1px solid var(--border)" }}>
        {/* ======== LEFT: Work List ======== */}
        <div className="panel" style={{ width: leftPanel.size, flexShrink: 0 }}>
          <div className="panel-body" style={{ padding: "8px 0" }}>
            {works.map(w => (
              <div
                key={w.ref_id}
                className={`report-list-item ${sel?.ref_id === w.ref_id ? "active" : ""}`}
                onClick={() => selectWork(w)}
                style={{ padding: "12px 14px", gap: 10 }}
              >
                {batchMode && (
                  <input
                    type="checkbox"
                    checked={selectedIds.has(w.ref_id)}
                    onChange={(e) => { e.stopPropagation(); toggleSelected(w.ref_id); }}
                    onClick={(e) => e.stopPropagation()}
                    style={{ flexShrink: 0, cursor: "pointer", width: 16, height: 16 }}
                  />
                )}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="flex items-center justify-between mb-4">
                    <span className="truncate" style={{ fontWeight: 600, fontSize: 14, color: "var(--text-primary)" }}>
                      {w.title}
                    </span>
                    {statusBadge(w.preprocessing_status)}
                  </div>
                  <div className="flex gap-8 text-xs" style={{ flexWrap: "wrap" }}>
                    <span style={{ color: mediaColor(w.media_type) }}>{mediaLabel(w.media_type)}</span>
                    {w.creator && <span className="text-muted">{w.creator}</span>}
                    {w.genre && <span className="text-muted" style={{ opacity: 0.7 }}>{w.genre}</span>}
                    {w.user_rating && <span style={{ color: "var(--gold)" }}>{stars(w.user_rating)}</span>}
                  </div>
                </div>
              </div>
            ))}
            {!works.length && !loading && (
              <div className="empty-state" style={{ padding: 40 }}>
                <p>暂无参考作品</p>
                <p className="text-xs mt-4">点击上方「添加作品」开始</p>
              </div>
            )}
          </div>
        </div>

        {/* Resize handle */}
        <div className="panel-resize-h" {...leftPanel.handleProps} />

        {/* ======== RIGHT: Detail Panel ======== */}
        <div className="panel flex-1" style={{ overflowY: "auto" }}>
          <div className="panel-body" style={{ padding: "16px 20px" }}>
            {sel ? (
              <div>
                {/* Work header */}
                <div className="flex justify-between" style={{ alignItems: "flex-start", marginBottom: 16 }}>
                  <div>
                    <div className="flex items-center gap-10 mb-4">
                      <h3 className="font-serif" style={{ fontSize: 20, fontWeight: 700, color: "var(--text-primary)" }}>
                        {sel.title}
                      </h3>
                      <span
                        className="tag"
                        style={{ color: mediaColor(sel.media_type), background: "var(--bg-surface-2)" }}
                      >
                        {mediaLabel(sel.media_type)}
                      </span>
                    </div>
                    <div className="flex gap-12 text-sm text-muted">
                      {sel.creator && <span>&#x270D;&#xFE0F; {sel.creator}</span>}
                      {sel.genre && <span>{sel.genre}</span>}
                      <span>{sel.created_at?.split("T")[0] || "\u2014"}</span>
                      {Boolean(sel.has_full_text) && (
                        <span style={{ color: "var(--jade)", fontWeight: 500 }}>&#x2705; 已上传正文</span>
                      )}
                    </div>
                  </div>
                  <div className="flex gap-6" style={{ flexShrink: 0 }}>
                    {!sel.has_full_text && (
                      <button className="btn" onClick={() => { setUTitle(sel.title); setUCreator(sel.creator || ""); setUMedia(sel.media_type); setUGenre(sel.genre || ""); setShowUpload(true); }}>
                        上传正文文本
                      </button>
                    )}
                    {Boolean(sel.has_full_text) && sel.preprocessing_status !== "done" && (
                      <button className="btn" onClick={() => runPreprocess(sel.ref_id)} style={{ color: "var(--jade)", borderColor: "var(--jade)" }}>
                        提取特征
                      </button>
                    )}
                    {Boolean(sel.has_full_text) && sel.preprocessing_status === "done" && (
                      <button className="btn" onClick={() => runPreprocess(sel.ref_id)} style={{ fontSize: 12 }}>
                        重新提取
                      </button>
                    )}
                    <button className="btn" style={{ color: "var(--error)" }} onClick={() => delWork(sel.ref_id)}>删除</button>
                  </div>
                </div>

                {/* User annotations / rating */}
                <div className="card mb-16">
                  <div className="card-body">
                    <div className="label" style={{ color: "var(--accent)", marginBottom: 6 }}>我的审美笔记</div>
                    <div style={{ marginBottom: 4 }}>
                      <StarRating value={sel.user_rating || 0} onChange={updateRating} />
                    </div>
                    {sel.user_why_i_like && (
                      <div className="text-sm" style={{ color: "var(--text-secondary)", lineHeight: 1.6 }}>
                        {sel.user_why_i_like}
                      </div>
                    )}
                  </div>
                </div>

                {/* Analysis results */}
                {sel.preprocessing_status === "done" && (
                  <div className="card mb-16">
                    <div className="card-body">
                      <div className="label" style={{ color: "var(--accent)", marginBottom: 10 }}>特征提取结果</div>
                      <div className="flex flex-col gap-8">
                        <AnalysisSection title="风格指纹" data={pj(sel.style_fingerprint_json)} fieldKey="style_fingerprint_json" onSave={saveAnalysisField} />
                        <AnalysisSection title="叙事结构" data={pj(sel.narrative_structure_json)} fieldKey="narrative_structure_json" onSave={saveAnalysisField} />
                        <AnalysisSection title="提取角色" data={pj(sel.extracted_characters_json)} fieldKey="extracted_characters_json" onSave={saveAnalysisField} />
                        <AnalysisSection title="节奏模板" data={pj(sel.rhythm_template_json)} fieldKey="rhythm_template_json" onSave={saveAnalysisField} />
                      </div>
                    </div>
                  </div>
                )}

                {/* Entries */}
                <div className="card">
                  <div className="card-header">
                    <h3>参考条目 ({entries.length})</h3>
                    <button className="btn" style={{ fontSize: 12, padding: "4px 10px" }} onClick={() => setShowAddEntry(true)}>
                      + 添加
                    </button>
                  </div>
                  <div className="card-body">
                    {/* Add entry form */}
                    {showAddEntry && (
                      <div style={{
                        padding: 12,
                        borderRadius: "var(--radius-md)",
                        background: "var(--bg-surface)",
                        border: "1px solid var(--border)",
                        marginBottom: 12,
                      }}>
                        <div className="flex gap-6 mb-8" style={{ flexWrap: "wrap" }}>
                          <select className="select" value={eType} onChange={e => setEType(e.target.value)} style={{ flex: "0 0 140px" }}>
                            {ENTRY_TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                          </select>
                          <input className="input" placeholder="标题" value={eTitle} onChange={e => setETitle(e.target.value)} style={{ flex: 1 }} />
                          <input className="input" placeholder="位置 (第3章/S1E05)" value={ePos} onChange={e => setEPos(e.target.value)} style={{ flex: "0 0 140px" }} />
                        </div>
                        <textarea
                          className="input mb-8"
                          placeholder="内容（原文摘录或你的描述）"
                          value={eCont}
                          onChange={e => setECont(e.target.value)}
                          rows={3}
                        />
                        <textarea
                          className="input mb-8"
                          placeholder="个人笔记（可选）"
                          value={eNotes}
                          onChange={e => setENotes(e.target.value)}
                          rows={2}
                        />
                        <div className="flex gap-6">
                          <button className="btn-primary" onClick={addEntry} disabled={!eCont.trim()}>保存</button>
                          <button className="btn" onClick={() => setShowAddEntry(false)}>取消</button>
                        </div>
                      </div>
                    )}

                    {/* Entry list */}
                    <div className="flex flex-col gap-6">
                      {entries.map(e => (
                        <div
                          key={e.entry_id}
                          style={{
                            padding: "10px 12px",
                            borderRadius: "var(--radius-sm)",
                            background: "var(--bg-surface)",
                            border: "1px solid var(--border)",
                          }}
                        >
                          <div className="flex items-center justify-between">
                            <div>
                              <span className="tag accent" style={{ marginRight: 6 }}>{e.entry_type}</span>
                              <span style={{ fontWeight: 500, fontSize: 13, color: "var(--text-primary)" }}>{e.title || "(无标题)"}</span>
                              {e.position_label && (
                                <span className="text-xs text-muted" style={{ marginLeft: 6 }}>{e.position_label}</span>
                              )}
                            </div>
                            <button className="btn-icon" style={{ fontSize: 14 }} onClick={() => delEntry(e.entry_id)}>
                              &times;
                            </button>
                          </div>
                          {e.content && (
                            <div className="text-sm mt-6" style={{ color: "var(--text-secondary)", lineHeight: 1.6 }}>
                              {e.content.length > 200 ? e.content.slice(0, 200) + "..." : e.content}
                            </div>
                          )}
                          {e.user_notes && (
                            <div className="text-xs mt-4" style={{ color: "var(--text-tertiary)", fontStyle: "italic" }}>
                              {e.user_notes}
                            </div>
                          )}
                        </div>
                      ))}
                      {!entries.length && (
                        <div className="text-xs text-muted text-center" style={{ padding: "16px 0" }}>
                          暂无条目 &mdash; 点击上方「添加」记录你的感受
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <div className="empty-state" style={{ paddingTop: 80 }}>
                <div className="empty-icon">&#x1F4DA;</div>
                <h4>选择一部作品查看详情</h4>
                <p>或点击上方添加新的参考作品</p>
              </div>
            )}
          </div>
          {/* LoRA Training Panel */}
          <LoRATrainingPanel works={works} />
        </div>
      </div>

      {/* ======== Add Work Modal ======== */}
      {showAddWork && (
        <>
          <div className="side-panel-overlay" onClick={() => setShowAddWork(false)} />
          <div style={{
            position: "fixed",
            inset: 0,
            zIndex: 100,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            pointerEvents: "none",
          }}>
            <div className="card" style={{
              width: 480,
              maxHeight: "80vh",
              overflow: "auto",
              pointerEvents: "auto",
              boxShadow: "var(--shadow-lg)",
              animation: "slideUp 0.2s var(--ease-out)",
            }}>
              <div className="card-header">
                <h3>添加参考作品</h3>
                <button className="side-panel-close" onClick={() => setShowAddWork(false)}>&times;</button>
              </div>
              <div className="card-body">
                <div className="flex flex-col gap-12">
                  <div className="field">
                    <label className="label">标题 *</label>
                    <input className="input" value={nTitle} onChange={e => setNTitle(e.target.value)} placeholder="作品名称" />
                  </div>
                  <div className="field">
                    <label className="label">创作者</label>
                    <input className="input" value={nCreator} onChange={e => setNCreator(e.target.value)} placeholder="作者 / 导演 / 制作组" />
                  </div>
                  <div className="field-row">
                    <div className="field" style={{ flex: 1 }}>
                      <label className="label">媒体类型</label>
                      <select className="select w-full" value={nMedia} onChange={e => setNMedia(e.target.value as MediaType)}>
                        {MEDIA_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                      </select>
                    </div>
                    <div className="field" style={{ flex: 1 }}>
                      <label className="label">题材</label>
                      <input className="input" value={nGenre} onChange={e => setNGenre(e.target.value)} placeholder="仙侠 / 悬疑 / 科幻..." />
                    </div>
                  </div>
                  <div className="field">
                    <label className="label">评分</label>
                    <div className="flex gap-4">
                      {[1, 2, 3, 4, 5].map(n => (
                        <button
                          key={n}
                          className="btn-ghost"
                          style={{
                            fontSize: 22,
                            padding: "2px 4px",
                            color: n <= nRating ? "var(--gold)" : "var(--text-disabled)",
                          }}
                          onClick={() => setNRating(nRating === n ? 0 : n)}
                        >
                          &#x2605;
                        </button>
                      ))}
                    </div>
                  </div>
                  <div className="field">
                    <label className="label">为什么喜欢？</label>
                    <textarea
                      className="input"
                      value={nWhy}
                      onChange={e => setNWhy(e.target.value)}
                      rows={3}
                      placeholder={"你喜欢这部作品的哪些方面？\n写作风格？世界观？角色塑造？情绪节奏？"}
                    />
                  </div>
                </div>
                <div className="flex gap-8 mt-24" style={{ justifyContent: "flex-end" }}>
                  <button className="btn" onClick={() => setShowAddWork(false)}>取消</button>
                  <button className="btn-primary" onClick={addWork} disabled={!nTitle.trim()}>保存</button>
                </div>
              </div>
            </div>
          </div>
        </>
      )}

      {/* ======== Upload Novel Text Modal ======== */}
      {showUpload && (
        <>
          <div className="side-panel-overlay" onClick={() => setShowUpload(false)} />
          <div style={{
            position: "fixed",
            inset: 0,
            zIndex: 100,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            pointerEvents: "none",
          }}>
            <div className="card" style={{
              width: 480,
              maxHeight: "80vh",
              overflow: "auto",
              pointerEvents: "auto",
              boxShadow: "var(--shadow-lg)",
              animation: "slideUp 0.2s var(--ease-out)",
            }}>
              <div className="card-header">
                <h3>上传正文文本</h3>
                <button className="side-panel-close" onClick={() => setShowUpload(false)}>&times;</button>
              </div>
              <div className="card-body">
                <div className="flex flex-col gap-12">
                  {!sel && (
                    <>
                      <div className="field">
                        <label className="label">标题 *</label>
                        <input className="input" value={uTitle} onChange={e => setUTitle(e.target.value)} placeholder="作品名称" />
                      </div>
                      <div className="field">
                        <label className="label">创作者</label>
                        <input className="input" value={uCreator} onChange={e => setUCreator(e.target.value)} placeholder="作者" />
                      </div>
                      <div className="field-row">
                        <div className="field" style={{ flex: 1 }}>
                          <label className="label">媒体类型</label>
                          <select className="select w-full" value={uMedia} onChange={e => setUMedia(e.target.value as MediaType)}>
                            {MEDIA_TYPES.map(t => <option key={t.value} value={t.value}>{t.label}</option>)}
                          </select>
                        </div>
                        <div className="field" style={{ flex: 1 }}>
                          <label className="label">题材</label>
                          <input className="input" value={uGenre} onChange={e => setUGenre(e.target.value)} placeholder="仙侠 / 悬疑 / 科幻..." />
                        </div>
                      </div>
                    </>
                  )}
                  <div className="field">
                    <label className="label">文本文件 *</label>
                    <input
                      ref={fileInputRef}
                      type="file"
                      accept=".txt"
                      className="input"
                      style={{ padding: "8px 10px" }}
                      onChange={e => setUFile(e.target.files?.[0] || null)}
                    />
                    <span className="text-xs text-muted" style={{ marginTop: 4 }}>支持 .txt 格式</span>
                  </div>
                </div>
                <div className="flex gap-8 mt-24" style={{ justifyContent: "flex-end" }}>
                  <button className="btn" onClick={() => setShowUpload(false)}>取消</button>
                  <button
                    className="btn-primary"
                    onClick={uploadNovelText}
                    disabled={(!sel && !uTitle.trim()) || !uFile || uploading}
                  >
                    {uploading ? "上传中..." : "上传"}
                  </button>
                </div>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

/* ---- Analysis Section (collapsible + editable) ---- */
function AnalysisSection({ title, data, fieldKey, onSave }: {
  title: string; data: any; fieldKey?: string; onSave?: (fieldKey: string, data: any) => void;
}) {
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState("");
  const [editError, setEditError] = useState("");
  if (!data) return null;

  const startEdit = () => {
    setEditText(JSON.stringify(data, null, 2));
    setEditError("");
    setEditing(true);
  };
  const cancelEdit = () => { setEditing(false); setEditError(""); };
  const saveEdit = () => {
    try {
      const parsed = JSON.parse(editText);
      if (onSave && fieldKey) onSave(fieldKey, parsed);
      setEditing(false);
      setEditError("");
    } catch (e: any) {
      setEditError("JSON 格式错误: " + (e.message || "解析失败"));
    }
  };

  return (
    <div style={{ borderRadius: "var(--radius-sm)", border: "1px solid var(--border)", overflow: "hidden" }}>
      <button
        className="btn-ghost w-full"
        style={{
          justifyContent: "space-between",
          padding: "8px 12px",
          background: "var(--bg-surface)",
          fontWeight: 500,
          borderRadius: 0,
        }}
        onClick={() => setOpen(!open)}
      >
        <span>{title}</span>
        <span
          className="text-xs text-muted"
          style={{ transition: "transform 0.15s", transform: open ? "rotate(180deg)" : "none", display: "inline-block" }}
        >
          &#x25BC;
        </span>
      </button>
      {open && (
        <div style={{ background: "var(--bg-card)" }}>
          {editing ? (
            <div style={{ padding: 8 }}>
              <textarea
                className="input font-mono"
                value={editText}
                onChange={e => setEditText(e.target.value)}
                style={{ width: "100%", minHeight: 200, maxHeight: 400, fontSize: 11, lineHeight: 1.5, resize: "vertical", boxSizing: "border-box" }}
              />
              {editError && <div style={{ fontSize: 11, color: "var(--error)", marginTop: 4 }}>{editError}</div>}
              <div style={{ display: "flex", gap: 6, marginTop: 6, justifyContent: "flex-end" }}>
                <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }} onClick={cancelEdit}>取消</button>
                <button className="btn-primary" style={{ fontSize: 11, padding: "3px 10px" }} onClick={saveEdit}>保存</button>
              </div>
            </div>
          ) : (
            <>
              <pre
                className="font-mono"
                style={{
                  margin: 0, padding: 12, fontSize: 11, lineHeight: 1.5,
                  color: "var(--text-secondary)", whiteSpace: "pre-wrap",
                  wordBreak: "break-all", maxHeight: 260, overflow: "auto",
                }}
              >
                {JSON.stringify(data, null, 2)}
              </pre>
              {fieldKey && onSave && (
                <div style={{ padding: "4px 12px 8px", textAlign: "right" }}>
                  <button className="btn-ghost" style={{ fontSize: 10, padding: "2px 8px", color: "var(--text-tertiary)" }} onClick={startEdit}>
                    编辑
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

/* ---- LoRA Training Panel ---- */
function LoRATrainingPanel({ works }: { works: ReferenceWork[] }) {
  const [open, setOpen] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [baseModel, setBaseModel] = useState("Qwen/Qwen2-1.5B");
  const [rank, setRank] = useState(16);
  const [alpha, setAlpha] = useState(32);
  const [epochs, setEpochs] = useState(3);
  const [lr, setLr] = useState(0.0002);
  const [status, setStatus] = useState<any>(null);
  const [training, setTraining] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const doneWorks = works.filter(w => w.preprocessing_status === "done");

  const toggleWork = (refId: string) => {
    setSelectedIds(prev => {
      const next = new Set(prev);
      if (next.has(refId)) next.delete(refId); else next.add(refId);
      return next;
    });
  };

  const startTraining = async () => {
    if (selectedIds.size === 0) return;
    setTraining(true);
    try {
      await apiPost("/api/references/lora/train", {
        work_ids: Array.from(selectedIds),
        base_model: baseModel, rank, alpha, epochs, learning_rate: lr,
      });
      // Start polling status
      pollRef.current = setInterval(async () => {
        try {
          const s = await apiGet<any>("/api/references/lora/status");
          setStatus(s);
          if (s.status === "done" || s.status === "error" || s.status === "idle") {
            if (pollRef.current) clearInterval(pollRef.current);
            setTraining(false);
          }
        } catch { /* ignore */ }
      }, 2000);
    } catch (e: any) {
      setStatus({ status: "error", error: e?.message || "启动失败" });
      setTraining(false);
    }
  };

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  return (
    <div style={{ borderTop: "1px solid var(--border)", flexShrink: 0 }}>
      <button
        className="btn-ghost w-full"
        style={{ padding: "10px 20px", justifyContent: "space-between", fontWeight: 600, fontSize: 13, borderRadius: 0 }}
        onClick={() => setOpen(!open)}
      >
        <span>LoRA 风格训练</span>
        <span className="text-xs text-muted" style={{ transition: "transform 0.15s", transform: open ? "rotate(180deg)" : "none", display: "inline-block" }}>&#x25BC;</span>
      </button>
      {open && (
        <div style={{ padding: "12px 20px" }}>
          <div className="label mb-8">选择参考作品（需已完成特征提取）</div>
          <div style={{ maxHeight: 200, overflowY: "auto", border: "1px solid var(--border)", borderRadius: 6, marginBottom: 12 }}>
            {doneWorks.length === 0 ? (
              <div className="text-xs text-muted" style={{ padding: 16, textAlign: "center" }}>没有已完成特征提取的作品</div>
            ) : doneWorks.map(w => (
              <label key={w.ref_id} style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 12px", cursor: "pointer", borderBottom: "1px solid var(--border)" }}>
                <input type="checkbox" checked={selectedIds.has(w.ref_id)} onChange={() => toggleWork(w.ref_id)} />
                <span style={{ fontSize: 12, flex: 1 }}>{w.title}</span>
                <span className="text-xs text-muted">{w.genre || w.media_type}</span>
              </label>
            ))}
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 8, marginBottom: 12 }}>
            <div className="field">
              <label className="label">基础模型</label>
              <select className="select w-full" value={baseModel} onChange={e => setBaseModel(e.target.value)}>
                <option value="Qwen/Qwen2-1.5B">Qwen2-1.5B</option>
                <option value="Qwen/Qwen2-7B">Qwen2-7B</option>
                <option value="meta-llama/Llama-3-8B">Llama-3-8B</option>
              </select>
            </div>
            <div className="field">
              <label className="label">Epochs</label>
              <input className="input" type="number" value={epochs} onChange={e => setEpochs(Number(e.target.value))} min={1} max={20} />
            </div>
            <div className="field">
              <label className="label">Rank</label>
              <input className="input" type="number" value={rank} onChange={e => setRank(Number(e.target.value))} min={4} max={128} />
            </div>
            <div className="field">
              <label className="label">Alpha</label>
              <input className="input" type="number" value={alpha} onChange={e => setAlpha(Number(e.target.value))} min={4} max={256} />
            </div>
          </div>

          <button
            className="btn-primary w-full"
            onClick={startTraining}
            disabled={selectedIds.size === 0 || training}
            style={{ marginBottom: 8 }}
          >
            {training ? "训练中..." : `开始训练 (${selectedIds.size} 部作品)`}
          </button>

          {status && (
            <div style={{
              padding: "8px 12px", borderRadius: 6, fontSize: 12,
              background: status.status === "error" ? "rgba(255,100,100,0.1)" : status.status === "done" ? "rgba(52,168,83,0.1)" : "var(--bg-surface-2)",
              color: status.status === "error" ? "var(--error)" : status.status === "done" ? "var(--jade)" : "var(--text-secondary)",
            }}>
              {status.status === "running" && <div>{status.progress || "训练进行中..."}</div>}
              {status.status === "done" && <div>训练完成！使用了 {status.samples_used} 个样本。</div>}
              {status.status === "error" && <div>训练失败: {status.error}</div>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
