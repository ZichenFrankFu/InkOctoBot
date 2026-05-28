/**
 * MarketFeatureExtractionPage — operations console for the
 * /api/market-extractor backend, matched to the real DB schema.
 *
 * Latest revision asks for:
 *  - Auto-fetched platform list (no hardcoded options).
 *  - Categories = 榜单 (rank_family + rank_sub_cat from rank_lists),
 *    again pulled from the crawler DB.
 *  - Cancel button on running jobs (cooperative — flips state to
 *    "cancelled"; the pipeline bails at the next phase checkpoint).
 *  - "Manual mode" button that calls /manual-prompt → opens
 *    UniversalLLMDialog so the user copies the prompt, runs it in a
 *    browser LLM, pastes the result back; commit writes a
 *    platform_profiles row via /manual-submit.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiGet, apiPost, apiDelete } from "../api/client";
import { useToast } from "../components/shared/Toast";
import UniversalLLMDialog from "../components/shared/UniversalLLMDialog";
import AnalysisDashboardPage from "./AnalysisDashboardPage";
import { tPlatform } from "../i18n";


type ResultTab = "jobs" | "profiles" | "loader" | "works" | "preview";
type TopTab = "extract" | "analysis";


interface Job {
  job_id: string;
  platform?: string;
  category?: string;
  state?: string;
  created_at?: string;
  started_at?: string;
  completed_at?: string;
  progress_phase?: string;
  progress_pct?: number;
  current_work_id?: string;
  works_count?: number;
  chapters_processed?: number;
}


interface PlatformProfile {
  profile_id?: string;
  platform: string;
  category?: string;
  profile_summary?: string;
  style_baseline?: string;
  signature_devices_description?: string;
  pacing_guidance?: string;
  source_works_count?: number;
  extraction_completed_at?: string;
  confidence_label?: string;
}


interface RepresentativeWork {
  work_id: string;
  title?: string;
  author?: string;
  rank_score?: number;
  word_count?: number;
}


interface PlatformOption { key: string; label: string; book_count: number; }
interface CategoryOption {
  key: string; label: string;
  rank_family: string; rank_sub_cat: string;
  list_count: number;
}


const STATE_COLOR: Record<string, string> = {
  queued:           "var(--text-tertiary)",
  running_phase_1:  "var(--accent)",
  running_phase_2:  "var(--accent)",
  running_phase_3:  "var(--accent)",
  running_phase_4:  "var(--accent)",
  running_phase_5:  "var(--accent)",
  completed:        "var(--success)",
  failed:           "var(--danger)",
  cancelled:        "var(--text-disabled)",
};

const STATE_LABEL_CN: Record<string, string> = {
  queued:           "排队中",
  running_phase_1:  "阶段 1 运行中",
  running_phase_2:  "阶段 2 运行中",
  running_phase_3:  "阶段 3 运行中",
  running_phase_4:  "阶段 4 运行中",
  running_phase_5:  "阶段 5 运行中",
  completed:        "已完成",
  failed:           "已失败",
  cancelled:        "已取消",
};

const PHASE_LABEL_CN: Record<string, string> = {
  phase_1: "代表作选取",
  phase_2: "章节抓取与抽取",
  phase_3: "作品级聚合",
  phase_4: "类目级聚合",
  phase_5: "合成平台档案",
};

const CONFIDENCE_LABEL_CN: Record<string, string> = {
  high:   "高",
  medium: "中",
  low:    "低",
  manual: "手动",
};

function tConfidence(c?: string): string {
  if (!c) return "—";
  return CONFIDENCE_LABEL_CN[c] || c;
}


function isActive(state?: string): boolean {
  return !!state && (state.startsWith("running") || state === "queued");
}


export default function MarketFeatureExtractionPage() {
  const { toast } = useToast();
  const [platforms, setPlatforms] = useState<PlatformOption[]>([]);
  const [categories, setCategories] = useState<CategoryOption[]>([]);
  const [platform, setPlatform] = useState("");
  const [category, setCategory] = useState("");
  const [launching, setLaunching] = useState(false);

  const [jobs, setJobs] = useState<Job[]>([]);
  const [profiles, setProfiles] = useState<PlatformProfile[]>([]);
  const [works, setWorks] = useState<RepresentativeWork[]>([]);
  const [selectedWork, setSelectedWork] = useState<RepresentativeWork | null>(null);
  const [chapterFeatures, setChapterFeatures] = useState<any[]>([]);
  const [neologisms, setNeologisms] = useState<any[]>([]);

  const [tab, setTab] = useState<ResultTab>("jobs");
  const [topTab, setTopTab] = useState<TopTab>("extract");
  const [loading, setLoading] = useState(false);

  // Manual-mode dialog
  const [manualOpen, setManualOpen] = useState(false);
  const [manualPrompt, setManualPrompt] = useState("");


  // ── data load ──

  const loadPlatforms = useCallback(async () => {
    try {
      const r = await apiGet<{ platforms: PlatformOption[]; warning?: string }>(
        "/api/market-extractor/platforms",
      );
      setPlatforms(r.platforms || []);
      if (r.warning) {
        toast(`平台列表: ${r.warning}`, "info");
      }
      if (r.platforms && r.platforms.length > 0 && !platform) {
        setPlatform(r.platforms[0].key);
      }
    } catch (e: any) {
      toast(`加载平台失败: ${e.message}`, "error");
    }
  }, [platform, toast]);

  const loadCategories = useCallback(async (pl: string) => {
    try {
      const params = pl ? `?platform=${encodeURIComponent(pl)}` : "";
      const r = await apiGet<{ categories: CategoryOption[] }>(
        `/api/market-extractor/categories${params}`,
      );
      setCategories(r.categories || []);
      if (r.categories && r.categories.length > 0
          && !r.categories.some(c => c.key === category)) {
        setCategory(r.categories[0].key);
      }
    } catch (e: any) {
      toast(`加载榜单失败: ${e.message}`, "error");
    }
  }, [category, toast]);

  const refreshJobs = useCallback(async () => {
    try {
      const r = await apiGet<{ jobs: Job[] }>("/api/market-extractor/jobs?limit=50");
      setJobs(r.jobs || []);
    } catch (e: any) {
      toast(`加载任务失败: ${e.message}`, "error");
    }
  }, [toast]);

  const refreshProfiles = useCallback(async () => {
    try {
      const r = await apiGet<{ profiles?: PlatformProfile[]; items?: PlatformProfile[] }>(
        "/api/platform-profiles",
      );
      setProfiles((r.profiles || r.items || []) as PlatformProfile[]);
    } catch (e: any) {
      toast(`加载平台档案失败: ${e.message}`, "error");
    }
  }, [toast]);

  const refreshWorks = useCallback(async (pl: string, cat: string) => {
    if (!pl || !cat) return;
    try {
      const r = await apiGet<{ works: RepresentativeWork[] }>(
        `/api/market-extractor/representative-works?platform=${encodeURIComponent(pl)}&category=${encodeURIComponent(cat)}`,
      );
      setWorks(r.works || []);
    } catch (e: any) {
      toast(`加载代表作失败: ${e.message}`, "error");
      setWorks([]);
    }
  }, [toast]);

  useEffect(() => {
    loadPlatforms();
    refreshJobs();
    refreshProfiles();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (platform) {
      loadCategories(platform);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [platform]);

  useEffect(() => {
    if (platform && category) {
      refreshWorks(platform, category);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [platform, category]);

  useEffect(() => {
    const active = jobs.some(j => isActive(j.state));
    if (!active) return;
    const id = window.setInterval(refreshJobs, 5000);
    return () => window.clearInterval(id);
  }, [jobs, refreshJobs]);

  // ── actions ──

  const launchJob = useCallback(async () => {
    if (!platform || !category) {
      toast("请先选择平台 + 榜单", "error");
      return;
    }
    if (!window.confirm(
      `准备启动 API 提取任务：\n平台: ${platform}\n榜单: ${category}\n\n` +
      `会调用大模型抽取代表作的章节特征与新词，` +
      `更新对应的平台风格档案。可能耗时数分钟。继续？`,
    )) return;
    setLaunching(true);
    try {
      const r = await apiPost<{ job_id: string; state: string }>(
        "/api/market-extractor/jobs",
        { platform, category },
      );
      toast(`任务已启动 (${r.job_id.slice(0, 8)}...)`, "success");
      setTab("jobs");
      await refreshJobs();
    } catch (e: any) {
      toast(`启动失败: ${e.message}`, "error");
    } finally {
      setLaunching(false);
    }
  }, [platform, category, refreshJobs, toast]);

  const cancelJob = useCallback(async (job_id: string) => {
    if (!window.confirm("取消运行中的任务？\n已完成的阶段会保留，但后续阶段会跳过。")) return;
    try {
      await apiPost(`/api/market-extractor/jobs/${job_id}/cancel`, {});
      toast("已请求取消", "success");
      refreshJobs();
    } catch (e: any) {
      toast(`取消失败: ${e.message}`, "error");
    }
  }, [refreshJobs, toast]);

  const deleteJob = useCallback(async (job_id: string) => {
    if (!window.confirm("从任务列表中永久删除该任务？\n仅删除任务行本身，已写入的平台档案 / 代表作不受影响。")) return;
    try {
      await apiDelete(`/api/market-extractor/jobs/${job_id}`);
      toast("任务已删除", "success");
      refreshJobs();
    } catch (e: any) {
      toast(`删除失败: ${e.message}`, "error");
    }
  }, [refreshJobs, toast]);

  const startManualMode = useCallback(async () => {
    if (!platform || !category) {
      toast("请先选择平台 + 榜单", "error");
      return;
    }
    try {
      const r = await apiPost<{ prompt: string }>(
        "/api/market-extractor/manual-prompt",
        { platform, category },
      );
      setManualPrompt(r.prompt || "");
      setManualOpen(true);
    } catch (e: any) {
      toast(`生成 prompt 失败: ${e.message}`, "error");
    }
  }, [platform, category, toast]);

  const commitManual = useCallback(async (payload: { text: string }) => {
    const r = await apiPost<{ profile_id: string }>(
      "/api/market-extractor/manual-submit",
      { platform, category, response_raw: payload.text },
    );
    toast(`平台档案已写入 (${r.profile_id})`, "success");
    setTab("profiles");
    refreshProfiles();
  }, [platform, category, refreshProfiles, toast]);

  const openWorkPreview = useCallback(async (work: RepresentativeWork) => {
    setSelectedWork(work);
    setTab("preview");
    try {
      const [cf, neo] = await Promise.all([
        apiGet<{ chapters: any[] }>(
          `/api/market-extractor/chapter-features/${encodeURIComponent(work.work_id)}`,
        ),
        apiGet<{ neologisms: any[] }>(
          `/api/market-extractor/neologisms/${encodeURIComponent(work.work_id)}`,
        ),
      ]);
      setChapterFeatures(cf.chapters || []);
      setNeologisms(neo.neologisms || []);
    } catch (e: any) {
      toast(`加载提取结果失败: ${e.message}`, "error");
      setChapterFeatures([]);
      setNeologisms([]);
    }
  }, [toast]);

  const platformProfiles = useMemo(() => {
    return profiles.filter(p =>
      (!platform || p.platform === platform)
      && (!category || p.category === category),
    );
  }, [profiles, platform, category]);

  const refreshAll = useCallback(async () => {
    setLoading(true);
    await Promise.all([refreshJobs(), refreshProfiles()]);
    if (platform && category) {
      await refreshWorks(platform, category);
    }
    setLoading(false);
  }, [refreshJobs, refreshProfiles, refreshWorks, platform, category]);

  const SELECTION_OK = !!platform && !!category;

  return (
    <div className="page-container" style={{ padding: "16px 20px", maxWidth: 1400, margin: "0 auto" }}>
      <div className="page-header" style={{ paddingBottom: 8 }}>
        <div className="page-header-row">
          <div>
            <h2>市场特征提取</h2>
          </div>
          {topTab === "extract" && (
            <button className="btn" onClick={refreshAll} disabled={loading}>
              {loading ? "刷新中..." : "刷新"}
            </button>
          )}
        </div>
      </div>

      {/* ── Top-level tab strip: 特征提取 | 分析面板 ── */}
      <div style={{
        display: "flex", gap: 0,
        borderBottom: "1px solid var(--border)",
        marginBottom: 14,
      }}>
        {([
          { key: "extract"  as const, label: "特征提取" },
          { key: "analysis" as const, label: "分析面板" },
        ]).map(opt => (
          <button
            key={opt.key}
            onClick={() => setTopTab(opt.key)}
            style={{
              padding: "10px 20px",
              fontSize: 13,
              fontWeight: topTab === opt.key ? 700 : 400,
              color: topTab === opt.key ? "var(--accent)" : "var(--text-secondary)",
              background: "none",
              border: "none",
              borderBottom: topTab === opt.key ? "2px solid var(--accent)" : "2px solid transparent",
              cursor: "pointer",
              marginBottom: -1,
            }}
          >{opt.label}</button>
        ))}
      </div>

      {topTab === "analysis" && <AnalysisDashboardPage hideOwnHeader />}

      {topTab === "extract" && (<>
      <div style={{ display: "grid", gridTemplateColumns: "360px 1fr", gap: 18, alignItems: "start" }}>
        {/* ════════════ LEFT: configurator + launch ════════════ */}
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>

          {/* Step 1 — platform */}
          <div className="card" style={{ padding: 16, borderTop: "3px solid var(--accent)" }}>
            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 12 }}>
              <h3 style={{ margin: 0, fontSize: 14 }}>
                <span style={{
                  display: "inline-block", width: 22, height: 22, borderRadius: "50%",
                  background: "var(--accent)", color: "white", fontSize: 12, fontWeight: 700,
                  textAlign: "center", lineHeight: "22px", marginRight: 8,
                }}>1</span>
                选择平台
              </h3>
              <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{platforms.length} 个</span>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, maxHeight: 120, overflowY: "auto" }}>
              {platforms.length === 0 ? (
                <span style={{ fontSize: 12, color: "var(--text-tertiary)", padding: 8 }}>
                  未读到平台 — 检查 Settings → 市场数据库路径
                </span>
              ) : platforms.map(p => (
                <button
                  key={p.key}
                  className={platform === p.key ? "btn-primary" : "btn"}
                  style={{ fontSize: 12, padding: "5px 14px", borderRadius: 20 }}
                  onClick={() => setPlatform(p.key)}
                  title={`${p.book_count} 本作品`}
                >{tPlatform(p.key)}</button>
              ))}
            </div>
          </div>

          {/* Step 2 — category */}
          <div className="card" style={{
            padding: 16,
            borderTop: `3px solid ${platform ? "var(--gold)" : "var(--border)"}`,
            opacity: platform ? 1 : 0.5,
            transition: "opacity 0.18s",
          }}>
            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginBottom: 12 }}>
              <h3 style={{ margin: 0, fontSize: 14 }}>
                <span style={{
                  display: "inline-block", width: 22, height: 22, borderRadius: "50%",
                  background: platform ? "var(--gold)" : "var(--text-disabled)",
                  color: "white", fontSize: 12, fontWeight: 700,
                  textAlign: "center", lineHeight: "22px", marginRight: 8,
                }}>2</span>
                选择榜单
              </h3>
              <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{categories.length} 个</span>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, maxHeight: 200, overflowY: "auto" }}>
              {categories.length === 0 ? (
                <span style={{ fontSize: 12, color: "var(--text-tertiary)", padding: 8 }}>
                  {platform ? "该平台还没有榜单数据" : "先选定平台"}
                </span>
              ) : categories.map(c => (
                <button
                  key={c.key}
                  className={category === c.key ? "btn-primary" : "btn"}
                  style={{ fontSize: 11, padding: "4px 12px", borderRadius: 16 }}
                  onClick={() => setCategory(c.key)}
                  title={`${c.list_count} 个榜单`}
                >{c.label}</button>
              ))}
            </div>
          </div>

          {/* Step 3 — launch */}
          <div className="card" style={{
            padding: 16,
            borderTop: `3px solid ${SELECTION_OK ? "var(--jade)" : "var(--border)"}`,
            opacity: SELECTION_OK ? 1 : 0.5,
            transition: "opacity 0.18s",
          }}>
            <h3 style={{ margin: "0 0 12px", fontSize: 14 }}>
              <span style={{
                display: "inline-block", width: 22, height: 22, borderRadius: "50%",
                background: SELECTION_OK ? "var(--jade)" : "var(--text-disabled)",
                color: "white", fontSize: 12, fontWeight: 700,
                textAlign: "center", lineHeight: "22px", marginRight: 8,
              }}>3</span>
              启动提取
            </h3>
            {SELECTION_OK && (
              <div style={{
                padding: 10, marginBottom: 12,
                background: "var(--bg-surface-2)", borderRadius: 6,
                fontSize: 12, color: "var(--text-secondary)",
              }}>
                <strong>{tPlatform(platform)}</strong>
                <span style={{ color: "var(--text-tertiary)", margin: "0 6px" }}>×</span>
                <strong>{category}</strong>
              </div>
            )}
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <button
                className="btn-primary"
                onClick={launchJob}
                disabled={launching || !SELECTION_OK}
                style={{
                  width: "100%", padding: "10px 0",
                  fontSize: 13, fontWeight: 600,
                  textAlign: "center",
                  display: "flex", alignItems: "center", justifyContent: "center",
                }}
              >
                {launching ? "启动中..." : "使用大模型 API 提取"}
              </button>
              <button
                className="btn"
                onClick={startManualMode}
                disabled={!SELECTION_OK}
                style={{
                  width: "100%", padding: "10px 0", fontSize: 13,
                  textAlign: "center",
                  display: "flex", alignItems: "center", justifyContent: "center",
                }}
              >
                使用大模型网页版提取
              </button>
            </div>
          </div>
        </div>

        {/* RIGHT — Results tabs */}
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <div style={{
            display: "flex", gap: 4, alignItems: "center",
            borderBottom: "1px solid var(--border)",
            padding: "10px 14px 0",
            background: "var(--bg-surface-2)",
          }}>
            {([
              { key: "jobs" as const,     label: "任务",          count: jobs.length },
              { key: "profiles" as const, label: "平台风格档案",   count: platformProfiles.length },
              { key: "loader" as const,   label: "注入预览",      count: null as number | null },
              { key: "works" as const,    label: "代表作",        count: works.length },
              { key: "preview" as const,
                label: selectedWork ? `预览·${selectedWork.title || selectedWork.work_id.slice(0, 6)}` : "预览",
                count: null as number | null },
            ]).map(t => {
              const active = tab === t.key;
              const disabled = t.key === "preview" && !selectedWork;
              return (
                <button
                  key={t.key}
                  onClick={() => setTab(t.key)}
                  disabled={disabled}
                  style={{
                    padding: "8px 14px",
                    fontSize: 12,
                    fontWeight: active ? 600 : 500,
                    color: active ? "var(--accent)" : "var(--text-secondary)",
                    background: active ? "var(--bg-surface)" : "transparent",
                    border: "1px solid",
                    borderColor: active ? "var(--border)" : "transparent",
                    borderBottomColor: active ? "var(--bg-surface)" : "transparent",
                    borderRadius: "6px 6px 0 0",
                    cursor: disabled ? "not-allowed" : "pointer",
                    marginBottom: -1,
                    opacity: disabled ? 0.4 : 1,
                    display: "inline-flex", alignItems: "center", gap: 6,
                    transition: "background 0.15s, color 0.15s",
                  }}
                >
                  {t.label}
                  {t.count !== null && (
                    <span style={{
                      display: "inline-block",
                      minWidth: 18,
                      padding: "1px 6px",
                      borderRadius: 9,
                      background: active ? "var(--accent)" : "var(--bg-surface-2)",
                      color: active ? "white" : "var(--text-tertiary)",
                      fontSize: 10,
                      fontWeight: 600,
                      textAlign: "center",
                      lineHeight: "14px",
                    }}>{t.count}</span>
                  )}
                </button>
              );
            })}
          </div>

          <div style={{ padding: 18, minHeight: 420, background: "var(--bg-surface)" }}>
            {tab === "jobs" && <JobsTab jobs={jobs} onCancel={cancelJob} onDelete={deleteJob} />}
            {tab === "profiles" && <ProfilesTab profiles={platformProfiles} />}
            {tab === "loader" && <LoaderPreviewTab platform={platform} category={category} profiles={platformProfiles} />}
            {tab === "works" && (
              <WorksTab
                works={works}
                onSelect={openWorkPreview}
                emptyHint={works.length === 0
                  ? `没有 (${platform || "—"}×${category || "—"}) 的代表作。先启动一次提取任务。`
                  : ""}
              />
            )}
            {tab === "preview" && (
              <PreviewTab
                work={selectedWork}
                chapterFeatures={chapterFeatures}
                neologisms={neologisms}
              />
            )}
          </div>
        </div>
      </div>

      {/* 开篇章节分析 — moved from RankingsPage */}
      <OpeningAnalysisPanel platform={platform} />

      </>)}

      {/* 网页版大模型手动粘贴弹窗 */}
      <UniversalLLMDialog
        open={manualOpen}
        onClose={() => setManualOpen(false)}
        title={`网页版大模型提取: ${tPlatform(platform)} × ${category}`}
        description="复制左侧提示词到网页版大模型运行，将完整回复粘回右侧；提交后写入平台档案表。"
        prompt={manualPrompt}
        onCommit={commitManual}
        minChars={80}
        initialMode="manual_only"
      />
    </div>
  );
}


