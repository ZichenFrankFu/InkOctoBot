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
 *  - Also folds in the AI 总结开篇技巧 surface that used to live in
 *    Rankings: a "AI 总结开篇技巧" panel below the launch console.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiGet, apiPost } from "../api/client";
import { useToast } from "../components/shared/Toast";
import UniversalLLMDialog from "../components/shared/UniversalLLMDialog";


type ResultTab = "jobs" | "profiles" | "works" | "preview";


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
  const [loading, setLoading] = useState(false);

  // Manual-mode dialog
  const [manualOpen, setManualOpen] = useState(false);
  const [manualPrompt, setManualPrompt] = useState("");

  // AI opening summary (moved from RankingsPage)
  const [openingPrompt, setOpeningPrompt] = useState("");
  const [openingDialogOpen, setOpeningDialogOpen] = useState(false);
  const [openingSummary, setOpeningSummary] = useState("");

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
      toast(`加载 profile 失败: ${e.message}`, "error");
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
      `会调用 LLM 抽取代表作的 chapter features 与 neologisms，` +
      `更新对应的 platform profile。可能耗时数分钟。继续？`,
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
    toast(`Profile 已写入 (${r.profile_id})`, "success");
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

  const runOpeningSummary = useCallback(async () => {
    try {
      const r = await apiPost<{ prompt: string }>(
        "/api/db/opening_ai_summary",
        { platform: platform || undefined, prompt_only: true },
      );
      setOpeningPrompt(r.prompt || "");
      setOpeningDialogOpen(true);
    } catch (e: any) {
      toast(`无法生成开篇分析 prompt: ${e.message}`, "error");
    }
  }, [platform, toast]);

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

  return (
    <div className="page-container" style={{ padding: "16px 20px", maxWidth: 1400, margin: "0 auto" }}>
      <div className="page-header" style={{ paddingBottom: 12 }}>
        <div className="page-header-row">
          <div>
            <h2>市场特征提取</h2>
            <p>选平台 + 榜单 → API 启动 / 手动模式 → 看任务进度、代表作提取、平台 profile</p>
          </div>
          <button className="btn" onClick={refreshAll} disabled={loading}>
            {loading ? "刷新中..." : "刷新"}
          </button>
        </div>
      </div>

      <div style={{
        display: "grid",
        gridTemplateColumns: "340px 1fr",
        gap: 16,
        alignItems: "start",
      }}>
        {/* LEFT — Launch panel */}
        <div className="card" style={{ padding: 14 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>启动新任务</h3>

          <div style={{ marginTop: 12 }}>
            <label style={{ fontSize: 12, color: "var(--text-secondary)" }}>
              平台 ({platforms.length}) — 自动从市场库读取
            </label>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4, maxHeight: 96, overflowY: "auto" }}>
              {platforms.length === 0 ? (
                <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
                  未读到平台。请先在设置页配置市场数据库路径。
                </span>
              ) : platforms.map(p => (
                <button
                  key={p.key}
                  className={platform === p.key ? "btn-primary" : "btn"}
                  style={{ fontSize: 11, padding: "3px 10px" }}
                  onClick={() => setPlatform(p.key)}
                  title={`${p.book_count} 本作品`}
                >{p.label}</button>
              ))}
            </div>
          </div>

          <div style={{ marginTop: 14 }}>
            <label style={{ fontSize: 12, color: "var(--text-secondary)" }}>
              榜单 / 类别 ({categories.length})
            </label>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4, maxHeight: 140, overflowY: "auto" }}>
              {categories.length === 0 ? (
                <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
                  选定平台后这里会出现该平台的所有 rank list。
                </span>
              ) : categories.map(c => (
                <button
                  key={c.key}
                  className={category === c.key ? "btn-primary" : "btn"}
                  style={{ fontSize: 11, padding: "3px 10px" }}
                  onClick={() => setCategory(c.key)}
                  title={`${c.list_count} 个榜单`}
                >{c.label}</button>
              ))}
            </div>
          </div>

          <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 16 }}>
            <button
              className="btn-primary"
              onClick={launchJob}
              disabled={launching || !platform || !category}
              style={{ width: "100%", padding: "8px 0" }}
            >
              {launching ? "启动中..." : "API 模式: 启动 LLM 提取"}
            </button>
            <button
              className="btn"
              onClick={startManualMode}
              disabled={!platform || !category}
              style={{ width: "100%", padding: "8px 0" }}
            >
              手动模式: 复制 prompt 跑网页 LLM
            </button>
          </div>

          <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--border)" }}>
            <div style={{ fontSize: 11, color: "var(--text-tertiary)", lineHeight: 1.55 }}>
              API 模式跑完整的 5 阶段管道（代表作选择 → 章节抓取 → NLP/LLM 提取 → 综合写 platform_directive → 入库）。
              运行中可点「取消」让管道在下一个阶段 checkpoint 停下。
              手动模式只生成一份综合 prompt，用网页 LLM 跑完后粘回 → 直接写入 profile。
            </div>
          </div>
        </div>

        {/* RIGHT — Results tabs */}
        <div className="card" style={{ padding: 0 }}>
          <div style={{
            display: "flex", gap: 0,
            borderBottom: "1px solid var(--border)",
            padding: "0 12px", paddingTop: 8,
          }}>
            {([
              { key: "jobs" as const,     label: `任务 (${jobs.length})` },
              { key: "profiles" as const, label: `平台 Profile (${platformProfiles.length})` },
              { key: "works" as const,    label: `代表作 (${works.length})` },
              { key: "preview" as const,  label: selectedWork ? `预览: ${selectedWork.title || selectedWork.work_id.slice(0, 6)}` : "预览" },
            ]).map(t => (
              <button
                key={t.key}
                onClick={() => setTab(t.key)}
                disabled={t.key === "preview" && !selectedWork}
                style={{
                  padding: "8px 16px",
                  fontSize: 12,
                  fontWeight: tab === t.key ? 600 : 400,
                  color: tab === t.key ? "var(--accent)" : "var(--text-secondary)",
                  background: "none",
                  border: "none",
                  borderBottom: tab === t.key ? "2px solid var(--accent)" : "2px solid transparent",
                  cursor: t.key === "preview" && !selectedWork ? "not-allowed" : "pointer",
                  marginBottom: -1,
                  opacity: t.key === "preview" && !selectedWork ? 0.4 : 1,
                }}
              >{t.label}</button>
            ))}
          </div>

          <div style={{ padding: 14, minHeight: 380 }}>
            {tab === "jobs" && <JobsTab jobs={jobs} onCancel={cancelJob} />}
            {tab === "profiles" && <ProfilesTab profiles={platformProfiles} />}
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

      {/* AI 总结开篇技巧 — moved from RankingsPage */}
      <div className="card" style={{ marginTop: 16 }}>
        <div className="card-header">
          <h3 style={{ margin: 0, fontSize: 14 }}>AI 总结开篇技巧（基于已爬取的开篇章节正文）</h3>
        </div>
        <div className="card-body">
          <p style={{ color: "var(--text-tertiary)", fontSize: 12, marginTop: 0 }}>
            汇总当前平台筛选下所有已采集的开篇章节，请 LLM 总结开篇技巧。可用 API 模式也可走手动粘贴。
          </p>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn-primary" onClick={runOpeningSummary}>
              生成 prompt 并打开对话框
            </button>
            {openingSummary && (
              <button className="btn" onClick={() => setOpeningSummary("")}>清空结果</button>
            )}
          </div>
          {openingSummary && (
            <div style={{
              marginTop: 12, padding: 12, background: "var(--bg-surface-2)",
              borderRadius: 4, fontSize: 13, lineHeight: 1.7,
              whiteSpace: "pre-wrap",
            }}>{openingSummary}</div>
          )}
        </div>
      </div>

      {/* Manual-mode dialog */}
      <UniversalLLMDialog
        open={manualOpen}
        onClose={() => setManualOpen(false)}
        title={`手动模式: ${platform} × ${category}`}
        description="复制下方 prompt 到网页 LLM 跑 → 粘回结果 → 提交后入 platform_profile 表"
        prompt={manualPrompt}
        onCommit={commitManual}
        minChars={80}
      />

      {/* AI 开篇总结 dialog */}
      <UniversalLLMDialog
        open={openingDialogOpen}
        onClose={() => setOpeningDialogOpen(false)}
        title="AI 总结开篇技巧"
        description="对当前平台筛选下的开篇章节做综合分析。结果会显示在主面板的「AI 总结」框里。"
        prompt={openingPrompt}
        onCommit={async (p) => {
          setOpeningSummary(p.text);
          toast("已应用 AI 总结结果", "success");
        }}
        minChars={50}
      />
    </div>
  );
}


