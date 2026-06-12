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
import { swrHydrate, swrStore, pollCompute, PollController } from "../api/swr";
import { useToast } from "../components/shared/Toast";
import UniversalLLMDialog from "../components/shared/UniversalLLMDialog";
import AnalysisDashboardPage from "./AnalysisDashboardPage";
import { tPlatform } from "../i18n";


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
  // 秒开: 平台/任务/档案先用上次会话的快照同步渲染，挂载后台刷新。
  const [platforms, setPlatforms] = useState<PlatformOption[]>(
    () => swrHydrate<PlatformOption[]>("mfe_platforms") || [],
  );
  const [categories, setCategories] = useState<CategoryOption[]>([]);
  const [platform, setPlatform] = useState(
    () => (swrHydrate<PlatformOption[]>("mfe_platforms") || [])[0]?.key || "",
  );
  // 榜单多选 (用户需求): selectedCats 为勾选集合；manual 模式聚焦第一项。
  const [selectedCats, setSelectedCats] = useState<string[]>([]);
  const category = selectedCats[0] || "";
  const [launching, setLaunching] = useState(false);

  const [jobs, setJobs] = useState<Job[]>(
    () => swrHydrate<Job[]>("mfe_jobs") || [],
  );
  const [profiles, setProfiles] = useState<PlatformProfile[]>(
    () => swrHydrate<PlatformProfile[]>("mfe_profiles") || [],
  );

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
      swrStore("mfe_platforms", r.platforms || []);
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

  // 竞态防护：仅接受最后一次请求的结果，避免早先发出的全平台请求
  // 晚到后把当前平台的榜单覆盖成混合列表。
  const catReqToken = React.useRef(0);
  const loadCategories = useCallback(async (pl: string) => {
    const token = ++catReqToken.current;
    // 秒开: 先用该平台上次的榜单列表渲染，后台拉新覆盖。
    const cachedCats = swrHydrate<CategoryOption[]>(`mfe_cats_${pl}`);
    if (cachedCats && cachedCats.length > 0) {
      setCategories(cachedCats);
      setSelectedCats(prev => {
        const valid = prev.filter(k => cachedCats.some(c => c.key === k));
        return valid.length > 0 ? valid : [cachedCats[0].key];
      });
    }
    try {
      const params = pl ? `?platform=${encodeURIComponent(pl)}` : "";
      const r = await apiGet<{ categories: CategoryOption[] }>(
        `/api/market-extractor/categories${params}`,
      );
      if (token !== catReqToken.current) return;   // stale response
      setCategories(r.categories || []);
      swrStore(`mfe_cats_${pl}`, r.categories || []);
      setSelectedCats(prev => {
        const valid = prev.filter(k => (r.categories || []).some(c => c.key === k));
        if (valid.length > 0) return valid;
        return r.categories && r.categories.length > 0 ? [r.categories[0].key] : [];
      });
    } catch (e: any) {
      toast(`加载榜单失败: ${e.message}`, "error");
    }
  }, [category, toast]);

  const refreshJobs = useCallback(async () => {
    try {
      const r = await apiGet<{ jobs: Job[] }>("/api/market-extractor/jobs?limit=50");
      setJobs(r.jobs || []);
      swrStore("mfe_jobs", r.jobs || []);
    } catch (e: any) {
      toast(`加载任务失败: ${e.message}`, "error");
    }
  }, [toast]);

  const refreshProfiles = useCallback(async () => {
    try {
      const r = await apiGet<{ profiles?: PlatformProfile[]; items?: PlatformProfile[] }>(
        "/api/platform-profiles",
      );
      const list = (r.profiles || r.items || []) as PlatformProfile[];
      setProfiles(list);
      swrStore("mfe_profiles", list);
    } catch (e: any) {
      toast(`加载平台档案失败: ${e.message}`, "error");
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
    const active = jobs.some(j => isActive(j.state));
    if (!active) return;
    const id = window.setInterval(refreshJobs, 5000);
    return () => window.clearInterval(id);
  }, [jobs, refreshJobs]);

  // ── actions ──

  const launchJob = useCallback(async () => {
    if (!platform || selectedCats.length === 0) {
      toast("请先选择平台 + 至少一个榜单", "error");
      return;
    }
    if (!window.confirm(
      `准备启动 API 提取任务：\n平台: ${platform}\n榜单 (${selectedCats.length} 个): ${selectedCats.join("、")}\n\n` +
      `每个榜单一个任务，会调用大模型抽取代表作的章节特征与新词，` +
      `更新对应的平台风格档案。可能耗时数分钟。继续？`,
    )) return;
    setLaunching(true);
    let ok = 0;
    const failed: string[] = [];
    for (const cat of selectedCats) {
      try {
        await apiPost<{ job_id: string; state: string }>(
          "/api/market-extractor/jobs",
          { platform, category: cat },
        );
        ok += 1;
      } catch (e: any) {
        failed.push(`${cat}: ${e.message}`);
      }
    }
    if (ok > 0) toast(`已启动 ${ok}/${selectedCats.length} 个提取任务`, "success");
    if (failed.length > 0) toast(`未能启动: ${failed.join("; ")}`, "error");
    await refreshJobs();
    setLaunching(false);
  }, [platform, selectedCats, refreshJobs, toast]);

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
    if (selectedCats.length > 1) {
      toast("手动模式一次只处理一个榜单 — 请只保留一个勾选", "error");
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
  }, [platform, category, selectedCats.length, toast]);

  const commitManual = useCallback(async (payload: { text: string }) => {
    const r = await apiPost<{ profile_id: string }>(
      "/api/market-extractor/manual-submit",
      { platform, category, response_raw: payload.text },
    );
    toast(`平台档案已写入 (${r.profile_id})`, "success");
    refreshProfiles();
  }, [platform, category, refreshProfiles, toast]);

  const platformProfiles = useMemo(() => {
    return profiles.filter(p =>
      (!platform || p.platform === platform)
      && (!category || p.category === category),
    );
  }, [profiles, platform, category]);

  const refreshAll = useCallback(async () => {
    setLoading(true);
    await Promise.all([refreshJobs(), refreshProfiles()]);
    setLoading(false);
  }, [refreshJobs, refreshProfiles]);

  const SELECTION_OK = !!platform && selectedCats.length > 0;

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
              <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
                已选 {selectedCats.length} / {categories.length} 个
              </span>
              <div style={{ marginLeft: "auto", display: "flex", gap: 6 }}>
                <button className="btn" style={{ fontSize: 10, padding: "2px 10px" }}
                  disabled={categories.length === 0}
                  onClick={() => setSelectedCats(categories.map(c => c.key))}>
                  全选本平台
                </button>
                <button className="btn" style={{ fontSize: 10, padding: "2px 10px" }}
                  disabled={selectedCats.length === 0}
                  onClick={() => setSelectedCats([])}>
                  清空
                </button>
              </div>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, maxHeight: 200, overflowY: "auto" }}>
              {categories.length === 0 ? (
                <span style={{ fontSize: 12, color: "var(--text-tertiary)", padding: 8 }}>
                  {platform ? "该平台还没有榜单数据" : "先选定平台"}
                </span>
              ) : categories.map(c => (
                <button
                  key={c.key}
                  className={selectedCats.includes(c.key) ? "btn-primary" : "btn"}
                  style={{ fontSize: 11, padding: "4px 12px", borderRadius: 16 }}
                  onClick={() => setSelectedCats(prev =>
                    prev.includes(c.key)
                      ? prev.filter(k => k !== c.key)
                      : [...prev, c.key])}
                  title={`${c.list_count} 个榜单 · 点击切换勾选`}
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
                <strong>{selectedCats.join("、")}</strong>
                <span style={{ color: "var(--text-tertiary)", marginLeft: 6 }}>
                  （{selectedCats.length} 个榜单{selectedCats.length > 1 ? "，逐个建任务" : ""}）
                </span>
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

        {/* RIGHT — 提取记录 (just one section, no tabs) */}
        <div className="card" style={{ padding: 0, overflow: "hidden" }}>
          <div style={{
            padding: "10px 14px",
            borderBottom: "1px solid var(--border)",
            background: "var(--bg-surface-2)",
            display: "flex", alignItems: "baseline", justifyContent: "space-between",
          }}>
            <h3 style={{ margin: 0, fontSize: 13 }}>
              提取记录
              <span style={{
                display: "inline-block", minWidth: 18, padding: "1px 7px",
                borderRadius: 9, background: "var(--accent)", color: "white",
                fontSize: 10, fontWeight: 600, marginLeft: 8,
              }}>{jobs.length}</span>
            </h3>
            <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
              提醒：以下任务的「结束」时间即对应平台档案的最近更新时间
            </span>
          </div>
          <div style={{ padding: 14, minHeight: 420, background: "var(--bg-surface)" }}>
            <JobsTab jobs={jobs} onCancel={cancelJob} onDelete={deleteJob} />
          </div>
        </div>
      </div>

      {/* 开篇章节NLP分析 — spec 2.1.3.2 全部市场信息 + 开篇 NLP 维度。
          懒加载：进页只读缓存（秒开），保留上次运行结果；数据更新时
          提示用户重新分析。 */}
      <OpeningNlpAnalysisSection platform={platform} />

      {/* 平台风格档案 — 独立 section */}
      <PlatformProfileSection profiles={platformProfiles} platform={platform} category={category} />

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


/**
 * PlatformProfileSection — full-width card under 开篇章节分析 that
 * shows the saved platform-style profile (was 平台风格档案 tab) and
 * an expandable header so the user can see when it was last updated.
 */
function PlatformProfileSection({
  profiles, platform, category,
}: { profiles: PlatformProfile[]; platform: string; category: string }) {
  const top = profiles[0] || null;
  return (
    <div className="card" style={{ marginTop: 16, borderTop: "3px solid var(--jade)" }}>
      <div className="card-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h3 style={{ margin: 0, fontSize: 14 }}>平台风格档案</h3>
        <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
          {top
            ? `${tPlatform(top.platform)} · ${top.category || "—"} · 更新于 ${top.extraction_completed_at || "—"}`
            : (platform && category
                ? `${tPlatform(platform)} × ${category} 暂无档案`
                : "请在左上选定平台与榜单")}
        </span>
      </div>
      <div className="card-body" style={{ padding: 14 }}>
        {profiles.length === 0 ? (
          <Empty msg={
            platform && category
              ? `(${tPlatform(platform)}×${category}) 暂无平台档案。先用上方的「使用大模型 API 提取」或「使用大模型网页版提取」生成档案。`
              : "请在上方选定平台与榜单，再启动一次提取以生成档案。"
          } />
        ) : (
          <ProfilesTab profiles={profiles} />
        )}
      </div>
    </div>
  );
}


/**
 * OpeningNlpAnalysisSection — 开篇章节NLP分析 (replaces the old
 * 开篇章节分析 section). One card carrying ALL spec-2.1.3.2 数据维度:
 *
 *  市场信息: 数量 + 数量变化趋势、热度 + 热度变化趋势、市场份额 +
 *  份额变化趋势（类目级 + 标签级）、开书机会、标签共现 — read from
 *  the cached /api/analysis/run result.
 *
 *  开篇章节NLP分析: 首章/章均/章中位字数、平均句长、分类型标点密度、
 *  高频词、生造词Step1 + 启发式分布（对白比/句长/首句类型/章末钩子）
 *  — read from the cached /api/db/opening_nlp_analysis result.
 *
 * 懒加载 (spec UI设计·机制4): mount 只发 cached_only 读（毫秒级），
 * 展示上一次运行完的结果；从不在挂载时启动重计算。爬虫库更新后
 * 服务器返回 stale 标记 → 显示「数据已更新」提醒，由用户点
 * 重新分析；重算在后台单飞线程进行，页面立即可用。
 */
function OpeningNlpAnalysisSection({ platform }: { platform: string }) {
  type NlpPayload = {
    available: boolean; reason?: string; sample_count?: number;
    spec_stats?: any;
    dialogue_ratio?: { mean: number; distribution: Record<string, number> };
    sentence_length?: { mean: number; distribution: Record<string, number> };
    first_sentence_types?: { label: string; count: number }[];
    end_hook_types?: { label: string; count: number }[];
    word_count_summary?: { mean: number; min: number; max: number };
  };
  type MarketPayload = {
    empty?: boolean; start_date?: string; end_date?: string;
    cat_rollup?: any[]; tag_rollup?: any[];
    opportunities?: any[]; pairs?: any[];
  };

  // /api/analysis/run only understands qidian / fanqie / both.
  const marketPlatform = platform === "qidian" || platform === "fanqie" ? platform : "both";
  const nlpKey = `mfe_nlp_${platform || "all"}`;
  const marketKey = `mfe_market_${marketPlatform}`;

  const [nlp, setNlp] = React.useState<NlpPayload | null>(null);
  const [market, setMarket] = React.useState<MarketPayload | null>(null);
  const [stale, setStale] = React.useState(false);
  // 两个数据源（NLP / 市场信息）各自的后台计算状态 — 共用一个标志会
  // 在先完成的一方把「分析中」提前清掉。
  const [nlpBusy, setNlpBusy] = React.useState(false);
  const [marketBusy, setMarketBusy] = React.useState(false);
  const computing = nlpBusy || marketBusy;
  const [everRun, setEverRun] = React.useState(true);   // false → 显示「尚未运行」
  const [err, setErr] = React.useState("");
  const pollsRef = React.useRef<PollController[]>([]);

  const stopPolls = () => {
    pollsRef.current.forEach(p => p.cancel());
    pollsRef.current = [];
  };

  const load = React.useCallback((refresh: boolean) => {
    stopPolls();
    setErr("");
    if (refresh) { setNlpBusy(true); setMarketBusy(true); setEverRun(true); }
    let emptyCount = 0;
    const nq = new URLSearchParams();
    if (platform) nq.set("platform", platform);
    if (refresh) nq.set("refresh", "true"); else nq.set("cached_only", "true");
    pollsRef.current.push(pollCompute<NlpPayload>(
      `/api/db/opening_nlp_analysis?${nq}`,
      {
        onReady: (payload, env) => {
          setNlp(payload); swrStore(nlpKey, payload);
          setStale(s => s || !!env.stale);
          setNlpBusy(!!env.computing);
          setEverRun(true);
        },
        onEmpty: () => { setNlpBusy(false); emptyCount += 1; if (emptyCount >= 2) setEverRun(false); },
        onComputing: () => setNlpBusy(true),
        onError: (m) => { setErr(m); setNlpBusy(false); },
      },
    ));
    const mq = new URLSearchParams({ platform: marketPlatform });
    if (refresh) mq.set("refresh", "true"); else mq.set("cached_only", "true");
    pollsRef.current.push(pollCompute<MarketPayload>(
      `/api/analysis/run?${mq}`,
      {
        onReady: (payload, env) => {
          setMarket(payload); swrStore(marketKey, payload);
          setStale(s => s || !!env.stale);
          setMarketBusy(!!env.computing);
          setEverRun(true);
        },
        onEmpty: () => { setMarketBusy(false); emptyCount += 1; if (emptyCount >= 2) setEverRun(false); },
        onComputing: () => setMarketBusy(true),
        onError: (m) => { setErr(m); setMarketBusy(false); },
      },
    ));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [platform, marketPlatform, nlpKey, marketKey]);

  React.useEffect(() => {
    // 秒开: 同步水合上次会话的结果，再用 cached_only 读校验。
    setNlp(swrHydrate<NlpPayload>(nlpKey));
    setMarket(swrHydrate<MarketPayload>(marketKey));
    setStale(false);
    load(false);
    return stopPolls;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [platform]);

  const hasAny = !!(nlp?.available || (market && !market.empty));
  const spec = nlp?.spec_stats;

  const slope = (v: number | null | undefined) => {
    if (v == null || isNaN(v)) return <span style={{ color: "var(--text-disabled)" }}>—</span>;
    const up = v > 0.0001, down = v < -0.0001;
    const color = up ? "var(--jade)" : down ? "var(--accent)" : "var(--text-tertiary)";
    return (
      <span className="font-mono" style={{ color, fontSize: 11 }}>
        {up ? "↑" : down ? "↓" : "→"} {Math.abs(v).toFixed(4)}
      </span>
    );
  };
  const num = (v: number | null | undefined, d = 0) =>
    v == null || isNaN(v as number) ? "—" : Number(v).toFixed(d);

  const catRows = (market?.cat_rollup || []).slice(0, 10);
  const tagRows = (market?.tag_rollup || []).slice(0, 10);
  const oppRows = (market?.opportunities || []).slice(0, 8);
  const pairRows = (market?.pairs || []).slice(0, 12);

  return (
    <div className="card" style={{ marginTop: 16, borderTop: "3px solid var(--indigo)" }}>
      <div className="card-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
        <div>
          <h3 style={{ margin: 0, fontSize: 14 }}>开篇章节NLP分析</h3>
          <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
            市场信息（数量/热度/份额及趋势 · 开书机会 · 标签共现）+ 开篇 NLP 维度 · 懒加载，保留上次结果
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {computing && (
            <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>后台分析中，完成后自动更新…</span>
          )}
          <button
            className="btn"
            onClick={() => load(true)}
            disabled={computing}
            style={{ fontSize: 12, padding: "5px 14px", borderRadius: 14 }}
          >
            {computing ? "分析中..." : (hasAny ? "重新分析" : "运行分析")}
          </button>
        </div>
      </div>
      <div className="card-body">
        {stale && !computing && (
          <div style={{
            padding: "8px 12px", marginBottom: 12, borderRadius: 6,
            background: "var(--gold-subtle, var(--bg-surface-2))",
            borderLeft: "3px solid var(--gold)",
            fontSize: 12, color: "var(--text-secondary)",
            display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
          }}>
            市场数据已更新 — 以下为上一次分析的结果。
            <button className="btn" style={{ fontSize: 11, padding: "3px 12px" }} onClick={() => load(true)}>
              重新分析
            </button>
          </div>
        )}
        {err && (
          <div style={{ fontSize: 12, color: "var(--danger)", marginBottom: 8 }}>分析失败：{err}</div>
        )}
        {!hasAny && !computing && (
          <Empty msg={everRun
            ? "暂无可分析数据 — 请确认市场数据库路径（Settings → 市场数据库）。"
            : "尚未运行分析。点击右上「运行分析」启动 — 首次需要在后台计算，完成后结果持久保留，下次进入页面即时展示。"} />
        )}
        {!hasAny && computing && (
          <Empty msg="正在后台计算市场信息与开篇 NLP 统计 — 可以离开此页面，结果会保留。" />
        )}

        {market && !market.empty && (
          <>
            <div style={{ fontSize: 13, fontWeight: 700, margin: "4px 0 8px" }}>
              市场信息
              <span style={{ fontSize: 11, fontWeight: 400, color: "var(--text-tertiary)", marginLeft: 8 }}>
                {market.start_date} ~ {market.end_date} · {tPlatform(marketPlatform)}
              </span>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginBottom: 14 }}>
              <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 6, padding: 10, overflowX: "auto" }}>
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>类目：数量 / 热度 / 份额 及变化趋势</div>
                {catRows.length === 0 ? <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>暂无</div> : (
                  <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
                    <thead><tr style={{ color: "var(--text-tertiary)", textAlign: "left" }}>
                      <th style={{ padding: "3px 6px" }}>类目</th>
                      <th style={{ padding: "3px 6px", textAlign: "right" }}>数量</th>
                      <th style={{ padding: "3px 6px", textAlign: "right" }}>数量趋势</th>
                      <th style={{ padding: "3px 6px", textAlign: "right" }}>热度</th>
                      <th style={{ padding: "3px 6px", textAlign: "right" }}>热度趋势</th>
                      <th style={{ padding: "3px 6px", textAlign: "right" }}>份额</th>
                      <th style={{ padding: "3px 6px", textAlign: "right" }}>份额趋势</th>
                    </tr></thead>
                    <tbody>
                      {catRows.map((r: any, i: number) => (
                        <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                          <td style={{ padding: "3px 6px" }}>{r.category}{r.platform && marketPlatform === "both" ? ` (${tPlatform(r.platform)})` : ""}</td>
                          <td style={{ padding: "3px 6px", textAlign: "right" }} className="font-mono">{num(r.latest_count)}</td>
                          <td style={{ padding: "3px 6px", textAlign: "right" }}>{slope(r.count_slope)}</td>
                          <td style={{ padding: "3px 6px", textAlign: "right" }} className="font-mono">{num(r.avg_heat)}</td>
                          <td style={{ padding: "3px 6px", textAlign: "right" }}>{slope(r.heat_slope)}</td>
                          <td style={{ padding: "3px 6px", textAlign: "right" }} className="font-mono">{num(r.latest_share, 3)}</td>
                          <td style={{ padding: "3px 6px", textAlign: "right" }}>{slope(r.share_slope)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
              <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 6, padding: 10, overflowX: "auto" }}>
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>标签：数量 / 热度 / 份额 及变化趋势</div>
                {tagRows.length === 0 ? <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>暂无</div> : (
                  <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
                    <thead><tr style={{ color: "var(--text-tertiary)", textAlign: "left" }}>
                      <th style={{ padding: "3px 6px" }}>标签</th>
                      <th style={{ padding: "3px 6px", textAlign: "right" }}>数量</th>
                      <th style={{ padding: "3px 6px", textAlign: "right" }}>热度</th>
                      <th style={{ padding: "3px 6px", textAlign: "right" }}>热度趋势</th>
                      <th style={{ padding: "3px 6px", textAlign: "right" }}>份额</th>
                      <th style={{ padding: "3px 6px", textAlign: "right" }}>份额趋势</th>
                    </tr></thead>
                    <tbody>
                      {tagRows.map((r: any, i: number) => (
                        <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                          <td style={{ padding: "3px 6px" }}>{r.tag}</td>
                          <td style={{ padding: "3px 6px", textAlign: "right" }} className="font-mono">{num(r.latest_count)}</td>
                          <td style={{ padding: "3px 6px", textAlign: "right" }} className="font-mono">{num(r.avg_heat)}</td>
                          <td style={{ padding: "3px 6px", textAlign: "right" }}>{slope(r.heat_slope)}</td>
                          <td style={{ padding: "3px 6px", textAlign: "right" }} className="font-mono">{num(r.latest_share, 3)}</td>
                          <td style={{ padding: "3px 6px", textAlign: "right" }}>{slope(r.share_slope)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
              <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 6, padding: 10 }}>
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>开书机会（份额上升 × 热度上升 × 新书占比）</div>
                {oppRows.length === 0 ? <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>暂无</div> : (
                  <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    {oppRows.map((o: any, i: number) => (
                      <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11 }}>
                        <span className="tag category" style={{ fontSize: 10 }}>{o.category}</span>
                        <span className="tag" style={{ fontSize: 10 }}>{o.tag}</span>
                        <span style={{ marginLeft: "auto", color: "var(--text-tertiary)" }}>
                          新书占比 {o.new_entry_ratio != null ? `${Math.round(o.new_entry_ratio * 100)}%` : "—"}
                        </span>
                        <span className="font-mono" style={{ color: "var(--jade)", fontWeight: 600 }}>
                          {num(o.opportunity_score, 3)}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 6, padding: 10 }}>
                <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>标签共现（高频组合）</div>
                {pairRows.length === 0 ? <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>暂无</div> : (
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {pairRows.map((p: any, i: number) => (
                      <span key={i} className="tag" style={{ fontSize: 10, padding: "2px 8px" }}>
                        {p.tag_a} × {p.tag_b}
                        <span style={{ color: "var(--text-tertiary)", marginLeft: 4 }}>{p.count}</span>
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </>
        )}

        {nlp?.available && (
          <>
            <div style={{ fontSize: 13, fontWeight: 700, margin: "4px 0 8px" }}>
              开篇章节 NLP 维度
              <span style={{ fontSize: 11, fontWeight: 400, color: "var(--text-tertiary)", marginLeft: 8 }}>
                样本 {nlp.sample_count} 章 · 启发式统计，无需大模型
              </span>
            </div>
            {spec?.available && (
              <div style={{ border: "1px solid var(--border)", borderRadius: 8, padding: "10px 12px", marginBottom: 12, background: "var(--bg-surface)" }}>
                <div style={{ fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.9 }}>
                  <div>
                    首章字数均值 <strong>{spec.first_chapter_words_avg ?? "—"}</strong>
                    <span style={{ margin: "0 8px", color: "var(--text-tertiary)" }}>·</span>
                    章平均字数 <strong>{spec.chapter_words_avg}</strong>
                    <span style={{ margin: "0 8px", color: "var(--text-tertiary)" }}>·</span>
                    章中位字数 <strong>{spec.chapter_words_median}</strong>
                    <span style={{ margin: "0 8px", color: "var(--text-tertiary)" }}>·</span>
                    平均句长 <strong>{spec.avg_sentence_length ?? "—"} 字</strong>
                    {nlp.word_count_summary && (
                      <>
                        <span style={{ margin: "0 8px", color: "var(--text-tertiary)" }}>·</span>
                        首章字数范围 <strong>{nlp.word_count_summary.min}–{nlp.word_count_summary.max}</strong>
                      </>
                    )}
                  </div>
                  <div>
                    标点密度（次/千字）：
                    {Object.entries(spec.punctuation_density_per_1k || {}).map(([k, v]) => (
                      <span key={k} style={{ marginRight: 10 }}>{k} <strong>{String(v)}</strong></span>
                    ))}
                  </div>
                  {(spec.top_words || []).length > 0 && (
                    <div>
                      高频词：
                      {(spec.top_words || []).slice(0, 15).map((w: any) => (
                        <span key={w.word} className="tag" style={{ fontSize: 10, padding: "0 6px", marginRight: 4 }}>
                          {w.word} {w.count}
                        </span>
                      ))}
                    </div>
                  )}
                  {(spec.neologism_step1 || []).length > 0 && (
                    <div>
                      生造词 Step1 候选（频率+凝合度初筛）：
                      {(spec.neologism_step1 || []).slice(0, 12).map((n: any) => (
                        <span key={n.term} className="tag" style={{ fontSize: 10, padding: "0 6px", marginRight: 4, color: "var(--gold)" }}>
                          {n.term} {n.count}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>
            )}
            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
              <NlpHistCard
                title={`对白占比分布（均值 ${nlp.dialogue_ratio ? Math.round(nlp.dialogue_ratio.mean * 100) : 0}%）`}
                entries={Object.entries(nlp.dialogue_ratio?.distribution || {})}
                accent="var(--accent)"
              />
              <NlpHistCard
                title={`平均句长分布（均值 ${nlp.sentence_length?.mean.toFixed(1) || 0} 字）`}
                entries={Object.entries(nlp.sentence_length?.distribution || {})}
                accent="var(--gold)"
              />
              <NlpHistCard
                title="首句类型分布"
                entries={(nlp.first_sentence_types || []).map(d => [d.label, d.count] as [string, number])}
                accent="var(--jade)"
              />
              <NlpHistCard
                title="章末钩子类型分布"
                entries={(nlp.end_hook_types || []).map(d => [d.label, d.count] as [string, number])}
                accent="var(--indigo)"
              />
            </div>
          </>
        )}
      </div>
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
