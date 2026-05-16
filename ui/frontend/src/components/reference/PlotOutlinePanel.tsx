import React, { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost } from "../../api/client";
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
  type: "volumes" | "chunks";
  segments: SegmentInfo[];
  completed: number[];
  total_chapters: number;
}

interface SegmentResult {
  index: number;
  title: string;
  start_chapter: number;
  end_chapter: number;
  char_count: number;
  elapsed_s: number;
  errors: string[];
  plot_outline: { logline?: string; epochs?: ChronicleEpoch[] };
  characters?: any[];
  style_fingerprint?: any;
  narrative?: any;
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
}: Props) {
  const { toast } = useToast();
  const [plan, setPlan] = useState<SegmentPlan | null>(null);
  const [planLoading, setPlanLoading] = useState(false);
  const [previewIdx, setPreviewIdx] = useState<number | null>(null);
  const [preview, setPreview] = useState<SegmentResult | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [committing, setCommitting] = useState(false);
  const [merging, setMerging] = useState(false);

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

  const total = plan?.segments.length || 0;
  const completed = new Set(plan?.completed || []);
  const doneCount = completed.size;
  const allDone = total > 0 && doneCount >= total;
  const nextIdx = plan?.segments.find(s => !completed.has(s.index))?.index;

  const generatePreview = async (idx: number) => {
    setPreviewIdx(idx);
    setPreview(null);
    setPreviewLoading(true);
    try {
      const r = await apiPost<SegmentResult>(
        `/api/references/works/${refId}/segments/preview`,
        { segment_index: idx },
        { timeoutMs: 600_000 },
      );
      setPreview(r);
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
      await loadPlan();
    } catch (e: any) {
      toast(e?.message || "保存失败", "error");
    } finally { setCommitting(false); }
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

  // ── render ──
  return (
    <div className="flex flex-col gap-12">
      {/* Segment plan + preview (only if work has full text and not in standalone manual mode) */}
      {hasFullText && plan && total > 0 && (
        <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", padding: 12, background: "var(--bg-surface)" }}>
          <div className="flex items-center justify-between" style={{ marginBottom: 8 }}>
            <div>
              <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
                分段提取大纲
              </div>
              <div className="text-xs text-muted" style={{ marginTop: 2 }}>
                {plan.type === "volumes" ? "按卷处理" : "按 ~10 万字分块"} · {doneCount}/{total} 已完成
              </div>
            </div>
            <div className="flex gap-6">
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

          {/* progress bar */}
          <div style={{ height: 5, background: "var(--bg-surface-2)", borderRadius: 3, overflow: "hidden", marginBottom: 10 }}>
            <div style={{ height: "100%", width: `${(doneCount / total) * 100}%`, background: "var(--jade)", borderRadius: 3, transition: "width 0.3s" }} />
          </div>

          {/* segments list */}
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
                      <div className="truncate" style={{ fontSize: 12, fontWeight: 500, color: "var(--text-primary)" }}>
                        {s.title}
                      </div>
                      <div className="text-xs text-muted">
                        第 {s.start_chapter}–{s.end_chapter} 章 · {fmtChars(s.char_count)}
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
                      <div className="flex items-center justify-between" style={{ marginBottom: 8 }}>
                        <div style={{ fontSize: 12, fontWeight: 600, color: "var(--accent)" }}>
                          预览 · {preview.elapsed_s}s
                          <span className="text-xs text-muted" style={{ marginLeft: 8, fontWeight: 400 }}>
                            {(preview.plot_outline?.epochs || []).length} 大段 ·
                            {" "}{(preview.plot_outline?.epochs || []).reduce((n, ep) => n + (ep.periods?.length || 0), 0)} 时间段 ·
                            {" "}{(preview.characters || []).length} 角色
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
                      {preview.errors && preview.errors.length > 0 && (
                        <div className="text-xs" style={{ color: "var(--error)", marginBottom: 6 }}>
                          警告：{preview.errors.join("; ")}
                        </div>
                      )}
                      <ChroniclePreview epochs={preview.plot_outline?.epochs || []} />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
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
