import React, { useEffect, useState, useCallback, useRef } from "react";
import { apiGet, apiPost, apiPut, apiDelete } from "../api/client";
import { useResizable } from "../hooks/useResizable";
import useDebounce from "../hooks/useDebounce";
import { useToast } from "../components/shared/Toast";
import type { ReferenceWork, MediaType } from "../api/types";
import {
  Section,
  StyleFingerprintEditor,
  NarrativeStructureEditor,
  CharactersEditor,
  RhythmTemplateEditor,
} from "../components/reference/AnalysisEditors";
import type { PlotOutline } from "../components/reference/AnalysisEditors";
import PlotOutlinePanel from "../components/reference/PlotOutlinePanel";

/* ── Extraction types ── */
interface ExtractionProgress {
  total_novels: number;
  phases: Record<string, Record<string, number>>;
  patterns: Record<string, number>;
  skills_emitted: number;
}
interface ExtPattern {
  pattern_id: string; category: string; subcategory: string;
  pattern_name: string; description: string; frequency: number;
  quality_score: number; skill_emitted: boolean; examples: any[];
}
interface PipelineStatus {
  status: string; progress?: string; error?: string; result?: any;
}
const CATEGORY_LABELS: Record<string, string> = {
  writing_technique: "写作手法", chapter_design: "章节设计", story_arc: "剧情设计",
};
const PHASE_LABELS: Record<string, string> = {
  clean: "数据清洗", chapter_extract: "章节提取", novel_aggregate: "全书聚合", pattern_mine: "模式挖掘",
};
function fmtChars(n: number | null | undefined): string {
  if (!n) return "0";
  if (n >= 10000) return (n / 10000).toFixed(1) + "万";
  return n.toLocaleString();
}

