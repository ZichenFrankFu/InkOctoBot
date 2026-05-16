import React, { useCallback, useEffect, useRef, useState } from "react";
import { apiGet, apiPost, apiPut } from "../../api/client";
import { useToast } from "../shared/Toast";

interface ChapterPattern {
  name: string;
  regex: string;
  enabled: boolean;
}

interface Chapter {
  number: number;
  title: string;
  title_only?: string;
  raw_marker?: string;
  pattern?: string;
  volume?: string | null;
  char_count: number;
  is_author_note?: boolean;
  author_note_score?: number;
  author_note_reasons?: string[];
}

interface LogEntry { ts: number; message: string; chapter?: number | null; }

interface PreprocessStatus {
  state: "idle" | "running" | "paused" | "done" | "error" | "cancelled";
  current_chapter: number;
  total_chapters: number;
  detected_pattern?: string | null;
  flagged_count: number;
  log: LogEntry[];
  chapters?: Chapter[];
  candidates?: { name: string; count: number; score: number }[];
  fallback_used?: boolean;
  error?: string | null;
  persisted?: boolean;
}

interface SegmentInfo {
  index: number;
  title: string;
  start_chapter: number;
  end_chapter: number;
  chapter_count?: number;
  char_count: number;
}

interface SegmentPlan {
  type: "volumes" | "chunks" | "custom";
  segments: SegmentInfo[];
  completed: number[];
  total_chapters: number;
  is_custom?: boolean;
}

