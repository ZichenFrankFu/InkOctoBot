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
import { apiGet, apiPost } from "../api/client";
import { swrHydrate, swrStore, pollCompute, PollController } from "../api/swr";
import { useToast } from "../components/shared/Toast";
import UniversalLLMDialog from "../components/shared/UniversalLLMDialog";
import { tPlatform } from "../i18n";


// 基础特征提取 (市场信息 + 开篇章节NLP维度) 在前；高级特征提取
// (代表作选取 → 生造词Step2 + 行文风格七组 LLM 提取) 在后。
type TopTab = "basic" | "advanced" | "resources";

interface RepCandidate {
  work_id: string;
  source_db_novel_id?: string;
  title: string;
  author: string;
  category?: string;
  total_words?: number;
  tags?: string[];
  rank_score?: number;
  reason?: string;
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


// 起点分类体系：大分类（novel_types）与副分类（sub_to_main_map 的键）。
// 起点用「大分类 / 副分类」而非「类目 / 标签」，两者集合互斥，用于去重
// 起点 大分类/副分类 taxonomy 与父级映射现由后端 config 维护
// (resources/taxonomy/qidian.json)，经 /api/analysis/run 的 panel 字段下发：
// 大分类/副分类已在后端按 taxonomy 过滤，副分类行带 ``parent`` 大分类。
// 前端不再硬编码此表。

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
  const [platform, setPlatform] = useState(
    () => (swrHydrate<PlatformOption[]>("mfe_platforms") || [])[0]?.key || "",
  );
  const [launching, setLaunching] = useState(false);

  const [jobs, setJobs] = useState<Job[]>(
    () => swrHydrate<Job[]>("mfe_jobs") || [],
  );
  const [profiles, setProfiles] = useState<PlatformProfile[]>(
    () => swrHydrate<PlatformProfile[]>("mfe_profiles") || [],
  );

  const [topTab, setTopTab] = useState<TopTab>("basic");

  // Manual-mode dialog
  const [manualOpen, setManualOpen] = useState(false);
  const [manualPrompt, setManualPrompt] = useState("");

  // 高级特征提取：自动选出的代表作候选 + 用户勾选集合（select/deselect）。
  const [candidates, setCandidates] = useState<RepCandidate[]>([]);
  const [selectedWorkIds, setSelectedWorkIds] = useState<string[]>([]);
  const [candLoading, setCandLoading] = useState(false);
  const candReqToken = React.useRef(0);

