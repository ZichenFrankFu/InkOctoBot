/**
 * DomainLearningPage — Part B UI.
 *
 * Walks suggestions through the two gates:
 *  - Gate 1: proposed → approve(api|manual) → compiling
 *           (or rejected)
 *  - Gate 2: needs_review → accept(edited?) → accepted
 *           (or rejected)
 *
 * Accepts manual-mode submission (paste from a browser LLM) and
 * lets the user edit the compiled content before final accept
 * (the spec's "soft door": preview + optional edit, not a
 * fact-check requirement).
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiGet, apiPost, apiPut } from "../api/client";
import type {
  DomainSuggestion, DomainReview, DomainStatus,
} from "../api/typesExt";
import { useToast } from "../components/shared/Toast";


const STATUS_LABEL: Record<DomainStatus, string> = {
  proposed:     "待决",
  compiling:    "编译中",
  needs_review: "待审 (Gate 2)",
  accepted:     "已采纳",
  rejected:     "已拒绝",
  failed:       "编译失败",
};


const STATUS_COLOR: Record<DomainStatus, string> = {
  proposed:     "var(--accent)",
  compiling:    "var(--gold)",
  needs_review: "var(--cyan)",
  accepted:     "var(--success)",
  rejected:     "var(--text-tertiary)",
  failed:       "var(--danger)",
};


interface Props {
  projectId: string;
}


export default function DomainLearningPage({ projectId }: Props) {
  const { toast } = useToast();
  const [suggestions, setSuggestions] = useState<DomainSuggestion[]>([]);
  const [loading, setLoading] = useState(false);
  const [reviewId, setReviewId] = useState<string | null>(null);
  const [review, setReview] = useState<DomainReview | null>(null);
  const [editedContent, setEditedContent] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [manualPrompt, setManualPrompt] = useState<string>("");
  const [manualPasteBack, setManualPasteBack] = useState<string>("");
  const [manualTargetId, setManualTargetId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    try {
      const r = await apiGet<{ suggestions: DomainSuggestion[] }>(
        `/api/domain-learning/suggestions?project_id=${encodeURIComponent(projectId)}`,
      );
      setSuggestions(r.suggestions || []);
    } catch (e: any) {
      toast(`加载失败: ${e.message}`, "error");
    } finally {
      setLoading(false);
    }
  }, [projectId, toast]);

  useEffect(() => { refresh(); }, [refresh]);

  const groups = useMemo(() => {
    const out: Record<DomainStatus, DomainSuggestion[]> = {
      proposed: [], compiling: [], needs_review: [],
      accepted: [], rejected: [], failed: [],
    };
    for (const s of suggestions) {
      (out[s.status as DomainStatus] || out.proposed).push(s);
    }
    return out;
  }, [suggestions]);

  const reject = useCallback(async (id: string) => {
    if (!window.confirm("确认拒绝？拒绝后该 domain 不会再次被建议。")) return;
    try {
      await apiPost(`/api/domain-learning/${id}/reject`, {});
      toast("已拒绝", "success");
      refresh();
    } catch (e: any) { toast(`失败: ${e.message}`, "error"); }
  }, [refresh, toast]);

  const approveApi = useCallback(async (s: DomainSuggestion) => {
    if (!window.confirm(
      `要用 API 模式编译【${s.domain}】吗？\n` +
      `会调用 web-search LLM (可能产生费用)。`,
    )) return;
    setBusy(true);
    try {
      const res = await apiPost<any>(
        `/api/domain-learning/${s.suggestion_id}/approve`,
        { mode: "api" },
      );
      if (res.status === "needs_review") {
        toast("编译完成，可以审阅了", "success");
      } else if (res.status === "failed" && res.hint === "manual") {
        toast("Web search 不可用，请改用手动模式", "error");
      } else {
        toast(`状态: ${res.status}`, "success");
      }
      refresh();
    } catch (e: any) {
      toast(`编译失败: ${e.message}`, "error");
    } finally {
      setBusy(false);
    }
  }, [refresh, toast]);

  const approveManual = useCallback(async (s: DomainSuggestion) => {
    setBusy(true);
    try {
      const res = await apiPost<any>(
        `/api/domain-learning/${s.suggestion_id}/approve`,
        { mode: "manual" },
      );
      setManualPrompt(res.research_prompt || "");
      setManualTargetId(s.suggestion_id);
      setManualPasteBack("");
      refresh();
    } catch (e: any) {
      toast(`失败: ${e.message}`, "error");
    } finally {
      setBusy(false);
    }
  }, [refresh, toast]);

  const submitManual = useCallback(async () => {
    if (!manualTargetId) return;
    if (manualPasteBack.trim().length < 100) {
      toast("贴回的内容太短，确定？", "error");
      return;
    }
    setBusy(true);
    try {
      await apiPost(
        `/api/domain-learning/${manualTargetId}/submit-manual`,
        { content: manualPasteBack.trim() },
      );
      toast("已提交，进入审阅", "success");
      setManualPrompt("");
      setManualPasteBack("");
      setManualTargetId(null);
      refresh();
    } catch (e: any) {
      toast(`提交失败: ${e.message}`, "error");
    } finally {
      setBusy(false);
    }
  }, [manualPasteBack, manualTargetId, refresh, toast]);

  const openReview = useCallback(async (id: string) => {
    setReviewId(id);
    try {
      const r = await apiGet<DomainReview>(`/api/domain-learning/${id}/review`);
      setReview(r);
      setEditedContent(r.compiled_content);
    } catch (e: any) {
      toast(`加载失败: ${e.message}`, "error");
      setReviewId(null);
    }
  }, [toast]);

  const accept = useCallback(async (useEdited: boolean) => {
    if (!reviewId) return;
    setBusy(true);
    try {
      const res = await apiPost<any>(
        `/api/domain-learning/${reviewId}/accept`,
        { edited_content: useEdited ? editedContent : null },
      );
      toast(`已采纳，skill: ${res.skill_name}`, "success");
      setReviewId(null);
      setReview(null);
      refresh();
    } catch (e: any) {
      toast(`采纳失败: ${e.message}`, "error");
    } finally {
      setBusy(false);
    }
  }, [editedContent, refresh, reviewId, toast]);

  const triggerSuggest = useCallback(async () => {
    if (!projectId) return;
    if (!window.confirm("立即触发一次批量提取 + 领域检测？（绕过阈值）")) return;
    try {
      await apiPost("/api/domain-learning/suggest", { project_id: projectId });
      toast("已调度，几秒后刷新", "success");
      setTimeout(refresh, 3000);
    } catch (e: any) {
      toast(`失败: ${e.message}`, "error");
    }
  }, [projectId, refresh, toast]);

  if (!projectId) {
    return <div style={{ padding: 24 }}>请先选择项目</div>;
  }

  return (
    <div className="page" style={{ padding: 16 }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <div>
          <h2 style={{ margin: 0 }}>🧠 领域知识（自学习）</h2>
          <p style={{ color: "var(--text-tertiary)", fontSize: 12, margin: "4px 0 0" }}>
            当你的编辑反复要求某个专业方向（如航天/法律/医学），系统会建议补充该领域知识。
            审阅+保存后，相关章节生成会自动注入此知识作为创作质感参考。
          </p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn" onClick={refresh} disabled={loading}>刷新</button>
          <button className="btn primary" onClick={triggerSuggest}>立即触发检测</button>
        </div>
      </header>

      {/* Status grid */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(6, 1fr)", gap: 8, marginBottom: 16 }}>
        {(Object.keys(STATUS_LABEL) as DomainStatus[]).map(st => (
          <div key={st} className="card" style={{ padding: 10, textAlign: "center" }}>
            <div style={{ fontSize: 11, color: STATUS_COLOR[st] }}>{STATUS_LABEL[st]}</div>
            <div style={{ fontSize: 24, fontWeight: 600, marginTop: 4 }}>{groups[st].length}</div>
          </div>
        ))}
      </div>

      {/* Gate 1: proposed list */}
      <Section title="Gate 1 · 待决建议（点 [研究] 进入编译）">
        {groups.proposed.length === 0 ? (
          <Empty msg="暂无待决建议——下次批量学习触发时会出现。" />
        ) : groups.proposed.map(s => (
          <div key={s.suggestion_id} className="card" style={{ padding: 12, marginBottom: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
              <div style={{ flex: 1 }}>
                <h4 style={{ margin: 0 }}>{s.domain}{s.sub_domain && <span style={{ color: "var(--text-tertiary)", fontWeight: 400 }}> / {s.sub_domain}</span>}</h4>
                <p style={{ fontSize: 13, color: "var(--text-secondary)", margin: "4px 0" }}>{s.rationale}</p>
              </div>
              <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
                <button className="btn primary" onClick={() => approveApi(s)} disabled={busy}>API 编译</button>
                <button className="btn" onClick={() => approveManual(s)} disabled={busy}>手动编译</button>
                <button className="btn danger" onClick={() => reject(s.suggestion_id)} disabled={busy}>拒绝</button>
              </div>
            </div>
          </div>
        ))}
      </Section>

      {/* Manual paste dialog */}
      {manualPrompt && (
        <div className="card" style={{
          padding: 16, marginBottom: 16,
          border: "2px solid var(--gold)", background: "var(--bg-surface-2)",
        }}>
          <h3 style={{ marginTop: 0 }}>📝 手动编译 — 把下面 prompt 拿去网页 LLM</h3>
          <div style={{ marginBottom: 8 }}>
            <button className="btn-sm" onClick={() => {
              navigator.clipboard.writeText(manualPrompt);
              toast("已复制", "success");
            }}>📋 复制 prompt</button>
          </div>
          <details>
            <summary style={{ cursor: "pointer", fontSize: 12, marginBottom: 6 }}>查看 prompt</summary>
            <pre style={{
              background: "var(--bg-surface)", padding: 10, fontSize: 11,
              maxHeight: 200, overflow: "auto", borderRadius: 4,
              whiteSpace: "pre-wrap",
            }}>{manualPrompt}</pre>
          </details>
          <div style={{ marginTop: 12 }}>
            <label style={{ fontSize: 13, fontWeight: 600 }}>把网页 LLM 的输出贴到这里：</label>
            <textarea
              value={manualPasteBack}
              onChange={e => setManualPasteBack(e.target.value)}
              rows={10}
              placeholder="## 核心概念&#10;..."
              style={{
                width: "100%", marginTop: 6, padding: 8, fontSize: 12,
                fontFamily: "var(--font-mono)", boxSizing: "border-box",
                border: "1px solid var(--border)", borderRadius: 4,
              }}
            />
            <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8 }}>
              <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{manualPasteBack.length} 字</span>
              <div style={{ display: "flex", gap: 8 }}>
                <button className="btn" onClick={() => {
                  setManualPrompt("");
                  setManualPasteBack("");
                  setManualTargetId(null);
                }}>关闭</button>
                <button className="btn primary" onClick={submitManual} disabled={busy || manualPasteBack.trim().length < 100}>
                  {busy ? "提交中..." : "提交进入审阅"}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* In progress */}
      {groups.compiling.length > 0 && (
        <Section title="编译中">
          {groups.compiling.map(s => (
            <div key={s.suggestion_id} className="card" style={{ padding: 12, marginBottom: 8 }}>
              <strong>{s.domain}</strong>
              <span style={{ color: "var(--text-tertiary)", marginLeft: 8 }}>
                模式: {s.source_mode || "未设"}
                {s.source_mode === "manual" && " (等待手动提交)"}
              </span>
            </div>
          ))}
        </Section>
      )}

      {/* Gate 2: needs_review */}
      <Section title="Gate 2 · 待审 (软门：可以直接采纳或编辑后采纳)">
        {groups.needs_review.length === 0 ? (
          <Empty msg="无待审。" />
        ) : groups.needs_review.map(s => (
          <div key={s.suggestion_id} className="card" style={{ padding: 12, marginBottom: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <div style={{ flex: 1 }}>
                <h4 style={{ margin: 0 }}>{s.domain}</h4>
                <p style={{ fontSize: 11, color: "var(--text-tertiary)", margin: "4px 0" }}>
                  来源: {s.source_mode} · 预览:
                </p>
                <p style={{ fontSize: 12, color: "var(--text-secondary)", margin: 0 }}>
                  {s.compiled_content_preview}...
                </p>
              </div>
              <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
                <button className="btn primary" onClick={() => openReview(s.suggestion_id)}>审阅 →</button>
                <button className="btn danger" onClick={() => reject(s.suggestion_id)}>弃用</button>
              </div>
            </div>
          </div>
        ))}
      </Section>

      {/* Accepted */}
      <Section title="已采纳（注入相关章节生成）">
        {groups.accepted.length === 0 ? (
          <Empty msg="还没有已采纳的领域知识。" />
        ) : groups.accepted.map(s => (
          <div key={s.suggestion_id} className="card" style={{ padding: 12, marginBottom: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between" }}>
              <div>
                <h4 style={{ margin: 0 }}>
                  ✅ {s.domain}
                  {s.skill_name && <span style={{ fontSize: 11, marginLeft: 8, color: "var(--text-tertiary)" }}>skill: <code>{s.skill_name}</code></span>}
                </h4>
                <p style={{ fontSize: 12, color: "var(--text-tertiary)", margin: "4px 0 0" }}>
                  采纳于 {new Date(s.updated_at * 1000).toLocaleString()}
                </p>
              </div>
              <button className="btn" onClick={() => openReview(s.suggestion_id)}>查看/编辑</button>
            </div>
          </div>
        ))}
      </Section>

      {/* Failed + Rejected (collapsed) */}
      {(groups.failed.length > 0 || groups.rejected.length > 0) && (
        <details>
          <summary style={{ cursor: "pointer", color: "var(--text-tertiary)", marginTop: 12 }}>
            历史记录 (失败 {groups.failed.length}, 已拒绝 {groups.rejected.length})
          </summary>
          {[...groups.failed, ...groups.rejected].map(s => (
            <div key={s.suggestion_id} style={{ padding: "6px 12px", fontSize: 12, color: "var(--text-tertiary)" }}>
              [{STATUS_LABEL[s.status as DomainStatus]}] {s.domain} — {s.rationale}
            </div>
          ))}
        </details>
      )}

      {/* Review modal */}
      {review && (
        <ReviewModal
          review={review}
          editedContent={editedContent}
          setEditedContent={setEditedContent}
          busy={busy}
          onAccept={accept}
          onReject={() => reject(review.suggestion_id)}
          onClose={() => { setReviewId(null); setReview(null); }}
        />
      )}
    </div>
  );
}


function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginTop: 12 }}>
      <h3 style={{ fontSize: 13, color: "var(--text-tertiary)", margin: "0 0 8px" }}>{title}</h3>
      {children}
    </div>
  );
}