function JobsTab({
  jobs, onCancel, onDelete,
}: { jobs: Job[]; onCancel: (id: string) => void; onDelete: (id: string) => void }) {
  if (jobs.length === 0) {
    return <Empty msg="还没有任何任务。在左侧选好平台 + 榜单后点「使用大模型 API 提取」或「使用大模型网页版提取」。" />;
  }
  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: 6, overflow: "hidden" }}>
      <table style={{ width: "100%", fontSize: 12, borderCollapse: "collapse" }}>
        <thead>
          <tr style={{
            color: "var(--text-tertiary)", textAlign: "left",
            background: "var(--bg-surface-2)",
            fontSize: 11, letterSpacing: 0.5,
          }}>
            <th style={{ padding: "8px 10px" }}>任务编号</th>
            <th style={{ padding: "8px 10px" }}>平台</th>
            <th style={{ padding: "8px 10px" }}>榜单</th>
            <th style={{ padding: "8px 10px" }}>状态</th>
            <th style={{ padding: "8px 10px" }}>进度</th>
            <th style={{ padding: "8px 10px" }}>开始</th>
            <th style={{ padding: "8px 10px" }}>结束</th>
            <th style={{ padding: "8px 10px", textAlign: "right" }}>操作</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map(j => {
            const pct = typeof j.progress_pct === "number" ? Math.round(j.progress_pct) : null;
            const stateColor = STATE_COLOR[j.state || ""] || "var(--text-secondary)";
            const stateLabel = STATE_LABEL_CN[j.state || ""] || j.state || "—";
            const phaseLabel = j.progress_phase ? (PHASE_LABEL_CN[j.progress_phase] || j.progress_phase) : "";
            return (
              <tr key={j.job_id} style={{ borderTop: "1px solid var(--border)" }}>
                <td style={{ padding: "8px 10px" }}>
                  <code style={{ fontSize: 11, color: "var(--text-secondary)" }}>{j.job_id.slice(0, 10)}</code>
                </td>
                <td style={{ padding: "8px 10px" }}>{j.platform ? tPlatform(j.platform) : "—"}</td>
                <td style={{ padding: "8px 10px" }}>{j.category || "—"}</td>
                <td style={{ padding: "8px 10px" }}>
                  <span style={{
                    display: "inline-block",
                    padding: "2px 8px",
                    borderRadius: 10,
                    background: `color-mix(in srgb, ${stateColor} 14%, transparent)`,
                    color: stateColor,
                    fontSize: 11,
                    fontWeight: 600,
                  }}>{stateLabel}</span>
                  {phaseLabel && j.state?.startsWith("running") && (
                    <span style={{ color: "var(--text-tertiary)", marginLeft: 6, fontSize: 11 }}>
                      {phaseLabel}
                    </span>
                  )}
                </td>
                <td style={{ padding: "8px 10px", minWidth: 90 }}>
                  {pct !== null ? (
                    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                      <div style={{
                        flex: 1, height: 6,
                        background: "var(--bg-surface-2)",
                        borderRadius: 3, overflow: "hidden", minWidth: 50,
                      }}>
                        <div style={{
                          width: `${pct}%`, height: "100%",
                          background: stateColor, transition: "width 0.3s",
                        }} />
                      </div>
                      <span style={{ fontSize: 11, fontFamily: "var(--font-mono)" }}>{pct}%</span>
                    </div>
                  ) : "—"}
                </td>
                <td style={{ padding: "8px 10px", fontSize: 11, color: "var(--text-tertiary)" }}>{j.started_at || "—"}</td>
                <td style={{ padding: "8px 10px", fontSize: 11, color: "var(--text-tertiary)" }}>{j.completed_at || "—"}</td>
                <td style={{ padding: "8px 10px", textAlign: "right", whiteSpace: "nowrap" }}>
                  {isActive(j.state) ? (
                    <button className="btn"
                            style={{ fontSize: 11, padding: "3px 12px", borderRadius: 12 }}
                            onClick={() => onCancel(j.job_id)}>
                      取消
                    </button>
                  ) : (
                    <button className="btn"
                            style={{ fontSize: 11, padding: "3px 12px", borderRadius: 12, color: "var(--danger)" }}
                            onClick={() => onDelete(j.job_id)}>
                      删除
                    </button>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}


function ProfilesTab({ profiles }: { profiles: PlatformProfile[] }) {
  if (profiles.length === 0) {
    return <Empty msg="尚无匹配当前平台 / 榜单的平台档案。任务跑完后会自动出现。" />;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {profiles.map((p, i) => (
        <div key={p.profile_id || `${p.platform}-${p.category || i}`}
             className="card" style={{ padding: 12, background: "var(--bg-surface-2)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
            <strong>{tPlatform(p.platform)} · {p.category || "—"}</strong>
            <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
              {p.confidence_label && <>置信度 {tConfidence(p.confidence_label)} · </>}
              样本 {p.source_works_count ?? "—"} · 更新 {p.extraction_completed_at || "—"}
            </span>
          </div>
          {[
            ["综述", p.profile_summary],
            ["风格基线", p.style_baseline],
            ["招牌叙事手法", p.signature_devices_description],
            ["节奏指南", p.pacing_guidance],
          ].map(([label, val]) => (val ? (
            <details key={label as string} style={{ marginTop: 6 }}>
              <summary style={{ fontSize: 12, cursor: "pointer" }}>{label}</summary>
              <pre style={{
                margin: 0, padding: 8, fontSize: 11,
                background: "var(--bg-surface)",
                borderRadius: 4, maxHeight: 200, overflow: "auto",
                whiteSpace: "pre-wrap", wordBreak: "break-word",
                fontFamily: "var(--font-mono)",
              }}>{val as string}</pre>
            </details>
          ) : null))}
        </div>
      ))}
    </div>
  );
}


function WorksTab({
  works, onSelect, emptyHint,
}: { works: RepresentativeWork[]; onSelect: (w: RepresentativeWork) => void; emptyHint?: string }) {
  if (works.length === 0) {
    return <Empty msg={emptyHint || "没有代表作。"} />;
  }
  return (
    <table style={{ width: "100%", fontSize: 12 }}>
      <thead>
        <tr style={{ color: "var(--text-tertiary)", textAlign: "left" }}>
          <th style={{ padding: "6px" }}>书名</th>
          <th style={{ padding: "6px" }}>作者</th>
          <th style={{ padding: "6px" }}>排名分</th>
          <th style={{ padding: "6px" }}>字数</th>
          <th style={{ padding: "6px" }}></th>
        </tr>
      </thead>
      <tbody>
        {works.map(w => (
          <tr key={w.work_id} style={{ borderTop: "1px solid var(--border)" }}>
            <td style={{ padding: "6px" }}><strong>{w.title || w.work_id.slice(0, 8)}</strong></td>
            <td style={{ padding: "6px" }}>{w.author || "—"}</td>
            <td style={{ padding: "6px" }}>{w.rank_score?.toFixed(2) || "—"}</td>
            <td style={{ padding: "6px" }}>{w.word_count ? w.word_count.toLocaleString() : "—"}</td>
            <td style={{ padding: "6px", textAlign: "right" }}>
              <button className="btn" style={{ fontSize: 11, padding: "2px 10px" }}
                      onClick={() => onSelect(w)}>查看提取结果</button>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}


function PreviewTab({
  work, chapterFeatures, neologisms,
}: { work: RepresentativeWork | null; chapterFeatures: any[]; neologisms: any[] }) {
  if (!work) {
    return <Empty msg="请在「代表作」标签页选一部作品查看提取结果。" />;
  }
  return (
    <div>
      <div style={{ marginBottom: 12 }}>
        <h3 style={{ marginTop: 0 }}>{work.title || work.work_id}</h3>
        <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
          作者 {work.author || "—"} · 字数 {work.word_count ? work.word_count.toLocaleString() : "—"}
        </div>
      </div>

      <h4 style={{ fontSize: 13, marginTop: 12 }}>章节特征 ({chapterFeatures.length})</h4>
      {chapterFeatures.length === 0 ? (
        <p style={{ color: "var(--text-tertiary)", fontSize: 12 }}>
          这部作品还没有提取结果。先在左侧启动一次任务。
        </p>
      ) : (
        <div style={{ maxHeight: 300, overflow: "auto", border: "1px solid var(--border)", borderRadius: 4 }}>
          <table style={{ width: "100%", fontSize: 11 }}>
            <thead style={{ position: "sticky", top: 0, background: "var(--bg-surface)" }}>
              <tr style={{ color: "var(--text-tertiary)" }}>
                <th style={{ padding: "4px 6px", textAlign: "left" }}>章</th>
                <th style={{ padding: "4px 6px", textAlign: "left" }}>对白比</th>
                <th style={{ padding: "4px 6px", textAlign: "left" }}>平均句长</th>
                <th style={{ padding: "4px 6px", textAlign: "left" }}>首句类型</th>
                <th style={{ padding: "4px 6px", textAlign: "left" }}>结尾情绪</th>
                <th style={{ padding: "4px 6px", textAlign: "left" }}>驱动类型</th>
              </tr>
            </thead>
            <tbody>
              {chapterFeatures.slice(0, 30).map((cf, i) => (
                <tr key={cf.feature_id || i} style={{ borderTop: "1px solid var(--border)" }}>
                  <td style={{ padding: "4px 6px" }}>{cf.chapter_num}</td>
                  <td style={{ padding: "4px 6px" }}>{cf.dialogue_ratio?.toFixed(2) ?? "—"}</td>
                  <td style={{ padding: "4px 6px" }}>{cf.avg_sentence_length?.toFixed(1) ?? "—"}</td>
                  <td style={{ padding: "4px 6px" }}>{cf.first_sentence_type || "—"}</td>
                  <td style={{ padding: "4px 6px" }}>{cf.last_200_chars_emotion || "—"}</td>
                  <td style={{ padding: "4px 6px" }}>{cf.drive_type || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h4 style={{ fontSize: 13, marginTop: 16 }}>新词 ({neologisms.length})</h4>
      {neologisms.length === 0 ? (
        <p style={{ color: "var(--text-tertiary)", fontSize: 12 }}>暂无</p>
      ) : (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
          {neologisms.slice(0, 50).map((n: any, i) => (
            <span key={n.term || i} style={{
              padding: "2px 8px", borderRadius: 10,
              background: "var(--bg-surface-2)", fontSize: 11,
            }}>
              {n.term || n.word} <span style={{ color: "var(--text-tertiary)" }}>({n.frequency_in_5_chapters ?? n.frequency})</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}


function LoaderPreviewTab({
  platform, category, profiles,
}: { platform: string; category: string; profiles: PlatformProfile[] }) {
  const [stats, setStats] = React.useState<any>(null);
  const [loading, setLoading] = React.useState(false);

  React.useEffect(() => {
    if (!platform || !category) return;
    setLoading(true);
    apiGet<{ stats: any }>(
      `/api/market-extractor/aggregated-stats?platform=${encodeURIComponent(platform)}&category=${encodeURIComponent(category)}`,
    ).then(r => setStats(r.stats))
     .catch(() => setStats(null))
     .finally(() => setLoading(false));
  }, [platform, category]);

  const activeProfile = profiles[0] || null;
  if (!platform || !category) {
    return <Empty msg="先在左侧选定平台 + 榜单。" />;
  }
  if (loading) {
    return <Empty msg="加载中..." />;
  }
  if (!activeProfile && !stats) {
    return <Empty msg={`(${tPlatform(platform)}×${category}) 还没有任何提取产物。先启动一次「使用大模型 API 提取」或「使用大模型网页版提取」生成平台档案。`} />;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <p style={{ fontSize: 12, color: "var(--text-tertiary)", margin: 0 }}>
        以下两块即生成章节时「平台风格」与「市场画像」两个注入器会读到并写入提示词的全部内容。
      </p>

      {/* ── 平台风格注入 (platform_market loader) ── */}
      <div className="card" style={{ padding: 14 }}>
        <h3 style={{ marginTop: 0, fontSize: 13 }}>
          平台风格注入
          <span style={{ fontSize: 11, color: "var(--text-tertiary)", fontWeight: 400, marginLeft: 8 }}>
            读取 platform_profiles 表的 loader_payload 列
          </span>
        </h3>
        {!activeProfile ? (
          <p style={{ color: "var(--text-tertiary)", fontSize: 12 }}>当前没有可用的有效档案（可能因置信度过低被过滤）。</p>
        ) : (
          <div>
            <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginBottom: 8 }}>
              档案编号 {activeProfile.profile_id} · 版本 {(activeProfile as any).profile_version || "—"}
              · 置信度 {tConfidence(activeProfile.confidence_label)}
              · 样本数 {activeProfile.source_works_count ?? "—"}
              · 完成于 {activeProfile.extraction_completed_at || "—"}
            </div>
            {[
              ["综述",            activeProfile.profile_summary],
              ["风格基线",        activeProfile.style_baseline],
              ["招牌叙事手法",    activeProfile.signature_devices_description],
              ["节奏指南",        activeProfile.pacing_guidance],
              ["推荐开篇套路",    (activeProfile as any).recommended_openings_json],
              ["最终注入正文",    (activeProfile as any).loader_payload],
            ].map(([label, val]) => (val ? (
              <details key={label as string} style={{ marginBottom: 6 }} open={label === "最终注入正文"}>
                <summary style={{ fontSize: 12, cursor: "pointer", color: "var(--text-secondary)" }}>{label}</summary>
                <pre style={{
                  margin: "4px 0 0", padding: 8, fontSize: 11,
                  background: "var(--bg-surface-2)",
                  borderRadius: 4, maxHeight: 220, overflow: "auto",
                  whiteSpace: "pre-wrap", wordBreak: "break-word",
                  fontFamily: "var(--font-mono)",
                }}>{typeof val === "string" ? val : JSON.stringify(val, null, 2)}</pre>
              </details>
            ) : null))}
          </div>
        )}
      </div>

      {/* ── 市场画像注入 (category_aggregated_stats) ── */}
      <div className="card" style={{ padding: 14 }}>
        <h3 style={{ marginTop: 0, fontSize: 13 }}>
          市场画像注入
          <span style={{ fontSize: 11, color: "var(--text-tertiary)", fontWeight: 400, marginLeft: 8 }}>
            该类目 × 平台的整体分布画像
          </span>
        </h3>
        {!stats ? (
          <p style={{ color: "var(--text-tertiary)", fontSize: 12 }}>
            尚无市场画像数据。该数据在「类目级聚合」阶段完成时写入。
          </p>
        ) : (
          <div>
            <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginBottom: 8 }}>
              样本数 {stats.source_works_count ?? "—"}
              · 首次突破章节中位数 {stats.first_breakthrough_chapter_median ?? "—"}
              · 反派首次出场章节中位数 {stats.antagonist_first_chapter_median ?? "—"}
              · 首次打脸章节中位数 {stats.first_face_slap_chapter_median ?? "—"}
              · 单作平均新词数 {stats.avg_neologisms_per_work?.toFixed?.(2) ?? "—"}
            </div>
            {/* 各项分布字段 */}
            {([
              ["opening_hook_type_distribution_json",       "开篇钩子类型分布"],
              ["protagonist_cheat_type_distribution_json",  "主角金手指类型分布"],
              ["protagonist_agency_distribution_json",      "主角主动性分布"],
              ["worldview_type_distribution_json",          "世界观类型分布"],
              ["writing_style_distribution_json",           "行文风格分布"],
              ["emotional_tone_distribution_json",          "情绪基调分布"],
              ["info_disclosure_distribution_json",         "信息释放策略分布"],
              ["chapter_word_count_stats_json",             "章节字数分布"],
              ["dialogue_ratio_stats_json",                 "对白占比分布"],
              ["setting_word_ratio_stats_json",             "场景描写占比分布"],
              ["genre_vocabulary_top_json",                 "题材高频词 Top"],
              ["neologism_type_distribution_json",          "新词类型分布"],
              ["chapter_end_hook_type_distribution_json",   "章末钩子类型分布"],
            ] as const).map(([field, label]) => {
              const raw = stats[field];
              if (!raw) return null;
              let parsed: any = raw;
              try { parsed = JSON.parse(raw); } catch { /* leave as string */ }
              return (
                <details key={field} style={{ marginBottom: 6 }}>
                  <summary style={{ fontSize: 11, cursor: "pointer", color: "var(--text-secondary)" }}>
                    {label}
                  </summary>
                  <pre style={{
                    margin: "4px 0 0", padding: 8, fontSize: 10,
                    background: "var(--bg-surface-2)",
                    borderRadius: 4, maxHeight: 200, overflow: "auto",
                    whiteSpace: "pre-wrap", wordBreak: "break-word",
                    fontFamily: "var(--font-mono)",
                  }}>{JSON.stringify(parsed, null, 2)}</pre>
                </details>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}


function OpeningAnalysisPanel({ platform }: { platform: string }) {
  const [stats, setStats] = React.useState<any>(null);
  const [loading, setLoading] = React.useState(false);
  const [open, setOpen] = React.useState(true);

  React.useEffect(() => {
    setLoading(true);
    const qs = platform ? `?platform=${encodeURIComponent(platform)}` : "";
    apiGet<any>(`/api/db/opening_analysis${qs}`)
      .then(setStats)
      .catch(() => setStats(null))
      .finally(() => setLoading(false));
  }, [platform]);

  if (!stats?.available || !stats.first_chapter) {
    return null;
  }
  const fc = stats.first_chapter;
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="card-header" style={{ cursor: "pointer", display: "flex", justifyContent: "space-between", alignItems: "center" }}
           onClick={() => setOpen(o => !o)}>
        <h3 style={{ margin: 0, fontSize: 14 }}>{open ? "▾ " : "▸ "}开篇章节分析</h3>
        <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
          已采集 {stats.novels_with_chapters} 部作品的开篇章节
        </span>
      </div>
      {open && (
        <div className="card-body">
          <div style={{ display: "flex", flexWrap: "wrap", gap: 20, marginBottom: 14 }}>
            {([
              ["已采集作品",    stats.novels_with_chapters ?? 0],
              ["开篇章节总数",  stats.total_chapters ?? 0],
              ["首章平均字数",  fc.avg_words],
              ["首章字数中位数", fc.median_words],
              ["首章字数范围",  `${fc.min_words}–${fc.max_words}`],
            ] as const).map(([label, val]) => (
              <div key={label}>
                <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{label}</div>
                <div style={{ fontSize: 18, fontWeight: 700, color: "var(--accent)" }}>{val}</div>
              </div>
            ))}
          </div>
          <div style={{ fontSize: 12, marginBottom: 6 }}>
            首章字数分布（共 {fc.count} 章）
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {Object.entries(fc.distribution as Record<string, number>).map(([bucket, n]) => {
              const total = fc.count || 1;
              const pct = Math.round(n / total * 100);
              return (
                <div key={bucket} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ width: 84, fontSize: 11, color: "var(--text-secondary)" }}>{bucket}</span>
                  <div style={{ flex: 1, height: 14, background: "var(--bg-surface-2)", borderRadius: 3, overflow: "hidden" }}>
                    <div style={{ width: `${pct}%`, height: "100%", background: "var(--accent)" }} />
                  </div>
                  <span style={{ width: 72, textAlign: "right", fontSize: 11, fontFamily: "var(--font-mono)" }}>{n}（{pct}%）</span>
                </div>
              );
            })}
          </div>

          {/* ── 一键 NLP 分析 ── */}
          <NlpAnalysisSection platform={platform} />
        </div>
      )}
      {loading && <div style={{ padding: 8, fontSize: 11, color: "var(--text-tertiary)" }}>加载中...</div>}
    </div>
  );
}


/**
 * NlpAnalysisSection — runs heuristic NLP over the already-crawled
 * opening chapters. Dual-mode caching:
 *  - Lazy trigger: results auto-load only when the crawler DB has
 *    changed (sessionStorage cache keyed by mtime + size + platform).
 *  - Manual refresh: a 「重新分析」 button forces a re-fetch even when
 *    the cache is fresh.
 * No backend job, no LLM call — the endpoint is pure regex/counting,
 * so triggering is cheap.
 */
function NlpAnalysisSection({ platform }: { platform: string }) {
  type NlpData = {
    available: boolean; reason?: string; sample_count?: number;
    dialogue_ratio?: { mean: number; distribution: Record<string, number> };
    sentence_length?: { mean: number; distribution: Record<string, number> };
    first_sentence_types?: { label: string; count: number }[];
    end_hook_types?: { label: string; count: number }[];
    word_count_summary?: { mean: number; min: number; max: number };
  };
  const [data, setData] = React.useState<NlpData | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [err, setErr] = React.useState("");
  const [hasRun, setHasRun] = React.useState(false);
  const [fromCache, setFromCache] = React.useState(false);

  const cacheKey = `inkoctobot_nlp_opening_v1_${platform || "all"}`;

  const runAnalysis = React.useCallback(async (force: boolean) => {
    setLoading(true);
    setErr("");
    try {
      // Check db-mtime first; if cache is fresh and !force, reuse.
      let mtimeKey = "";
      try {
        const m = await apiGet<{ mtime: number; size?: number; exists: boolean }>(
          "/api/db/db-mtime",
        );
        mtimeKey = m.exists ? `${m.mtime}.${m.size ?? 0}` : "";
      } catch { /* fall through to live fetch */ }

      if (!force && mtimeKey) {
        const raw = sessionStorage.getItem(cacheKey);
        if (raw) {
          try {
            const c = JSON.parse(raw);
            if (c.mtimeKey === mtimeKey && c.data) {
              setData(c.data);
              setHasRun(true);
              setFromCache(true);
              return;
            }
          } catch { /* ignore corrupt cache */ }
        }
      }

      const qs = platform ? `?platform=${encodeURIComponent(platform)}` : "";
      const r = await apiGet<NlpData>(`/api/db/opening_nlp_analysis${qs}`);
      setData(r);
      setHasRun(true);
      setFromCache(false);
      if (mtimeKey && r?.available) {
        try {
          sessionStorage.setItem(cacheKey, JSON.stringify({ mtimeKey, data: r }));
        } catch { /* ignore quota */ }
      }
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, [cacheKey, platform]);

  // Lazy trigger on mount / platform change — only fires the network
  // call when the cache is stale. Cached results show immediately.
  React.useEffect(() => {
    runAnalysis(false);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [platform]);

  return (
    <div style={{
      marginTop: 20, padding: 14,
      background: "var(--bg-surface-2)",
      borderRadius: 6,
      borderLeft: "3px solid var(--jade)",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
        <div>
          <strong style={{ fontSize: 13 }}>开篇章节 NLP 分析</strong>
          <span style={{ fontSize: 11, color: "var(--text-tertiary)", marginLeft: 8 }}>
            对白比 · 句长 · 首句类型 · 章末钩子（启发式分析，无需大模型）
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {fromCache && hasRun && (
            <span style={{ fontSize: 10, color: "var(--text-tertiary)" }}>
              来自缓存（数据库未变化）
            </span>
          )}
          <button
            className="btn"
            onClick={() => runAnalysis(true)}
            disabled={loading}
            style={{ fontSize: 12, padding: "5px 14px", borderRadius: 14 }}
          >
            {loading ? "分析中..." : (hasRun ? "重新分析" : "一键启动分析")}
          </button>
        </div>
      </div>
      {err && (
        <div style={{ fontSize: 12, color: "var(--danger)", marginBottom: 8 }}>
          分析失败：{err}
        </div>
      )}
      {data && !data.available && (
        <div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
          暂无可分析数据{data.reason ? `（${data.reason}）` : ""}。
        </div>
      )}
      {data && data.available && (
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
          <NlpHistCard
            title={`对白占比分布（均值 ${data.dialogue_ratio ? Math.round(data.dialogue_ratio.mean * 100) : 0}%）`}
            entries={Object.entries(data.dialogue_ratio?.distribution || {})}
            accent="var(--accent)"
          />
          <NlpHistCard
            title={`平均句长分布（均值 ${data.sentence_length?.mean.toFixed(1) || 0} 字）`}
            entries={Object.entries(data.sentence_length?.distribution || {})}
            accent="var(--gold)"
          />
          <NlpHistCard
            title="首句类型分布"
            entries={(data.first_sentence_types || []).map(d => [d.label, d.count] as [string, number])}
            accent="var(--jade)"
          />
          <NlpHistCard
            title="章末钩子类型分布"
            entries={(data.end_hook_types || []).map(d => [d.label, d.count] as [string, number])}
            accent="var(--indigo)"
          />
          <div style={{ gridColumn: "1 / -1", fontSize: 11, color: "var(--text-tertiary)" }}>
            样本数 {data.sample_count} 章
            {data.word_count_summary ? ` · 字数 均值 ${data.word_count_summary.mean} / 最小 ${data.word_count_summary.min} / 最大 ${data.word_count_summary.max}` : ""}
          </div>
        </div>
      )}
    </div>
  );
}


function NlpHistCard({
  title, entries, accent,
}: { title: string; entries: [string, number][]; accent: string }) {
  const total = entries.reduce((s, [, v]) => s + v, 0) || 1;
  return (
    <div style={{
      background: "var(--bg-surface)",
      padding: 10,
      borderRadius: 4,
      border: "1px solid var(--border)",
    }}>
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>{title}</div>
      {entries.length === 0 ? (
        <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>暂无</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {entries.map(([label, n]) => {
            const pct = Math.round((n / total) * 100);
            return (
              <div key={label} style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ width: 96, fontSize: 11, color: "var(--text-secondary)" }}>{label}</span>
                <div style={{ flex: 1, height: 10, background: "var(--bg-surface-2)", borderRadius: 2, overflow: "hidden" }}>
                  <div style={{ width: `${pct}%`, height: "100%", background: accent }} />
                </div>
                <span style={{ width: 56, textAlign: "right", fontSize: 10, fontFamily: "var(--font-mono)" }}>
                  {n}（{pct}%）
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}


function Empty({ msg }: { msg: string }) {
  return (
    <div style={{
      padding: "48px 24px",
      textAlign: "center",
      color: "var(--text-tertiary)",
      fontSize: 13,
      lineHeight: 1.6,
      background: "var(--bg-surface-2)",
      border: "1px dashed var(--border)",
      borderRadius: 8,
    }}>{msg}</div>
  );
}