  const loadCandidates = useCallback(async (pl: string, cat: string) => {
    if (!pl) { setCandidates([]); setSelectedWorkIds([]); return; }
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

  // 高级特征提取：平台变化时，拉取整平台多维度代表作候选（不再单选榜单）。
  useEffect(() => {
    if (topTab === "advanced" && platform) {
      loadCandidates(platform, "");
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topTab, platform]);

  // 市场数据库有变化时自动刷新（轮询版本指纹，替代手动刷新按钮）。
  const lastCrawlerVer = React.useRef<string | null>(null);
  useEffect(() => {
    if (topTab !== "advanced") return;
    let alive = true;
    const check = async () => {
      try {
        const r = await apiGet<{ version: string }>("/api/analysis/crawler-version");
        if (!alive) return;
        if (lastCrawlerVer.current === null) {
          lastCrawlerVer.current = r.version;
        } else if (r.version && r.version !== lastCrawlerVer.current) {
          lastCrawlerVer.current = r.version;
          refreshProfiles();
          if (platform) loadCandidates(platform, "");
        }
      } catch { /* 离线/未配置 — 忽略 */ }
    };
    check();
    const id = window.setInterval(check, 20000);
    return () => { alive = false; window.clearInterval(id); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topTab, platform]);

  useEffect(() => {
    const active = jobs.some(j => isActive(j.state));
    if (!active) return;
    const id = window.setInterval(refreshJobs, 5000);
    return () => window.clearInterval(id);
  }, [jobs, refreshJobs]);

  // ── actions ──

  const launchJob = useCallback(async () => {
    if (!platform) {
      toast("请先选择平台", "error");
      return;
    }
    if (selectedWorkIds.length === 0) {
      toast("请至少勾选一部代表作", "error");
      return;
    }
    if (!window.confirm(
      `准备启动 API 提取任务：\n平台：${tPlatform(platform)}（整平台）\n` +
      `代表作：已勾选 ${selectedWorkIds.length} 部\n\n` +
      `将调用大模型对勾选的代表作做生造词Step2 + 行文风格七组抽取，更新该平台` +
      `风格档案。可能耗时数分钟。继续？`,
    )) return;
    setLaunching(true);
    try {
      await apiPost<{ job_id: string; state: string }>(
        "/api/market-extractor/jobs",
        { platform, category: "", work_ids: selectedWorkIds },
      );
      toast("已启动平台提取任务", "success");
    } catch (e: any) {
      toast(`启动失败：${e.message}`, "error");
    }
    await refreshJobs();
    setLaunching(false);
  }, [platform, selectedWorkIds, refreshJobs, toast]);

  const startManualMode = useCallback(async () => {
    if (!platform) {
      toast("请先选择平台", "error");
      return;
    }
    if (selectedWorkIds.length === 0) {
      toast("请至少勾选一部代表作", "error");
      return;
    }
    try {
      const r = await apiPost<{ prompt: string }>(
        "/api/market-extractor/manual-prompt",
        { platform, category: "", work_ids: selectedWorkIds },
      );
      setManualPrompt(r.prompt || "");
      setManualOpen(true);
    } catch (e: any) {
      toast(`生成 prompt 失败: ${e.message}`, "error");
    }
  }, [platform, selectedWorkIds, toast]);

  const commitManual = useCallback(async (payload: { text: string }) => {
    const r = await apiPost<{ profile_id: string }>(
      "/api/market-extractor/manual-submit",
      { platform, category: "", response_raw: payload.text },
    );
    toast(`平台档案已写入 (${r.profile_id})`, "success");
    refreshProfiles();
  }, [platform, refreshProfiles, toast]);

  const platformProfiles = useMemo(() => {
    return profiles.filter(p => !platform || p.platform === platform);
  }, [profiles, platform]);

  const SELECTION_OK = !!platform;

  return (
    <div className="page-container" style={{ padding: "16px 20px", maxWidth: 1400, margin: "0 auto" }}>
      <div className="page-header" style={{ paddingBottom: 8 }}>
        <div className="page-header-row">
          <div>
            <h2>市场特征提取</h2>
          </div>
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
          { key: "resources" as const, label: "资源管理" },
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

      {topTab === "resources" && <ResourceManagerTab />}

      {topTab === "advanced" && (<>
        {/* Step 1 — 选择平台（整平台，不再单选榜单） */}
        <div className="card" style={{ padding: 16, borderTop: "3px solid var(--accent)", marginBottom: 14 }}>
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
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(160px, 1fr))", gap: 10 }}>
            {platforms.length === 0 ? (
              <span style={{ fontSize: 12, color: "var(--text-tertiary)", padding: 8 }}>
                未读到平台 — 检查 Settings → 市场数据库路径
              </span>
            ) : platforms.map(p => {
              const on = platform === p.key;
              return (
                <button key={p.key} onClick={() => setPlatform(p.key)} style={{
                  display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 4,
                  padding: "12px 16px", borderRadius: 8, cursor: "pointer", textAlign: "left",
                  border: `1.5px solid ${on ? "var(--accent)" : "var(--border)"}`,
                  background: on ? "var(--bg-surface-2)" : "var(--bg-surface)",
                }}>
                  <span style={{ fontSize: 15, fontWeight: 700, color: on ? "var(--accent)" : "var(--text-primary)" }}>{tPlatform(p.key)}</span>
                  <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{p.book_count} 本作品</span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Step 2 — 代表作选取（整平台多维度，用户选完平台后挑选） */}
        <RepWorksSelector
          platform={platform}
          category=""
          candidates={candidates}
          loading={candLoading}
          selected={selectedWorkIds}
          onToggle={(id) => setSelectedWorkIds(prev =>
            prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id])}
          onSelectAll={() => setSelectedWorkIds(candidates.map(c => c.work_id))}
          onClear={() => setSelectedWorkIds([])}
          onReload={() => loadCandidates(platform, "")}
        />

        {/* Step 3 — 启动提取 */}
        <div className="card" style={{
          padding: 16, marginTop: 16,
          borderTop: `3px solid ${SELECTION_OK ? "var(--jade)" : "var(--border)"}`,
          opacity: SELECTION_OK ? 1 : 0.5, transition: "opacity 0.18s",
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
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 12 }}>
            {([
              { onClick: launchJob, primary: true, busy: launching,
                title: "使用大模型 API 提取",
                desc: "调用已配置的大模型 API 自动完成，无需人工粘贴；可能耗时数分钟。" },
              { onClick: startManualMode, primary: false, busy: false,
                title: "使用大模型网页版提取",
                desc: "复制提示词到网页版大模型运行，再把回复粘回；无需 API key。" },
            ]).map((opt) => {
              const disabled = !SELECTION_OK || selectedWorkIds.length === 0 || opt.busy;
              return (
                <button key={opt.title} onClick={opt.onClick} disabled={disabled} style={{
                  display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 6,
                  padding: 16, borderRadius: 8, cursor: disabled ? "not-allowed" : "pointer",
                  textAlign: "left", opacity: disabled ? 0.55 : 1,
                  border: `1.5px solid ${opt.primary ? "var(--accent)" : "var(--border)"}`,
                  background: opt.primary ? "var(--accent)" : "var(--bg-surface)",
                  color: opt.primary ? "white" : "var(--text-primary)",
                }}>
                  <span style={{ fontSize: 14, fontWeight: 700 }}>{opt.busy ? "启动中..." : opt.title}</span>
                  <span style={{ fontSize: 11, lineHeight: 1.5, opacity: opt.primary ? 0.9 : 0.7 }}>{opt.desc}</span>
                </button>
              );
            })}
          </div>
          {(!SELECTION_OK || selectedWorkIds.length === 0) && (
            <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 10 }}>
              {!SELECTION_OK ? "先在上方选择平台" : "请在「代表作选取」中至少勾选一部作品"}
            </div>
          )}
        </div>

        {/* 提取结果 — 当前平台风格档案（含生造词Step2 + 行文风格七组） */}
        <ExtractionResultSection
          profiles={platformProfiles} platform={platform} category="" />
      </>)}

      {/* 网页版大模型手动粘贴弹窗 */}
      <UniversalLLMDialog
        open={manualOpen}
        onClose={() => setManualOpen(false)}
        title={`网页版大模型提取：${tPlatform(platform)}`}
        prompt={manualPrompt}
        onCommit={commitManual}
        minChars={80}
        initialMode="manual_only"
      />
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


const REASON_ORDER = ["总排行最高", "热度最高", "上榜最多·稳定", "新书榜抽样"];
const REASON_DESC: Record<string, string> = {
  "总排行最高": "横跨多个榜单、长期稳居榜单前列",
  "热度最高": "平台热度最高",
  "上榜最多·稳定": "历史上榜次数最多、发挥稳定",
  "新书榜抽样": "新书榜中抽样，覆盖新生力量",
};
const REASON_COLOR: Record<string, string> = {
  "总排行最高": "var(--gold)",
  "热度最高": "var(--accent)",
  "上榜最多·稳定": "var(--jade)",
  "新书榜抽样": "var(--indigo)",
};

/** 单部代表作卡片：整卡点击切换勾选；可展开前2章「开头+结尾」节选。 */
function RepCard({ c, on, onToggle, expanded, onExcerpt }: {
  c: RepCandidate; on: boolean; onToggle: () => void;
  expanded: boolean; onExcerpt: () => void;
}) {
  return (
    <div onClick={onToggle} style={{
      border: `1px solid ${on ? "var(--accent)" : "var(--border)"}`,
      borderRadius: 8, padding: 10, cursor: "pointer",
      background: on ? "var(--bg-surface-2)" : "var(--bg-surface)",
      display: "flex", flexDirection: "column", gap: 6,
    }}>
      <div style={{ display: "flex", alignItems: "flex-start", gap: 8 }}>
        <div style={{
          width: 16, height: 16, borderRadius: 4, flexShrink: 0, marginTop: 1,
          border: `1.5px solid ${on ? "var(--accent)" : "var(--border)"}`,
          background: on ? "var(--accent)" : "transparent",
          color: "white", fontSize: 11, lineHeight: "14px", textAlign: "center",
        }}>{on ? "✓" : ""}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 13, fontWeight: 600, wordBreak: "break-word" }}>
            《{c.title}》
            {c.is_holdout ? <span className="tag" style={{ fontSize: 9, color: "var(--gold)", marginLeft: 4 }}>holdout</span> : null}
          </div>
          <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 2 }}>
            {c.author}{c.category && c.category !== "—" ? ` · ${c.category}` : ""}
            {c.total_words ? ` · ${(c.total_words / 10000).toFixed(1)}万字` : ""}
          </div>
        </div>
      </div>
      {(c.tags && c.tags.length) ? (
        <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
          {c.tags.slice(0, 4).map(t => <span key={t} className="tag" style={{ fontSize: 9 }}>{t}</span>)}
        </div>
      ) : null}
      {c.has_chapters && (
        <button className="btn" style={{ fontSize: 10, padding: "2px 10px", alignSelf: "flex-start" }}
          onClick={(e) => { e.stopPropagation(); onExcerpt(); }}>
          {expanded ? "收起节选" : "查看节选"}
        </button>
      )}
      {expanded && c.excerpt && (
        <pre onClick={(e) => e.stopPropagation()} style={{
          margin: 0, padding: 8, fontSize: 11, background: "var(--bg-surface-2)",
          borderRadius: 4, maxHeight: 220, overflow: "auto", whiteSpace: "pre-wrap",
          wordBreak: "break-word", fontFamily: "var(--font-mono)", cursor: "auto",
        }}>{c.excerpt}</pre>
      )}
    </div>
  );
}

/** RepWorksSelector — 高级特征提取：整平台多维度代表作，按「选取原因」分组的
 *  卡片网格，用户勾选后用于生成 prompt / 调 API。 */
function RepWorksSelector({
  platform, candidates, loading, selected,
  onToggle, onSelectAll, onClear, onReload,
}: {
  platform: string; category: string;
  candidates: RepCandidate[]; loading: boolean; selected: string[];
  onToggle: (id: string) => void; onSelectAll: () => void;
  onClear: () => void; onReload: () => void;
}) {
  const [openExcerpt, setOpenExcerpt] = React.useState<Record<string, boolean>>({});
  const groups = React.useMemo(() => {
    const m = new Map<string, RepCandidate[]>();
    for (const c of candidates) {
      const r = c.reason || "其他";
      (m.get(r) || m.set(r, []).get(r)!).push(c);
    }
    const ordered: [string, RepCandidate[]][] = [];
    for (const r of REASON_ORDER) if (m.has(r)) ordered.push([r, m.get(r)!]);
    for (const [r, list] of m) if (!REASON_ORDER.includes(r)) ordered.push([r, list]);
    return ordered;
  }, [candidates]);

  return (
    <div className="card" style={{ marginTop: 16, borderTop: "3px solid var(--gold)" }}>
      <div className="card-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8 }}>
        <h3 style={{ margin: 0, fontSize: 14 }}>
          <span style={{
            display: "inline-block", width: 22, height: 22, borderRadius: "50%",
            background: "var(--gold)", color: "white", fontSize: 12, fontWeight: 700,
            textAlign: "center", lineHeight: "22px", marginRight: 8,
          }}>2</span>
          代表作选取
        </h3>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
            已勾选 {selected.length} / {candidates.length}
          </span>
          <button className="btn" style={{ fontSize: 10, padding: "2px 10px" }}
            disabled={candidates.length === 0} onClick={onSelectAll}>全选</button>
          <button className="btn" style={{ fontSize: 10, padding: "2px 10px" }}
            disabled={selected.length === 0} onClick={onClear}>清空</button>
          <button className="btn" style={{ fontSize: 10, padding: "2px 10px" }}
            disabled={loading || !platform} onClick={onReload}>
            {loading ? "加载中..." : "重新选取"}
          </button>
        </div>
      </div>
      <div className="card-body" style={{ padding: 14 }}>
        {!platform ? (
          <Empty msg="请在上方选定平台，InkOctoBot 会自动选出整平台多维度代表作。" />
        ) : loading && candidates.length === 0 ? (
          <Empty msg="正在选取代表作并读取前2章节选…" />
        ) : candidates.length === 0 ? (
          <Empty msg="未选到代表作 — 请确认该平台已采集作品与章节（Settings → 市场数据库）。" />
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            {groups.map(([reason, list]) => (
              <div key={reason}>
                <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 8 }}>
                  <span style={{
                    fontSize: 12, fontWeight: 700, padding: "2px 10px", borderRadius: 12,
                    color: "white", background: REASON_COLOR[reason] || "var(--text-tertiary)",
                  }}>{reason}</span>
                  <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
                    {REASON_DESC[reason] || ""} · {list.length} 部
                  </span>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 8 }}>
                  {list.map(c => (
                    <RepCard key={c.work_id} c={c}
                      on={selected.includes(c.work_id)}
                      onToggle={() => onToggle(c.work_id)}
                      expanded={!!openExcerpt[c.work_id]}
                      onExcerpt={() => setOpenExcerpt(p => ({ ...p, [c.work_id]: !p[c.work_id] }))} />
                  ))}
                </div>
              </div>
            ))}
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
const DIM_GROUP_LABELS: Record<string, string> = {
  A_protagonist: "A · 主角", B_social: "B · 社会关系", C_world: "C · 世界观",
  D_hook: "D · 钩子爽点", E_style: "E · 写作风格", F_info: "F · 信息节奏",
  G_pacing: "G · 节奏策略",
};
const DIM_FIELD_LABELS: Record<string, string> = {
  A1_appearance: "主角登场", A2_image: "主角形象", A3_cheat: "金手指",
  A4_agency: "主动性", A5_drive: "核心驱动", B1_network: "人物关系网",
  B2_ensemble: "角色聚焦", C1_type: "世界类型", C2_unfold: "设定铺展",
  C3_contrast: "题材突破点", D1_opening_hook: "开篇钩子", D2_early_payoff: "前期爽点",
  D3_chapter_end_hooks: "章末钩子", E1_writing_style: "写作风格", E2_emotion: "情绪基调",
  F1_disclosure: "信息揭露", F2_volume_concept: "第一卷概念", G1_rhythm: "节奏类型",
  G2_early_strategy: "前5章策略",
};
const BASELINE_LABELS: Record<string, string> = {
  narration_pov: "视角人称", tone: "语气", language_register: "语言风格",
  sentence_rhythm: "句式节奏", dialogue_ratio: "对白比", vocabulary_features: "高频词",
};
const PACING_LABELS: Record<string, string> = {
  first_chapter_words: "首章字数", chapter_words: "章字数", first_hook_chapter: "首爆点章",
  antagonist_intro_chapter: "反派出场章", first_face_slap_chapter: "首次反击章",
  info_release_strategy: "信息释放策略",
};

function _valText(v: any): string {
  if (v == null) return "";
  if (Array.isArray(v)) return v.map(_valText).filter(Boolean).join("、");
  if (typeof v === "object") return Object.values(v).map(_valText).filter(Boolean).join("；");
  return String(v);
}

/** 标题 + 一段正文（可视化代替 mono JSON）。 */
function ResultPara({ title, text, accent }: { title: string; text?: any; accent?: string }) {
  const s = _valText(text).trim();
  if (!s) return null;
  return (
    <div style={{ background: "var(--bg-surface-2)", borderRadius: 6, padding: "10px 12px",
      borderLeft: `3px solid ${accent || "var(--border)"}` }}>
      <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 4 }}>{title}</div>
      <div style={{ fontSize: 12, lineHeight: 1.7, color: "var(--text-secondary)", whiteSpace: "pre-wrap" }}>{s}</div>
    </div>
  );
}