function Empty({ msg }: { msg: string }) {
  return <p style={{ color: "var(--text-tertiary)", fontSize: 12, paddingLeft: 8 }}>{msg}</p>;
}


function ReviewModal({
  review, editedContent, setEditedContent, busy, onAccept, onReject, onClose,
}: {
  review: DomainReview;
  editedContent: string;
  setEditedContent: (v: string) => void;
  busy: boolean;
  onAccept: (useEdited: boolean) => void;
  onReject: () => void;
  onClose: () => void;
}) {
  const isAccepted = review.status === "accepted";
  return (
    <div style={{
      position: "fixed", inset: 0, background: "rgba(0,0,0,0.5)",
      display: "flex", alignItems: "center", justifyContent: "center", zIndex: 1000,
    }} onClick={onClose}>
      <div className="card" style={{
        padding: 16, width: "min(900px, 90vw)", maxHeight: "90vh",
        overflow: "auto", background: "var(--bg-surface)",
      }} onClick={e => e.stopPropagation()}>
        <h2 style={{ marginTop: 0 }}>
          {isAccepted ? "📚" : "🔍"} {review.domain}
          {review.sub_domain && <span style={{ color: "var(--text-tertiary)", fontWeight: 400 }}> / {review.sub_domain}</span>}
        </h2>
        <p style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
          理由: {review.rationale} · 编译模式: {review.source_mode}
        </p>
        <div style={{
          padding: 10, background: "var(--bg-warn-subtle, #fff8e1)",
          fontSize: 12, borderRadius: 4, marginBottom: 12,
          border: "1px solid var(--gold)",
        }}>
          ⚠️ 本内容由 AI 综合搜索结果编译，<strong>非权威来源</strong>。
          仅供创作质感参考。你可以直接采纳，也可以改后再采纳——不要求你判断真伪。
        </div>
        <textarea
          value={editedContent}
          onChange={e => setEditedContent(e.target.value)}
          rows={20}
          style={{
            width: "100%", padding: 10, fontSize: 13,
            fontFamily: "var(--font-mono)",
            border: "1px solid var(--border)", borderRadius: 4,
            boxSizing: "border-box",
          }}
        />
        <div style={{
          display: "flex", justifyContent: "space-between",
          alignItems: "center", marginTop: 12,
        }}>
          <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
            {editedContent.length} 字
            {editedContent !== review.compiled_content && (
              <span style={{ color: "var(--accent)", marginLeft: 8 }}>· 已编辑</span>
            )}
          </span>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn" onClick={onClose}>关闭</button>
            {!isAccepted && (
              <>
                <button className="btn danger" onClick={onReject} disabled={busy}>弃用</button>
                <button
                  className="btn"
                  onClick={() => onAccept(false)}
                  disabled={busy || editedContent === ""}
                >
                  原样采纳
                </button>
                <button
                  className="btn primary"
                  onClick={() => onAccept(true)}
                  disabled={busy || editedContent === ""}
                >
                  {editedContent !== review.compiled_content ? "保存编辑后采纳" : "采纳"}
                </button>
              </>
            )}
            {isAccepted && (
              <button
                className="btn primary"
                onClick={() => updateAccepted(review.suggestion_id, editedContent)}
                disabled={busy || editedContent === review.compiled_content}
              >
                保存编辑（重算 embedding）
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}


async function updateAccepted(id: string, content: string) {
  try {
    await apiPut(`/api/domain-learning/${id}/content`, { new_content: content });
    window.location.reload();  // simplest — refresh state
  } catch (e: any) {
    alert(`更新失败: ${e.message}`);
  }
}
