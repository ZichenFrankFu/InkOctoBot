/**
 * MarketFeatureExtractionPage — operations console for the
 * /api/market-extractor backend.
 *
 * Layout: left = job-launch form (platform / category + run button),
 * right = results tabbed view (jobs / platform profiles / works /
 * per-work preview).
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiGet, apiPost } from "../api/client";
import { useToast } from "../components/shared/Toast";


type ResultTab = "jobs" | "profiles" | "works" | "preview";


interface Job {
  job_id: string;
  platform?: string;
  category?: string;
  state?: string;
  created_at?: string;
  finished_at?: string;
  error?: string;
  progress?: number;
}


interface PlatformProfile {
  profile_id?: string;
  platform: string;
  genre?: string;
  category?: string;
  directive_text?: string;
  sample_count?: number;
  updated_at?: string;
}


interface RepresentativeWork {
  work_id: string;
  title?: string;
  author?: string;
  rank?: number;
  word_count?: number;
}


const PLATFORM_OPTIONS = [
  { key: "qidian", label: "起点中文网" },
  { key: "fanqie", label: "番茄小说" },
  { key: "ciweimao", label: "刺猬猫" },
  { key: "zongheng", label: "纵横中文" },
];
const CATEGORY_OPTIONS = [
  "都市", "玄幻", "科幻", "历史", "悬疑", "军事",
  "言情", "二次元", "武侠", "仙侠", "灵异", "游戏", "体育",
];


const STATE_COLOR: Record<string, string> = {
  queued:    "var(--text-tertiary)",
  running:   "var(--accent)",
  completed: "var(--success)",
  failed:    "var(--danger)",
  cancelled: "var(--text-disabled)",
};


export default function MarketFeatureExtractionPage() {
  const { toast } = useToast();
  const [platform, setPlatform] = useState(PLATFORM_OPTIONS[0].key);
  const [category, setCategory] = useState(CATEGORY_OPTIONS[0]);
  const [launching, setLaunching] = useState(false);

  const [jobs, setJobs] = useState<Job[]>([]);
  const [profiles, setProfiles] = useState<PlatformProfile[]>([]);
  const [works, setWorks] = useState<RepresentativeWork[]>([]);
  const [selectedWork, setSelectedWork] = useState<RepresentativeWork | null>(null);
  const [chapterFeatures, setChapterFeatures] = useState<any[]>([]);
  const [neologisms, setNeologisms] = useState<any[]>([]);

  const [tab, setTab] = useState<ResultTab>("jobs");
  const [loading, setLoading] = useState(false);

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

  const refreshAll = useCallback(async () => {
    setLoading(true);
    await Promise.all([refreshJobs(), refreshProfiles(), refreshWorks(platform, category)]);
    setLoading(false);
  }, [refreshJobs, refreshProfiles, refreshWorks, platform, category]);

  useEffect(() => { refreshAll(); }, [refreshAll]);

  useEffect(() => {
    const active = jobs.some(j => j.state === "queued" || j.state === "running");
    if (!active) return;
    const id = window.setInterval(refreshJobs, 5000);
    return () => window.clearInterval(id);
  }, [jobs, refreshJobs]);

  const launchJob = useCallback(async () => {
    if (!platform || !category) {
      toast("请先选择平台 + 类别", "error");
      return;
    }
    if (!window.confirm(
      `准备启动提取任务：\n平台: ${platform}\n类别: ${category}\n\n` +
      `会调用 LLM 抽取代表作的 chapter features 与 neologisms，` +
      `最终更新 platform profile（生成时自动注入 platform_directive loader）。\n\n` +
      `可能耗时数分钟。继续？`,
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
      && (!category || (p.category === category || p.genre === category)),
    );
  }, [profiles, platform, category]);

  return (
    <div className="page-container" style={{ padding: "16px 20px", maxWidth: 1400, margin: "0 auto" }}>
      <div className="page-header" style={{ paddingBottom: 12 }}>
        <div className="page-header-row">
          <div>
            <h2>市场特征提取</h2>
            <p>选择平台 + 类别 → 启动 LLM 提取 → 查看 chapter features / 新词 / 平台 profile</p>
          </div>
          <button className="btn" onClick={refreshAll} disabled={loading}>
            {loading ? "刷新中..." : "刷新"}
          </button>
        </div>
      </div>

      <div style={{
        display: "grid",
        gridTemplateColumns: "320px 1fr",
        gap: 16,
        alignItems: "start",
      }}>
        {/* LEFT — Launch panel */}
        <div className="card" style={{ padding: 14 }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>启动新任务</h3>

          <div style={{ marginTop: 12 }}>
            <label style={{ fontSize: 12, color: "var(--text-secondary)" }}>平台</label>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4 }}>
              {PLATFORM_OPTIONS.map(p => (
                <button
                  key={p.key}
                  className={platform === p.key ? "btn-primary" : "btn"}
                  style={{ fontSize: 11, padding: "3px 10px" }}
                  onClick={() => { setPlatform(p.key); refreshWorks(p.key, category); }}
                >{p.label}</button>
              ))}
            </div>
          </div>

          <div style={{ marginTop: 14 }}>
            <label style={{ fontSize: 12, color: "var(--text-secondary)" }}>类别</label>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4 }}>
              {CATEGORY_OPTIONS.map(c => (
                <button
                  key={c}
                  className={category === c ? "btn-primary" : "btn"}
                  style={{ fontSize: 11, padding: "3px 10px" }}
                  onClick={() => { setCategory(c); refreshWorks(platform, c); }}
                >{c}</button>
              ))}
            </div>
          </div>

          <button
            className="btn-primary"
            onClick={launchJob}
            disabled={launching || !platform || !category}
            style={{ width: "100%", marginTop: 16, padding: "8px 0" }}
          >
            {launching ? "启动中..." : "启动 LLM 提取"}
          </button>

          <div style={{ marginTop: 14, paddingTop: 12, borderTop: "1px solid var(--border)" }}>
            <div style={{ fontSize: 11, color: "var(--text-tertiary)", lineHeight: 1.55 }}>
              本任务会从市场库选取该 (平台×类别) 的代表作，
              对每部代表作的前若干章节做特征抽取（节奏 / 情感弧 / 钩子 / 新词），
              最后汇总成一份 platform profile。在章节生成时由
              <code style={{ fontSize: 10 }}> platform_directive </code>loader 自动注入。
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
            {tab === "jobs" && <JobsTab jobs={jobs} />}
            {tab === "profiles" && <ProfilesTab profiles={platformProfiles} />}
            {tab === "works" && (
              <WorksTab
                works={works}
                onSelect={openWorkPreview}
                emptyHint={works.length === 0
                  ? `没有 (${platform}×${category}) 的代表作。先在市场库灌入数据，然后启动一次提取任务。`
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
    </div>
  );
}


function JobsTab({ jobs }: { jobs: Job[] }) {
  if (jobs.length === 0) {
    return <Empty msg="还没有任何任务。在左侧选好平台+类别后点「启动 LLM 提取」。" />;
  }
  return (
    <table style={{ width: "100%", fontSize: 12 }}>
      <thead>
        <tr style={{ color: "var(--text-tertiary)", textAlign: "left" }}>
          <th style={{ padding: "6px" }}>Job</th>
          <th style={{ padding: "6px" }}>平台</th>
          <th style={{ padding: "6px" }}>类别</th>
          <th style={{ padding: "6px" }}>状态</th>
          <th style={{ padding: "6px" }}>进度</th>
          <th style={{ padding: "6px" }}>创建</th>
          <th style={{ padding: "6px" }}>结束</th>
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
            </td>
            <td style={{ padding: "6px" }}>{typeof j.progress === "number" ? `${Math.round(j.progress * 100)}%` : "—"}</td>
            <td style={{ padding: "6px" }}>{j.created_at || "—"}</td>
            <td style={{ padding: "6px" }}>{j.finished_at || "—"}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}


function ProfilesTab({ profiles }: { profiles: PlatformProfile[] }) {
  if (profiles.length === 0) {
    return <Empty msg="尚无匹配当前平台/类别的 profile。任务跑完后会自动出现。" />;
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {profiles.map((p, i) => (
        <div key={p.profile_id || `${p.platform}-${p.genre || i}`}
             className="card" style={{ padding: 12, background: "var(--bg-surface-2)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6 }}>
            <strong>{p.platform} · {p.genre || p.category || "—"}</strong>
            <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
              样本 {p.sample_count ?? "—"} · 更新 {p.updated_at || "—"}
            </span>
          </div>
          <pre style={{
            margin: 0, padding: 8, fontSize: 11,
            background: "var(--bg-surface)",
            borderRadius: 4, maxHeight: 240, overflow: "auto",
            whiteSpace: "pre-wrap", wordBreak: "break-word",
            fontFamily: "var(--font-mono)",
          }}>{p.directive_text || "（profile 内容为空）"}</pre>
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
          <th style={{ padding: "6px" }}>排名</th>
          <th style={{ padding: "6px" }}>书名</th>
          <th style={{ padding: "6px" }}>作者</th>
          <th style={{ padding: "6px" }}>字数</th>
          <th style={{ padding: "6px" }}></th>
        </tr>
      </thead>
      <tbody>
        {works.map(w => (
          <tr key={w.work_id} style={{ borderTop: "1px solid var(--border)" }}>
            <td style={{ padding: "6px" }}>{w.rank ?? "—"}</td>
            <td style={{ padding: "6px" }}><strong>{w.title || w.work_id.slice(0, 8)}</strong></td>
            <td style={{ padding: "6px" }}>{w.author || "—"}</td>
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
        <div style={{ maxHeight: 260, overflow: "auto", border: "1px solid var(--border)", borderRadius: 4 }}>
          <table style={{ width: "100%", fontSize: 11 }}>
            <thead style={{ position: "sticky", top: 0, background: "var(--bg-surface)" }}>
              <tr style={{ color: "var(--text-tertiary)" }}>
                <th style={{ padding: "4px 6px", textAlign: "left" }}>章</th>
                <th style={{ padding: "4px 6px", textAlign: "left" }}>开篇模式</th>
                <th style={{ padding: "4px 6px", textAlign: "left" }}>钩子密度</th>
                <th style={{ padding: "4px 6px", textAlign: "left" }}>信息密度</th>
                <th style={{ padding: "4px 6px", textAlign: "left" }}>节奏标签</th>
              </tr>
            </thead>
            <tbody>
              {chapterFeatures.slice(0, 30).map((cf, i) => (
                <tr key={cf.id || i} style={{ borderTop: "1px solid var(--border)" }}>
                  <td style={{ padding: "4px 6px" }}>{cf.chapter_num}</td>
                  <td style={{ padding: "4px 6px" }}>{cf.opening_pattern || "—"}</td>
                  <td style={{ padding: "4px 6px" }}>{cf.hook_density ?? "—"}</td>
                  <td style={{ padding: "4px 6px" }}>{cf.info_density ?? "—"}</td>
                  <td style={{ padding: "4px 6px" }}>{cf.pacing_tag || "—"}</td>
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
          {neologisms.slice(0, 50).map((n, i) => (
            <span key={n.id || i} style={{
              padding: "2px 8px", borderRadius: 10,
              background: "var(--bg-surface-2)", fontSize: 11,
            }}>
              {n.word} <span style={{ color: "var(--text-tertiary)" }}>({n.frequency_in_5_chapters})</span>
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
