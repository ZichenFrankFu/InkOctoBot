import React, { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost, apiPatch } from "../../api/client";
import { useToast } from "../shared/Toast";
import { PlotOutlineEditor } from "./AnalysisEditors";
import type { PlotOutline, ChronicleEpoch, ChroniclePeriod } from "./AnalysisEditors";

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

interface SegmentResult {
  index: number;
  title: string;
  start_chapter: number;
  end_chapter: number;
  char_count: number;
  elapsed_s: number;
  errors: string[];
  warnings?: string[];
  ai_methods_used?: string[];
  ai_methods_fallback?: string[];
  plot_outline: { logline?: string; epochs?: ChronicleEpoch[] };
  characters?: any[];
  settings?: any[];
  style_fingerprint?: any;
  rhythm?: any;
}


function fmtChars(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)} 万字`;
  return `${n.toLocaleString()} 字`;
}

interface Props {
  refId: string;
  hasFullText: boolean;
  plotOutline: PlotOutline | null;
  preprocessingStatus: string;
  onSavePlot: (d: PlotOutline) => Promise<void> | void;
  onAfterMerge: () => Promise<void> | void;
  onRegenerateFromText?: () => void;
  regenerating?: boolean;
  /** Switch to the "预处理" tab — used by the "编辑分卷" button now that
   * volume creation/editing lives in the preprocess tab. */
  onGoToPreprocess?: () => void;
}

export default function PlotOutlinePanel({
  refId,
  hasFullText,
  plotOutline,
  preprocessingStatus,
  onSavePlot,
  onAfterMerge,
  onRegenerateFromText,
  regenerating,
  onGoToPreprocess,
}: Props) {
  const { toast } = useToast();
  const [plan, setPlan] = useState<SegmentPlan | null>(null);
  const [planLoading, setPlanLoading] = useState(false);
  const [useWebSearch, setUseWebSearch] = useState(false);
  const [webSearchCap, setWebSearchCap] = useState<{ enabled: boolean; reason: string; provider: string; model: string } | null>(null);
  const [previewIdx, setPreviewIdx] = useState<number | null>(null);
  const [preview, setPreview] = useState<SegmentResult | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [merging, setMerging] = useState(false);
  // Prompt-display state — surfaces the EXACT text being sent to the LLM
  // for the currently-previewing segment so the user can audit.
  const [shownPrompt, setShownPrompt] = useState<{ key: string; rendered: string } | null>(null);
  const [promptLoading, setPromptLoading] = useState(false);
  const [promptOpen, setPromptOpen] = useState(false);
  // Chat-with-AI state for the currently-previewed segment
  const [chatMessages, setChatMessages] = useState<{ role: "user" | "assistant"; content: string }[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatSending, setChatSending] = useState(false);
  // Inline title editing in the segment timeline (separate from full plan-edit
  // mode) — lets the user rename a single volume's story-time title without
  // wiping any completed extraction.
  const [titleEditIdx, setTitleEditIdx] = useState<number | null>(null);
  const [titleEditValue, setTitleEditValue] = useState("");
  const [titleSaving, setTitleSaving] = useState(false);

  const loadPlan = useCallback(async () => {
    if (!hasFullText) { setPlan(null); return; }
    setPlanLoading(true);
    try {
      const p = await apiGet<SegmentPlan>(`/api/references/works/${refId}/segments/plan`);
      setPlan(p);
    } catch (e: any) {
      // silent on first load
    } finally { setPlanLoading(false); }
  }, [refId, hasFullText]);

  useEffect(() => { loadPlan(); }, [loadPlan]);

  // Capability probe so we can disable the web-search toggle with a
  // useful tooltip when the user hasn't configured a search-capable model.
  useEffect(() => {
    let cancelled = false;
    apiGet<{ enabled: boolean; reason: string; provider: string; model: string }>(
      "/api/references/web_search/capability",
    ).then(r => { if (!cancelled) setWebSearchCap(r); })
     .catch(() => { /* no-op; toggle stays disabled */ });
    return () => { cancelled = true; };
  }, []);

  const total = plan?.segments.length || 0;
  const completed = new Set(plan?.completed || []);
  const doneCount = completed.size;
  const allDone = total > 0 && doneCount >= total;
  const nextIdx = plan?.segments.find(s => !completed.has(s.index))?.index;

  const fetchSegmentPrompt = useCallback(async (idx: number) => {
    setPromptLoading(true);
    try {
      const params = new URLSearchParams({
        ref_id: refId, segment_index: String(idx),
      });
      // We surface the rhythm prompt by default — it's the heaviest one
      // and most representative of what the model sees for the segment.
      const r = await apiGet<{ key: string; rendered: string }>(
        `/api/references/prompts/reference.rhythm/preview?${params}`,
      );
      setShownPrompt({ key: r.key, rendered: r.rendered });
    } catch (e: any) {
      setShownPrompt({ key: "reference.rhythm", rendered: `（获取 prompt 失败：${e?.message || "未知错误"}）` });
    } finally { setPromptLoading(false); }
  }, [refId]);

  const generatePreview = async (idx: number) => {
    setPreviewIdx(idx);
    setPreview(null);
    setPreviewLoading(true);
    // Fetch the exact rendered prompt in parallel so the user can audit.
    fetchSegmentPrompt(idx);
    try {
      const r = await apiPost<SegmentResult>(
        `/api/references/works/${refId}/segments/preview`,
        { segment_index: idx, use_ai: true, use_web_search: useWebSearch && !!webSearchCap?.enabled },
        { timeoutMs: 900_000 },
      );
      setPreview(r);
      setChatMessages([]);
    } catch (e: any) {
      toast(e?.message || "预览失败", "error");
      setPreviewIdx(null);
    } finally { setPreviewLoading(false); }
  };

  const commitPreview = async () => {
    if (!preview) return;
    setCommitting(true);
    try {
      const r = await apiPost<any>(
        `/api/references/works/${refId}/segments/commit`,
        { result: preview },
      );
      toast(`第 ${(preview.index ?? 0) + 1} 段已保存（${r.completed_count}/${r.total_segments}）`, "success");
      setPreview(null);
      setPreviewIdx(null);
      setChatMessages([]);
      await loadPlan();
    } catch (e: any) {
      toast(e?.message || "保存失败", "error");
    } finally { setCommitting(false); }
  };

  const sendChat = async () => {
    const text = chatInput.trim();
    if (!text || !preview || previewIdx === null || chatSending) return;
    const userMsg = { role: "user" as const, content: text };
    const next = [...chatMessages, userMsg];
    setChatMessages(next);
    setChatInput("");
    setChatSending(true);
    try {
      const r = await apiPost<{ assistant_message: string; revised: { plot_outline?: any; characters?: any[]; settings?: any[] } }>(
        `/api/references/works/${refId}/segments/chat`,
        {
          segment_index: previewIdx,
          messages: next,
          current_result: preview,
        },
        { timeoutMs: 300_000 },
      );
      setChatMessages([...next, { role: "assistant", content: r.assistant_message || "（无回复）" }]);
      // If the AI returned a revision, merge it into the preview so the
      // user sees the updated chronicle / characters / settings inline.
      if (r.revised && Object.keys(r.revised).length > 0) {
        setPreview(cur => cur ? {
          ...cur,
          plot_outline: r.revised.plot_outline ?? cur.plot_outline,
          characters: r.revised.characters ?? cur.characters,
          settings: r.revised.settings ?? cur.settings,
        } : cur);
      }
    } catch (e: any) {
      setChatMessages([...next, { role: "assistant", content: `（出错）${e?.message || "对话失败"}` }]);
    } finally { setChatSending(false); }
  };

  const finalize = async () => {
    setMerging(true);
    try {
      const r = await apiPost<any>(`/api/references/works/${refId}/segments/finalize`, {});
      toast(`已合并 ${r.merge?.merged_segments || 0} 段到全书`, "success");
      await onAfterMerge();
      await loadPlan();
    } catch (e: any) {
      toast(e?.message || "合并失败", "error");
    } finally { setMerging(false); }
  };

  const reset = async () => {
    if (!confirm("确认清空所有已完成的分段进度？")) return;
    try {
      await apiPost(`/api/references/works/${refId}/segments/reset`, {});
      toast("已重置分段进度", "success");
      await loadPlan();
    } catch (e: any) {
      toast(e?.message || "重置失败", "error");
    }
  };

  // ── Inline title edit (timeline view, non-destructive) ──

  const beginTitleEdit = (idx: number, current: string) => {
    setTitleEditIdx(idx);
    setTitleEditValue(current);
  };

  const cancelTitleEdit = () => {
    setTitleEditIdx(null);
    setTitleEditValue("");
  };

  const saveTitleEdit = async () => {
    if (titleEditIdx === null || !plan) return;
    const newTitle = titleEditValue.trim();
    const orig = plan.segments[titleEditIdx]?.title || "";
    if (newTitle === orig) { cancelTitleEdit(); return; }
    setTitleSaving(true);
    try {
      await apiPatch(
        `/api/references/works/${refId}/segments/${titleEditIdx}/title`,
        { title: newTitle },
      );
      // Optimistic local update so we don't have to wait for a full reload
      setPlan(p => p ? {
        ...p,
        segments: p.segments.map((s, i) =>
          i === titleEditIdx ? { ...s, title: newTitle || s.title } : s,
        ),
      } : p);
      cancelTitleEdit();
    } catch (e: any) {
      toast(e?.message || "重命名失败", "error");
    } finally {
      setTitleSaving(false);
    }
  };

  // ── render ──
  return (
    <div className="flex flex-col gap-12">
      {/* Segment plan + preview (only if work has full text and not in standalone manual mode) */}
      {hasFullText && plan && (
        <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", padding: 12, background: "var(--bg-surface)" }}>
          <div className="flex items-center justify-between" style={{ marginBottom: 8, flexWrap: "wrap", gap: 8 }}>
            <div>
              <div className="flex items-center gap-8">
                <span style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
                  分段提取大纲
                </span>
                {plan.is_custom && (
                  <span className="tag" style={{
                    fontSize: 10, padding: "1px 6px",
                    color: "var(--accent)",
                    background: "var(--accent-subtle)",
                    border: "1px solid var(--accent)",
                  }} title="已使用自定义分段">已自定义</span>
                )}
              </div>
              <div className="text-xs text-muted" style={{ marginTop: 2 }}>
                {total === 0
                  ? `共 ${plan.total_chapters} 章 · 尚未划分卷`
                  : plan.is_custom
                    ? `自定义 · ${plan.segments.length} 段 · ${doneCount}/${total} 已完成`
                    : (plan.type === "volumes" ? "按卷处理" : "按 ~10 万字分块")
                      + ` · ${doneCount}/${total} 已完成`}
              </div>
            </div>
            <div className="flex items-center gap-8" style={{ flexWrap: "wrap" }}>
              <label
                className="flex items-center gap-6"
                style={{
                  fontSize: 11,
                  cursor: webSearchCap?.enabled ? "pointer" : "not-allowed",
                  color: webSearchCap?.enabled ? "var(--text-secondary)" : "var(--text-tertiary)",
                  padding: "3px 8px",
                  borderRadius: 3,
                  border: `1px solid ${useWebSearch && webSearchCap?.enabled ? "var(--accent)" : "var(--border)"}`,
                  background: useWebSearch && webSearchCap?.enabled ? "var(--accent-subtle)" : "transparent",
                }}
                title={
                  webSearchCap?.enabled
                    ? `开启后 AI 会用 ${webSearchCap.provider}/${webSearchCap.model} 联网验证抽取结果，降低幻觉。`
                    : (webSearchCap?.reason || "未配置联网模型；请到「设置 → Pipeline 配置 → 参考作品 AI 联网补全」选择支持 web search 的模型")
                }
              >
                <input
                  type="checkbox"
                  checked={useWebSearch && !!webSearchCap?.enabled}
                  onChange={e => setUseWebSearch(e.target.checked)}
                  disabled={!webSearchCap?.enabled}
                  style={{ width: 13, height: 13 }}
                />
                AI 联网验证
              </label>
              {onGoToPreprocess && (
                <button className="btn" style={{ fontSize: 11, padding: "3px 10px" }}
                        onClick={onGoToPreprocess}
                        disabled={committing || merging || previewLoading}
                        title="到「预处理」tab 新建 / 编辑分卷">
                  编辑分段
                </button>
              )}
              {doneCount > 0 && !allDone && (
                <button className="btn" style={{ fontSize: 11, padding: "3px 10px", color: "var(--text-tertiary)" }} onClick={reset} disabled={committing || merging}>
                  重置
                </button>
              )}
              {allDone && (
                <button className="btn-primary" style={{ fontSize: 12, padding: "4px 12px" }} onClick={finalize} disabled={merging}>
                  {merging ? "合并中..." : "合并到全书"}
                </button>
              )}
            </div>
          </div>

          {/* progress bar (hidden when there are no segments yet) */}
          {total > 0 && (
            <div style={{ height: 5, background: "var(--bg-surface-2)", borderRadius: 3, overflow: "hidden", marginBottom: 10 }}>
              <div style={{ height: "100%", width: `${(doneCount / total) * 100}%`, background: "var(--jade)", borderRadius: 3, transition: "width 0.3s" }} />
            </div>
          )}

          {/* EMPTY STATE — no volumes yet. Direct user to the 预处理 tab. */}
          {total === 0 && (
            <div style={{
              padding: 16, textAlign: "center",
              border: "1px dashed var(--border)", borderRadius: 4,
              background: "var(--bg-surface)",
            }}>
              <div className="text-xs text-muted" style={{ marginBottom: 12, lineHeight: 1.6 }}>
                还没有分卷。请到「预处理」tab 划分卷后再进行特征提取。
              </div>
              {onGoToPreprocess && (
                <button className="btn-primary" style={{ fontSize: 12, padding: "5px 14px" }}
                        onClick={onGoToPreprocess}>
                  去「预处理」tab 编辑分卷
                </button>
              )}
            </div>
          )}

          {total > 0 && (
          <div className="flex flex-col gap-4">
            {plan.segments.map(s => {
              const isDone = completed.has(s.index);
              const isPreviewing = previewIdx === s.index;
              const isNext = nextIdx === s.index;
              return (
                <div key={s.index}>
                  <div style={{
                    display: "flex", alignItems: "center", gap: 8,
                    padding: "6px 10px",
                    background: isDone ? "rgba(52,168,83,0.06)" : isNext ? "var(--bg-card)" : "transparent",
                    border: "1px solid var(--border)",
                    borderRadius: 4,
                  }}>
                    <span className="tag" style={{
                      fontSize: 10, minWidth: 36, textAlign: "center",
                      color: isDone ? "var(--jade)" : "var(--text-secondary)",
                      background: "transparent",
                      border: `1px solid ${isDone ? "var(--jade)" : "var(--border)"}`,
                    }}>
                      {isDone ? "已完成" : `#${s.index + 1}`}
                    </span>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      {titleEditIdx === s.index ? (
                        <div className="flex items-center gap-4">
                          <input
                            className="input"
                            autoFocus
                            value={titleEditValue}
                            placeholder='故事时间（如 "1954 年"）'
                            onChange={e => setTitleEditValue(e.target.value)}
                            onKeyDown={e => {
                              if (e.key === "Enter") { e.preventDefault(); saveTitleEdit(); }
                              else if (e.key === "Escape") { e.preventDefault(); cancelTitleEdit(); }
                            }}
                            disabled={titleSaving}
                            style={{ flex: 1, fontSize: 12, padding: "2px 6px" }}
                            title="此处填写本段对应的故事时间；无明确时间时填写章节范围"
                          />
                          <button className="btn"
                                  style={{ fontSize: 10, padding: "2px 6px" }}
                                  onClick={saveTitleEdit}
                                  disabled={titleSaving}>{titleSaving ? "..." : "保存"}</button>
                          <button className="btn"
                                  style={{ fontSize: 10, padding: "2px 6px" }}
                                  onClick={cancelTitleEdit}
                                  disabled={titleSaving}>取消</button>
                        </div>
                      ) : (
                        <div className="flex items-center gap-4">
                          <div className="truncate" style={{ fontSize: 12, fontWeight: 500, color: "var(--text-primary)", flex: 1, minWidth: 0 }}>
                            {s.title}
                          </div>
                          <button
                            className="btn-ghost"
                            onClick={() => beginTitleEdit(s.index, s.title || `第 ${s.start_chapter}–${s.end_chapter} 章`)}
                            style={{ fontSize: 10, padding: "1px 6px", color: "var(--text-tertiary)" }}
                            title='编辑标题（故事时间，如 "1954 年"）'
                          >改名</button>
                        </div>
                      )}
                      <div className="text-xs text-muted">
                        第 {s.start_chapter}–{s.end_chapter} 章 · 共 {s.chapter_count ?? (s.end_chapter - s.start_chapter + 1)} 章 · {fmtChars(s.char_count)}
                      </div>
                    </div>
                    {isPreviewing && previewLoading ? (
                      <span className="text-xs" style={{ color: "var(--gold)" }}>生成预览中...</span>
                    ) : isDone ? (
                      <button
                        className="btn"
                        style={{ fontSize: 11, padding: "3px 10px" }}
                        onClick={() => generatePreview(s.index)}
                        disabled={previewLoading || committing}
                      >重新生成</button>
                    ) : (
                      <button
                        className={isNext ? "btn-primary" : "btn"}
                        style={{ fontSize: 11, padding: "3px 10px" }}
                        onClick={() => generatePreview(s.index)}
                        disabled={previewLoading || committing}
                      >{isNext ? "提取并预览" : "预览"}</button>
                    )}
                  </div>

                  {/* inline preview */}
                  {isPreviewing && preview && (
                    <div style={{
                      margin: "6px 0 4px 28px",
                      padding: 10,
                      border: "1px solid var(--accent)",
                      borderRadius: 4,
                      background: "var(--bg-card)",
                    }}>
                      <div className="flex items-center justify-between" style={{ marginBottom: 8, flexWrap: "wrap", gap: 8 }}>
                        <div style={{ fontSize: 12, fontWeight: 600, color: "var(--accent)" }}>
                          预览 · {preview.elapsed_s}s
                          <span className="text-xs text-muted" style={{ marginLeft: 8, fontWeight: 400 }}>
                            {(preview.plot_outline?.epochs || []).length} 大段 ·
                            {" "}{(preview.plot_outline?.epochs || []).reduce((n, ep) => n + (ep.periods?.length || 0), 0)} 时间段 ·
                            {" "}{(preview.characters || []).length} 角色 ·
                            {" "}{(preview.settings || []).length} 设定
                          </span>
                        </div>
                        <div className="flex gap-6">
                          <button
                            className="btn"
                            style={{ fontSize: 11, padding: "3px 10px" }}
                            onClick={() => { setPreview(null); setPreviewIdx(null); }}
                            disabled={committing}
                          >取消</button>
                          <button
                            className="btn"
                            style={{ fontSize: 11, padding: "3px 10px" }}
                            onClick={() => generatePreview(s.index)}
                            disabled={committing}
                          >重新生成</button>
                          <button
                            className="btn-primary"
                            style={{ fontSize: 11, padding: "3px 10px" }}
                            onClick={commitPreview}
                            disabled={committing}
                          >{committing ? "保存中..." : "确认保存"}</button>
                        </div>
                      </div>
                      {preview.warnings && preview.warnings.length > 0 && (
                        <div style={{
                          padding: "8px 10px",
                          marginBottom: 8,
                          background: "var(--bg-surface)",
                          border: "1px solid var(--gold)",
                          borderRadius: 4,
                          fontSize: 11,
                          color: "var(--gold)",
                          lineHeight: 1.55,
                        }}>
                          <div style={{ fontWeight: 600, marginBottom: 4 }}>AI 提取警告</div>
                          <ul style={{ margin: 0, paddingLeft: 16 }}>
                            {preview.warnings.map((w, i) => (
                              <li key={i} style={{ marginBottom: 2 }}>{w}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      {preview.errors && preview.errors.length > 0 && (
                        <div className="text-xs" style={{ color: "var(--error)", marginBottom: 6 }}>
                          错误：{preview.errors.join("; ")}
                        </div>
                      )}

                      {/* Exact prompt sent to the LLM (collapsible) */}
                      <div style={{ marginBottom: 10, border: "1px dashed var(--border)", borderRadius: 4 }}>
                        <button
                          className="btn-ghost w-full"
                          onClick={() => setPromptOpen(o => !o)}
                          style={{
                            justifyContent: "space-between",
                            padding: "6px 10px",
                            fontSize: 11, fontWeight: 600,
                            color: "var(--text-secondary)", borderRadius: 0,
                          }}
                        >
                          <span>查看本次发送给 LLM 的 prompt（{shownPrompt?.key || "reference.rhythm"}）</span>
                          <span className="text-xs text-muted" style={{
                            transition: "transform 0.15s",
                            transform: promptOpen ? "rotate(180deg)" : "none",
                            display: "inline-block",
                          }}>&#x25BC;</span>
                        </button>
                        {promptOpen && (
                          <div style={{ padding: 8, background: "var(--bg-surface)" }}>
                            {promptLoading ? (
                              <div className="text-xs text-muted" style={{ padding: 6 }}>加载中…</div>
                            ) : (
                              <pre className="font-mono" style={{
                                margin: 0, padding: 8, fontSize: 11, lineHeight: 1.55,
                                background: "var(--bg-card)", borderRadius: 3,
                                color: "var(--text-secondary)",
                                maxHeight: 320, overflow: "auto",
                                whiteSpace: "pre-wrap", wordBreak: "break-word",
                              }}>{shownPrompt?.rendered || "（无）"}</pre>
                            )}
                          </div>
                        )}
                      </div>

                      <ChroniclePreview epochs={preview.plot_outline?.epochs || []} />

                      {/* AI chat box — refine this segment conversationally */}
                      <div style={{
                        marginTop: 12,
                        borderTop: "1px dashed var(--border)",
                        paddingTop: 10,
                      }}>
                        <div className="flex items-center justify-between" style={{ marginBottom: 6 }}>
                          <span style={{ fontSize: 11, fontWeight: 600, color: "var(--accent)" }}>
                            与 AI 对话调整本段
                          </span>
                          <span className="text-xs text-muted">
                            修改会即时应用到上方预览
                          </span>
                        </div>
                        {chatMessages.length > 0 && (
                          <div style={{
                            maxHeight: 220,
                            overflowY: "auto",
                            border: "1px solid var(--border)",
                            borderRadius: 4,
                            padding: 8,
                            marginBottom: 8,
                            background: "var(--bg-surface)",
                          }}>
                            {chatMessages.map((m, i) => (
                              <div key={i} style={{
                                marginBottom: i === chatMessages.length - 1 ? 0 : 8,
                                display: "flex",
                                flexDirection: m.role === "user" ? "row-reverse" : "row",
                              }}>
                                <div style={{
                                  maxWidth: "85%",
                                  padding: "6px 10px",
                                  borderRadius: 6,
                                  fontSize: 12,
                                  lineHeight: 1.55,
                                  whiteSpace: "pre-wrap",
                                  background: m.role === "user" ? "var(--accent-subtle)" : "var(--bg-card)",
                                  color: m.role === "user" ? "var(--accent)" : "var(--text-primary)",
                                  border: `1px solid ${m.role === "user" ? "var(--accent)" : "var(--border)"}`,
                                }}>{m.content}</div>
                              </div>
                            ))}
                            {chatSending && (
                              <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 6, fontStyle: "italic" }}>
                                AI 正在回复...
                              </div>
                            )}
                          </div>
                        )}
                        <div className="flex gap-6">
                          <textarea
                            className="input"
                            placeholder="例：把第 3 章的「打脸」事件改名为「初次出手」；或者添加一个 [隐]：……"
                            value={chatInput}
                            rows={2}
                            onChange={e => setChatInput(e.target.value)}
                            onKeyDown={e => {
                              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                                e.preventDefault();
                                sendChat();
                              }
                            }}
                            style={{ flex: 1, fontSize: 12, resize: "vertical" }}
                            disabled={chatSending || committing}
                          />
                          <button
                            className="btn-primary"
                            style={{ fontSize: 11, padding: "3px 12px", alignSelf: "stretch" }}
                            onClick={sendChat}
                            disabled={!chatInput.trim() || chatSending || committing}
                            title="发送（⌘/Ctrl + Enter）"
                          >{chatSending ? "发送中" : "发送"}</button>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
          )}
        </div>
      )}

      {planLoading && !plan && (
        <div className="text-xs text-muted" style={{ padding: 8 }}>加载分段计划中...</div>
      )}

      {/* The actual chronicle viewer/editor (merged data) */}
      <div>
        <PlotOutlineEditor
          data={plotOutline}
          onSave={onSavePlot}
          onExtract={hasFullText && onRegenerateFromText ? onRegenerateFromText : undefined}
          extracting={regenerating}
        />
      </div>
    </div>
  );
}

