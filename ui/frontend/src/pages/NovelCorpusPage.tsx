import React, { useEffect, useState, useCallback } from "react";
import { apiGet, apiPost } from "../api/client";
import { useToast } from "../components/shared/Toast";

/* ── Types ─────────────────────────────────────────────── */

interface NovelMeta {
  ref_id: string;
  title: string;
  author: string;
  synopsis: string;
  status: string;
  total_chapters: number;
  total_chars: number;
  excluded_author_notes: number;
  created_at: string;
}

interface ChapterEntry {
  chapter_num: number;
  original_title: string;
  char_count: number;
  is_author_note: boolean;
}

interface PhaseStatus {
  [status: string]: number;
}

interface ExtractionProgress {
  total_novels: number;
  phases: Record<string, PhaseStatus>;
  patterns: Record<string, number>;
  skills_emitted: number;
}

interface Pattern {
  pattern_id: string;
  category: string;
  subcategory: string;
  pattern_name: string;
  description: string;
  frequency: number;
  quality_score: number;
  skill_emitted: boolean;
  examples: any[];
}

interface PipelineStatus {
  status: string;
  progress?: string;
  error?: string;
  result?: any;
}

/* ── Helpers ───────────────────────────────────────────── */

function fmtChars(n: number | null | undefined): string {
  if (!n) return "0";
  if (n >= 10000) return (n / 10000).toFixed(1) + "万";
  return n.toLocaleString();
}

const CATEGORY_LABELS: Record<string, string> = {
  writing_technique: "写作手法",
  chapter_design: "章节设计",
  story_arc: "剧情设计",
};

const PHASE_LABELS: Record<string, string> = {
  clean: "数据清洗",
  chapter_extract: "章节提取",
  novel_aggregate: "全书聚合",
  pattern_mine: "模式挖掘",
};

type ViewTab = "novels" | "progress" | "patterns";

/* ── Component ─────────────────────────────────────────── */