function fmtChars(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)} 万字`;
  return `${n.toLocaleString()} 字`;
}

interface Props {
  refId: string;
  hasFullText: boolean;
  onUpload: () => void;
  onAfterApplyExclusions?: () => void | Promise<void>;
}

export default function PreprocessPanel({ refId, hasFullText, onUpload, onAfterApplyExclusions }: Props) {
  const { toast } = useToast();
  const [status, setStatus] = useState<PreprocessStatus | null>(null);
  const [excluded, setExcluded] = useState<Set<number>>(new Set());
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [filter, setFilter] = useState<"all" | "flagged" | "kept">("all");
  const [applying, setApplying] = useState(false);
  // Volume plan editor (moved here from PlotOutlinePanel)
  const [plan, setPlan] = useState<SegmentPlan | null>(null);
  const [planDraft, setPlanDraft] = useState<{ title: string; start_chapter: number; end_chapter: number }[] | null>(null);
  const [planSaving, setPlanSaving] = useState(false);
  // Custom chapter patterns
  const [patterns, setPatterns] = useState<ChapterPattern[]>([]);
  const [patternsOpen, setPatternsOpen] = useState(false);
  const [patternTesting, setPatternTesting] = useState<{ idx: number; count: number; preview: any[] } | null>(null);
  // Multi-file upload (append mode)
  const appendFileInputRef = useRef<HTMLInputElement | null>(null);
  const [appending, setAppending] = useState(false);

  const pollTimerRef = useRef<number | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const r = await apiGet<PreprocessStatus>(`/api/references/works/${refId}/preprocess/status`);
      setStatus(r);
      // Once flagged chapters are known, pre-select them for exclusion
      if (r.chapters && r.chapters.length > 0 && excluded.size === 0) {
        const initial = new Set<number>();
        r.chapters.forEach(c => { if (c.is_author_note) initial.add(c.number); });
        if (initial.size > 0) setExcluded(initial);
      }
      return r;
    } catch (e) {
      return null;
    }
  }, [refId, excluded.size]);

  const fetchPlan = useCallback(async () => {
    if (!hasFullText) { setPlan(null); return; }
    try {
      const p = await apiGet<SegmentPlan>(`/api/references/works/${refId}/segments/plan`);
      setPlan(p);
    } catch { /* silent */ }
  }, [refId, hasFullText]);

  const fetchPatterns = useCallback(async () => {
    try {
      const r = await apiGet<{ patterns: ChapterPattern[] }>("/api/references/chapter_patterns");
      setPatterns(r.patterns || []);
    } catch { /* silent */ }
  }, []);

  useEffect(() => { fetchStatus(); fetchPlan(); fetchPatterns(); }, [fetchStatus, fetchPlan, fetchPatterns]);

  const savePatterns = async (next: ChapterPattern[]) => {
    try {
      const r = await apiPut<{ patterns: ChapterPattern[] }>(
        "/api/references/chapter_patterns", { patterns: next },
      );
      setPatterns(r.patterns || []);
      toast("已保存自定义章节格式", "success");
    } catch (e: any) {
      toast(e?.message || "保存失败", "error");
    }
  };

  const addPattern = () => {
    const next = [...patterns, { name: `自定义 ${patterns.length + 1}`, regex: "", enabled: true }];
    setPatterns(next);
  };

  const removePattern = (idx: number) => {
    const next = patterns.filter((_, i) => i !== idx);
    setPatterns(next);
    savePatterns(next);
  };

  const updatePattern = (idx: number, patch: Partial<ChapterPattern>) => {
    const next = patterns.map((p, i) => i === idx ? { ...p, ...patch } : p);
    setPatterns(next);
  };

  const testPattern = async (idx: number) => {
    const p = patterns[idx];
    if (!p?.regex) { toast("请先填写正则", "info"); return; }
    try {
      const r = await apiPost<{ count: number; preview: any[] }>(
        "/api/references/chapter_patterns/test",
        { regex: p.regex, ref_id: refId },
      );
      setPatternTesting({ idx, count: r.count, preview: r.preview });
    } catch (e: any) {
      toast(e?.message || "测试失败", "error");
    }
  };

  const appendFile = async (file: File) => {
    if (!file.name.toLowerCase().endsWith(".txt")) {
      toast("仅支持 .txt 文件", "error");
      return;
    }
    setAppending(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("append", "true");
      const resp = await fetch(`/api/references/works/${refId}/upload`, {
        method: "POST", body: fd,
      });
      if (!resp.ok) throw new Error(await resp.text());
      toast(`已追加 ${file.name}`, "success");
      setStatus(null);
      await fetchStatus();
      await fetchPlan();
      await onAfterApplyExclusions?.();
    } catch (e: any) {
      toast(e?.message || "追加失败", "error");
    } finally {
      setAppending(false);
      if (appendFileInputRef.current) appendFileInputRef.current.value = "";
    }
  };

  // Poll while a job is running/paused
  useEffect(() => {
    const s = status?.state;
    if (s === "running" || s === "paused") {
      pollTimerRef.current = window.setInterval(() => { fetchStatus(); }, 600);
    }
    return () => {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
      }
    };
  }, [status?.state, fetchStatus]);

  const startJob = async () => {
    try {
      const r = await apiPost<PreprocessStatus>(`/api/references/works/${refId}/preprocess/start`, {});
      setStatus(r);
      setExcluded(new Set());
    } catch (e: any) {
      toast(e?.message || "启动失败", "error");
    }
  };

  const pauseJob = async () => {
    try { await apiPost(`/api/references/works/${refId}/preprocess/pause`, {}); await fetchStatus(); }
    catch (e: any) { toast(e?.message || "暂停失败", "error"); }
  };
  const resumeJob = async () => {
    try { await apiPost(`/api/references/works/${refId}/preprocess/resume`, {}); await fetchStatus(); }
    catch (e: any) { toast(e?.message || "恢复失败", "error"); }
  };
  const cancelJob = async () => {
    if (!confirm("确认取消当前预处理任务？")) return;
    try { await apiPost(`/api/references/works/${refId}/preprocess/cancel`, {}); await fetchStatus(); }
    catch (e: any) { toast(e?.message || "取消失败", "error"); }
  };

  const toggleExclude = (n: number) => {
    setExcluded(prev => {
      const next = new Set(prev);
      if (next.has(n)) next.delete(n);
      else next.add(n);
      return next;
    });
  };

  const applyExclusions = async () => {
    if (excluded.size === 0) {
      toast("未选择任何要排除的章节", "info");
      return;
    }
    if (!confirm(`将从正文中物理删除 ${excluded.size} 个章节，无法撤销。继续？`)) return;
    setApplying(true);
    try {
      const r = await apiPost<{ ok: boolean; removed_chapters: number[]; new_char_count: number }>(
        `/api/references/works/${refId}/preprocess/apply_exclusions`,
        { excluded_chapters: Array.from(excluded) },
        { timeoutMs: 120_000 },
      );
      toast(`已删除 ${r.removed_chapters.length} 章，现剩 ${fmtChars(r.new_char_count)}`, "success");
      setExcluded(new Set());
      setStatus(null);
      await onAfterApplyExclusions?.();
      // Re-fetch — the on-disk text changed so detection should be re-run
      await fetchStatus();
      await fetchPlan();
    } catch (e: any) {
      toast(e?.message || "应用失败", "error");
    } finally {
      setApplying(false);
    }
  };

  // ── Volume plan editor (moved from PlotOutlinePanel) ──

  const startPlanEdit = () => {
    if (!plan) return;
    if (plan.segments.length === 0) {
      const total = plan.total_chapters || 1;
      setPlanDraft([{ title: `第 1–${total} 章`, start_chapter: 1, end_chapter: total }]);
    } else {
      setPlanDraft(plan.segments.map(s => ({
        title: s.title || `第 ${s.start_chapter}–${s.end_chapter} 章`,
        start_chapter: s.start_chapter,
        end_chapter: s.end_chapter,
      })));
    }
  };

  const loadAutoSuggest = async () => {
    try {
      const sug = await apiGet<SegmentPlan>(`/api/references/works/${refId}/segments/plan/auto_suggest`);
      if (!sug.segments || sug.segments.length === 0) {
        toast("自动检测未识别到可分卷的结构", "info");
        return;
      }
      setPlanDraft(sug.segments.map(s => ({
        title: s.title || `第 ${s.start_chapter}–${s.end_chapter} 章`,
        start_chapter: s.start_chapter,
        end_chapter: s.end_chapter,
      })));
      toast(`已载入 ${sug.segments.length} 段建议，请检查后保存`, "success");
    } catch (e: any) {
      toast(e?.message || "自动检测失败", "error");
    }
  };

  const addPlanRow = (afterIdx: number) => {
    if (!planDraft) return;
    const total = plan?.total_chapters || 0;
    const prev = afterIdx >= 0 ? planDraft[afterIdx] : null;
    const newStart = prev ? prev.end_chapter + 1 : 1;
    const newEnd = total > 0 ? Math.min(total, newStart) : newStart;
    const next = [...planDraft];
    next.splice(afterIdx + 1, 0, {
      title: `第 ${newStart}–${newEnd} 章`,
      start_chapter: newStart,
      end_chapter: newEnd,
    });
    setPlanDraft(next);
  };

  const removePlanRow = (idx: number) => {
    if (!planDraft) return;
    setPlanDraft(planDraft.filter((_, i) => i !== idx));
  };

  const savePlan = async () => {
    if (!planDraft) return;
    const cleaned = planDraft.map(s => ({
      title: (s.title || "").trim(),
      start_chapter: Math.max(1, Math.floor(s.start_chapter || 1)),
      end_chapter: Math.max(1, Math.floor(s.end_chapter || 1)),
    }));
    if (cleaned.some(s => s.end_chapter < s.start_chapter)) {
      toast("某段的结束章号小于起始章号", "error");
      return;
    }
    if (!confirm("保存自定义分段会清空所有已完成的提取结果。继续？")) return;
    setPlanSaving(true);
    try {
      await apiPut(`/api/references/works/${refId}/segments/plan`,
        { segments: cleaned, plan_type: cleaned.length > 1 ? "volumes" : "custom" },
        { timeoutMs: 60_000 });
      toast("分段计划已保存", "success");
      setPlanDraft(null);
      await fetchPlan();
    } catch (e: any) {
      toast(e?.message || "保存失败", "error");
    } finally { setPlanSaving(false); }
  };

  const cancelPlanEdit = () => setPlanDraft(null);

  // ── Render ──

  if (!hasFullText) {
    return (
      <div className="empty-state" style={{ padding: 32, textAlign: "center" }}>
        <p style={{ marginBottom: 12 }}>本作品尚未上传正文。上传后会自动按「第 N 章」/「1、…」/「Chapter N」等多种格式切分章节。</p>
        <button className="btn-primary" onClick={onUpload}>上传正文</button>
      </div>
    );
  }

  const state = status?.state || "idle";
  const chapters = status?.chapters || [];
  const filteredChapters = chapters.filter(c => {
    if (filter === "flagged") return c.is_author_note;
    if (filter === "kept") return !excluded.has(c.number);
    return true;
  });

  const toggleExpand = (n: number) => {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(n)) next.delete(n); else next.add(n);
      return next;
    });
  };

  const progress = status && status.total_chapters > 0
    ? (status.current_chapter / status.total_chapters) * 100
    : 0;

  return (
    <div className="flex flex-col gap-12">
      {/* Section 1: chapter detection + status */}
      <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", padding: 12, background: "var(--bg-surface)" }}>
        <div className="flex items-center justify-between" style={{ marginBottom: 8, flexWrap: "wrap", gap: 8 }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
              智能章节识别
              {status?.detected_pattern && (
                <span className="tag" style={{
                  marginLeft: 8, fontSize: 10, padding: "1px 6px",
                  color: "var(--accent)", background: "var(--accent-subtle)",
                  border: "1px solid var(--accent)",
                }}>{status.detected_pattern}</span>
              )}
              {status?.fallback_used && (
                <span className="tag" style={{
                  marginLeft: 8, fontSize: 10, padding: "1px 6px",
                  color: "var(--gold)", background: "var(--bg-surface-2)",
                  border: "1px solid var(--gold)",
                }}>未识别结构 · 已按 3000 字切块</span>
              )}
            </div>
            <div className="text-xs text-muted" style={{ marginTop: 2 }}>
              支持「第N章」「第N回」「1、标题」「Chapter N」等多种格式（含阿拉伯/中文数字）；自动标记疑似作者题外话。
            </div>
          </div>
          <div className="flex items-center gap-6" style={{ flexWrap: "wrap" }}>
            {state === "idle" && (
              <button className="btn-primary" style={{ fontSize: 12, padding: "5px 14px" }} onClick={startJob}>
                智能识别章节
              </button>
            )}
            {state === "running" && (
              <>
                <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }} onClick={pauseJob}>暂停</button>
                <button className="btn" style={{ fontSize: 11, padding: "3px 10px", color: "var(--error)" }} onClick={cancelJob}>取消</button>
              </>
            )}
            {state === "paused" && (
              <>
                <button className="btn-primary" style={{ fontSize: 11, padding: "3px 10px" }} onClick={resumeJob}>恢复</button>
                <button className="btn" style={{ fontSize: 11, padding: "3px 10px", color: "var(--error)" }} onClick={cancelJob}>取消</button>
              </>
            )}
            {(state === "done" || state === "cancelled" || state === "error") && (
              <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }} onClick={startJob}>
                重新识别
              </button>
            )}
            <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }} onClick={onUpload}>
              重新上传
            </button>
            <input
              ref={appendFileInputRef}
              type="file"
              accept=".txt"
              style={{ display: "none" }}
              onChange={e => {
                const f = e.target.files?.[0];
                if (f) appendFile(f);
              }}
            />
            <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
                    onClick={() => appendFileInputRef.current?.click()}
                    disabled={appending}
                    title="将另一个 .txt 文件追加到当前正文末尾（适合分卷上传）">
              {appending ? "追加中..." : "追加文件"}
            </button>
          </div>
        </div>

        {/* Progress bar */}
        {(state === "running" || state === "paused") && (
          <div style={{ marginBottom: 8 }}>
            <div className="text-xs" style={{
              color: state === "paused" ? "var(--gold)" : "var(--accent)",
              marginBottom: 4,
              display: "flex", justifyContent: "space-between",
            }}>
              <span>{state === "paused" ? "已暂停" : "处理中"} · 第 {status?.current_chapter} / {status?.total_chapters} 章</span>
              <span>{progress.toFixed(0)}%</span>
            </div>
            <div style={{ height: 5, background: "var(--bg-surface-2)", borderRadius: 3, overflow: "hidden" }}>
              <div style={{
                height: "100%",
                width: `${progress}%`,
                background: state === "paused" ? "var(--gold)" : "var(--accent)",
                borderRadius: 3, transition: "width 0.2s",
              }} />
            </div>
          </div>
        )}

        {/* Status summary when done */}
        {state === "done" && status && (
          <div className="text-xs text-muted" style={{ marginBottom: 8 }}>
            共识别 {status.total_chapters} 章 · 疑似作者题外话 <span style={{ color: "var(--gold)", fontWeight: 600 }}>{status.flagged_count}</span> 章
            {status.persisted && " · （上次保存的结果）"}
          </div>
        )}

        {/* Live log tail */}
        {status && status.log && status.log.length > 0 && (state === "running" || state === "paused") && (
          <div style={{
            maxHeight: 120, overflowY: "auto",
            border: "1px solid var(--border)", borderRadius: 3,
            background: "var(--bg-card)", padding: 6,
            fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: 1.5,
            color: "var(--text-secondary)",
          }}>
            {status.log.slice(-30).map((e, i) => (
              <div key={i} style={{ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>
                <span style={{ color: "var(--text-tertiary)" }}>
                  {new Date(e.ts * 1000).toLocaleTimeString()}
                </span>{" "}{e.message}
              </div>
            ))}
          </div>
        )}

        {status?.error && (
          <div className="text-xs" style={{ color: "var(--error)", marginTop: 6 }}>{status.error}</div>
        )}

        {/* Custom chapter patterns (collapsible) */}
        <div style={{ marginTop: 10, borderTop: "1px dashed var(--border)", paddingTop: 8 }}>
          <button className="btn-ghost w-full"
                  onClick={() => setPatternsOpen(o => !o)}
                  style={{
                    justifyContent: "space-between", padding: "4px 0",
                    fontSize: 11, fontWeight: 600, color: "var(--text-secondary)",
                    borderRadius: 0,
                  }}>
            <span>自定义章节格式（{patterns.length}）</span>
            <span className="text-xs text-muted" style={{
              transition: "transform 0.15s",
              transform: patternsOpen ? "rotate(180deg)" : "none",
              display: "inline-block",
            }}>&#x25BC;</span>
          </button>
          {patternsOpen && (
            <div style={{ marginTop: 6 }}>
              <div className="text-xs text-muted" style={{ marginBottom: 8, lineHeight: 1.55 }}>
                内置「第N章」「第N回」「1、标题」「Chapter N」等格式。若你的小说用了不同的章节标记，可在这里添加正则，应捕获 2 个组：
                <code style={{ background: "var(--bg-card)", padding: "1px 4px", borderRadius: 2 }}>(章节号)</code> 和
                <code style={{ background: "var(--bg-card)", padding: "1px 4px", borderRadius: 2 }}>(标题)</code>。
                示例：<code style={{ background: "var(--bg-card)", padding: "1px 4px", borderRadius: 2 }}>{'^[\\s　]*卷([0-9]+)[\\s.、]+(.{1,60})$'}</code>
              </div>
              {patterns.length > 0 && (
                <div className="flex flex-col gap-6" style={{ marginBottom: 8 }}>
                  {patterns.map((p, i) => (
                    <div key={i} style={{
                      padding: 6, border: "1px solid var(--border)", borderRadius: 4,
                    }}>
                      <div className="flex gap-6 items-center" style={{ marginBottom: 4 }}>
                        <input
                          type="checkbox" checked={p.enabled}
                          onChange={e => updatePattern(i, { enabled: e.target.checked })}
                          style={{ flexShrink: 0, width: 13, height: 13 }}
                          title="启用此格式"
                        />
                        <input
                          className="input"
                          placeholder="名称（如：卷N格式）"
                          value={p.name}
                          onChange={e => updatePattern(i, { name: e.target.value })}
                          style={{ width: 140, fontSize: 12 }}
                        />
                        <input
                          className="input font-mono"
                          placeholder="^[\s　]*卷([0-9]+)[\s.、]+(.{1,60})$"
                          value={p.regex}
                          onChange={e => updatePattern(i, { regex: e.target.value })}
                          style={{ flex: 1, fontSize: 11 }}
                        />
                        <button className="btn" style={{ fontSize: 11, padding: "3px 8px" }}
                                onClick={() => testPattern(i)}
                                title="对当前作品的正文测试匹配数">
                          测试
                        </button>
                        <button className="btn-icon"
                                onClick={() => removePattern(i)}
                                style={{ fontSize: 14, color: "var(--error)" }}
                                title="删除">&times;</button>
                      </div>
                      {patternTesting?.idx === i && (
                        <div className="text-xs" style={{
                          padding: 6, marginTop: 4,
                          background: "var(--bg-card)", borderRadius: 3,
                          color: "var(--text-secondary)", lineHeight: 1.55,
                        }}>
                          匹配 <span style={{
                            color: patternTesting.count > 5 ? "var(--jade)" : "var(--gold)",
                            fontWeight: 600,
                          }}>{patternTesting.count}</span> 处。
                          {patternTesting.preview.length > 0 && (
                            <ul style={{ margin: "4px 0 0 16px", padding: 0 }}>
                              {patternTesting.preview.map((m, k) => (
                                <li key={k} style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>
                                  {m.match}
                                </li>
                              ))}
                            </ul>
                          )}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
              <div className="flex gap-6" style={{ justifyContent: "space-between" }}>
                <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }} onClick={addPattern}>
                  + 添加格式
                </button>
                <button className="btn-primary" style={{ fontSize: 11, padding: "3px 10px" }}
                        onClick={() => savePatterns(patterns)}>
                  保存格式
                </button>
              </div>
              {/* Show what each candidate scored last run, useful for debugging custom pats */}
              {status?.candidates && status.candidates.length > 0 && (
                <div className="text-xs text-muted" style={{ marginTop: 8, lineHeight: 1.6 }}>
                  上次识别评分：
                  {status.candidates.map((c, i) => (
                    <span key={i} className="tag" style={{
                      marginLeft: 4, fontSize: 10, padding: "1px 6px",
                      background: c.score > 1.0 ? "var(--accent-subtle)" : "var(--bg-surface-2)",
                      color: c.score > 1.0 ? "var(--accent)" : "var(--text-tertiary)",
                      border: `1px solid ${c.score > 1.0 ? "var(--accent)" : "var(--border)"}`,
                    }}>{c.name}: {c.count}/score {c.score}</span>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Section 2: chapter list + author-note flags */}
      {chapters.length > 0 && (
        <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", padding: 12, background: "var(--bg-surface)" }}>
          <div className="flex items-center justify-between" style={{ marginBottom: 8, gap: 8, flexWrap: "wrap" }}>
            <div>
              <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
                章节清理（共 {chapters.length} 章）
              </div>
              <div className="text-xs text-muted" style={{ marginTop: 2 }}>
                勾选要排除的章节 → 点「应用清理」会从正文中<span style={{ color: "var(--error)" }}>物理删除</span>这些章节。
              </div>
            </div>
            <div className="flex items-center gap-6">
              <div className="flex gap-4" style={{
                border: "1px solid var(--border)", borderRadius: 3, overflow: "hidden",
              }}>
                {[
                  { k: "all", label: `全部 ${chapters.length}` },
                  { k: "flagged", label: `疑似题外话 ${status?.flagged_count || 0}` },
                  { k: "kept", label: `保留 ${chapters.length - excluded.size}` },
                ].map(o => (
                  <button key={o.k}
                          onClick={() => setFilter(o.k as any)}
                          className="btn-ghost"
                          style={{
                            fontSize: 10, padding: "3px 8px", borderRadius: 0,
                            background: filter === o.k ? "var(--accent-subtle)" : "transparent",
                            color: filter === o.k ? "var(--accent)" : "var(--text-secondary)",
                          }}>{o.label}</button>
                ))}
              </div>
              <button className="btn-primary"
                      style={{ fontSize: 11, padding: "4px 12px" }}
                      onClick={applyExclusions}
                      disabled={applying || excluded.size === 0}
                      title={excluded.size === 0 ? "未选择任何章节" : `物理删除 ${excluded.size} 章`}>
                {applying ? "应用中…" : `应用清理（${excluded.size}）`}
              </button>
            </div>
          </div>

          <div className="flex flex-col gap-4" style={{
            maxHeight: 460, overflowY: "auto",
            padding: 4, border: "1px solid var(--border)", borderRadius: 4,
          }}>
            {filteredChapters.length === 0 && (
              <div className="text-xs text-muted" style={{ padding: 12, textAlign: "center" }}>
                当前筛选下没有章节。
              </div>
            )}
            {filteredChapters.map(c => {
              const isOpen = expanded.has(c.number);
              const isFlagged = !!c.is_author_note;
              const isExcluded = excluded.has(c.number);
              return (
                <div key={c.number} style={{
                  border: `1px solid ${isExcluded ? "var(--error)" : isFlagged ? "var(--gold)" : "var(--border)"}`,
                  borderRadius: 4,
                  padding: "5px 8px",
                  background: isExcluded ? "rgba(220,38,38,0.06)" : isFlagged ? "rgba(250,204,21,0.06)" : "transparent",
                  opacity: isExcluded ? 0.7 : 1,
                }}>
                  <div className="flex items-center gap-8" style={{ minWidth: 0 }}>
                    <input
                      type="checkbox"
                      checked={isExcluded}
                      onChange={() => toggleExclude(c.number)}
                      style={{ flexShrink: 0, width: 14, height: 14, cursor: "pointer" }}
                      title={isExcluded ? "已勾选排除" : "勾选以排除此章节"}
                    />
                    <span className="tag" style={{
                      fontSize: 10, minWidth: 38, textAlign: "center", flexShrink: 0,
                      color: "var(--text-secondary)",
                      background: "transparent",
                      border: "1px solid var(--border)",
                    }}>#{c.number}</span>
                    <button
                      className="btn-ghost"
                      onClick={() => toggleExpand(c.number)}
                      style={{
                        flex: 1, minWidth: 0,
                        padding: 0, borderRadius: 0,
                        justifyContent: "flex-start",
                      }}>
                      <div className="truncate" style={{
                        fontSize: 12, fontWeight: 500,
                        color: isExcluded ? "var(--text-tertiary)" : "var(--text-primary)",
                        textDecoration: isExcluded ? "line-through" : "none",
                        textAlign: "left",
                      }}>{c.title}</div>
                    </button>
                    {isFlagged && (
                      <span className="tag" style={{
                        fontSize: 10, padding: "1px 6px", flexShrink: 0,
                        color: "var(--gold)", background: "var(--bg-surface-2)",
                        border: "1px solid var(--gold)",
                      }} title={(c.author_note_reasons || []).join(" · ")}>题外话?</span>
                    )}
                    <span className="text-xs text-muted" style={{ flexShrink: 0, fontFamily: "var(--font-mono)" }}>
                      {fmtChars(c.char_count)}
                    </span>
                  </div>
                  {isOpen && c.author_note_reasons && c.author_note_reasons.length > 0 && (
                    <div className="text-xs" style={{
                      marginTop: 4, padding: "4px 8px",
                      background: "var(--bg-card)", borderRadius: 3,
                      color: "var(--text-secondary)", lineHeight: 1.5,
                    }}>
                      <span className="text-muted">作者题外话信号：</span>
                      {c.author_note_reasons.map((r, i) => (
                        <span key={i} className="tag" style={{
                          marginLeft: 4, fontSize: 10, padding: "1px 6px",
                          background: "var(--bg-surface-2)", color: "var(--text-secondary)",
                          border: "1px solid var(--border)",
                        }}>{r}</span>
                      ))}
                      {c.author_note_score !== undefined && (
                        <span className="text-xs text-muted" style={{ marginLeft: 8 }}>
                          score = {c.author_note_score}
                        </span>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Section 3: volume editor (moved here from 剧情大纲) */}
      <VolumeEditor
        plan={plan}
        planDraft={planDraft}
        planSaving={planSaving}
        startPlanEdit={startPlanEdit}
        loadAutoSuggest={loadAutoSuggest}
        addPlanRow={addPlanRow}
        removePlanRow={removePlanRow}
        savePlan={savePlan}
        cancelPlanEdit={cancelPlanEdit}
        setPlanDraft={setPlanDraft}
      />
    </div>
  );
}

interface VolumeEditorProps {
  plan: SegmentPlan | null;
  planDraft: { title: string; start_chapter: number; end_chapter: number }[] | null;
  planSaving: boolean;
  startPlanEdit: () => void;
  loadAutoSuggest: () => Promise<void>;
  addPlanRow: (afterIdx: number) => void;
  removePlanRow: (idx: number) => void;
  savePlan: () => Promise<void>;
  cancelPlanEdit: () => void;
  setPlanDraft: (d: { title: string; start_chapter: number; end_chapter: number }[] | null) => void;
}

function VolumeEditor(p: VolumeEditorProps) {
  const { plan, planDraft, planSaving } = p;
  if (!plan) return null;

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", padding: 12, background: "var(--bg-surface)" }}>
      <div className="flex items-center justify-between" style={{ marginBottom: 8, flexWrap: "wrap", gap: 8 }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
            分卷与分段
          </div>
          <div className="text-xs text-muted" style={{ marginTop: 2 }}>
            {plan.segments.length === 0
              ? `共 ${plan.total_chapters} 章 · 尚未划分卷`
              : `${plan.is_custom ? "自定义" : (plan.type === "volumes" ? "按卷处理" : "按 ~10 万字分块")} · ${plan.segments.length} 段`}
          </div>
        </div>
        {!planDraft && plan.segments.length > 0 && (
          <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
                  onClick={p.startPlanEdit}
                  title="编辑卷的标题和章节范围">
            编辑分卷
          </button>
        )}
      </div>

      {/* Empty state */}
      {plan.segments.length === 0 && !planDraft && (
        <div style={{
          padding: 16, textAlign: "center",
          border: "1px dashed var(--border)", borderRadius: 4,
        }}>
          <div className="text-xs text-muted" style={{ marginBottom: 12, lineHeight: 1.6 }}>
            还没有分卷。卷标题代表故事中的时间（如「1954 年」），无明确时间时填写章节范围。
          </div>
          <div className="flex gap-8" style={{ justifyContent: "center", flexWrap: "wrap" }}>
            <button className="btn-primary" style={{ fontSize: 12, padding: "5px 14px" }} onClick={p.startPlanEdit}>
              新建卷
            </button>
            <button className="btn" style={{ fontSize: 12, padding: "5px 14px" }} onClick={p.loadAutoSuggest}
                    title="按文中「第 X 卷」标记或 ~10 万字切块自动建议分卷">
              自动检测分卷
            </button>
          </div>
        </div>
      )}

      {/* Edit mode */}
      {planDraft && (
        <div>
          <div className="flex items-center justify-between" style={{ marginBottom: 8, gap: 8, flexWrap: "wrap" }}>
            <div className="text-xs text-muted" style={{ lineHeight: 1.55, flex: 1, minWidth: 220 }}>
              卷标题代表故事中的时间（如「1954 年」），无明确时间时填写章节范围。共 {plan.total_chapters} 章。
              {plan.segments.length > 0 && <><br />保存后会清空已有的提取结果。</>}
            </div>
            <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
                    onClick={p.loadAutoSuggest}
                    disabled={planSaving}>
              自动检测分卷
            </button>
          </div>

          {planDraft.length > 0 && (
            <div className="flex flex-col gap-6" style={{ marginBottom: 10 }}>
              {planDraft.map((s, i) => (
                <div key={i} className="flex gap-6 items-center" style={{
                  padding: 6, border: "1px solid var(--border)", borderRadius: 4,
                }}>
                  <span className="text-xs text-muted" style={{
                    minWidth: 30, textAlign: "center", fontFamily: "var(--font-mono)",
                  }}>#{i + 1}</span>
                  <input
                    className="input"
                    placeholder='故事时间（如 "1954 年"，无则填 "第 1–8 章"）'
                    value={s.title}
                    onChange={e => {
                      const next = [...planDraft];
                      next[i] = { ...s, title: e.target.value };
                      p.setPlanDraft(next);
                    }}
                    style={{ flex: 1, fontSize: 12 }}
                  />
                  <input
                    className="input" type="number" min={1} max={plan.total_chapters || undefined}
                    value={s.start_chapter}
                    onChange={e => {
                      const next = [...planDraft];
                      next[i] = { ...s, start_chapter: parseInt(e.target.value, 10) || 1 };
                      p.setPlanDraft(next);
                    }}
                    style={{ width: 70, fontSize: 12 }}
                    title="起始章号"
                  />
                  <span className="text-xs text-muted">–</span>
                  <input
                    className="input" type="number" min={1} max={plan.total_chapters || undefined}
                    value={s.end_chapter}
                    onChange={e => {
                      const next = [...planDraft];
                      next[i] = { ...s, end_chapter: parseInt(e.target.value, 10) || 1 };
                      p.setPlanDraft(next);
                    }}
                    style={{ width: 70, fontSize: 12 }}
                    title="结束章号"
                  />
                  <span className="text-xs text-muted"
                        style={{ minWidth: 56, textAlign: "right", fontFamily: "var(--font-mono)" }}>
                    {Math.max(0, (s.end_chapter || 0) - (s.start_chapter || 0) + 1)} 章
                  </span>
                  <button className="btn" style={{ fontSize: 11, padding: "3px 8px" }}
                          onClick={() => p.addPlanRow(i)}
                          title="在该段后新建一个分卷">新建卷</button>
                  <button className="btn-icon"
                          onClick={() => p.removePlanRow(i)}
                          style={{ fontSize: 14 }}
                          title="删除该段">&times;</button>
                </div>
              ))}
            </div>
          )}
          {planDraft.length === 0 && (
            <div className="text-xs text-muted" style={{
              padding: 12, marginBottom: 10, textAlign: "center",
              border: "1px dashed var(--border)", borderRadius: 4,
            }}>
              当前没有分卷。点击下方「新建卷」开始添加。
            </div>
          )}
          <div className="flex gap-6" style={{ justifyContent: "space-between", flexWrap: "wrap" }}>
            <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
                    onClick={() => p.addPlanRow(planDraft.length - 1)}
                    disabled={planSaving}>+ 新建卷</button>
            <div className="flex gap-6">
              <button className="btn" onClick={p.cancelPlanEdit} disabled={planSaving}>取消</button>
              <button className="btn-primary" onClick={p.savePlan}
                      disabled={planSaving || planDraft.length === 0}>
                {planSaving ? "保存中..." : "保存分段计划"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* List mode */}
      {!planDraft && plan.segments.length > 0 && (
        <div className="flex flex-col gap-4">
          {plan.segments.map(s => (
            <div key={s.index} style={{
              padding: "6px 10px", border: "1px solid var(--border)", borderRadius: 4,
            }}>
              <div style={{ fontSize: 12, fontWeight: 500, color: "var(--text-primary)" }}>
                #{s.index + 1} · {s.title}
              </div>
              <div className="text-xs text-muted">
                第 {s.start_chapter}–{s.end_chapter} 章 · 共 {s.chapter_count ?? (s.end_chapter - s.start_chapter + 1)} 章 · {fmtChars(s.char_count)}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