/** 对象 → 标签:值 网格（风格基线 / 节奏指南）。 */
function ResultKV({ title, obj, labels, accent }: {
  title: string; obj: any; labels: Record<string, string>; accent?: string;
}) {
  const data = parseMaybeJson(obj);
  if (!data) return null;
  if (typeof data !== "object") return <ResultPara title={title} text={data} accent={accent} />;
  const rows = Object.entries(data).filter(([, v]) => _valText(v).trim());
  if (!rows.length) return null;
  return (
    <div style={{ background: "var(--bg-surface-2)", borderRadius: 6, padding: "10px 12px",
      borderLeft: `3px solid ${accent || "var(--border)"}` }}>
      <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 6 }}>{title}</div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))", gap: "4px 14px" }}>
        {rows.map(([k, v]) => (
          <div key={k} style={{ fontSize: 11.5, lineHeight: 1.6 }}>
            <span style={{ color: "var(--text-tertiary)" }}>{labels[k] || k}：</span>
            <span style={{ color: "var(--text-secondary)" }}>{_valText(v)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function ResultChips({ label, items }: { label: string; items?: any[] }) {
  if (!items || !items.length) return null;
  return (
    <div style={{ fontSize: 11.5, lineHeight: 1.8 }}>
      <span style={{ color: "var(--text-tertiary)" }}>{label}：</span>
      {items.map((it, i) => (
        <span key={i} className="tag" style={{ fontSize: 10, margin: "0 3px 3px 0", display: "inline-block" }}>{String(it)}</span>
      ))}
    </div>
  );
}

/** 提取结果（平台风格档案）— 结构化 / 可视化展示，替代 mono 文本块。 */
function ExtractionResultSection({
  profiles, platform,
}: { profiles: PlatformProfile[]; platform: string; category: string }) {
  const top = (profiles[0] || null) as any;
  const styleDims = parseMaybeJson(top?.style_dimensions_json);
  const neo = parseMaybeJson(top?.neologism_step2_json);
  return (
    <div className="card" style={{ marginTop: 16, borderTop: "3px solid var(--indigo)" }}>
      <div className="card-header" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 6 }}>
        <h3 style={{ margin: 0, fontSize: 14 }}>提取结果（平台风格档案）</h3>
        <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
          {top
            ? `${tPlatform(top.platform)}（整平台）· ${top.confidence_label ? `置信度 ${tConfidence(top.confidence_label)} · ` : ""}样本 ${top.source_works_count ?? "—"} · 更新于 ${top.extraction_completed_at || "—"}`
            : (platform ? `${tPlatform(platform)} 暂无结果` : "请选定平台")}
        </span>
      </div>
      <div className="card-body" style={{ padding: 14 }}>
        {profiles.length === 0 ? (
          <Empty msg={platform ? `${tPlatform(platform)} 暂无提取结果。` : "请选定平台。"} />
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {/* 注入正文（最关键）+ 综述 */}
            <ResultPara title="注入正文（写作风格基线，会注入生成 prompt）" text={top.loader_payload} accent="var(--jade)" />
            <ResultPara title="平台综述" text={top.profile_summary} accent="var(--accent)" />

            {/* 风格基线 / 节奏指南 — 键值网格 */}
            <ResultKV title="风格基线" obj={top.style_baseline} labels={BASELINE_LABELS} accent="var(--indigo)" />
            <ResultKV title="节奏指南" obj={top.pacing_guidance} labels={PACING_LABELS} accent="var(--gold)" />
            <ResultPara title="招牌叙事手法" text={top.signature_devices_description} />

            {/* 生造词 Step2 — chip 组 */}
            {neo && (
              <div style={{ background: "var(--bg-surface-2)", borderRadius: 6, padding: "10px 12px" }}>
                <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 6 }}>生造词 Step2</div>
                <ResultChips label="专有名词" items={neo.proper_nouns} />
                <ResultChips label="人名" items={neo.person_names} />
                <ResultChips label="地名" items={neo.place_names} />
                <ResultChips label="常见字" items={neo.common_chars} />
                {neo.naming_patterns ? <div style={{ fontSize: 11.5, lineHeight: 1.7 }}><span style={{ color: "var(--text-tertiary)" }}>常见构词模式：</span>{_valText(neo.naming_patterns)}</div> : null}
              </div>
            )}

            {/* 行文风格七组 — A-G 卡片网格 */}
            {styleDims && typeof styleDims === "object" && (
              <div>
                <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 8 }}>行文风格七组</div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 10 }}>
                  {Object.entries(styleDims).map(([group, fields]) => (
                    <div key={group} style={{ border: "1px solid var(--border)", borderRadius: 6, padding: 10, background: "var(--bg-surface)" }}>
                      <div style={{ fontSize: 12, fontWeight: 700, marginBottom: 6, color: "var(--accent)" }}>{DIM_GROUP_LABELS[group] || group}</div>
                      {fields && typeof fields === "object" ? (
                        Object.entries(fields as Record<string, any>).filter(([, v]) => _valText(v).trim()).map(([k, v]) => (
                          <div key={k} style={{ fontSize: 11, lineHeight: 1.6, marginBottom: 3 }}>
                            <span style={{ color: "var(--text-tertiary)" }}>{DIM_FIELD_LABELS[k] || k}：</span>
                            <span style={{ color: "var(--text-secondary)" }}>{_valText(v)}</span>
                          </div>
                        ))
                      ) : <div style={{ fontSize: 11 }}>{_valText(fields)}</div>}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
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

  // 分析进度条 + 预计时间：compute_cache 不报细粒度进度，用经过时间相对
  // 一个估计值推进度。估计值随章节量略增（jieba 分词为主要耗时）。
  const [elapsed, setElapsed] = React.useState(0);
  const startRef = React.useRef<number | null>(null);
  const estSec = Math.max(8, Math.min(45, Math.round((nlp?.total_chapters || 300) / 40)));
  React.useEffect(() => {
    if (computing) {
      if (startRef.current == null) startRef.current = Date.now();
      const id = window.setInterval(() => {
        if (startRef.current != null) setElapsed((Date.now() - startRef.current) / 1000);
      }, 250);
      return () => window.clearInterval(id);
    }
    startRef.current = null;
    setElapsed(0);
  }, [computing]);
  const progressPct = computing ? Math.min(95, (elapsed / estSec) * 100) : 0;
  const etaSec = Math.max(0, Math.round(estSec - elapsed));

  const LOOKBACKS: { key: string; label: string }[] = [
    { key: "week", label: "最近7天" }, { key: "month", label: "最近30天" },
    { key: "quarter", label: "最近90天" }, { key: "year", label: "最近365天" },
    { key: "all", label: "全部数据" },
  ];

  return (
    <>
      {/* Controls — 平台 / 时间范围 标签对齐，去掉 Top K 显示 */}
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-body">
          <div style={{ display: "flex", gap: 24, alignItems: "flex-start", flexWrap: "wrap" }}>
            <div>
              <label className="label" style={{ display: "block", marginBottom: 6 }}>平台</label>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", minHeight: 32, alignItems: "center" }}>
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
            <div>
              <label className="label" style={{ display: "block", marginBottom: 6 }}>时间范围</label>
              <div style={{ minHeight: 32, display: "flex", alignItems: "center" }}>
                <select className="select" value={lookback} onChange={e => setLookback(e.target.value)}
                  style={{ minWidth: 130, padding: "6px 10px", lineHeight: 1.4 }}>
                  {LOOKBACKS.map(l => <option key={l.key} value={l.key}>{l.label}</option>)}
                </select>
              </div>
            </div>
            <div style={{ marginLeft: "auto" }}>
              <label className="label" style={{ display: "block", marginBottom: 6, visibility: "hidden" }}>.</label>
              <div style={{ minHeight: 32, display: "flex", alignItems: "center" }}>
                <button className="btn-primary" onClick={() => load(true)} disabled={computing || !platform}>
                  {computing ? "分析中..." : (hasAny ? "重新分析" : "运行分析")}
                </button>
              </div>
            </div>
          </div>
          {/* 进度条 + 预计时间 */}
          {computing && (
            <div style={{ marginTop: 12 }}>
              <div style={{ height: 8, background: "var(--bg-surface-2)", borderRadius: 4, overflow: "hidden" }}>
                <div style={{ width: `${progressPct}%`, height: "100%", background: "var(--accent)", transition: "width 0.25s linear" }} />
              </div>
              <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 4 }}>
                分析中… 已用 {elapsed.toFixed(0)} 秒{etaSec > 0 ? ` · 预计还需约 ${etaSec} 秒` : " · 即将完成"}
              </div>
            </div>
          )}
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

      {/* 开篇章节NLP维度 */}
      {hasNlp && <NlpDimsBlock nlp={nlp!} onReanalyze={() => load(true)} busy={computing} />}
    </>
  );
}


/** 资源管理 tab — 人名（按国家分组、可折叠）/ 常用词 的搜索、CRUD、导入导出
 *  (统一 txt)。归类自高频词或手动新增的词都进入对应 resource，后续基础特征
 *  提取不再识别为高频词。所有调用统一走 /api/analysis/wordlist。 */
type WLItem = { word: string; user: boolean };

/** 词条 chip 网格 + 删除。 */
function WordChips({ items, list, busy, onRemove, emptyMsg }: {
  items: WLItem[]; list: string; busy: boolean;
  onRemove: (word: string, list: string) => void; emptyMsg?: string;
}) {
  if (!items.length) return <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{emptyMsg || "暂无"}</div>;
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
      {items.map(it => (
        <span key={it.word} style={{
          display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12,
          padding: "3px 6px 3px 10px", borderRadius: 4,
          border: `1px solid ${it.user ? "var(--accent)" : "var(--border)"}`,
          background: "var(--bg-surface)",
        }} title={it.user ? "用户添加" : "内置"}>
          {it.word}
          <button onClick={() => onRemove(it.word, list)} disabled={busy}
            style={{ border: "none", background: "none", cursor: "pointer", color: "var(--text-tertiary)", fontSize: 14, lineHeight: 1, padding: 0 }}>×</button>
        </span>
      ))}
    </div>
  );
}

/** 国家 section（中文/日本/西方/用户添加）— 标题旁带 姓氏/名字 切换。 */
function NameCountrySection({ title, surnames, given, busy, onRemove }: {
  title: string; surnames: WLItem[]; given: WLItem[];
  busy: boolean; onRemove: (word: string, list: string) => void;
}) {
  const [kind, setKind] = React.useState<"surnames" | "given">("surnames");
  const items = kind === "surnames" ? surnames : given;
  return (
    <div className="card">
      <div className="card-header" style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <h3 style={{ margin: 0, fontSize: 14 }}>{title}</h3>
        <div style={{ display: "flex", gap: 4, padding: 3, borderRadius: 8, background: "var(--bg-surface-2)" }}>
          {([["surnames", "姓氏"], ["given", "名字"]] as const).map(([k, lbl]) => (
            <button key={k} onClick={() => setKind(k)} style={{
              fontSize: 11, padding: "2px 14px", borderRadius: 6, cursor: "pointer", border: "none",
              background: kind === k ? "var(--accent)" : "transparent",
              color: kind === k ? "white" : "var(--text-secondary)", fontWeight: kind === k ? 600 : 400,
            }}>{lbl}</button>
          ))}
        </div>
        <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-tertiary)" }}>{items.length} 条</span>
      </div>
      <div className="card-body">
        <WordChips items={items} list={kind === "surnames" ? "surnames" : "given_names"}
          busy={busy} onRemove={onRemove} />
      </div>
    </div>
  );
}