/* ──────────────── Compact read-only chronicle preview (no editing) ──────────────── */
function ChroniclePreview({ epochs }: { epochs: ChronicleEpoch[] }) {
  if (!epochs || epochs.length === 0) {
    return <div className="text-xs text-muted">未生成任何大纲条目。</div>;
  }
  return (
    <div className="flex flex-col gap-10" style={{ maxHeight: 360, overflowY: "auto" }}>
      {epochs.map((ep, ei) => (
        <div key={ei}>
          {ep.title && (
            <div style={{ fontWeight: 700, fontSize: 13, color: "var(--text-primary)", marginBottom: 4 }}>
              {ep.title}
            </div>
          )}
          {(ep.periods || []).map((per: ChroniclePeriod, pi: number) => (
            <div key={pi} style={{ marginBottom: 8, paddingLeft: ep.title ? 8 : 0 }}>
              <div style={{ fontWeight: 600, fontSize: 12, color: "var(--accent)", marginBottom: 4 }}>
                {per.time || "(未填写时间)"}
              </div>
              <div className="flex flex-col gap-4" style={{ paddingLeft: 8, borderLeft: "2px solid var(--border)" }}>
                {(per.events || []).map((ev: any, evi: number) => (
                  <div key={evi} style={{ paddingLeft: 8 }}>
                    <div style={{ fontSize: 11, lineHeight: 1.55 }}>
                      <span style={{ fontWeight: 600, color: "var(--accent)" }}>
                        【{ev.subject}·{ev.category}·{ev.name}】
                      </span>
                      <span style={{ color: "var(--text-secondary)" }}>{ev.description}</span>
                    </div>
                    {ev.hidden && (
                      <div style={{ fontSize: 11, lineHeight: 1.55, marginTop: 2, color: "var(--gold)" }}>
                        <span style={{ fontWeight: 600 }}>[隐]</span> {ev.hidden}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
