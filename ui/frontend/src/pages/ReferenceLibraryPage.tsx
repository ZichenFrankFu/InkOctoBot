import React, { useEffect, useMemo, useState, useCallback, useRef } from "react";
import { apiGet, apiPost, apiPut, apiDelete } from "../api/client";
import { useResizable } from "../hooks/useResizable";
import useDebounce from "../hooks/useDebounce";
import { useToast } from "../components/shared/Toast";
import type { ReferenceWork, MediaType } from "../api/types";
import {
  Section,
  StyleFingerprintEditor,
  RhythmEditor,
  PlotOutlineEditor,
  StyleByChunkView,
} from "../components/reference/AnalysisEditors";
import {
  CharactersRichDisplay,
  SettingsRichDisplay,
} from "../components/reference/CharactersAndSettingsExtraction";
import { UnifiedExtractionPanel } from "../components/reference/UnifiedExtractionPanel";
import type { PlotOutline } from "../components/reference/AnalysisEditors";
import { useSegmentation } from "../components/reference/segmentationCache";
import type { ChunkLoc } from "../components/reference/referenceMerge";
import PreprocessPanel from "../components/reference/PreprocessPanel";
import FilesPanel from "../components/reference/FilesPanel";
import { splitGenres } from "../utils/genre";

const MEDIA_TYPES: { value: MediaType; label: string; color: string }[] = [
  { value: "web_novel", label: "网文", color: "var(--accent)" },
  { value: "literature", label: "文学", color: "var(--jade)" },
  { value: "poetry", label: "诗歌", color: "var(--purple)" },
  { value: "film", label: "电影", color: "var(--gold)" },
  { value: "anime", label: "动漫", color: "#f472b6" },
  { value: "tv_series", label: "电视剧", color: "var(--indigo)" },
  { value: "other", label: "其他", color: "var(--text-tertiary)" },
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

  // Batch management
  const [batchMode, setBatchMode] = useState(false);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  // Plot outline re-extraction

  const leftPanel = useResizable({ direction: "horizontal", initialSize: 220, minSize: 180, maxSize: 420 });


  /* ---- Data loading ---- */
  const debouncedSearch = useDebounce(search, 300);

  const load = useCallback(async () => {
    setLoading(true);
    const params = new URLSearchParams();
    if (debouncedSearch) params.set("search", debouncedSearch);
    if (filterMedia) params.set("media_type", filterMedia);
    try {
      const r = await apiGet<{ items: ReferenceWork[]; total: number }>(`/api/references/works?${params}`);
      setWorks(r.items || []);
      setTotal(r.total || 0);
    } catch (e) {
      console.error(e);
    }
    setLoading(false);
  }, [debouncedSearch, filterMedia]);

  useEffect(() => {
    load();
  }, [load]);

  // Pre-select a work from sessionStorage (set by ReferenceOverviewPage's
  // "open details" button). Cleared after first use.
  useEffect(() => {
    if (sel || works.length === 0) return;
    try {
      const target = sessionStorage.getItem("ref_open_ref_id");
      if (target) {
        const match = works.find(w => w.ref_id === target);
        if (match) {
          setSel(match);
          sessionStorage.removeItem("ref_open_ref_id");
        }
      }
    } catch { /* sessionStorage may be unavailable; ignore */ }
  }, [works, sel]);

  function selectWork(w: ReferenceWork) {
    setSel(w);
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
        toast(`已上传正文：${uFile.name}`, "success");
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
    if (!confirm("确定删除这部参考作品？")) return;
    try {
      await apiDelete(`/api/references/works/${id}`);
      setSel(null);
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
            <h2>参考作品详情</h2>
            <p>录入你喜欢的作品 · 上传正文 · 提取风格/角色/剧情大纲</p>
          </div>
          <div className="flex gap-8">
            <button className="btn-primary" onClick={() => setShowAddWork(true)}>+ 添加作品</button>
          </div>
        </div>
      </div>
      {/* Toolbar */}
      <div className="flex gap-8 items-center" style={{ padding: "10px 0 14px", flexWrap: "wrap" }}>
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
                style={{ padding: "10px 12px", gap: 8 }}
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
                <div style={{
                  width: 3,
                  alignSelf: "stretch",
                  background: mediaColor(w.media_type),
                  borderRadius: 2,
                  flexShrink: 0,
                }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="truncate" style={{ fontWeight: 600, fontSize: 13, color: "var(--text-primary)", marginBottom: 4 }}>
                    {w.title}
                  </div>
                  <div className="flex items-center gap-6 text-xs" style={{ flexWrap: "wrap", marginBottom: 2 }}>
                    {w.creator && <span className="text-muted truncate" style={{ maxWidth: 100 }}>{w.creator}</span>}
                    {w.user_rating ? <span style={{ color: "var(--gold)" }}>{stars(w.user_rating)}</span> : null}
                    <div style={{ flex: 1 }} />
                    {statusBadge(w.preprocessing_status)}
                  </div>
                  {splitGenres(w.genre).length > 0 && (
                    <div className="flex gap-4" style={{ flexWrap: "wrap" }}>
                      {splitGenres(w.genre).slice(0, 3).map(g => (
                        <span key={g} className="tag" style={{
                          fontSize: 10, padding: "1px 6px",
                          background: "var(--bg-surface)",
                          color: "var(--text-secondary)",
                          border: "1px solid var(--border)",
                        }}>{g}</span>
                      ))}
                    </div>
                  )}
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
          {/* No top padding here — the sticky work-header below provides
            * its own top spacing. A padding-top on the scroll container
            * is exactly what let content peek through above the sticky
            * bar; removing it fixes that seam. */}
          <div className="panel-body" style={{ padding: "0 20px 24px", background: "var(--bg-app)" }}>
            {sel ? (
              <WorkDetail
                key={sel.ref_id}
                sel={sel}
                onUpload={() => { setUTitle(sel.title); setUCreator(sel.creator || ""); setUMedia(sel.media_type); setUGenre(sel.genre || ""); setShowUpload(true); }}
                onDelete={() => delWork(sel.ref_id)}
                onUpdateRating={updateRating}
                onUpdateWhy={async (text) => {
                  try {
                    const updated = await apiPut<ReferenceWork>(`/api/references/works/${sel.ref_id}`, { user_why_i_like: text });
                    setSel(updated);
                    setWorks(prev => prev.map(x => x.ref_id === updated.ref_id ? updated : x));
                  } catch (e: any) { toast(e.message || "保存失败", "error"); }
                }}
                onSaveAnalysisField={saveAnalysisField}
                onAfterMerge={async () => {
                  try {
                    const w = await apiGet<ReferenceWork>(`/api/references/works/${sel.ref_id}`);
                    setSel(w);
                    setWorks(prev => prev.map(x => x.ref_id === w.ref_id ? w : x));
                  } catch {}
                }}
              />
                        ) : (
              <div className="empty-state" style={{ paddingTop: 80, maxWidth: 420, margin: "0 auto", textAlign: "center" }}>
                <h4>从左侧选择一部作品</h4>
                <p style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.7 }}>
                  在左侧列表中选择任意作品查看详情、编辑大纲、调整角色与设定。<br />
                  浏览全部参考作品的统计请前往「数据库概览」。
                </p>
                {works.length === 0 && (
                  <button className="btn-primary" style={{ marginTop: 16 }} onClick={() => setShowAddWork(true)}>
                    + 添加第一部作品
                  </button>
                )}
              </div>
            )}
          </div>
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

/* ───────────────── Work Detail (horizontal tabs) ───────────────── */

type WorkDetailTab = "files" | "preprocess" | "extract" | "plot" | "characters" | "settings" | "features";

const STATUS_LABEL: Record<string, { label: string; color: string }> = {
  done: { label: "已分析", color: "var(--jade)" },
  processing: { label: "处理中", color: "var(--gold)" },
  pending: { label: "待处理", color: "var(--accent)" },
  error: { label: "出错", color: "var(--error)" },
  not_applicable: { label: "手动维护", color: "var(--text-tertiary)" },
};

function WorkDetail({
  sel, onUpload, onDelete,
  onUpdateRating, onUpdateWhy, onSaveAnalysisField, onAfterMerge,
}: {
  sel: ReferenceWork;
  onUpload: () => void;
  onDelete: () => void;
  onUpdateRating: (rating: number) => void;
  onUpdateWhy: (text: string) => Promise<void> | void;
  onSaveAnalysisField: (fieldKey: string, data: any) => Promise<void> | void;
  onAfterMerge: () => Promise<void> | void;
}) {
  const [tab, setTab] = useState<WorkDetailTab>("files");
  const [whyDraft, setWhyDraft] = useState(sel.user_why_i_like || "");
  const [editingWhy, setEditingWhy] = useState(false);
  // 文本特征 tab: 分段视图 vs 全书视图.
  const [featuresView, setFeaturesView] = useState<"segment" | "book">("book");
  // Shared segmentation — powers the 分段视图 of the browse tabs. We
  // eagerly load every volume's chunk list so the browse tabs can group
  // items by extraction 分段 (chunk), matching the 特征提取 tab.
  const { plan: segmentPlan, chunks: segChunks, ensureChunks } =
    useSegmentation(sel.ref_id, Boolean(sel.has_full_text));
  useEffect(() => {
    if (!segmentPlan) return;
    for (const seg of segmentPlan.segments) ensureChunks(seg.index);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [segmentPlan]);
  const chunkList: ChunkLoc[] = useMemo(() => {
    if (!segmentPlan) return [];
    const out: ChunkLoc[] = [];
    let g = 1;
    for (const seg of segmentPlan.segments) {
      for (const ck of (segChunks[seg.index] || [])) {
        out.push({
          key: `${seg.index}:${ck.chunk_index}`,
          segIdx: seg.index, chunkIndex: ck.chunk_index,
          globalIndex: g++, volumeTitle: seg.title,
          startChapter: ck.start_chapter, endChapter: ck.end_chapter,
        });
      }
    }
    return out;
  }, [segmentPlan, segChunks]);

  useEffect(() => {
    setWhyDraft(sel.user_why_i_like || "");
    setEditingWhy(false);
  }, [sel.ref_id, sel.user_why_i_like]);

  const status = STATUS_LABEL[sel.preprocessing_status] || STATUS_LABEL.not_applicable;
  // Cache JSON.parse + .reduce work — the tab-bar re-renders on every
  // setTab() click and these parsed blobs can be megabytes (a full plot
  // outline with hundreds of events). Without memoization a single tab
  // switch did 3 JSON.parse + 2 nested reduce passes.
  const plot = useMemo(
    () => pj(sel.plot_outline_json) as PlotOutline | null,
    [sel.plot_outline_json],
  );
  const chars = useMemo(
    () => pj(sel.extracted_characters_json) as any[] | null,
    [sel.extracted_characters_json],
  );
  const settings = useMemo(
    () => pj(sel.settings_json) as any[] | null,
    [sel.settings_json],
  );
  const { epochCount, periodCount, eventCount } = useMemo(() => {
    const epochs = plot?.epochs || [];
    let periods = 0;
    let events = 0;
    for (const ep of epochs) {
      const eps = ep.periods || [];
      periods += eps.length;
      for (const per of eps) events += (per.events?.length || 0);
    }
    return { epochCount: epochs.length, periodCount: periods, eventCount: events };
  }, [plot]);
  const charCount = (chars || []).length;
  const settingsCount = (settings || []).length;

  const TABS: { key: WorkDetailTab; label: string; count?: number | string }[] = [
    { key: "files", label: "原始文件" },
    { key: "preprocess", label: "预处理" },
    { key: "extract", label: "特征提取" },
    { key: "plot", label: "剧情大纲", count: eventCount || undefined },
    { key: "characters", label: "角色", count: charCount || undefined },
    { key: "settings", label: "设定", count: settingsCount || undefined },
    { key: "features", label: "文本特征" },
  ];

  return (
    <div>
      {/* Sticky work header. The scroll container (panel-body) has no
       * top padding, so the bar sits flush at the top and sticks at
       * top:0 — no negative top margin, no sub-pixel seam above it. */}
      <div style={{
        position: "sticky",
        top: 0,
        zIndex: 10,
        background: "var(--bg-app)",
        margin: "0 -20px 12px",
        padding: "16px 20px 10px",
        boxSizing: "border-box",
        borderBottom: "1px solid var(--border)",
        boxShadow: "0 6px 6px -6px rgba(0,0,0,0.18)",
      }}>
        <div className="flex items-center" style={{ gap: 10, flexWrap: "wrap" }}>
          <h3 className="font-serif" style={{ fontSize: 18, fontWeight: 700, color: "var(--text-primary)", margin: 0 }}>
            {sel.title}
          </h3>
          <span className="tag" style={{
            color: mediaColor(sel.media_type),
            background: "var(--bg-surface-2)",
            fontSize: 11, padding: "1px 8px",
          }}>{mediaLabel(sel.media_type)}</span>
          <span className="tag" style={{
            fontSize: 11, padding: "1px 8px",
            color: status.color,
            background: "var(--bg-surface-2)",
            border: `1px solid ${status.color}`,
          }}>{status.label}</span>
          <div style={{ flex: 1 }} />
          <div className="flex gap-6" style={{ flexShrink: 0 }}>
            {!sel.has_full_text && (
              <button className="btn" onClick={onUpload}>上传正文</button>
            )}
            <button className="btn" style={{ color: "var(--error)" }} onClick={onDelete}>删除</button>
          </div>
        </div>
        <div className="flex gap-12 text-xs text-muted" style={{ marginTop: 6, flexWrap: "wrap" }}>
          {sel.creator && <span>{sel.creator}</span>}
          {sel.genre && <span>· {sel.genre}</span>}
          {sel.updated_at && <span>· 更新 {sel.updated_at.split("T")[0]}</span>}
          {sel.user_rating ? <span style={{ color: "var(--gold)" }}>· {stars(sel.user_rating)}</span> : null}
        </div>

        {/* Horizontal tab bar */}
        <div className="flex" style={{ marginTop: 12, gap: 4, borderBottom: "1px solid var(--border)" }}>
          {TABS.map(t => (
            <button
              key={t.key}
              className="btn-ghost"
              onClick={() => setTab(t.key)}
              style={{
                padding: "8px 16px",
                fontSize: 13,
                fontWeight: tab === t.key ? 600 : 400,
                color: tab === t.key ? "var(--accent)" : "var(--text-secondary)",
                borderBottom: tab === t.key ? "2px solid var(--accent)" : "2px solid transparent",
                marginBottom: -1,
                borderRadius: 0,
              }}
            >
              {t.label}
              {t.count != null && (
                <span style={{
                  marginLeft: 6,
                  fontSize: 11,
                  fontWeight: 600,
                  color: tab === t.key ? "var(--accent)" : "var(--text-tertiary)",
                  background: tab === t.key ? "var(--accent-subtle)" : "var(--bg-surface-2)",
                  padding: "1px 6px",
                  borderRadius: 10,
                }}>{t.count}</span>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content */}
      {tab === "files" && (
        <div className="flex flex-col gap-12">
          <FilesPanel
            refId={sel.ref_id}
            onAfterChange={onAfterMerge}
          />

          {/* 信息与笔记 — merged into the 原始文件 tab so作品元数据 /
            * 评分 / 笔记 live next to the source files they describe. */}
          {/* Stats */}
          <div className="card">
            <div className="card-body">
              <div className="label" style={{ color: "var(--accent)", marginBottom: 10 }}>作品摘要</div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 12 }}>
                <Stat label="处理状态" value={status.label} accent={status.color} />
                <Stat label="正文" value={sel.has_full_text ? "已上传" : "未上传"} />
                <Stat label="时间段" value={periodCount} />
                <Stat label="事件" value={eventCount} />
                <Stat label="角色" value={charCount} />
                <Stat label="设定" value={settingsCount} />
                <Stat label="评分" value={sel.user_rating ? stars(sel.user_rating) : "—"} />
              </div>
            </div>
          </div>

          {/* Rating */}
          <div className="card">
            <div className="card-body">
              <div className="label" style={{ color: "var(--accent)", marginBottom: 6 }}>我的评分</div>
              <StarRating value={sel.user_rating || 0} onChange={onUpdateRating} />
            </div>
          </div>

          {/* Why I like it */}
          <div className="card">
            <div className="card-body">
              <div className="flex items-center justify-between" style={{ marginBottom: 6 }}>
                <div className="label" style={{ color: "var(--accent)", margin: 0 }}>为什么喜欢</div>
                {!editingWhy && (
                  <button className="btn-ghost" style={{ fontSize: 11, color: "var(--text-tertiary)" }} onClick={() => setEditingWhy(true)}>编辑</button>
                )}
              </div>
              {editingWhy ? (
                <>
                  <textarea
                    className="input"
                    rows={4}
                    value={whyDraft}
                    onChange={e => setWhyDraft(e.target.value)}
                    placeholder="写作风格？世界观？角色塑造？情绪节奏？"
                  />
                  <div className="flex gap-6 mt-8" style={{ justifyContent: "flex-end" }}>
                    <button className="btn" onClick={() => { setEditingWhy(false); setWhyDraft(sel.user_why_i_like || ""); }}>取消</button>
                    <button className="btn-primary" onClick={async () => { await onUpdateWhy(whyDraft); setEditingWhy(false); }}>保存</button>
                  </div>
                </>
              ) : sel.user_why_i_like ? (
                <div className="text-sm" style={{ color: "var(--text-secondary)", lineHeight: 1.7, whiteSpace: "pre-wrap" }}>
                  {sel.user_why_i_like}
                </div>
              ) : (
                <div className="text-xs text-muted" style={{ fontStyle: "italic" }}>
                  暂无笔记。点击「编辑」记录你为什么喜欢这部作品。
                </div>
              )}
            </div>
          </div>

          {/* Metadata */}
          <div className="card">
            <div className="card-body">
              <div className="label" style={{ color: "var(--accent)", marginBottom: 10 }}>元数据</div>
              <dl style={{ display: "grid", gridTemplateColumns: "120px 1fr", gap: "6px 14px", fontSize: 12, margin: 0 }}>
                <dt className="text-muted">作品 ID</dt>
                <dd style={{ fontFamily: "var(--font-mono)", color: "var(--text-secondary)", margin: 0 }}>{sel.ref_id}</dd>
                <dt className="text-muted">作者</dt><dd style={{ margin: 0 }}>{sel.creator || "—"}</dd>
                <dt className="text-muted">题材</dt><dd style={{ margin: 0 }}>{sel.genre || "—"}</dd>
                <dt className="text-muted">来源</dt><dd style={{ margin: 0 }}>{sel.source || "—"}</dd>
                <dt className="text-muted">创建</dt><dd style={{ margin: 0 }}>{sel.created_at?.split("T")[0] || "—"}</dd>
                <dt className="text-muted">更新</dt><dd style={{ margin: 0 }}>{sel.updated_at?.split("T")[0] || "—"}</dd>
              </dl>
            </div>
          </div>
        </div>
      )}

      {tab === "preprocess" && (
        <PreprocessPanel
          refId={sel.ref_id}
          hasFullText={Boolean(sel.has_full_text)}
          onUpload={() => setTab("files")}
          onAfterApplyExclusions={onAfterMerge}
        />
      )}

      {tab === "extract" && (
        <UnifiedExtractionPanel
          refId={sel.ref_id}
          hasFullText={Boolean(sel.has_full_text)}
          plot={plot}
          characters={(chars || []) as any}
          settings={(settings || []) as any}
          style={pj(sel.style_fingerprint_json) as any}
          onSavePlot={d => onSaveAnalysisField("plot_outline_json", d)}
          onSaveCharacters={d => onSaveAnalysisField("extracted_characters_json", d)}
          onSaveSettings={d => onSaveAnalysisField("settings_json", d)}
          onSaveStyle={d => onSaveAnalysisField("style_fingerprint_json", d)}
          onSaveRhythm={d => onSaveAnalysisField("rhythm_json", d)}
        />
      )}

      {/* The 剧情大纲 / 角色 / 设定 / 文本特征 tabs are整体浏览 — display
        * and CRUD only. Extraction happens once in the「特征提取」tab. */}
      {tab === "plot" && (
        <PlotOutlineEditor
          data={plot}
          onSave={d => onSaveAnalysisField("plot_outline_json", d)}
          refId={sel.ref_id}
          chunkList={chunkList}
        />
      )}

      {tab === "characters" && (
        <div className="flex flex-col gap-12">
          <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)" }}>
            角色列表
          </div>
          <CharactersRichDisplay
            data={(chars || []) as any}
            chronicle={plot}
            onSave={d => onSaveAnalysisField("extracted_characters_json", d)}
            chunkList={chunkList}
          />
        </div>
      )}

      {tab === "settings" && (
        <div className="flex flex-col gap-12">
          <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-secondary)" }}>
            设定列表
          </div>
          <SettingsRichDisplay
            data={(settings || []) as any}
            onSave={d => onSaveAnalysisField("settings_json", d)}
            chunkList={chunkList}
          />
        </div>
      )}

      {tab === "features" && (
        <div className="flex flex-col gap-12">
          {/* 分段视图 / 全书视图 toggle — shown once chunks exist. */}
          {chunkList.length > 0 && (
            <div className="flex items-center" style={{ gap: 6 }}>
              <span className="text-xs text-muted">视图：</span>
              {([
                { key: "segment" as const, label: "分段视图" },
                { key: "book" as const, label: "全书视图" },
              ]).map(opt => (
                <button key={opt.key} className="btn-ghost"
                        onClick={() => setFeaturesView(opt.key)}
                        style={{
                          padding: "3px 10px", fontSize: 11,
                          fontWeight: featuresView === opt.key ? 600 : 400,
                          color: featuresView === opt.key ? "var(--accent)" : "var(--text-secondary)",
                          background: featuresView === opt.key ? "var(--accent-subtle)" : "transparent",
                          border: "1px solid var(--border)", borderRadius: 3,
                        }}>
                  {opt.label}
                </button>
              ))}
            </div>
          )}

          {(chunkList.length === 0 || featuresView === "book") ? (
            <>
              <Section title="风格指纹（全书）" subtitle="句长 / 对话 / 描写 / 修辞 / 节奏 / 信息密度 / 爽点 / 钩子"
                empty={!pj(sel.style_fingerprint_json)}
                emptyHint="暂无风格指纹。请到「特征提取」tab 分段提取。"
                defaultOpen
              >
                <StyleFingerprintEditor
                  data={pj(sel.style_fingerprint_json)}
                  onSave={d => onSaveAnalysisField("style_fingerprint_json", d)}
                />
              </Section>
              <Section title="节奏（全书）" subtitle="每章特征 · 信息密度 · 钩子 · 节奏分段（含叙事结构）"
                defaultOpen
              >
                <RhythmEditor
                  data={pj(sel.rhythm_json) as any}
                  legacyNarrative={pj(sel.narrative_structure_json)}
                  legacyRhythm={pj(sel.rhythm_template_json)}
                  onSave={d => onSaveAnalysisField("rhythm_json", d)}
                />
              </Section>
            </>
          ) : (
            <Section title="分段风格指纹" subtitle="每个提取分段各自的风格 / 节奏特征"
              defaultOpen
            >
              <StyleByChunkView
                chunks={((pj(sel.style_fingerprint_json) as any)?._chunks) || {}}
                chunkList={chunkList}
              />
            </Section>
          )}
        </div>
      )}
    </div>
  );
}

function Stat({ label, value, accent }: { label: string; value: React.ReactNode; accent?: string }) {
  return (
    <div>
      <div className="text-xs text-muted" style={{ marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 16, fontWeight: 600, color: accent || "var(--text-primary)" }}>{value}</div>
    </div>
  );
}
