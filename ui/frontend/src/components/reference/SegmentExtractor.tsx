import React, { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost } from "../../api/client";
import { useToast } from "../shared/Toast";

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

function fmtChars(n: number): string {
  if (n >= 10000) return `${(n / 10000).toFixed(1)} 万字`;
  return `${n.toLocaleString()} 字`;
}

interface Props {
  refId: string;
  hasFullText: boolean;
  preprocessingStatus: string;
  onWorkUpdated?: () => void; // re-fetch the work after merge
}

export default function SegmentExtractor({ refId, hasFullText, preprocessingStatus, onWorkUpdated }: Props) {
  const { toast } = useToast();
  const [open, setOpen] = useState(false);
  const [plan, setPlan] = useState<SegmentPlan | null>(null);
  const [loading, setLoading] = useState(false);
  const [runningIdx, setRunningIdx] = useState<number | null>(null);
  const [merging, setMerging] = useState(false);

  const loadPlan = useCallback(async () => {
    if (!hasFullText) return;
    setLoading(true);
    try {
      const p = await apiGet<SegmentPlan>(`/api/references/works/${refId}/segments/plan`);
      setPlan(p);
    } catch (e: any) {
      toast(e?.message || "加载分段计划失败", "error");
    } finally { setLoading(false); }
  }, [refId, hasFullText, toast]);

  useEffect(() => {
    if (open && hasFullText) loadPlan();
  }, [open, hasFullText, loadPlan]);

  const runOne = async (idx: number) => {
    setRunningIdx(idx);
    try {
      const r = await apiPost<any>(`/api/references/works/${refId}/segments/run`, { segment_index: idx }, { timeoutMs: 600_000 });
      toast(`第 ${idx + 1} 段已完成（${r.completed_count}/${r.total_segments}）`, "success");
      await loadPlan();
    } catch (e: any) {
      toast(e?.message || "分段提取失败", "error");
    } finally { setRunningIdx(null); }
  };

  const finalize = async () => {
    setMerging(true);
    try {
      const r = await apiPost<any>(`/api/references/works/${refId}/segments/finalize`, {});
      toast(`已合并 ${r.merge?.merged_segments || 0} 段结果到全书`, "success");
      if (onWorkUpdated) onWorkUpdated();
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

  if (!hasFullText) return null;

  const segments = plan?.segments || [];
  const completed = new Set(plan?.completed || []);
  const total = segments.length;
  const doneCount = completed.size;
  const nextIdx = segments.find(s => !completed.has(s.index))?.index;
  const allDone = total > 0 && doneCount >= total;

  return (
    <div className="card mb-16">
      <button
        className="btn-ghost w-full"
        style={{ justifyContent: "space-between", padding: "10px 14px", fontWeight: 600, fontSize: 14, borderRadius: 0 }}
        onClick={() => setOpen(!open)}
      >
        <span>
          分段提取 <span className="text-xs text-muted" style={{ marginLeft: 8, fontWeight: 400 }}>
            {plan ? `${plan.type === "volumes" ? "按卷" : "按字数"} · ${doneCount}/${total} 已完成` : "（点击展开规划与处理）"}
          </span>
        </span>
        <span className="text-xs text-muted" style={{ transition: "transform 0.15s", transform: open ? "rotate(180deg)" : "none", display: "inline-block" }}>&#x25BC;</span>
      </button>

      {open && (
        <div style={{ padding: "0 14px 14px" }}>
          <div style={{
            padding: "8px 10px",
            marginBottom: 10,
            background: "var(--bg-surface)",
            border: "1px solid var(--border)",
            borderRadius: 4,
            fontSize: 11,
            color: "var(--text-secondary)",
            lineHeight: 1.6,
          }}>
            分段提取按卷或按 ~10 万字切块,每段提取完后由你审阅再处理下一段。
            全部完成后点「合并到全书」会把每段结果聚合到顶层风格/角色/大纲字段。
          </div>

          {loading && <div className="text-xs text-muted" style={{ padding: 8 }}>加载中...</div>}

          {plan && !loading && (
            <>
              {segments.length === 0 ? (
                <div className="text-xs text-muted text-center" style={{ padding: 12 }}>
                  正文为空或无法识别章节。
                </div>
              ) : (
                <>
                  <div className="flex items-center gap-8" style={{ marginBottom: 8 }}>
                    <div style={{ flex: 1, height: 6, background: "var(--bg-surface-2)", borderRadius: 3, overflow: "hidden" }}>
                      <div style={{ height: "100%", width: `${(doneCount / total) * 100}%`, background: "var(--jade)", borderRadius: 3, transition: "width 0.3s" }} />
                    </div>
                    <span className="text-xs text-muted" style={{ minWidth: 60, textAlign: "right" }}>{doneCount}/{total}</span>
                  </div>

                  <div className="flex flex-col gap-4" style={{ marginBottom: 12 }}>
                    {segments.map(s => {
                      const isDone = completed.has(s.index);
                      const isRunning = runningIdx === s.index;
                      const isNext = nextIdx === s.index;
                      return (
                        <div key={s.index} style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                          padding: "8px 10px",
                          background: isDone ? "rgba(52,168,83,0.06)" : isNext ? "var(--bg-surface)" : "transparent",
                          border: "1px solid var(--border)",
                          borderRadius: 4,
                        }}>
                          <span className="tag" style={{
                            fontSize: 10,
                            minWidth: 36,
                            textAlign: "center",
                            color: isDone ? "var(--jade)" : "var(--text-secondary)",
                            background: "transparent",
                            border: `1px solid ${isDone ? "var(--jade)" : "var(--border)"}`,
                          }}>
                            {isDone ? "✓" : `#${s.index + 1}`}
                          </span>
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div className="truncate" style={{ fontSize: 12, fontWeight: 500, color: "var(--text-primary)" }}>
                              {s.title}
                            </div>
                            <div className="text-xs text-muted">
                              第 {s.start_chapter}–{s.end_chapter} 章 · {fmtChars(s.char_count)}
                            </div>
                          </div>
                          {isDone ? (
                            <span className="text-xs" style={{ color: "var(--jade)" }}>已完成</span>
                          ) : isRunning ? (
                            <span className="text-xs" style={{ color: "var(--gold)" }}>处理中...</span>
                          ) : (
                            <button
                              className={isNext ? "btn-primary" : "btn"}
                              style={{ fontSize: 11, padding: "3px 10px" }}
                              onClick={() => runOne(s.index)}
                              disabled={runningIdx !== null}
                            >
                              {isNext ? "处理此段" : "处理"}
                            </button>
                          )}
                        </div>
                      );
                    })}
                  </div>

                  <div className="flex gap-8" style={{ justifyContent: "flex-end" }}>
                    {doneCount > 0 && (
                      <button className="btn" style={{ fontSize: 11, color: "var(--text-tertiary)" }} onClick={reset} disabled={runningIdx !== null || merging}>
                        重置进度
                      </button>
                    )}
                    {allDone && (
                      <button className="btn-primary" onClick={finalize} disabled={merging}>
                        {merging ? "合并中..." : "合并到全书"}
                      </button>
                    )}
                  </div>
                </>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