type PageTab = "works" | "extraction" | "patterns";

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
  const [pageTab, setPageTab] = useState<PageTab>("works");
  const [works, setWorks] = useState<ReferenceWork[]>([]);
  const [total, setTotal] = useState(0);
  const [search, setSearch] = useState("");
  const [filterMedia, setFilterMedia] = useState("");
  const [sel, setSel] = useState<ReferenceWork | null>(null);
  const [loading, setLoading] = useState(false);

  // Extraction pipeline state
  const [extProgress, setExtProgress] = useState<ExtractionProgress | null>(null);
  const [pipeStatus, setPipeStatus] = useState<PipelineStatus>({ status: "idle" });
  const [extPatterns, setExtPatterns] = useState<ExtPattern[]>([]);
  const [extPatternsTotal, setExtPatternsTotal] = useState(0);
  const [extPatternCat, setExtPatternCat] = useState("");
  const [ingesting, setIngesting] = useState(false);
  const corpusFileRef = useRef<HTMLInputElement>(null);

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
  const [extractingPlot, setExtractingPlot] = useState(false);

  const leftPanel = useResizable({ direction: "horizontal", initialSize: 360, minSize: 260, maxSize: 560 });

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

  /* ---- Extraction data loading ---- */
  const loadExtProgress = useCallback(async () => {
    try {
      const [prog, st] = await Promise.all([
        apiGet<ExtractionProgress>("/api/extraction/progress"),
        apiGet<PipelineStatus>("/api/extraction/run/status"),
      ]);
      setExtProgress(prog); setPipeStatus(st);
    } catch { /* ignore */ }
  }, []);

  const loadExtPatterns = useCallback(async () => {
    try {
      const url = `/api/extraction/patterns?limit=100${extPatternCat ? `&category=${extPatternCat}` : ""}`;
      const r = await apiGet<{ items: ExtPattern[]; total: number }>(url);
      setExtPatterns(r.items || []); setExtPatternsTotal(r.total || 0);
    } catch { /* ignore */ }
  }, [extPatternCat]);

  useEffect(() => {
    if (pageTab === "extraction") loadExtProgress();
    if (pageTab === "patterns") loadExtPatterns();
  }, [pageTab, loadExtProgress, loadExtPatterns]);

  useEffect(() => {
    if (pipeStatus.status !== "running") return;
    const id = setInterval(loadExtProgress, 3000);
    return () => clearInterval(id);
  }, [pipeStatus.status, loadExtProgress]);

  const handleIngestCorpus = async () => {
    setIngesting(true);
    try {
      const r = await apiPost<{ ingested: number }>("/api/extraction/ingest", {}, { timeoutMs: 300_000 });
      toast(`已导入 ${r.ingested} 本小说`, "success");
      load();
    } catch (e: any) { toast(e.message, "error"); }
    finally { setIngesting(false); }
  };

  const handleCorpusUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    try {
      const fd = new FormData(); fd.append("file", file);
      const res = await fetch("/api/extraction/ingest/upload", { method: "POST", body: fd });
      if (!res.ok) { const err = await res.json().catch(() => ({ detail: "上传失败" })); throw new Error(err.detail || "上传失败"); }
      const r = await res.json();
      toast(`已导入《${r.title}》(${r.chapters}章)`, "success");
      load();
    } catch (err: any) { toast(err.message, "error"); }
    finally { e.target.value = ""; }
  };

  const handleRunPipeline = async (phases?: string[]) => {
    try {
      await apiPost("/api/extraction/run", { phases, resume: true });
      toast("提取流程已启动", "success");
      setPipeStatus({ status: "running", progress: "初始化..." });
    } catch (e: any) { toast(e.message, "error"); }
  };

  const handleEmitSkills = async () => {
    try {
      const r = await apiPost<{ skills_emitted: any }>("/api/extraction/emit", {});
      toast(`Skill生成完成: ${JSON.stringify(r.skills_emitted)}`, "success");
      loadExtProgress();
    } catch (e: any) { toast(e.message, "error"); }
  };

  function selectWork(w: ReferenceWork) {
    setSel(w);
  }

  async function extractPlotOutline(refId: string) {
    setExtractingPlot(true);
    try {
      const updated = await apiPost<ReferenceWork>(`/api/references/works/${refId}/plot_outline/extract`, {}, { timeoutMs: 300_000 });
      setSel(updated);
      setWorks(prev => prev.map(w => w.ref_id === updated.ref_id ? updated : w));
      toast("剧情大纲已重新提取", "success");
    } catch (e: any) {
      toast(e?.message || "操作失败", "error");
    } finally {
      setExtractingPlot(false);
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
            <p>录入你喜欢的作品 &middot; 批量导入小说语料 &middot; 自动提取写作技巧为Skill</p>
          </div>
          <div className="flex gap-8">
            <label className="btn" style={{ cursor: "pointer" }}>
              上传小说
              <input ref={corpusFileRef} type="file" accept=".txt" onChange={handleCorpusUpload} hidden />
            </label>
            <button className="btn" onClick={handleIngestCorpus} disabled={ingesting}>
              {ingesting ? "导入中..." : "批量导入语料"}
            </button>
            <button className="btn-primary" onClick={() => setShowAddWork(true)}>+ 添加作品</button>
          </div>
        </div>
      </div>

      {/* Page-level tabs */}
      <div className="flex gap-8 items-center" style={{ padding: "0 0 8px", borderBottom: "1px solid var(--border)" }}>
        {([["works", "作品库"], ["extraction", "提取进度"], ["patterns", "模式库"]] as [PageTab, string][]).map(([k, l]) => (
          <button key={k} className="btn-ghost" style={{
            padding: "6px 16px", fontWeight: pageTab === k ? 600 : 400,
            color: pageTab === k ? "var(--accent)" : "var(--text-secondary)",
            borderBottom: pageTab === k ? "2px solid var(--accent)" : "2px solid transparent",
          }} onClick={() => setPageTab(k)}>{l}</button>
        ))}
      </div>

      {/* ════════ Works Tab ════════ */}
      {pageTab === "works" && <>
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
                      {sel.creator && <span>{sel.creator}</span>}
                      {sel.genre && <span>{sel.genre}</span>}
                      <span>{sel.created_at?.split("T")[0] || "\u2014"}</span>
                      {Boolean(sel.has_full_text) && (
                        <span style={{ color: "var(--jade)", fontWeight: 500 }}>已上传正文</span>
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

                {/* Analysis results — structured editors */}
                <div className="card mb-16">
                  <div className="card-body">
                    <div className="label" style={{ color: "var(--accent)", marginBottom: 10 }}>特征提取结果</div>
                    <div className="flex flex-col gap-8">
                      {/* 1. 剧情大纲 (with integrated segment workflow) */}
                      <Section title="剧情大纲" subtitle="编年史格式 · 时间段 + 事件" defaultOpen>
                        <PlotOutlinePanel
                          refId={sel.ref_id}
                          hasFullText={Boolean(sel.has_full_text)}
                          preprocessingStatus={sel.preprocessing_status}
                          plotOutline={pj(sel.plot_outline_json) as PlotOutline | null}
                          onSavePlot={d => saveAnalysisField("plot_outline_json", d)}
                          onAfterMerge={async () => {
                            try {
                              const w = await apiGet<ReferenceWork>(`/api/references/works/${sel.ref_id}`);
                              setSel(w);
                              setWorks(prev => prev.map(x => x.ref_id === w.ref_id ? w : x));
                            } catch {}
                          }}
                          onRegenerateFromText={Boolean(sel.has_full_text) ? () => extractPlotOutline(sel.ref_id) : undefined}
                          regenerating={extractingPlot}
                        />
                      </Section>

                      {/* 2. 提取角色 */}
                      <Section title="提取角色" subtitle="姓名 / 对白 / 关系"
                        empty={!pj(sel.extracted_characters_json)}
                        emptyHint="暂无角色数据。请先提取特征。"
                        defaultOpen
                      >
                        <CharactersEditor
                          data={pj(sel.extracted_characters_json)}
                          onSave={d => saveAnalysisField("extracted_characters_json", d)}
                        />
                      </Section>

                      {/* 3. 风格指纹 — collapsed by default */}
                      <Section title="风格指纹" subtitle="句长 / 对话 / 描写 / 修辞 / 节奏"
                        empty={!pj(sel.style_fingerprint_json)}
                        emptyHint="暂无风格指纹。请先提取特征。"
                        defaultOpen={false}
                      >
                        <StyleFingerprintEditor
                          data={pj(sel.style_fingerprint_json)}
                          onSave={d => saveAnalysisField("style_fingerprint_json", d)}
                        />
                      </Section>

                      {/* 4. 叙事结构 — collapsed by default */}
                      <Section title="叙事结构" subtitle="开篇 / 高潮 / 钩子 / 爽点"
                        empty={!pj(sel.narrative_structure_json)}
                        emptyHint="暂无叙事结构。请先提取特征。"
                        defaultOpen={false}
                      >
                        <NarrativeStructureEditor
                          data={pj(sel.narrative_structure_json)}
                          onSave={d => saveAnalysisField("narrative_structure_json", d)}
                        />
                      </Section>

                      {/* 5. 节奏模板 — collapsed by default */}
                      <Section title="节奏模板" subtitle="张力曲线 / 分段"
                        empty={!pj(sel.rhythm_template_json)}
                        emptyHint="暂无节奏数据。请先提取特征。"
                        defaultOpen={false}
                      >
                        <RhythmTemplateEditor
                          data={pj(sel.rhythm_template_json)}
                          onSave={d => saveAnalysisField("rhythm_template_json", d)}
                        />
                      </Section>
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <div className="empty-state" style={{ paddingTop: 80 }}>
                <h4>选择一部作品查看详情</h4>
                <p>或点击上方添加新的参考作品</p>
              </div>
            )}
          </div>
          {/* LoRA Training Panel */}
          <LoRATrainingPanel works={works} />
        </div>
      </div>

      </>}

      {/* ════════ Extraction Tab ════════ */}
      {pageTab === "extraction" && (
        <div style={{ padding: "16px 0" }}>
          {/* Pipeline controls */}
          <div style={{ display: "flex", gap: 8, marginBottom: 16, padding: 12, background: "var(--bg-surface)", borderRadius: 6, border: "1px solid var(--border)", alignItems: "center", flexWrap: "wrap" }}>
            <span style={{ fontSize: 12, color: "var(--text-secondary)", marginRight: 8 }}>
              流程状态:
              <span style={{ marginLeft: 6, fontWeight: 600, color: pipeStatus.status === "running" ? "var(--gold)" : pipeStatus.status === "done" ? "var(--jade)" : pipeStatus.status === "error" ? "var(--error)" : "var(--text-tertiary)" }}>
                {pipeStatus.status === "idle" ? "空闲" : pipeStatus.progress || pipeStatus.status}
              </span>
            </span>
            <div style={{ flex: 1 }} />
            {(["chapter", "novel", "pattern"] as const).map(p => (
              <button key={p} className="btn" onClick={() => handleRunPipeline([p])} disabled={pipeStatus.status === "running"} style={{ fontSize: 11 }}>
                {{chapter: "章节提取", novel: "全书聚合", pattern: "模式挖掘"}[p]}
              </button>
            ))}
            <button className="btn-primary" onClick={() => handleRunPipeline()} disabled={pipeStatus.status === "running"} style={{ fontSize: 11 }}>全部运行</button>
            <button className="btn" onClick={handleEmitSkills} disabled={pipeStatus.status === "running"} style={{ fontSize: 11 }}>生成Skill</button>
          </div>

          {pipeStatus.error && (
            <div style={{ padding: "8px 12px", marginBottom: 12, background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)", borderRadius: 6, color: "var(--error)", fontSize: 12 }}>
              {pipeStatus.error}
            </div>
          )}

          {extProgress && (
            <div>
              <div style={{ marginBottom: 12, fontSize: 13, color: "var(--text-secondary)" }}>已注册小说: <strong>{extProgress.total_novels}</strong></div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 12, marginBottom: 20 }}>
                {Object.entries(PHASE_LABELS).map(([phase, label]) => {
                  const ps = extProgress.phases[phase] || {};
                  const completed = ps["completed"] || 0, failed = ps["failed"] || 0, pending = ps["pending"] || 0, inProg = ps["in_progress"] || 0;
                  const tot = completed + failed + pending + inProg;
                  return (
                    <div key={phase} style={{ padding: 16, background: "var(--bg-surface)", borderRadius: 8, border: "1px solid var(--border)" }}>
                      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>{label}</div>
                      {tot === 0 ? <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>尚未开始</div> : <>
                        <div style={{ height: 6, background: "var(--bg-surface-2)", borderRadius: 3, overflow: "hidden", marginBottom: 6 }}>
                          <div style={{ height: "100%", width: `${(completed / tot) * 100}%`, background: "var(--jade)", borderRadius: 3, transition: "width 0.3s" }} />
                        </div>
                        <div style={{ display: "flex", gap: 12, fontSize: 11, color: "var(--text-secondary)" }}>
                          <span style={{ color: "var(--jade)" }}>{completed} 完成</span>
                          {inProg > 0 && <span style={{ color: "var(--gold)" }}>{inProg} 进行中</span>}
                          {failed > 0 && <span style={{ color: "var(--error)" }}>{failed} 失败</span>}
                          {pending > 0 && <span>{pending} 待处理</span>}
                        </div>
                      </>}
                    </div>
                  );
                })}
              </div>
              <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
                <div style={{ padding: 16, background: "var(--bg-surface)", borderRadius: 8, border: "1px solid var(--border)", minWidth: 200 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>已挖掘模式</div>
                  {Object.keys(extProgress.patterns).length === 0
                    ? <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>暂无</div>
                    : Object.entries(extProgress.patterns).map(([cat, cnt]) => (
                      <div key={cat} style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 2 }}>{CATEGORY_LABELS[cat] || cat}: <strong>{cnt}</strong></div>
                    ))
                  }
                </div>
                <div style={{ padding: 16, background: "var(--bg-surface)", borderRadius: 8, border: "1px solid var(--border)", minWidth: 200 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>已生成Skill</div>
                  <div style={{ fontSize: 24, fontWeight: 700, color: "var(--accent)" }}>{extProgress.skills_emitted}</div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ════════ Patterns Tab ════════ */}
      {pageTab === "patterns" && (
        <div style={{ padding: "16px 0" }}>
          <div className="flex gap-8 items-center" style={{ marginBottom: 12 }}>
            <select className="select" value={extPatternCat} onChange={e => setExtPatternCat(e.target.value)}>
              <option value="">全部类别</option>
              {Object.entries(CATEGORY_LABELS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            <span className="text-xs text-muted">共 {extPatternsTotal} 个模式</span>
          </div>
          {extPatterns.length === 0 ? (
            <div className="empty-state" style={{ padding: 40 }}>暂无模式。请先在"提取进度"中运行模式挖掘。</div>
          ) : (
            <div className="flex flex-col gap-8">
              {extPatterns.map(p => (
                <div key={p.pattern_id} style={{ padding: 12, background: "var(--bg-surface)", borderRadius: 6, border: "1px solid var(--border)" }}>
                  <div className="flex justify-between items-center" style={{ marginBottom: 4 }}>
                    <div className="flex gap-8 items-center">
                      <span style={{ fontWeight: 600, fontSize: 13 }}>{p.pattern_name}</span>
                      <span className="tag" style={{ fontSize: 10 }}>{CATEGORY_LABELS[p.category] || p.category}</span>
                      {p.subcategory && <span className="text-xs text-muted">/ {p.subcategory}</span>}
                    </div>
                    <div className="flex gap-12 text-xs text-muted">
                      <span>出现: {p.frequency}本</span>
                      <span>质量: {(p.quality_score || 0).toFixed(1)}</span>
                      {p.skill_emitted && <span style={{ color: "var(--jade)" }}>已生成Skill</span>}
                    </div>
                  </div>
                  {p.description && (
                    <div className="text-sm" style={{ color: "var(--text-secondary)", lineHeight: 1.5 }}>
                      {p.description.length > 200 ? p.description.slice(0, 200) + "..." : p.description}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

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