export default function NovelCorpusPage() {
  const { toast } = useToast();
  const [tab, setTab] = useState<ViewTab>("novels");

  // Novels
  const [novels, setNovels] = useState<NovelMeta[]>([]);
  const [novelsTotal, setNovelsTotal] = useState(0);
  const [novelSearch, setNovelSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [selectedNovel, setSelectedNovel] = useState<string | null>(null);
  const [chapters, setChapters] = useState<ChapterEntry[]>([]);
  const [novelDetail, setNovelDetail] = useState<NovelMeta | null>(null);

  // Progress
  const [progress, setProgress] = useState<ExtractionProgress | null>(null);
  const [pipelineStatus, setPipelineStatus] = useState<PipelineStatus>({ status: "idle" });

  // Patterns
  const [patterns, setPatterns] = useState<Pattern[]>([]);
  const [patternsTotal, setPatternsTotal] = useState(0);
  const [patternCategory, setPatternCategory] = useState("");

  // Ingest
  const [ingesting, setIngesting] = useState(false);
  const [uploading, setUploading] = useState(false);

  /* ── Data loading ──────────────────────────────────── */

  const loadNovels = useCallback(async () => {
    setLoading(true);
    try {
      const r = await apiGet<{ items: NovelMeta[]; total: number }>(
        `/api/extraction/novels?limit=200${novelSearch ? `&search=${encodeURIComponent(novelSearch)}` : ""}`
      );
      setNovels(r.items || []);
      setNovelsTotal(r.total || 0);
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setLoading(false);
    }
  }, [novelSearch]);

  const loadProgress = useCallback(async () => {
    try {
      const [prog, status] = await Promise.all([
        apiGet<ExtractionProgress>("/api/extraction/progress"),
        apiGet<PipelineStatus>("/api/extraction/run/status"),
      ]);
      setProgress(prog);
      setPipelineStatus(status);
    } catch (e: any) {
      toast(e.message, "error");
    }
  }, []);

  const loadPatterns = useCallback(async () => {
    try {
      const url = `/api/extraction/patterns?limit=100${patternCategory ? `&category=${patternCategory}` : ""}`;
      const r = await apiGet<{ items: Pattern[]; total: number }>(url);
      setPatterns(r.items || []);
      setPatternsTotal(r.total || 0);
    } catch (e: any) {
      toast(e.message, "error");
    }
  }, [patternCategory]);

  useEffect(() => { loadNovels(); }, [loadNovels]);
  useEffect(() => {
    if (tab === "progress") loadProgress();
    if (tab === "patterns") loadPatterns();
  }, [tab, loadProgress, loadPatterns]);

  // Auto-refresh pipeline status while running
  useEffect(() => {
    if (pipelineStatus.status !== "running") return;
    const id = setInterval(loadProgress, 3000);
    return () => clearInterval(id);
  }, [pipelineStatus.status, loadProgress]);

  /* ── Actions ───────────────────────────────────────── */

  const handleIngest = async () => {
    setIngesting(true);
    try {
      const r = await apiPost<{ ingested: number; needs_review: number }>(
        "/api/extraction/ingest", {}, { timeoutMs: 300_000 }
      );
      toast(`已导入 ${r.ingested} 本小说`, "success");
      loadNovels();
    } catch (e: any) {
      toast(e.message, "error");
    } finally {
      setIngesting(false);
    }
  };

  const handleUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch("/api/extraction/ingest/upload", {
        method: "POST",
        body: fd,
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: "上传失败" }));
        throw new Error(err.detail || "上传失败");
      }
      const r = await res.json();
      toast(`已导入《${r.title}》(${r.chapters}章)`, "success");
      loadNovels();
    } catch (err: any) {
      toast(err.message, "error");
    } finally {
      setUploading(false);
      e.target.value = "";
    }
  };

  const handleRunPipeline = async (phases?: string[]) => {
    try {
      await apiPost("/api/extraction/run", { phases, resume: true });
      toast("提取流程已启动", "success");
      setPipelineStatus({ status: "running", progress: "初始化..." });
    } catch (e: any) {
      toast(e.message, "error");
    }
  };

  const handleEmitSkills = async () => {
    try {
      const r = await apiPost<{ skills_emitted: any }>("/api/extraction/emit", {});
      toast(`Skill生成完成: ${JSON.stringify(r.skills_emitted)}`, "success");
      loadProgress();
    } catch (e: any) {
      toast(e.message, "error");
    }
  };

  const selectNovel = async (ref_id: string) => {
    if (selectedNovel === ref_id) {
      setSelectedNovel(null);
      return;
    }
    setSelectedNovel(ref_id);
    try {
      const r = await apiGet<{ novel: NovelMeta; chapters: ChapterEntry[] }>(
        `/api/extraction/novels/${ref_id}`
      );
      setNovelDetail(r.novel);
      setChapters(r.chapters || []);
    } catch (e: any) {
      toast(e.message, "error");
    }
  };

  /* ── Render ────────────────────────────────────────── */

  return (
    <div className="page-container" style={{ padding: 24 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <h2 style={{ margin: 0, fontSize: 20 }}>
          {"\u229E"} 小说语料库 &amp; 技巧提取
        </h2>
        <div style={{ display: "flex", gap: 8 }}>
          <label
            className="btn btn-secondary"
            style={{ cursor: uploading ? "wait" : "pointer", opacity: uploading ? 0.6 : 1 }}
          >
            {uploading ? "上传中..." : "上传小说"}
            <input type="file" accept=".txt" onChange={handleUpload} hidden disabled={uploading} />
          </label>
          <button
            className="btn btn-secondary"
            onClick={handleIngest}
            disabled={ingesting}
          >
            {ingesting ? "导入中..." : "批量导入"}
          </button>
        </div>
      </div>

      {/* Tab bar */}
      <div style={{ display: "flex", gap: 4, marginBottom: 16, borderBottom: "1px solid var(--border)", paddingBottom: 2 }}>
        {([
          ["novels", "小说列表"],
          ["progress", "提取进度"],
          ["patterns", "模式库"],
        ] as [ViewTab, string][]).map(([key, label]) => (
          <button
            key={key}
            className={`btn-ghost${tab === key ? " active" : ""}`}
            style={{
              padding: "6px 16px",
              borderBottom: tab === key ? "2px solid var(--accent)" : "2px solid transparent",
              color: tab === key ? "var(--accent)" : "var(--text-secondary)",
              fontWeight: tab === key ? 600 : 400,
            }}
            onClick={() => setTab(key)}
          >
            {label}
          </button>
        ))}
      </div>

      {/* ── Novels Tab ──────────────────────────────── */}
      {tab === "novels" && (
        <div>
          <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
            <input
              className="input"
              placeholder="搜索书名/作者..."
              value={novelSearch}
              onChange={e => setNovelSearch(e.target.value)}
              style={{ maxWidth: 300 }}
            />
            <span style={{ color: "var(--text-tertiary)", lineHeight: "32px", fontSize: 12 }}>
              共 {novelsTotal} 本
            </span>
          </div>

          {loading ? (
            <div style={{ color: "var(--text-tertiary)", padding: 40, textAlign: "center" }}>加载中...</div>
          ) : novels.length === 0 ? (
            <div style={{ color: "var(--text-tertiary)", padding: 40, textAlign: "center" }}>
              暂无小说。请使用"批量导入"或"上传小说"添加。
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
              {/* Header */}
              <div style={{
                display: "grid",
                gridTemplateColumns: "2fr 1fr 80px 80px 80px",
                gap: 8,
                padding: "6px 12px",
                fontSize: 11,
                color: "var(--text-tertiary)",
                fontWeight: 600,
                borderBottom: "1px solid var(--border)",
              }}>
                <span>书名</span>
                <span>作者</span>
                <span style={{ textAlign: "right" }}>章节</span>
                <span style={{ textAlign: "right" }}>字数</span>
                <span style={{ textAlign: "right" }}>状态</span>
              </div>

              {novels.map(n => (
                <React.Fragment key={n.ref_id}>
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "2fr 1fr 80px 80px 80px",
                      gap: 8,
                      padding: "8px 12px",
                      cursor: "pointer",
                      background: selectedNovel === n.ref_id ? "var(--bg-surface-2)" : "transparent",
                      borderRadius: 4,
                    }}
                    onClick={() => selectNovel(n.ref_id)}
                  >
                    <span style={{ fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {n.title}
                    </span>
                    <span style={{ color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {n.author || "\u2014"}
                    </span>
                    <span style={{ textAlign: "right" }}>{n.total_chapters}</span>
                    <span style={{ textAlign: "right" }}>{fmtChars(n.total_chars)}</span>
                    <span style={{ textAlign: "right" }}>
                      <span style={{
                        fontSize: 10,
                        padding: "2px 6px",
                        borderRadius: 8,
                        background: n.status === "completed" ? "var(--jade)" : "var(--gold)",
                        color: "#fff",
                      }}>
                        {n.status === "completed" ? "完结" : "连载"}
                      </span>
                    </span>
                  </div>

                  {/* Detail panel */}
                  {selectedNovel === n.ref_id && novelDetail && (
                    <div style={{
                      padding: "12px 16px",
                      background: "var(--bg-surface)",
                      borderRadius: 6,
                      border: "1px solid var(--border)",
                      marginBottom: 4,
                    }}>
                      <div style={{ display: "flex", gap: 24, marginBottom: 8, fontSize: 12, color: "var(--text-secondary)" }}>
                        <span>过滤作者说: {novelDetail.excluded_author_notes} 章</span>
                        <span>总字数: {fmtChars(novelDetail.total_chars)}</span>
                      </div>
                      {novelDetail.synopsis && (
                        <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8, maxHeight: 60, overflow: "hidden" }}>
                          {novelDetail.synopsis}
                        </div>
                      )}
                      <div style={{ maxHeight: 200, overflow: "auto", fontSize: 11 }}>
                        <table style={{ width: "100%", borderCollapse: "collapse" }}>
                          <thead>
                            <tr style={{ color: "var(--text-tertiary)", borderBottom: "1px solid var(--border)" }}>
                              <th style={{ textAlign: "left", padding: "4px 8px", fontWeight: 500 }}>#</th>
                              <th style={{ textAlign: "left", padding: "4px 8px", fontWeight: 500 }}>章节标题</th>
                              <th style={{ textAlign: "right", padding: "4px 8px", fontWeight: 500 }}>字数</th>
                            </tr>
                          </thead>
                          <tbody>
                            {chapters.slice(0, 50).map(c => (
                              <tr key={c.chapter_num} style={{ borderBottom: "1px solid var(--border-light, #2a2a2a)" }}>
                                <td style={{ padding: "3px 8px", color: "var(--text-tertiary)" }}>{c.chapter_num}</td>
                                <td style={{ padding: "3px 8px" }}>{c.original_title}</td>
                                <td style={{ padding: "3px 8px", textAlign: "right" }}>{fmtChars(c.char_count)}</td>
                              </tr>
                            ))}
                            {chapters.length > 50 && (
                              <tr>
                                <td colSpan={3} style={{ padding: "4px 8px", color: "var(--text-tertiary)", textAlign: "center" }}>
                                  ... 共 {chapters.length} 章
                                </td>
                              </tr>
                            )}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </React.Fragment>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Progress Tab ────────────────────────────── */}
      {tab === "progress" && (
        <div>
          {/* Pipeline controls */}
          <div style={{
            display: "flex", gap: 8, marginBottom: 16, padding: 12,
            background: "var(--bg-surface)", borderRadius: 6, border: "1px solid var(--border)",
            alignItems: "center",
          }}>
            <span style={{ fontSize: 12, color: "var(--text-secondary)", marginRight: 8 }}>
              流程状态:
              <span style={{
                marginLeft: 6,
                fontWeight: 600,
                color: pipelineStatus.status === "running" ? "var(--gold)"
                  : pipelineStatus.status === "done" ? "var(--jade)"
                  : pipelineStatus.status === "error" ? "var(--red, #ef4444)"
                  : "var(--text-tertiary)",
              }}>
                {pipelineStatus.status === "idle" ? "空闲" : pipelineStatus.progress || pipelineStatus.status}
              </span>
            </span>
            <div style={{ flex: 1 }} />
            <button
              className="btn btn-secondary"
              onClick={() => handleRunPipeline(["chapter"])}
              disabled={pipelineStatus.status === "running"}
              style={{ fontSize: 11 }}
            >
              章节提取
            </button>
            <button
              className="btn btn-secondary"
              onClick={() => handleRunPipeline(["novel"])}
              disabled={pipelineStatus.status === "running"}
              style={{ fontSize: 11 }}
            >
              全书聚合
            </button>
            <button
              className="btn btn-secondary"
              onClick={() => handleRunPipeline(["pattern"])}
              disabled={pipelineStatus.status === "running"}
              style={{ fontSize: 11 }}
            >
              模式挖掘
            </button>
            <button
              className="btn"
              onClick={() => handleRunPipeline()}
              disabled={pipelineStatus.status === "running"}
              style={{ fontSize: 11 }}
            >
              全部运行
            </button>
            <button
              className="btn btn-secondary"
              onClick={handleEmitSkills}
              disabled={pipelineStatus.status === "running"}
              style={{ fontSize: 11 }}
            >
              生成Skill
            </button>
          </div>

          {pipelineStatus.error && (
            <div style={{
              padding: "8px 12px", marginBottom: 12,
              background: "rgba(239,68,68,0.1)", border: "1px solid rgba(239,68,68,0.3)",
              borderRadius: 6, color: "var(--red, #ef4444)", fontSize: 12,
            }}>
              {pipelineStatus.error}
            </div>
          )}

          {/* Progress cards */}
          {progress && (
            <div>
              <div style={{ marginBottom: 12, fontSize: 13, color: "var(--text-secondary)" }}>
                已注册小说: <strong>{progress.total_novels}</strong>
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(220px, 1fr))", gap: 12, marginBottom: 20 }}>
                {Object.entries(PHASE_LABELS).map(([phase, label]) => {
                  const ps = progress.phases[phase] || {};
                  const completed = ps["completed"] || 0;
                  const failed = ps["failed"] || 0;
                  const pending = ps["pending"] || 0;
                  const inProgress = ps["in_progress"] || 0;
                  const total = completed + failed + pending + inProgress;

                  return (
                    <div key={phase} style={{
                      padding: 16, background: "var(--bg-surface)", borderRadius: 8,
                      border: "1px solid var(--border)",
                    }}>
                      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>{label}</div>
                      {total === 0 ? (
                        <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>尚未开始</div>
                      ) : (
                        <>
                          <div style={{
                            height: 6, background: "var(--bg-surface-2)", borderRadius: 3, overflow: "hidden", marginBottom: 6,
                          }}>
                            <div style={{
                              height: "100%", width: `${total > 0 ? (completed / total) * 100 : 0}%`,
                              background: "var(--jade)", borderRadius: 3, transition: "width 0.3s",
                            }} />
                          </div>
                          <div style={{ display: "flex", gap: 12, fontSize: 11, color: "var(--text-secondary)" }}>
                            <span style={{ color: "var(--jade)" }}>{completed} 完成</span>
                            {inProgress > 0 && <span style={{ color: "var(--gold)" }}>{inProgress} 进行中</span>}
                            {failed > 0 && <span style={{ color: "var(--red, #ef4444)" }}>{failed} 失败</span>}
                            {pending > 0 && <span>{pending} 待处理</span>}
                          </div>
                        </>
                      )}
                    </div>
                  );
                })}
              </div>

              {/* Pattern & Skill summary */}
              <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
                <div style={{
                  padding: 16, background: "var(--bg-surface)", borderRadius: 8,
                  border: "1px solid var(--border)", minWidth: 200,
                }}>
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>已挖掘模式</div>
                  {Object.keys(progress.patterns).length === 0 ? (
                    <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>暂无</div>
                  ) : (
                    Object.entries(progress.patterns).map(([cat, cnt]) => (
                      <div key={cat} style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 2 }}>
                        {CATEGORY_LABELS[cat] || cat}: <strong>{cnt}</strong>
                      </div>
                    ))
                  )}
                </div>
                <div style={{
                  padding: 16, background: "var(--bg-surface)", borderRadius: 8,
                  border: "1px solid var(--border)", minWidth: 200,
                }}>
                  <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 8 }}>已生成Skill</div>
                  <div style={{ fontSize: 24, fontWeight: 700, color: "var(--accent)" }}>
                    {progress.skills_emitted}
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Patterns Tab ────────────────────────────── */}
      {tab === "patterns" && (
        <div>
          <div style={{ display: "flex", gap: 8, marginBottom: 12, alignItems: "center" }}>
            <select
              className="input"
              value={patternCategory}
              onChange={e => setPatternCategory(e.target.value)}
              style={{ maxWidth: 200 }}
            >
              <option value="">全部类别</option>
              {Object.entries(CATEGORY_LABELS).map(([k, v]) => (
                <option key={k} value={k}>{v}</option>
              ))}
            </select>
            <span style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
              共 {patternsTotal} 个模式
            </span>
          </div>

          {patterns.length === 0 ? (
            <div style={{ color: "var(--text-tertiary)", padding: 40, textAlign: "center" }}>
              暂无模式。请先运行"模式挖掘"阶段。
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {patterns.map(p => (
                <div key={p.pattern_id} style={{
                  padding: 12, background: "var(--bg-surface)", borderRadius: 6,
                  border: "1px solid var(--border)",
                }}>
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 4 }}>
                    <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                      <span style={{ fontWeight: 600, fontSize: 13 }}>{p.pattern_name}</span>
                      <span style={{
                        fontSize: 10, padding: "1px 6px", borderRadius: 8,
                        background: "var(--bg-surface-2)", color: "var(--text-tertiary)",
                      }}>
                        {CATEGORY_LABELS[p.category] || p.category}
                      </span>
                      {p.subcategory && (
                        <span style={{ fontSize: 10, color: "var(--text-tertiary)" }}>
                          / {p.subcategory}
                        </span>
                      )}
                    </div>
                    <div style={{ display: "flex", gap: 12, fontSize: 11, color: "var(--text-tertiary)" }}>
                      <span>出现: {p.frequency}本</span>
                      <span>质量: {(p.quality_score || 0).toFixed(1)}</span>
                      {p.skill_emitted && (
                        <span style={{ color: "var(--jade)" }}>已生成Skill</span>
                      )}
                    </div>
                  </div>
                  {p.description && (
                    <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5 }}>
                      {p.description.length > 200 ? p.description.slice(0, 200) + "..." : p.description}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
