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
import { tPlatform } from "../i18n";


// 基础特征提取 (市场信息 + 开篇章节NLP维度) 在前；高级特征提取
// (代表作选取 → 生造词Step2 + 行文风格七组 LLM 提取) 在后。
type TopTab = "basic" | "advanced";

interface RepCandidate {
  work_id: string;
  source_db_novel_id?: string;
  title: string;
  author: string;
  category?: string;
  total_words?: number;
  tags?: string[];
  rank_score?: number;
  is_holdout?: number;
  excerpt: string;
  has_chapters: boolean;
}


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


// 起点分类体系：大分类（novel_types）与副分类（sub_to_main_map 的键）。
// 起点用「大分类 / 副分类」而非「类目 / 标签」，两者集合互斥，用于去重
// 与正确归类（起点没有番茄式的标签共现）。
const QIDIAN_MAIN_CATS = new Set<string>([
  "玄幻", "奇幻", "武侠", "仙侠", "都市", "现实", "军事", "历史",
  "游戏", "体育", "科幻", "诸天无限", "悬疑", "轻小说", "短篇",
]);
const QIDIAN_SUB_CATS = new Set<string>([
  "东方玄幻", "异世大陆", "高武世界", "王朝争霸",
  "剑与魔法", "史诗奇幻", "神秘幻想", "现代魔法", "历史神话", "另类幻想",
  "传统武侠", "武侠幻想", "国术无双", "古武未来", "武侠同人",
  "修真文明", "幻想修仙", "现代修真", "神话修真", "古典仙侠",
  "都市生活", "娱乐明星", "商战职场", "异术超能", "都市异能", "青春校园",
  "架空历史", "两宋元明", "外国历史", "上古先秦", "秦汉三国", "两晋隋唐",
  "五代十国", "清史民国", "历史传记", "民间传说",
  "战争幻想", "谍战特工", "军旅生涯", "抗战烽火", "军事战争",
  "悬疑侦探", "诡秘悬疑", "探险生存", "奇妙世界", "古今传奇",
  "星际文明", "时空穿梭", "未来世界", "古武机甲", "超级科技", "进化变异", "末世危机",
  "电子竞技", "虚拟网游", "游戏异界", "游戏系统", "游戏主播",
  "体育赛事", "篮球运动", "足球运动",
  "原生幻想", "衍生同人", "搞笑吐槽", "恋爱日常",
]);

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

  const [topTab, setTopTab] = useState<TopTab>("basic");
  const [loading, setLoading] = useState(false);

  // Manual-mode dialog
  const [manualOpen, setManualOpen] = useState(false);
  const [manualPrompt, setManualPrompt] = useState("");

  // 高级特征提取：自动选出的代表作候选 + 用户勾选集合（select/deselect）。
  const [candidates, setCandidates] = useState<RepCandidate[]>([]);
  const [selectedWorkIds, setSelectedWorkIds] = useState<string[]>([]);
  const [candLoading, setCandLoading] = useState(false);
  const candReqToken = React.useRef(0);

  const loadCandidates = useCallback(async (pl: string, cat: string) => {
    if (!pl || !cat) { setCandidates([]); setSelectedWorkIds([]); return; }
    const token = ++candReqToken.current;
    setCandLoading(true);
    try {
      const r = await apiGet<{ candidates: RepCandidate[] }>(
        `/api/market-extractor/representative-candidates?platform=${encodeURIComponent(pl)}&category=${encodeURIComponent(cat)}`,
      );
      if (token !== candReqToken.current) return;
      const list = r.candidates || [];
      setCandidates(list);
      // Default: all selected.
      setSelectedWorkIds(list.map(c => c.work_id));
    } catch (e: any) {
      if (token === candReqToken.current) {
        toast(`加载代表作候选失败: ${e.message}`, "error");
        setCandidates([]); setSelectedWorkIds([]);
      }
    } finally {
      if (token === candReqToken.current) setCandLoading(false);
    }
  }, [toast]);


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

  // 高级特征提取：平台 / 首个勾选榜单变化时，拉取自动选出的代表作候选。
  useEffect(() => {
    if (topTab === "advanced" && platform && category) {
      loadCandidates(platform, category);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topTab, platform, category]);

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
    if (selectedWorkIds.length === 0) {
      toast("请至少勾选一部代表作", "error");
      return;
    }
    if (!window.confirm(
      `准备启动 API 提取任务：\n平台: ${platform}\n榜单 (${selectedCats.length} 个): ${selectedCats.join("、")}\n` +
      `代表作: 已勾选 ${selectedWorkIds.length} 部\n\n` +
      `每个榜单一个任务，会调用大模型对勾选的代表作做生造词Step2 + 行文风格七组抽取，` +
      `更新对应的平台风格档案。可能耗时数分钟。继续？`,
    )) return;
    setLaunching(true);
    let ok = 0;
    const failed: string[] = [];
    for (const cat of selectedCats) {
      try {
        await apiPost<{ job_id: string; state: string }>(
          "/api/market-extractor/jobs",
          // selected work_ids only apply to the matching category's pool;
          // for other selected 榜单 the backend re-selects + uses all.
          { platform, category: cat,
            work_ids: cat === category ? selectedWorkIds : undefined },
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
  }, [platform, category, selectedCats, selectedWorkIds, refreshJobs, toast]);

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
    if (selectedWorkIds.length === 0) {
      toast("请至少勾选一部代表作", "error");
      return;
    }
    try {
      const r = await apiPost<{ prompt: string }>(
        "/api/market-extractor/manual-prompt",
        { platform, category, work_ids: selectedWorkIds },
      );
      setManualPrompt(r.prompt || "");
      setManualOpen(true);
    } catch (e: any) {
      toast(`生成 prompt 失败: ${e.message}`, "error");
    }
  }, [platform, category, selectedCats.length, selectedWorkIds, toast]);

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
          {topTab === "advanced" && (
            <button className="btn" onClick={refreshAll} disabled={loading}>
              {loading ? "刷新中..." : "刷新"}
            </button>
          )}
        </div>
      </div>

      {/* ── Top-level tab strip: 基础特征提取 | 高级特征提取 ── */}
      <div style={{
        display: "flex", gap: 0,
        borderBottom: "1px solid var(--border)",
        marginBottom: 14,
      }}>
        {([
          { key: "basic"    as const, label: "基础特征提取" },
          { key: "advanced" as const, label: "高级特征提取" },
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

      {topTab === "basic" && <BasicExtractionTab />}

      {topTab === "advanced" && (<>
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
                <div style={{ marginTop: 6, color: "var(--text-tertiary)" }}>
                  代表作：已勾选 <strong style={{ color: "var(--jade)" }}>{selectedWorkIds.length}</strong>
                  {candidates.length > 0 ? ` / ${candidates.length}` : ""} 部
                  （在下方「代表作选取」中调整）
                </div>
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

      {/* 代表作选取 — InkOctoBot 自动选出代表作 + 前2章「开头+结尾」节选，
          用户可手动 select/deselect，再据勾选生成 prompt / 调 API。 */}
      <RepWorksSelector
        platform={platform}
        category={category}
        candidates={candidates}
        loading={candLoading}
        selected={selectedWorkIds}
        onToggle={(id) => setSelectedWorkIds(prev =>
          prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id])}
        onSelectAll={() => setSelectedWorkIds(candidates.map(c => c.work_id))}
        onClear={() => setSelectedWorkIds([])}
        onReload={() => loadCandidates(platform, category)}
      />

      {/* 提取结果 — 当前平台风格档案（含生造词Step2 + 行文风格七组） */}
      <ExtractionResultSection
        profiles={platformProfiles} platform={platform} category={category} />

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


/** Horizontal mini-bar for inline visualization in dense tables. */
function MiniBar({ value, max, color }: { value: number; max: number; color: string }) {
  const pct = max > 0 ? Math.max(2, Math.min(100, (value / max) * 100)) : 0;
  return (
    <div style={{ height: 6, background: "var(--bg-surface-2)", borderRadius: 3, overflow: "hidden", minWidth: 40 }}>
      <div style={{ width: `${pct}%`, height: "100%", background: color }} />
    </div>
  );
}


/**
 * RepWorksSelector — 高级特征提取 第一步：展示 InkOctoBot 自动选出的
 * 代表作 + 每部前2章「开头+结尾」节选，用户可手动 select/deselect，
 * 再据勾选生成 prompt / 调 API（见 Step 3 的两个按钮）。
 */
function RepWorksSelector({
  platform, category, candidates, loading, selected,
  onToggle, onSelectAll, onClear, onReload,
}: {
  platform: string; category: string;
  candidates: RepCandidate[]; loading: boolean; selected: string[];
  onToggle: (id: string) => void; onSelectAll: () => void;
  onClear: () => void; onReload: () => void;
}) {
  const [openExcerpt, setOpenExcerpt] = React.useState<Record<string, boolean>>({});
  return (
    <div className="card" style={{ marginTop: 16, borderTop: "3px solid var(--accent)" }}>
      <div className="card-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
        <div>
          <h3 style={{ margin: 0, fontSize: 14 }}>代表作选取</h3>
          <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
            自动选出的代表作 + 前2章「开头+结尾」节选 · 勾选后用于生成 prompt / 调用 API
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
            已勾选 {selected.length} / {candidates.length}
          </span>
          <button className="btn" style={{ fontSize: 10, padding: "2px 10px" }}
            disabled={candidates.length === 0} onClick={onSelectAll}>全选</button>
          <button className="btn" style={{ fontSize: 10, padding: "2px 10px" }}
            disabled={selected.length === 0} onClick={onClear}>清空</button>
          <button className="btn" style={{ fontSize: 10, padding: "2px 10px" }}
            disabled={loading || !platform || !category} onClick={onReload}>
            {loading ? "加载中..." : "重新选取"}
          </button>
        </div>
      </div>
      <div className="card-body" style={{ padding: 14 }}>
        {!platform || !category ? (
          <Empty msg="请在上方选定平台与单个榜单，InkOctoBot 会自动选出代表作。" />
        ) : loading && candidates.length === 0 ? (
          <Empty msg="正在选取代表作并读取前2章节选…" />
        ) : candidates.length === 0 ? (
          <Empty msg="未选到代表作 — 请确认该平台/榜单已采集作品与章节（Settings → 市场数据库）。" />
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {candidates.map(c => {
              const on = selected.includes(c.work_id);
              const words = c.total_words ? `${(c.total_words / 10000).toFixed(1)}万字` : "—";
              const expanded = !!openExcerpt[c.work_id];
              return (
                <div key={c.work_id} style={{
                  border: `1px solid ${on ? "var(--accent)" : "var(--border)"}`,
                  borderRadius: 6, padding: "8px 10px",
                  background: on ? "var(--accent-subtle, var(--bg-surface))" : "var(--bg-surface)",
                }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                    <input type="checkbox" checked={on} onChange={() => onToggle(c.work_id)}
                      style={{ width: 16, height: 16, cursor: "pointer" }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 13, fontWeight: 600 }}>
                        《{c.title}》
                        {c.is_holdout ? (
                          <span className="tag" style={{ fontSize: 9, marginLeft: 6, color: "var(--gold)" }}>holdout</span>
                        ) : null}
                      </div>
                      <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
                        {c.author} · {c.category || category} · {words}
                        {(c.tags && c.tags.length) ? ` · ${c.tags.slice(0, 5).join("、")}` : ""}
                        {!c.has_chapters ? " · （暂无章节节选）" : ""}
                      </div>
                    </div>
                    {c.has_chapters && (
                      <button className="btn" style={{ fontSize: 10, padding: "2px 10px" }}
                        onClick={() => setOpenExcerpt(p => ({ ...p, [c.work_id]: !expanded }))}>
                        {expanded ? "收起节选" : "查看节选"}
                      </button>
                    )}
                  </div>
                  {expanded && c.excerpt && (
                    <pre style={{
                      margin: "8px 0 0", padding: 8, fontSize: 11,
                      background: "var(--bg-surface-2)", borderRadius: 4,
                      maxHeight: 240, overflow: "auto", whiteSpace: "pre-wrap",
                      wordBreak: "break-word", fontFamily: "var(--font-mono)",
                    }}>{c.excerpt}</pre>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}


/**
 * ExtractionResultSection — 高级特征提取 的「提取结果」：当前平台风格
 * 档案（综述/风格基线/招牌手法/节奏指南）+ 生造词Step2 + 行文风格
 * 七组 (A1-G2) 的结构化结果。
 */
function ExtractionResultSection({
  profiles, platform, category,
}: { profiles: PlatformProfile[]; platform: string; category: string }) {
  const top = (profiles[0] || null) as any;
  const styleDims = parseMaybeJson(top?.style_dimensions_json);
  const neo = parseMaybeJson(top?.neologism_step2_json);
  return (
    <div className="card" style={{ marginTop: 16, borderTop: "3px solid var(--jade)" }}>
      <div className="card-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h3 style={{ margin: 0, fontSize: 14 }}>提取结果（平台风格档案）</h3>
        <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
          {top
            ? `${tPlatform(top.platform)} · ${top.category || "—"} · 更新于 ${top.extraction_completed_at || "—"}`
            : (platform && category
                ? `${tPlatform(platform)} × ${category} 暂无结果`
                : "请在左上选定平台与榜单")}
        </span>
      </div>
      <div className="card-body" style={{ padding: 14 }}>
        {profiles.length === 0 ? (
          <Empty msg={
            platform && category
              ? `(${tPlatform(platform)}×${category}) 暂无提取结果。先勾选代表作，再用「使用大模型 API 提取」或「使用大模型网页版提取」生成。`
              : "请在上方选定平台与榜单，勾选代表作后启动一次提取以生成结果。"
          } />
        ) : (
          <>
            {(neo || styleDims) && (
              <div style={{ display: "flex", flexDirection: "column", gap: 10, marginBottom: 12 }}>
                {neo && (
                  <details open style={{ border: "1px solid var(--border)", borderRadius: 6, padding: "8px 10px", background: "var(--bg-surface-2)" }}>
                    <summary style={{ fontSize: 12, fontWeight: 600, cursor: "pointer" }}>生造词 Step2（专有名词 / 人名 / 地名 + 常见模式与常见字）</summary>
                    <div style={{ fontSize: 11, lineHeight: 1.9, marginTop: 6 }}>
                      {neo.proper_nouns?.length ? <div>专有名词：{neo.proper_nouns.join("、")}</div> : null}
                      {neo.person_names?.length ? <div>人名：{neo.person_names.join("、")}</div> : null}
                      {neo.place_names?.length ? <div>地名：{neo.place_names.join("、")}</div> : null}
                      {neo.naming_patterns ? <div>常见模式：{neo.naming_patterns}</div> : null}
                      {neo.common_chars?.length ? <div>常见字：{neo.common_chars.join("、")}</div> : null}
                    </div>
                  </details>
                )}
                {styleDims && (
                  <details style={{ border: "1px solid var(--border)", borderRadius: 6, padding: "8px 10px", background: "var(--bg-surface-2)" }}>
                    <summary style={{ fontSize: 12, fontWeight: 600, cursor: "pointer" }}>行文风格七组（A 主角 / B 社会 / C 世界 / D 钩子 / E 风格 / F 信息 / G 节奏）</summary>
                    <div style={{ fontSize: 11, lineHeight: 1.8, marginTop: 6 }}>
                      {Object.entries(styleDims).map(([group, fields]) => (
                        <div key={group} style={{ marginBottom: 6 }}>
                          <strong>{group}</strong>
                          {fields && typeof fields === "object" ? (
                            <div style={{ paddingLeft: 10 }}>
                              {Object.entries(fields as Record<string, any>).map(([k, v]) => (
                                <div key={k}><span style={{ color: "var(--text-tertiary)" }}>{k}：</span>{String(v)}</div>
                              ))}
                            </div>
                          ) : <span> {String(fields)}</span>}
                        </div>
                      ))}
                    </div>
                  </details>
                )}
              </div>
            )}
            <ProfilesTab profiles={profiles} />
          </>
        )}
      </div>
    </div>
  );
}


function parseMaybeJson(s: any): any {
  if (!s) return null;
  if (typeof s === "object") return s;
  try { return JSON.parse(s); } catch { return null; }
}


/**
 * BasicExtractionTab — 基础特征提取 (spec 2.1.3.2 市场信息 + 开篇章节
 * NLP维度). Single-platform required, time-range selectable, TOP K fixed
 * at 10. All dimensions are visualized. Served through the server
 * compute-cache (秒开 + 懒加载): mount reads cached_only, refresh forces
 * a background recompute, the DB-changed stale flag prompts re-run.
 */
function BasicExtractionTab() {
  const { toast } = useToast();
  type NlpPayload = {
    available: boolean; reason?: string; sample_count?: number;
    unique_novels?: number; total_chapters?: number;
    spec_stats?: any;
    word_count_summary?: { mean: number; min: number; max: number };
  };
  type MarketPayload = {
    empty?: boolean; start_date?: string; end_date?: string;
    cat_rollup?: any[]; tag_rollup?: any[];
    opportunities?: any[]; pairs?: any[];
  };

  const TOP_K = 10;   // 固定 TOP 10 (用户需求)
  const [platforms, setPlatforms] = React.useState<PlatformOption[]>(
    () => swrHydrate<PlatformOption[]>("mfe_platforms") || [],
  );
  // 用户必须选择单一平台进行分析 (用户需求)。
  const [platform, setPlatform] = React.useState<string>(
    () => (swrHydrate<PlatformOption[]>("mfe_platforms") || [])[0]?.key || "",
  );
  const [lookback, setLookback] = React.useState("all");

  const [nlp, setNlp] = React.useState<NlpPayload | null>(null);
  const [market, setMarket] = React.useState<MarketPayload | null>(null);
  const [stale, setStale] = React.useState(false);
  const [nlpBusy, setNlpBusy] = React.useState(false);
  const [marketBusy, setMarketBusy] = React.useState(false);
  const computing = nlpBusy || marketBusy;
  const [everRun, setEverRun] = React.useState(true);
  const [err, setErr] = React.useState("");
  const pollsRef = React.useRef<PollController[]>([]);

  const nlpKey = `mfe_basic_nlp_${platform}`;
  const marketKey = `mfe_basic_market_${platform}_${lookback}`;

  React.useEffect(() => {
    let alive = true;
    apiGet<{ platforms: PlatformOption[] }>("/api/market-extractor/platforms")
      .then(r => {
        if (!alive) return;
        const list = r.platforms || [];
        setPlatforms(list);
        swrStore("mfe_platforms", list);
        if (list.length > 0 && !platform) setPlatform(list[0].key);
      })
      .catch(() => { /* keep hydrated list */ });
    return () => { alive = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const stopPolls = () => {
    pollsRef.current.forEach(p => p.cancel());
    pollsRef.current = [];
  };

  const load = React.useCallback((refresh: boolean) => {
    if (!platform) return;
    stopPolls();
    setErr("");
    if (refresh) { setNlpBusy(true); setMarketBusy(true); setEverRun(true); }
    let emptyCount = 0;
    const nq = new URLSearchParams({ platform });
    if (refresh) nq.set("refresh", "true"); else nq.set("cached_only", "true");
    pollsRef.current.push(pollCompute<NlpPayload>(
      `/api/db/opening_nlp_analysis?${nq}`,
      {
        onReady: (payload, env) => {
          setNlp(payload); swrStore(nlpKey, payload);
          setStale(s => s || !!env.stale);
          setNlpBusy(!!env.computing); setEverRun(true);
        },
        onEmpty: () => { setNlpBusy(false); emptyCount += 1; if (emptyCount >= 2) setEverRun(false); },
        onComputing: () => setNlpBusy(true),
        onError: (m) => { setErr(m); setNlpBusy(false); },
      },
    ));
    const mq = new URLSearchParams({ platform, lookback, top_k: String(TOP_K) });
    if (refresh) mq.set("refresh", "true"); else mq.set("cached_only", "true");
    pollsRef.current.push(pollCompute<MarketPayload>(
      `/api/analysis/run?${mq}`,
      {
        onReady: (payload, env) => {
          setMarket(payload); swrStore(marketKey, payload);
          setStale(s => s || !!env.stale);
          setMarketBusy(!!env.computing); setEverRun(true);
        },
        onEmpty: () => { setMarketBusy(false); emptyCount += 1; if (emptyCount >= 2) setEverRun(false); },
        onComputing: () => setMarketBusy(true),
        onError: (m) => { setErr(m); setMarketBusy(false); },
      },
    ));
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [platform, lookback, nlpKey, marketKey]);

  React.useEffect(() => {
    setNlp(swrHydrate<NlpPayload>(nlpKey));
    setMarket(swrHydrate<MarketPayload>(marketKey));
    setStale(false);
    load(false);
    return stopPolls;
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [platform, lookback]);

  const hasMarket = !!(market && !market.empty);
  const hasNlp = !!nlp?.available;
  const hasAny = hasMarket || hasNlp;

  const LOOKBACKS: { key: string; label: string }[] = [
    { key: "week", label: "最近7天" }, { key: "month", label: "最近30天" },
    { key: "quarter", label: "最近90天" }, { key: "year", label: "最近365天" },
    { key: "all", label: "全部数据" },
  ];

  return (
    <>
      {/* Controls */}
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-body" style={{ display: "flex", gap: 20, alignItems: "flex-end", flexWrap: "wrap" }}>
          <div className="field">
            <label className="label">平台（单选，必选）</label>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {platforms.length === 0 ? (
                <span style={{ fontSize: 12, color: "var(--text-tertiary)" }}>未读到平台 — 检查 Settings → 市场数据库</span>
              ) : platforms.map(p => (
                <button key={p.key}
                  className={platform === p.key ? "btn-primary" : "btn"}
                  style={{ fontSize: 12, padding: "5px 14px", borderRadius: 20 }}
                  onClick={() => setPlatform(p.key)}>{tPlatform(p.key)}</button>
              ))}
            </div>
          </div>
          <div className="field">
            <label className="label">时间范围</label>
            <select className="select" value={lookback} onChange={e => setLookback(e.target.value)}>
              {LOOKBACKS.map(l => <option key={l.key} value={l.key}>{l.label}</option>)}
            </select>
          </div>
          <div className="field">
            <label className="label">Top K</label>
            <div style={{ fontSize: 13, fontWeight: 600, padding: "6px 0" }}>10（固定）</div>
          </div>
          <button className="btn-primary" onClick={() => load(true)} disabled={computing || !platform}>
            {computing ? "分析中..." : (hasAny ? "重新分析" : "运行分析")}
          </button>
        </div>
      </div>

      {/* 分析范围概览：涉及多少 unique 小说 · 共多少章 · 时间区间 */}
      {hasAny && (
        <div style={{ display: "flex", gap: 18, flexWrap: "wrap", margin: "0 0 14px", fontSize: 12, color: "var(--text-secondary)" }}>
          <span>涉及小说 <strong style={{ color: "var(--text-primary)" }}>{nlp?.unique_novels ?? "—"}</strong> 部</span>
          <span>共 <strong style={{ color: "var(--text-primary)" }}>{nlp?.total_chapters ?? "—"}</strong> 章</span>
          <span>时间区间 <strong style={{ color: "var(--text-primary)" }}>{market?.start_date ? `${market.start_date} ~ ${market.end_date}` : "—"}</strong></span>
          {computing && <span style={{ color: "var(--text-tertiary)" }}>后台分析中…</span>}
        </div>
      )}

      {stale && !computing && (
        <div className="card" style={{ marginBottom: 14, borderLeft: "3px solid var(--gold)" }}>
          <div className="card-body" style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", fontSize: 12 }}>
            市场数据已更新 — 当前展示上一次分析结果。
            <button className="btn" style={{ fontSize: 11, padding: "3px 12px" }} onClick={() => load(true)}>重新分析</button>
          </div>
        </div>
      )}
      {err && <div className="card" style={{ marginBottom: 14, borderLeft: "3px solid var(--danger)" }}><div className="card-body" style={{ fontSize: 12, color: "var(--danger)" }}>分析失败：{err}</div></div>}

      {!hasAny && !computing && (
        <Empty msg={everRun
          ? "暂无可分析数据 — 请确认已选平台且市场数据库路径正确（Settings → 市场数据库）。"
          : "尚未运行分析。点击右上「运行分析」启动 — 首次需后台计算，完成后结果持久保留，下次进入秒开。"} />
      )}
      {!hasAny && computing && <Empty msg="正在后台计算 — 可离开此页面，结果会保留。" />}

      {/* 市场信息 (spec 2.1.3.2 全部维度，可视化) */}
      {hasMarket && <MarketInfoBlock market={market!} platform={platform} />}

      {/* 开篇章节NLP维度 (spec 2.1.3.2) */}
      {hasNlp && <NlpDimsBlock nlp={nlp!} />}
    </>
  );
}


/** 市场信息可视化：类目 / 标签（数量·热度·份额 及各自趋势，带 mini-bar）
 *  + 开书机会 + 标签共现。 */
function MarketInfoBlock({ market, platform }: { market: any; platform: string }) {
  const slope = (v: number | null | undefined) => {
    if (v == null || isNaN(v)) return <span style={{ color: "var(--text-disabled)" }}>—</span>;
    const up = v > 0.0001, down = v < -0.0001;
    const color = up ? "var(--jade)" : down ? "var(--accent)" : "var(--text-tertiary)";
    return <span className="font-mono" style={{ color, fontSize: 11 }}>{up ? "↑" : down ? "↓" : "→"} {Math.abs(v).toFixed(4)}</span>;
  };
  const num = (v: number | null | undefined, d = 0) =>
    v == null || isNaN(v as number) ? "—" : Number(v).toFixed(d);

  const isQidian = platform === "qidian";
  // Dedupe across rank boards: the same 类目/标签 appears once per board in
  // the rollup; keep the highest-count row per name so the table has no
  // within-list duplicates.
  const dedupeByName = (rows: any[], key: string) => {
    const m = new Map<string, any>();
    for (const r of rows || []) {
      const name = r[key];
      if (!name) continue;
      const prev = m.get(name);
      if (!prev || (r.latest_count || 0) > (prev.latest_count || 0)) m.set(name, r);
    }
    return [...m.values()].sort((a, b) => (b.latest_count || 0) - (a.latest_count || 0));
  };
  let catAll = dedupeByName(market.cat_rollup || [], "category");
  let tagAll = dedupeByName(market.tag_rollup || [], "tag");
  if (isQidian) {
    // 起点: 类目=大分类, 标签=副分类。按权威集合归类（集合互斥 → 两表
    // 不重复）；若过滤后为空则回退到去重后的原始列表，避免空表。
    const mainF = catAll.filter(r => QIDIAN_MAIN_CATS.has(r.category));
    const subF = tagAll.filter(r => QIDIAN_SUB_CATS.has(r.tag));
    if (mainF.length) catAll = mainF;
    if (subF.length) tagAll = subF;
  }
  // Cross-list dedupe: a name shown as 类目/大分类 is removed from 标签/副分类.
  const catNameSet = new Set(catAll.map(r => r.category));
  tagAll = tagAll.filter(r => !catNameSet.has(r.tag));

  const catLabel = isQidian ? "大分类" : "类目";
  const tagLabel = isQidian ? "副分类" : "标签";
  const showCooccur = !isQidian;   // 起点没有标签共现

  const catRows = catAll.slice(0, 10);
  const tagRows = tagAll.slice(0, 10);
  const oppRows = (market.opportunities || []).slice(0, 10);
  const pairRows = (market.pairs || []).slice(0, 14);
  const catMaxCount = Math.max(1, ...catRows.map((r: any) => r.latest_count || 0));
  const catMaxHeat = Math.max(1, ...catRows.map((r: any) => r.avg_heat || 0));
  const tagMaxCount = Math.max(1, ...tagRows.map((r: any) => r.latest_count || 0));
  const tagMaxHeat = Math.max(1, ...tagRows.map((r: any) => r.avg_heat || 0));
  const oppMax = Math.max(1, ...oppRows.map((o: any) => o.opportunity_score || 0));

  return (
    <div className="card" style={{ marginBottom: 14, borderTop: "3px solid var(--indigo)" }}>
      <div className="card-header"><h3 style={{ margin: 0, fontSize: 14 }}>市场信息</h3></div>
      <div className="card-body">
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginBottom: 14 }}>
          {/* 类目 */}
          <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 6, padding: 10, overflowX: "auto" }}>
            <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>{catLabel} TOP10：数量 / 热度 / 份额 及变化趋势</div>
            {catRows.length === 0 ? <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>暂无</div> : (
              <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
                <thead><tr style={{ color: "var(--text-tertiary)", textAlign: "left" }}>
                  <th style={{ padding: "3px 6px" }}>{catLabel}</th>
                  <th style={{ padding: "3px 6px" }}>数量</th>
                  <th style={{ padding: "3px 6px", textAlign: "right" }}>趋势</th>
                  <th style={{ padding: "3px 6px" }}>热度</th>
                  <th style={{ padding: "3px 6px", textAlign: "right" }}>趋势</th>
                  <th style={{ padding: "3px 6px", textAlign: "right" }}>份额</th>
                  <th style={{ padding: "3px 6px", textAlign: "right" }}>趋势</th>
                </tr></thead>
                <tbody>
                  {catRows.map((r: any, i: number) => (
                    <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                      <td style={{ padding: "3px 6px" }}>{r.category}</td>
                      <td style={{ padding: "3px 6px", minWidth: 70 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                          <span className="font-mono" style={{ minWidth: 22 }}>{num(r.latest_count)}</span>
                          <MiniBar value={r.latest_count || 0} max={catMaxCount} color="var(--accent)" />
                        </div>
                      </td>
                      <td style={{ padding: "3px 6px", textAlign: "right" }}>{slope(r.count_slope)}</td>
                      <td style={{ padding: "3px 6px", minWidth: 70 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                          <span className="font-mono" style={{ minWidth: 28 }}>{num(r.avg_heat)}</span>
                          <MiniBar value={r.avg_heat || 0} max={catMaxHeat} color="var(--gold)" />
                        </div>
                      </td>
                      <td style={{ padding: "3px 6px", textAlign: "right" }}>{slope(r.heat_slope)}</td>
                      <td style={{ padding: "3px 6px", textAlign: "right" }} className="font-mono">{num(r.latest_share, 3)}</td>
                      <td style={{ padding: "3px 6px", textAlign: "right" }}>{slope(r.share_slope)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          {/* 标签 */}
          <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 6, padding: 10, overflowX: "auto" }}>
            <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>{tagLabel} TOP10：数量 / 热度 / 份额 及变化趋势</div>
            {tagRows.length === 0 ? <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>暂无</div> : (
              <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
                <thead><tr style={{ color: "var(--text-tertiary)", textAlign: "left" }}>
                  <th style={{ padding: "3px 6px" }}>{tagLabel}</th>
                  <th style={{ padding: "3px 6px" }}>数量</th>
                  <th style={{ padding: "3px 6px" }}>热度</th>
                  <th style={{ padding: "3px 6px", textAlign: "right" }}>趋势</th>
                  <th style={{ padding: "3px 6px", textAlign: "right" }}>份额</th>
                  <th style={{ padding: "3px 6px", textAlign: "right" }}>趋势</th>
                </tr></thead>
                <tbody>
                  {tagRows.map((r: any, i: number) => (
                    <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                      <td style={{ padding: "3px 6px" }}>{r.tag}</td>
                      <td style={{ padding: "3px 6px", minWidth: 70 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                          <span className="font-mono" style={{ minWidth: 22 }}>{num(r.latest_count)}</span>
                          <MiniBar value={r.latest_count || 0} max={tagMaxCount} color="var(--accent)" />
                        </div>
                      </td>
                      <td style={{ padding: "3px 6px", minWidth: 70 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                          <span className="font-mono" style={{ minWidth: 28 }}>{num(r.avg_heat)}</span>
                          <MiniBar value={r.avg_heat || 0} max={tagMaxHeat} color="var(--gold)" />
                        </div>
                      </td>
                      <td style={{ padding: "3px 6px", textAlign: "right" }}>{slope(r.heat_slope)}</td>
                      <td style={{ padding: "3px 6px", textAlign: "right" }} className="font-mono">{num(r.latest_share, 3)}</td>
                      <td style={{ padding: "3px 6px", textAlign: "right" }}>{slope(r.share_slope)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          {/* 开书机会 */}
          <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 6, padding: 10 }}>
            <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>开书机会（份额↑ × 热度↑ × 新书占比）</div>
            {oppRows.length === 0 ? <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>暂无</div> : (
              <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                {oppRows.map((o: any, i: number) => (
                  <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11 }}>
                    <span className="tag category" style={{ fontSize: 10 }}>{o.category}</span>
                    <span className="tag" style={{ fontSize: 10 }}>{o.tag}</span>
                    <span style={{ marginLeft: "auto", color: "var(--text-tertiary)", whiteSpace: "nowrap" }}>
                      新书 {o.new_entry_ratio != null ? `${Math.round(o.new_entry_ratio * 100)}%` : "—"}
                    </span>
                    <div style={{ width: 60 }}><MiniBar value={o.opportunity_score || 0} max={oppMax} color="var(--jade)" /></div>
                    <span className="font-mono" style={{ color: "var(--jade)", fontWeight: 600, minWidth: 34, textAlign: "right" }}>{num(o.opportunity_score, 3)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
          {/* 标签共现（起点无此维度，隐藏） */}
          {showCooccur && (
            <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 6, padding: 10 }}>
              <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>标签共现（高频组合）</div>
              {pairRows.length === 0 ? <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>暂无</div> : (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                  {pairRows.map((p: any, i: number) => (
                    <span key={i} className="tag" style={{ fontSize: 10, padding: "2px 8px" }}>
                      {p.tag_a} × {p.tag_b}<span style={{ color: "var(--text-tertiary)", marginLeft: 4 }}>{p.count}</span>
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}


/** 开篇章节NLP维度 (spec 2.1.3.2)：首章/章均/章中位字数、平均句长、
 *  分类型标点密度（可视化）、高频词、生造词Step1。 */
function NlpDimsBlock({ nlp }: { nlp: any }) {
  const spec = nlp.spec_stats;
  if (!spec?.available) {
    return (
      <div className="card" style={{ marginBottom: 14, borderTop: "3px solid var(--jade)" }}>
        <div className="card-header"><h3 style={{ margin: 0, fontSize: 14 }}>开篇章节 NLP 维度</h3></div>
        <div className="card-body"><div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>暂无开篇章节统计数据。</div></div>
      </div>
    );
  }
  const wordStats: [string, number | null][] = [
    ["首章字数（均值）", spec.first_chapter_words_avg],
    ["章平均字数", spec.chapter_words_avg],
    ["章中位字数", spec.chapter_words_median],
  ];
  const wordMax = Math.max(1, ...wordStats.map(([, v]) => v || 0));
  const punct = (spec.punctuation_density_per_1k || {}) as Record<string, number>;
  const punctMax = Math.max(1, ...Object.values(punct).map(v => Number(v) || 0));

  return (
    <div className="card" style={{ marginBottom: 14, borderTop: "3px solid var(--jade)" }}>
      <div className="card-header"><h3 style={{ margin: 0, fontSize: 14 }}>开篇章节 NLP 维度</h3></div>
      <div className="card-body">
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14 }}>
          {/* 字数 */}
          <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 6, padding: 10 }}>
            <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>字数维度</div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {wordStats.map(([label, v]) => (
                <div key={label} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ width: 110, fontSize: 11, color: "var(--text-secondary)" }}>{label}</span>
                  <div style={{ flex: 1 }}><MiniBar value={v || 0} max={wordMax} color="var(--accent)" /></div>
                  <span className="font-mono" style={{ width: 56, textAlign: "right", fontSize: 11 }}>{v ?? "—"}</span>
                </div>
              ))}
              <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
                平均句长 <strong style={{ color: "var(--text-secondary)" }}>{spec.avg_sentence_length ?? "—"} 字</strong>
                {nlp.word_count_summary ? ` · 首章字数范围 ${nlp.word_count_summary.min}–${nlp.word_count_summary.max}` : ""}
              </div>
            </div>
          </div>
          {/* 标点密度 */}
          <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 6, padding: 10 }}>
            <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>标点符号密度（次/千字）</div>
            {Object.keys(punct).length === 0 ? <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>暂无</div> : (
              <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
                {Object.entries(punct).map(([k, v]) => (
                  <div key={k} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span style={{ width: 48, fontSize: 11, color: "var(--text-secondary)" }}>{k}</span>
                    <div style={{ flex: 1 }}><MiniBar value={Number(v) || 0} max={punctMax} color="var(--indigo)" /></div>
                    <span className="font-mono" style={{ width: 48, textAlign: "right", fontSize: 11 }}>{String(v)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
          {/* 高频词 */}
          <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 6, padding: 10 }}>
            <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>高频词</div>
            {(spec.top_words || []).length === 0 ? <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>暂无</div> : (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                {(spec.top_words || []).slice(0, 20).map((w: any) => (
                  <span key={w.word} className="tag" style={{ fontSize: 11, padding: "2px 8px" }}>
                    {w.word}<span style={{ color: "var(--text-tertiary)", marginLeft: 4 }}>{w.count}</span>
                  </span>
                ))}
              </div>
            )}
          </div>
          {/* 生造词Step1 */}
          <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 6, padding: 10 }}>
            <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>生造词 Step1 候选（频率 + PMI 凝合度初筛）</div>
            {(spec.neologism_step1 || []).length === 0 ? <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>暂无</div> : (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
                {(spec.neologism_step1 || []).slice(0, 18).map((n: any) => (
                  <span key={n.term} className="tag" style={{ fontSize: 11, padding: "2px 8px", color: "var(--gold)" }}>
                    {n.term}<span style={{ color: "var(--text-tertiary)", marginLeft: 4 }}>{n.count}</span>
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
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