function JobsTab({ jobs, onCancel }: { jobs: Job[]; onCancel: (id: string) => void }) {
  if (jobs.length === 0) {
    return <Empty msg="还没有任何任务。在左侧选好平台 + 榜单后点「API 模式」或「手动模式」。" />;
  }
  return (
    <table style={{ width: "100%", fontSize: 12 }}>
      <thead>
        <tr style={{ color: "var(--text-tertiary)", textAlign: "left" }}>
          <th style={{ padding: "6px" }}>Job</th>
          <th style={{ padding: "6px" }}>平台</th>
          <th style={{ padding: "6px" }}>榜单</th>
          <th style={{ padding: "6px" }}>状态</th>
          <th style={{ padding: "6px" }}>进度</th>
          <th style={{ padding: "6px" }}>开始</th>
          <th style={{ padding: "6px" }}>结束</th>
          <th style={{ padding: "6px" }}></th>
        </tr>
      </thead>
      <tbody>
        {jobs.map(j => (
          <tr key={j.job_id} style={{ borderTop: "1px solid var(--border)" }}>
            <td style={{ padding: "6px" }}><code style={{ fontSize: 11 }}>{j.job_id.slice(0, 10)}</code></td>
            <td style={{ padding: "6px" }}>{j.platform || "—"}</td>
            <td style={{ padding: "6px" }}>{j.category || "—"}</td>
            <td style={{ padding: "6px", color: STATE_COLOR[j.state || ""] || undefined }}>
              {j.state || "—"}
              {j.progress_phase && j.state?.startsWith("running") && (
                <span style={{ color: "var(--text-tertiary)", marginLeft: 4 }}>
                  ({j.progress_phase})
                </span>
              )}
            </td>
            <td style={{ padding: "6px" }}>{typeof j.progress_pct === "number" ? `${Math.round(j.progress_pct)}%` : "—"}</td>
            <td style={{ padding: "6px" }}>{j.started_at || "—"}</td>
            <td style={{ padding: "6px" }}>{j.completed_at || "—"}</td>
            <td style={{ padding: "6px", textAlign: "right" }}>
              {isActive(j.state) && (
                <button className="btn" style={{ fontSize: 11, padding: "2px 10px" }}
                        onClick={() => onCancel(j.job_id)}>
                  取消
                </button>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}


function ProfilesTab({ profiles }: { profiles: PlatformProfile[] }) {
  if (profiles.length === 0) {
    return <Empty msg="尚无匹配当前平台 / 榜单的 profile。任务跑完后会自动出现。" />;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {profiles.map((p, i) => (
        <div key={p.profile_id || `${p.platform}-${p.category || i}`}
             className="card" style={{ padding: 12, background: "var(--bg-surface-2)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
            <strong>{p.platform} · {p.category || "—"}</strong>
            <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
              {p.confidence_label && <>置信度 {p.confidence_label} · </>}
              样本 {p.source_works_count ?? "—"} · 更新 {p.extraction_completed_at || "—"}
            </span>
          </div>
          {[
            ["综述", p.profile_summary],
            ["风格基线", p.style_baseline],
            ["特征设备", p.signature_devices_description],
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
    return <Empty msg="在「代表作」tab 选一部作品查看提取结果。" />;
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


function Empty({ msg }: { msg: string }) {
  return (
    <div style={{
      padding: 36, textAlign: "center", color: "var(--text-tertiary)", fontSize: 12,
    }}>{msg}</div>
  );
}