/** 资源管理 tab — 人名（中文/日本/西方 三国 section，各自切换 姓氏/名字）/ 常用词。 */
function ResourceManagerTab() {
  const { toast } = useToast();
  type WLGroup = { group: string; items: WLItem[] };
  type CommonData = { total: number; groups: WLGroup[] };
  type Kind2 = { surnames: WLItem[]; given: WLItem[] };
  type NamesData = { countries: string[]; data: Record<string, Kind2>; user_added: Kind2 };

  const [topCat, setTopCat] = React.useState<"names" | "common">("names");
  const [q, setQ] = React.useState("");
  const [names, setNames] = React.useState<NamesData | null>(null);
  const [common, setCommon] = React.useState<CommonData | null>(null);
  const [addKind, setAddKind] = React.useState<"surnames" | "given_names">("surnames");
  const [newWord, setNewWord] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  const fileRef = React.useRef<HTMLInputElement>(null);

  const reload = React.useCallback(async () => {
    try {
      if (topCat === "names") {
        setNames(await apiGet<NamesData>(`/api/analysis/wordlist/names?q=${encodeURIComponent(q)}`));
      } else {
        setCommon(await apiGet<CommonData>(`/api/analysis/wordlist/grouped?list=common_words&q=${encodeURIComponent(q)}`));
      }
    } catch (e: any) { toast(`加载资源失败：${e.message}`, "error"); }
  }, [topCat, q, toast]);

  React.useEffect(() => {
    const t = window.setTimeout(reload, 200);   // debounce 搜索
    return () => window.clearTimeout(t);
  }, [reload]);

  const addList = topCat === "common" ? "common_words" : addKind;
  const add = async () => {
    const w = newWord.trim();
    if (!w) return;
    setBusy(true);
    try {
      const r = await apiPost<{ added: boolean }>("/api/analysis/wordlist/add", { list: addList, word: w });
      toast(r.added ? `已添加「${w}」` : `「${w}」已存在`, r.added ? "success" : "info");
      setNewWord("");
      reload();
    } catch (e: any) { toast(`添加失败：${e.message}`, "error"); }
    finally { setBusy(false); }
  };
  const remove = async (word: string, list: string) => {
    setBusy(true);
    try { await apiPost("/api/analysis/wordlist/remove", { list, word }); reload(); }
    catch (e: any) { toast(`删除失败：${e.message}`, "error"); }
    finally { setBusy(false); }
  };
  const doImport = async (file: File) => {
    setBusy(true);
    try {
      const content = await file.text();
      const r = await apiPost<{ added: number }>("/api/analysis/wordlist/import", { list: addList, content });
      toast(`已导入 ${r.added} 个新词`, "success");
      reload();
    } catch (e: any) { toast(`导入失败：${e.message}`, "error"); }
    finally { setBusy(false); if (fileRef.current) fileRef.current.value = ""; }
  };
  const doExport = async (list: string) => {
    try {
      const res = await fetch(`/api/analysis/wordlist/export?list=${list}`);
      const text = await res.text();
      const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
      const a = document.createElement("a");
      a.href = url; a.download = `${list}.txt`;
      document.body.appendChild(a); a.click();
      document.body.removeChild(a); URL.revokeObjectURL(url);
    } catch (e: any) { toast(`导出失败：${e.message}`, "error"); }
  };

  const addKindLabel = addKind === "surnames" ? "姓" : "名";
  const hasUser = !!names && (names.user_added.surnames.length > 0 || names.user_added.given.length > 0);

  return (
    <>
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {/* 资源类型 + 搜索 */}
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
            <div style={{ display: "flex", gap: 6 }}>
              {([["names", "人名"], ["common", "常用词"]] as const).map(([k, lbl]) => (
                <button key={k} className={topCat === k ? "btn-primary" : "btn"}
                  style={{ fontSize: 12, padding: "5px 16px", borderRadius: 20 }}
                  onClick={() => setTopCat(k)}>{lbl}</button>
              ))}
            </div>
            <input className="input" value={q} onChange={e => setQ(e.target.value)}
              placeholder="搜索…" style={{ flex: 1, minWidth: 160, maxWidth: 300, padding: "7px 12px" }} />
          </div>
          {/* 新增 + 导入导出 */}
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            {topCat === "names" && (
              <div style={{ display: "flex", gap: 4, padding: 3, borderRadius: 8, background: "var(--bg-surface-2)" }}>
                {([["surnames", "姓"], ["given_names", "名"]] as const).map(([k, lbl]) => (
                  <button key={k} onClick={() => setAddKind(k)} style={{
                    fontSize: 11, padding: "3px 14px", borderRadius: 6, cursor: "pointer", border: "none",
                    background: addKind === k ? "var(--accent)" : "transparent",
                    color: addKind === k ? "white" : "var(--text-secondary)", fontWeight: addKind === k ? 600 : 400,
                  }}>{lbl}</button>
                ))}
              </div>
            )}
            <input className="input" value={newWord} onChange={e => setNewWord(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") add(); }}
              placeholder={topCat === "names" ? `新增${addKindLabel}` : "新增常用词"}
              style={{ width: 160, padding: "7px 12px" }} />
            <button className="btn-primary" disabled={busy || !newWord.trim()}
              onClick={add} style={{ fontSize: 12, padding: "7px 18px" }}>添加</button>
            <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
              <button className="btn" style={{ fontSize: 12, padding: "6px 14px" }}
                disabled={busy} onClick={() => fileRef.current?.click()}>
                导入{topCat === "names" ? addKindLabel : ""} txt
              </button>
              <button className="btn" style={{ fontSize: 12, padding: "6px 14px" }}
                onClick={() => doExport(addList)}>导出 txt</button>
              <input ref={fileRef} type="file" accept=".txt,text/plain" style={{ display: "none" }}
                onChange={e => { const f = e.target.files?.[0]; if (f) doImport(f); }} />
            </div>
          </div>
        </div>
      </div>

      {topCat === "names" ? (
        !names ? <div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>加载中…</div> : (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {names.countries.map(country => (
              <NameCountrySection key={country} title={country}
                surnames={names.data[country].surnames} given={names.data[country].given}
                busy={busy} onRemove={remove} />
            ))}
            {hasUser && (
              <NameCountrySection title="用户添加"
                surnames={names.user_added.surnames} given={names.user_added.given}
                busy={busy} onRemove={remove} />
            )}
          </div>
        )
      ) : (
        !common ? <div style={{ fontSize: 12, color: "var(--text-tertiary)" }}>加载中…</div> :
          common.total === 0 ? <Empty msg={q ? `没有匹配「${q}」的常用词。` : "常用词资源为空。"} /> : (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {common.groups.map(grp => (
                <div key={grp.group} className="card">
                  <div className="card-header">
                    <h3 style={{ margin: 0, fontSize: 14 }}>{grp.group}
                      <span style={{ fontSize: 11, fontWeight: 400, color: "var(--text-tertiary)", marginLeft: 8 }}>{grp.items.length}</span>
                    </h3>
                  </div>
                  <div className="card-body">
                    <WordChips items={grp.items} list="common_words" busy={busy} onRemove={remove} />
                  </div>
                </div>
              ))}
            </div>
          )
      )}
    </>
  );
}


/** 趋势箭头：窗口内百分比变化。正=升(jade)、负=降(accent)、零=平。 */
function TrendArrow({ v }: { v: number | null | undefined }) {
  if (v == null || isNaN(v)) return <span style={{ color: "var(--text-disabled)", fontSize: 11 }}>—</span>;
  const up = v > 0.0005, down = v < -0.0005;
  const color = up ? "var(--jade)" : down ? "var(--accent)" : "var(--text-tertiary)";
  return <span className="font-mono" style={{ color, fontSize: 11, whiteSpace: "nowrap" }}>{up ? "↑" : down ? "↓" : "→"} {(Math.abs(v) * 100).toFixed(1)}%</span>;
}

const fmtNum = (v: number | null | undefined, d = 0) =>
  v == null || isNaN(v as number) ? "—" : Number(v).toFixed(d);

/** 大数缩写：12345 → 1.2万，1.2e8 → 1.2亿。用于热度列避免溢出错位。 */
const fmtBig = (v: number | null | undefined) => {
  if (v == null || isNaN(v as number)) return "—";
  const n = Number(v);
  if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(1) + "亿";
  if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(1) + "万";
  return String(Math.round(n));
};

interface MetricRow {
  name: string;
  total?: number; count_pct?: number;
  avg_heat?: number; heat_pct?: number;
  latest_share?: number; share_pct?: number;
  new_count?: number; parent?: string;
}

/** One metric as its own small block: ranked list (top 10) with a bar +
 *  百分比趋势. 数量 / 热度 / 份额 各自成块。``fmt`` formats the value (热度
 *  用万/亿缩写避免大数错位)。 */
function MetricMiniBlock({
  title, rows, valueKey, pctKey, color, fmt = "int", hideTrend = false,
}: {
  title: string; rows: MetricRow[];
  valueKey: keyof MetricRow; pctKey: keyof MetricRow;
  color: string; fmt?: "int" | "big" | "share"; hideTrend?: boolean;
}) {
  const sorted = [...rows]
    .filter(r => (r[valueKey] as number) != null)
    .sort((a, b) => ((b[valueKey] as number) || 0) - ((a[valueKey] as number) || 0))
    .slice(0, 10);
  const max = Math.max(1, ...sorted.map(r => (r[valueKey] as number) || 0));
  // 市场份额展示为百分比（12.3%），不再用小数。
  const fmtVal = (v: any) =>
    fmt === "big" ? fmtBig(v)
      : fmt === "share" ? (v == null || isNaN(v as number) ? "—" : (Number(v) * 100).toFixed(1) + "%")
      : fmtNum(v, 0);
  const valW = fmt === "big" ? 56 : fmt === "share" ? 52 : 44;
  return (
    <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 6, padding: 10 }}>
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>{title}</div>
      {sorted.length === 0 ? (
        <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>暂无</div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 5 }}>
          {sorted.map((r, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11 }}>
              <span style={{ width: 60, color: "var(--text-secondary)", whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis", flexShrink: 0 }} title={r.name}>{r.name}</span>
              <div style={{ flex: 1, minWidth: 18 }}><MiniBar value={(r[valueKey] as number) || 0} max={max} color={color} /></div>
              <span className="font-mono" style={{ minWidth: valW, textAlign: "right", flexShrink: 0, whiteSpace: "nowrap" }}>{fmtVal(r[valueKey])}</span>
              {/* 趋势列按字符串自适应宽度 + 不换行：+1038.9% 这类长涨幅不再折成两行 */}
              {!hideTrend && <span style={{ minWidth: 52, textAlign: "right", flexShrink: 0, whiteSpace: "nowrap" }}><TrendArrow v={r[pctKey] as number} /></span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** 标签共现表格 + 可视化（番茄）。 */
function CooccurTable({ pairs }: { pairs: any[] }) {
  const rows = (pairs || []).slice(0, 12);
  const max = Math.max(1, ...rows.map((p: any) => p.count || 0));
  return (
    <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 6, padding: 10 }}>
      <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>标签共现（高频组合）</div>
      {rows.length === 0 ? (
        <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>暂无</div>
      ) : (
        <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
          <thead><tr style={{ color: "var(--text-tertiary)", textAlign: "left" }}>
            <th style={{ padding: "3px 6px" }}>标签 A</th>
            <th style={{ padding: "3px 6px" }}>标签 B</th>
            <th style={{ padding: "3px 6px" }}>共现次数</th>
          </tr></thead>
          <tbody>
            {rows.map((p: any, i: number) => (
              <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                <td style={{ padding: "3px 6px" }}><span className="tag" style={{ fontSize: 10 }}>{p.tag_a}</span></td>
                <td style={{ padding: "3px 6px" }}><span className="tag" style={{ fontSize: 10 }}>{p.tag_b}</span></td>
                <td style={{ padding: "3px 6px", minWidth: 90 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                    <div style={{ flex: 1 }}><MiniBar value={p.count || 0} max={max} color="var(--indigo)" /></div>
                    <span className="font-mono" style={{ minWidth: 24, textAlign: "right" }}>{p.count}</span>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

/** 市场信息可视化：spec 2.1.3.2 全部维度。数量取自数据库真实小说计数，
 *  热度/份额及各自趋势取自榜单分析；数量/热度/份额各自成块。开书机会
 *  综合「份额/热度增长 + 既有份额/热度 + 跨类目新书占比(和为100%)」。 */
function MarketInfoBlock({ market, platform }: { market: any; platform: string }) {
  const isQidian = platform === "qidian";
  const panel = market.panel || { categories: [], tags: [] };

  // Panel rows are computed server-side directly from the DB (数量=库内
  // 小说数, 热度=该分类作品热度之和, 份额/趋势/新书 from snapshots).
  // 起点的 大分类/副分类 过滤 + 父级(parent) 已在后端按 taxonomy 完成。
  const toRows = (items: any[]): MetricRow[] =>
    (items || []).map(it => ({
      name: it.name,
      total: it.total,
      count_pct: it.count_pct,
      avg_heat: it.avg_heat,
      heat_pct: it.heat_pct,
      latest_share: it.latest_share,
      share_pct: it.share_pct,
      new_count: it.new_count,
      parent: it.parent,
    } as MetricRow & { new_count?: number; parent?: string }))
    .sort((a, b) => (b.total || 0) - (a.total || 0));

  let catRows = toRows(panel.categories);
  let tagRows = toRows(panel.tags);
  // Cross-list dedupe: a 大分类/类目 name never repeats as a 副分类/标签.
  const catNameSet = new Set(catRows.map(r => r.name));
  tagRows = tagRows.filter(r => !catNameSet.has(r.name));
  catRows = catRows.slice(0, 10);
  tagRows = tagRows.slice(0, 10);

  const catLabel = isQidian ? "大分类" : "类目";
  const tagLabel = isQidian ? "副分类" : "标签";
  const showCooccur = !isQidian;     // 起点没有标签共现
  // 番茄榜单已按题材分类：份额恒为 1 且不变 → 不展示份额 block；类目数量
  // 也固定（每榜单前 30）→ 不展示数量趋势。
  const showShare = isQidian;
  const hideCatCountTrend = !isQidian;
  // 开书机会：起点细到「大分类·副分类」(轻小说·原生幻想)；番茄改用标签
  // （番茄每类目取固定数量新书，类目新书占比无意义）。新书统一为「来自
  // 新书榜」(panel.new_count)。
  const oppBySub = (isQidian || !isQidian) && tagRows.length > 0;  // 两平台都用标签级
  const oppSource = oppBySub ? tagRows : (catRows.length ? catRows : tagRows);
  const parentOf = (name: string) =>
    (oppSource.find(r => r.name === name) as any)?.parent || "其他";
  const oppLabel = (name: string) =>
    isQidian && oppBySub ? `${parentOf(name)}·${name}` : name;
  const oppNewTotal = oppSource.reduce((s, r) => s + ((r as any).new_count || 0), 0) || 1;
  const mk = (key: keyof MetricRow) => {
    const vals = oppSource.map(r => (r[key] as number) || 0);
    const mx = Math.max(...vals, 1e-9), mn = Math.min(...vals, 0);
    return (v: number) => (mx > mn ? (v - mn) / (mx - mn) : 0);
  };
  const nSharePct = mk("share_pct"), nHeatPct = mk("heat_pct");
  const nShare = mk("latest_share"), nHeat = mk("avg_heat");
  const oppRows = oppSource.map(r => {
    const newShare = ((r as any).new_count || 0) / oppNewTotal;
    const growth = showShare
      ? (nSharePct(r.share_pct || 0) + nHeatPct(r.heat_pct || 0)) / 2
      : nHeatPct(r.heat_pct || 0);
    const base = showShare
      ? (nShare(r.latest_share || 0) + nHeat(r.avg_heat || 0)) / 2
      : nHeat(r.avg_heat || 0);
    const score = 0.4 * growth + 0.3 * newShare + 0.3 * base;
    return { name: r.name, label: oppLabel(r.name), score, newShare };
  }).sort((a, b) => b.score - a.score).slice(0, 10);
  const oppMax = Math.max(1e-9, ...oppRows.map(o => o.score));
  const oppColLabel = isQidian ? "大分类·副分类" : "标签";

  return (
    <div className="card" style={{ marginBottom: 14, borderTop: "3px solid var(--indigo)" }}>
      <div className="card-header"><h3 style={{ margin: 0, fontSize: 14 }}>市场信息</h3></div>
      <div className="card-body">
        {/* 大分类 / 类目：数量·热度(·份额) 各自成块 */}
        <div style={{ fontSize: 13, fontWeight: 700, margin: "2px 0 8px" }}>{catLabel}</div>
        <div style={{ display: "grid", gridTemplateColumns: showShare ? "1fr 1fr 1fr" : "1fr 1fr", gap: 12, marginBottom: 16 }}>
          <MetricMiniBlock title="数量" rows={catRows} valueKey="total" pctKey="count_pct" color="var(--accent)" fmt="int" hideTrend={hideCatCountTrend} />
          <MetricMiniBlock title="热度" rows={catRows} valueKey="avg_heat" pctKey="heat_pct" color="var(--gold)" fmt="big" />
          {showShare && <MetricMiniBlock title="市场份额" rows={catRows} valueKey="latest_share" pctKey="share_pct" color="var(--jade)" fmt="share" />}
        </div>

        {/* 副分类 / 标签：数量·热度(·份额) 各自成块 */}
        <div style={{ fontSize: 13, fontWeight: 700, margin: "2px 0 8px" }}>{tagLabel}</div>
        <div style={{ display: "grid", gridTemplateColumns: showShare ? "1fr 1fr 1fr" : "1fr 1fr", gap: 12, marginBottom: 16 }}>
          <MetricMiniBlock title="数量" rows={tagRows} valueKey="total" pctKey="count_pct" color="var(--accent)" fmt="int" />
          <MetricMiniBlock title="热度" rows={tagRows} valueKey="avg_heat" pctKey="heat_pct" color="var(--gold)" fmt="big" />
          {showShare && <MetricMiniBlock title="市场份额" rows={tagRows} valueKey="latest_share" pctKey="share_pct" color="var(--jade)" fmt="share" />}
        </div>

        {/* 开书机会 + （番茄）标签共现 */}
        <div style={{ display: "grid", gridTemplateColumns: showCooccur ? "1fr 1fr" : "1fr", gap: 12 }}>
          <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 6, padding: 10 }}>
            <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>开书机会</div>
            {oppRows.length === 0 ? <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>暂无</div> : (
              <table style={{ width: "100%", fontSize: 11, borderCollapse: "collapse" }}>
                <thead><tr style={{ color: "var(--text-tertiary)", textAlign: "left" }}>
                  <th style={{ padding: "3px 6px" }}>{oppColLabel}</th>
                  <th style={{ padding: "3px 6px" }}>机会指数</th>
                  <th style={{ padding: "3px 6px", textAlign: "right" }}>新书占比</th>
                </tr></thead>
                <tbody>
                  {oppRows.map((o, i) => (
                    <tr key={i} style={{ borderTop: "1px solid var(--border)" }}>
                      <td style={{ padding: "3px 6px" }}>{o.label}</td>
                      <td style={{ padding: "3px 6px", minWidth: 90 }}>
                        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                          <div style={{ flex: 1 }}><MiniBar value={o.score} max={oppMax} color="var(--jade)" /></div>
                          <span className="font-mono" style={{ minWidth: 34, textAlign: "right", color: "var(--jade)", fontWeight: 600 }}>{(o.score * 100).toFixed(0)}</span>
                        </div>
                      </td>
                      <td style={{ padding: "3px 6px", textAlign: "right" }} className="font-mono">{(o.newShare * 100).toFixed(1)}%</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
          {showCooccur && <CooccurTable pairs={market.pairs} />}
        </div>
      </div>
    </div>
  );
}


/** 在片段里高亮高频词。 */
function highlightWord(text: string, word: string): React.ReactNode {
  if (!word || !text.includes(word)) return text;
  const parts = text.split(word);
  return parts.map((p, i) => (
    <React.Fragment key={i}>
      {p}
      {i < parts.length - 1 && (
        <span style={{ color: "var(--accent)", fontWeight: 600 }}>{word}</span>
      )}
    </React.Fragment>
  ));
}

/** 单个高频词 chip：点击展开「使用最多的作品」+ 归类按钮。 */
function HighFreqWord({ w, selected, busy, onSelect }: {
  w: any; selected: boolean; busy: boolean; onSelect: () => void;
}) {
  return (
    <button
      onClick={onSelect}
      style={{
        fontSize: 11, padding: "3px 9px", borderRadius: 4, cursor: "pointer",
        border: `1px solid ${selected ? "var(--accent)" : "var(--border)"}`,
        background: selected ? "var(--bg-surface-2)" : "var(--bg-surface)",
        color: "var(--text-primary)", opacity: busy ? 0.5 : 1,
      }}
    >
      {w.word}<span style={{ color: "var(--text-tertiary)", marginLeft: 4 }}>{w.count}</span>
    </button>
  );
}

/** 开篇章节NLP维度：首章/章均/章中位字数、平均句长、分类型标点密度、高频词。 */
function NlpDimsBlock({ nlp, onReanalyze, busy: parentBusy }: {
  nlp: any; onReanalyze?: () => void; busy?: boolean;
}) {
  const { toast } = useToast();
  const [selWord, setSelWord] = React.useState<string | null>(null);
  const [classifying, setClassifying] = React.useState<string | null>(null);
  const [dirty, setDirty] = React.useState(false);   // 词库被归类修改、待重算

  // 归类某高频词为 人名 / 常用词 → 写入资源；不立即重算，仅在高频词上方标记
  // 「词库已变更」，由用户点「重新分析」统一触发（避免每归类一个就重算）。
  const classify = async (word: string, list: "given_names" | "common_words") => {
    setClassifying(word);
    try {
      await apiPost("/api/analysis/wordlist/add", { list, word });
      toast(`已将「${word}」归为${list === "given_names" ? "人名" : "常用词"}`, "success");
      setSelWord(null);
      setDirty(true);
    } catch (e: any) {
      toast(`归类失败：${e.message}`, "error");
    } finally {
      setClassifying(null);
    }
  };

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
        <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 14, marginBottom: 14 }}>
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
        </div>
        {/* 词库变更提示 — 紧贴高频词上方 */}
        {dirty && (
          <div style={{
            display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap",
            fontSize: 12, padding: "8px 12px", marginBottom: 10, borderRadius: 6,
            background: "var(--bg-surface-2)", borderLeft: "3px solid var(--accent)",
          }}>
            词库已变更（人名 / 常用词），需要重新分析后生效。
            <button className="btn-primary" disabled={parentBusy}
              style={{ fontSize: 11, padding: "3px 12px" }}
              onClick={() => { setDirty(false); onReanalyze?.(); }}>重新分析</button>
          </div>
        )}
        {/* 高频词（满宽）— 点击展开「使用最多的作品」+ 归类按钮 */}
        <div style={{ background: "var(--bg-surface)", border: "1px solid var(--border)", borderRadius: 6, padding: 10 }}>
          <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 8 }}>高频词</div>
          {(spec.top_words || []).length === 0 ? <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>暂无</div> : (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {(spec.top_words || []).slice(0, 40).map((w: any) => (
                <HighFreqWord
                  key={w.word}
                  w={w}
                  selected={selWord === w.word}
                  busy={classifying === w.word}
                  onSelect={() => setSelWord(prev => prev === w.word ? null : w.word)}
                />
              ))}
            </div>
          )}
          {selWord && (() => {
            const sel = (spec.top_words || []).find((w: any) => w.word === selWord);
            if (!sel) return null;
            const examples = sel.examples || [];
            const busy = classifying === sel.word;
            return (
              <div style={{ marginTop: 10, paddingTop: 10, borderTop: "1px dashed var(--border)" }}>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, marginBottom: 8 }}>
                  <div style={{ fontSize: 11, fontWeight: 600 }}>「{sel.word}」使用最多的作品</div>
                  <div style={{ display: "flex", gap: 6 }}>
                    <button className="btn" disabled={busy} style={{ fontSize: 10, padding: "3px 10px" }}
                      onClick={() => classify(sel.word, "given_names")}>归为人名</button>
                    <button className="btn" disabled={busy} style={{ fontSize: 10, padding: "3px 10px" }}
                      onClick={() => classify(sel.word, "common_words")}>归为常用词</button>
                  </div>
                </div>
                {examples.length === 0 ? (
                  <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>暂无可定位的作品片段。</div>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    {examples.map((ex: any, i: number) => (
                      <div key={i} style={{ fontSize: 11, lineHeight: 1.5 }}>
                        <span style={{ fontWeight: 600 }}>{i + 1}.《{ex.title}》</span>
                        <span style={{ color: "var(--accent)", marginLeft: 6 }}>×{ex.count}</span>
                        {ex.sentence && (
                          <div style={{ color: "var(--text-secondary)", marginTop: 2, paddingLeft: 16 }}>
                            {highlightWord(ex.sentence, sel.word)}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })()}
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
