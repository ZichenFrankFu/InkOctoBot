/**
 * UniversalLLMDialog — the one dialogue box for every LLM call.
 *
 * Solves the user's recurring problem of "I never know what's actually
 * in the prompt before I commit to calling the LLM, and the manual
 * paste flow is hidden in 5 different spots." Single component, two
 * execution modes:
 *
 *   1. API mode: hit a backend endpoint, show streaming/loading state,
 *      allow abort + resume (the abort sets the cancellation flag in
 *      the session; resume re-fires the same call from where it
 *      stopped or with the same prompt).
 *
 *   2. Manual paste mode: hand the user a copy button for the
 *      prompt, take a textarea paste-back, parse via the optional
 *      parseResponse callback, then preview the parsed result.
 *
 * In both modes the user reviews the result in a preview pane, can
 * edit it, then clicks "提交 / Commit" — which fires the onCommit
 * callback so the parent page persists.
 *
 * Loader-status display reuses the PromptInspector heuristic so the
 * user can see "did worldbook actually inject" at a glance, before
 * burning an LLM call.
 */
import React, { useCallback, useEffect, useRef, useState } from "react";
import { apiPost } from "../../api/client";
import { useToast } from "./Toast";


export type LLMCallMode = "api" | "manual";


/** What the dialog asks the parent to do when the user finalizes. */
export interface LLMCommitPayload {
  /** Final text the user is committing (post-edit if they edited). */
  text: string;
  /** Where the text came from (so the parent can label provenance). */
  source: "api" | "manual_paste";
  /** Anything the parent's parseResponse returned (if defined). */
  parsed?: any;
  /** The prompt + system the call was made with (audit hook). */
  prompt: string;
  system?: string;
}


export interface UniversalLLMDialogProps {
  open: boolean;
  onClose: () => void;

  /** Window title + small help text. */
  title: string;
  description?: string;

  /** Fully-rendered prompt + system that will be sent. */
  prompt: string;
  system?: string;

  /** Optional: 14-loader status to show inline (PromptInspector style). */
  loaderStatus?: { title: string; present: boolean; chars: number }[];

  /**
   * API-mode entry: a function that hits the backend and resolves to
   * the LLM response text. The dialog supplies an AbortSignal so the
   * call can be aborted; the parent should pass it down to fetch().
   */
  invokeApi?: (signal: AbortSignal) => Promise<string>;

  /** Optional: parse the raw response (manual or API) into a struct. */
  parseResponse?: (raw: string) => any;

  /** Called when the user clicks "提交". Parent does the actual save. */
  onCommit: (payload: LLMCommitPayload) => void | Promise<void>;

  /** Optional minimum chars before commit is enabled. Default 30. */
  minChars?: number;

  /**
   * Force a single execution mode. ``"manual_only"`` skips the
   * preview/picker entirely and jumps straight to the manual-paste
   * phase, so the user sees the copy-prompt + paste-back surface as
   * the first screen. Useful when the parent already labels the
   * action "使用大模型网页版提取".
   */
  initialMode?: "picker" | "manual_only";
}


type Phase =
  | "preview"            // pre-call: user inspects prompt, picks mode
  | "running"            // API call in flight
  | "manual_pending"     // user is supposed to paste content
  | "committing"         // manual_only: parsing + persisting the paste
  | "result"             // we have a response, user reviews/edits
  | "error";             // last call errored


export default function UniversalLLMDialog({
  open, onClose, title, description,
  prompt, system, loaderStatus,
  invokeApi, parseResponse, onCommit,
  minChars = 30,
  initialMode = "picker",
}: UniversalLLMDialogProps) {
  const { toast } = useToast();
  const [phase, setPhase] = useState<Phase>(
    initialMode === "manual_only" ? "manual_pending" : "preview",
  );
  const [mode, setMode] = useState<LLMCallMode>(
    initialMode === "manual_only" ? "manual" : "api",
  );
  const [responseText, setResponseText] = useState("");
  const [errorMsg, setErrorMsg] = useState("");
  const [pasteBuf, setPasteBuf] = useState("");
  const [parsed, setParsed] = useState<any>(undefined);
  const [committing, setCommitting] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // Reset on open.
  useEffect(() => {
    if (open) {
      setPhase(initialMode === "manual_only" ? "manual_pending" : "preview");
      setMode(initialMode === "manual_only" ? "manual" : "api");
      setResponseText("");
      setPasteBuf("");
      setParsed(undefined);
      setErrorMsg("");
    } else {
      abortRef.current?.abort();
      abortRef.current = null;
    }
  }, [open, initialMode]);

  // ── actions ──
  // NOTE: every hook below MUST run on every render — the early
  // ``if (!open) return null`` lives at the bottom of the function so
  // hook count stays constant whether the dialog is mounted-but-hidden
  // or visible. Putting hooks after a conditional return produces
  // React error #310 the first time ``open`` flips.

  const startApi = useCallback(async () => {
    if (!invokeApi) {
      toast("此调用不支持 API 模式", "error");
      return;
    }
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    setMode("api");
    setPhase("running");
    setErrorMsg("");
    try {
      const text = await invokeApi(ctrl.signal);
      if (ctrl.signal.aborted) {
        // Aborted — leave us in preview so user can retry.
        setPhase("preview");
        return;
      }
      setResponseText(text || "");
      if (parseResponse) {
        try { setParsed(parseResponse(text || "")); } catch { setParsed(undefined); }
      }
      setPhase("result");
    } catch (e: any) {
      if (e?.name === "AbortError" || ctrl.signal.aborted) {
        setPhase("preview");
        toast("已中断", "info");
        return;
      }
      setErrorMsg(e?.message || String(e));
      setPhase("error");
    } finally {
      abortRef.current = null;
    }
  }, [invokeApi, parseResponse, toast]);

  const abort = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const resume = useCallback(() => {
    // For pure-request endpoints there's nothing to resume; refire.
    startApi();
  }, [startApi]);

  const startManual = useCallback(() => {
    setMode("manual");
    setPhase("manual_pending");
    setErrorMsg("");
  }, []);

  // Shared commit path so manual_only can submit straight from paste
  // without a redundant result-preview step.
  const doCommit = useCallback(async (text: string, parsedVal: any) => {
    const finalText = text.trim();
    if (finalText.length < minChars) {
      if (!window.confirm(
        `内容只有 ${finalText.length} 字 (< ${minChars} 推荐下限)。\n` +
        `这可能是空响应或粘贴错误。仍要提交吗？`
      )) return;
    }
    setCommitting(true);
    try {
      await onCommit({
        text: finalText,
        source: mode === "api" ? "api" : "manual_paste",
        parsed: parsedVal,
        prompt,
        system,
      });
      onClose();
    } catch (e: any) {
      setErrorMsg(e?.message || String(e));
      setPhase("error");
    } finally {
      setCommitting(false);
    }
  }, [minChars, onCommit, mode, prompt, system, onClose]);

  const submitManual = useCallback(() => {
    const txt = pasteBuf.trim();
    if (txt.length < 10) {
      toast("粘贴的内容看起来太短", "error");
      return;
    }
    let parsedVal: any = undefined;
    if (parseResponse) {
      try { parsedVal = parseResponse(txt); } catch { parsedVal = undefined; }
    }
    setResponseText(txt);
    setParsed(parsedVal);
    if (initialMode === "manual_only") {
      // No review step — parse + persist immediately (spec: 去掉预览).
      setPhase("committing");
      void doCommit(txt, parsedVal);
    } else {
      setPhase("result");
    }
  }, [pasteBuf, parseResponse, toast, initialMode, doCommit]);

  const copyPrompt = useCallback(() => {
    const full = system ? `${system}\n\n${prompt}` : prompt;
    navigator.clipboard.writeText(full).then(
      () => toast(`已复制 ${full.length} 字`, "success"),
      () => toast("复制失败，请手动选中", "error"),
    );
  }, [prompt, system, toast]);

  const commit = useCallback(
    () => doCommit(responseText, parsed),
    [doCommit, responseText, parsed],
  );

  // ── render ──

  if (!open) return null;

  return (
    <div
      style={{
        position: "fixed", inset: 0, zIndex: 1100,
        background: "rgba(0,0,0,0.45)",
        display: "flex", alignItems: "center", justifyContent: "center",
      }}
      onClick={() => phase === "preview" && onClose()}
    >
      <div
        className="card"
        style={{
          width: "min(1000px, 95vw)",
          height: "min(720px, 90vh)",
          background: "var(--bg-surface)",
          display: "flex", flexDirection: "column",
        }}
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <header style={{
          padding: "12px 16px",
          borderBottom: "1px solid var(--border)",
          display: "flex", justifyContent: "space-between", alignItems: "center",
        }}>
          <div>
            <h3 style={{ margin: 0, fontSize: 15 }}>{title}</h3>
            {description && (
              <p style={{ fontSize: 11, color: "var(--text-tertiary)", margin: "2px 0 0" }}>
                {description}
              </p>
            )}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <PhaseBadge phase={phase} />
            <button className="btn-sm" onClick={onClose} disabled={phase === "running" || phase === "committing"}></button>
          </div>
        </header>

        {/* Body — two columns: prompt left, action/result right */}
        <div style={{
          flex: 1, minHeight: 0,
          display: "grid", gridTemplateColumns: "1fr 1fr",
          gap: 0,
        }}>
          {/* LEFT: Prompt + loader status */}
          <div style={{
            padding: 12, overflowY: "auto",
            borderRight: "1px solid var(--border)",
            display: "flex", flexDirection: "column", minHeight: 0,
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 10 }}>
              <strong style={{ fontSize: 13 }}>提示词预览</strong>
              <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
                  {(prompt.length + (system?.length || 0))} 字
                </span>
                <button
                  onClick={copyPrompt}
                  title="复制完整提示词到剪贴板"
                  style={{
                    padding: "6px 14px",
                    fontSize: 12,
                    fontWeight: 600,
                    color: "white",
                    background: "var(--accent)",
                    border: "none",
                    borderRadius: 16,
                    cursor: "pointer",
                    boxShadow: "0 1px 2px rgba(0,0,0,0.08)",
                    display: "inline-flex",
                    alignItems: "center",
                    gap: 6,
                    transition: "background 0.15s, transform 0.08s",
                  }}
                  onMouseDown={e => (e.currentTarget as HTMLButtonElement).style.transform = "scale(0.97)"}
                  onMouseUp={e => (e.currentTarget as HTMLButtonElement).style.transform = "scale(1)"}
                  onMouseLeave={e => (e.currentTarget as HTMLButtonElement).style.transform = "scale(1)"}
                >
                  ⧉ 复制提示词
                </button>
              </div>
            </div>

            {/* Loader status (compact) */}
            {loaderStatus && loaderStatus.length > 0 && (
              <details style={{ marginBottom: 10 }} open>
                <summary style={{ cursor: "pointer", fontSize: 12, color: "var(--text-secondary)" }}>
                  注入状态 ({loaderStatus.filter(l => l.present).length}/{loaderStatus.length})
                </summary>
                <div style={{ marginTop: 6, display: "grid", gridTemplateColumns: "1fr", gap: 2 }}>
                  {loaderStatus.map(l => (
                    <div key={l.title} style={{
                      fontSize: 11, padding: "2px 6px",
                      color: l.present ? "var(--text-secondary)" : "var(--text-disabled)",
                      borderLeft: `2px solid ${l.present ? "var(--success)" : "var(--text-disabled)"}`,
                    }}>
                      {l.present ? "" : "·"} {l.title}
                      {l.present && <span style={{ color: "var(--text-tertiary)", marginLeft: 6 }}>{l.chars}字</span>}
                    </div>
                  ))}
                </div>
              </details>
            )}

            {/* System (collapsed) */}
            {system && (
              <details style={{ marginBottom: 8 }}>
                <summary style={{ cursor: "pointer", fontSize: 12 }}>系统提示 ({system.length})</summary>
                <pre style={preStyle()}>{system}</pre>
              </details>
            )}

            {/* User prompt */}
            <details open style={{ flex: 1, minHeight: 0, display: "flex", flexDirection: "column" }}>
              <summary style={{ cursor: "pointer", fontSize: 12 }}>用户提示词 ({prompt.length})</summary>
              <pre style={{ ...preStyle(), flex: 1, maxHeight: "none" }}>{prompt}</pre>
            </details>
          </div>

          {/* RIGHT: Action / Result */}
          <div style={{
            padding: 12, overflowY: "auto",
            display: "flex", flexDirection: "column", minHeight: 0,
          }}>
            {phase === "preview" && (
              <PreviewPane
                onUseApi={startApi}
                onUseManual={startManual}
                hasInvokeApi={!!invokeApi}
              />
            )}

            {phase === "running" && (
              <RunningPane onAbort={abort} />
            )}

            {phase === "manual_pending" && (
              <ManualPastePane
                pasteBuf={pasteBuf}
                setPasteBuf={setPasteBuf}
                onSubmit={submitManual}
                onCancel={initialMode === "manual_only" ? onClose : () => setPhase("preview")}
                cancelLabel={initialMode === "manual_only" ? "关闭" : "取消"}
                submitLabel={initialMode === "manual_only" ? "提交并解析" : "提交粘贴"}
              />
            )}

            {phase === "committing" && (
              <div style={{ textAlign: "center", padding: 40 }}>
                <div style={{ fontSize: 28, marginBottom: 16 }}>⏳</div>
                <h4>正在解析并写入...</h4>
                <p style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
                  正在解析大模型返回的内容并保存到数据库。
                </p>
              </div>
            )}

            {phase === "result" && (
              <ResultPane
                text={responseText}
                onTextChange={setResponseText}
                parsed={parsed}
                source={mode === "api" ? "API 调用" : "网页版粘贴"}
                onCommit={commit}
                onRetry={() => setPhase("preview")}
                committing={committing}
              />
            )}

            {phase === "error" && (
              <ErrorPane
                msg={errorMsg}
                onRetry={
                  initialMode === "manual_only"
                    ? () => setPhase("manual_pending")
                    : mode === "api" ? resume : () => setPhase("preview")
                }
                onCancel={
                  initialMode === "manual_only"
                    ? () => setPhase("manual_pending")
                    : () => setPhase("preview")
                }
                retryLabel={initialMode === "manual_only" ? "返回粘贴" : "续跑 / 重试"}
                cancelLabel={initialMode === "manual_only" ? "返回粘贴" : "关闭"}
              />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}


// ── sub-components ──


function preStyle(): React.CSSProperties {
  return {
    marginTop: 6, padding: 8, background: "var(--bg-surface-2)",
    fontSize: 11, fontFamily: "var(--font-mono)",
    maxHeight: 280, overflow: "auto", borderRadius: 3,
    whiteSpace: "pre-wrap", wordBreak: "break-word",
  };
}


function PhaseBadge({ phase }: { phase: Phase }) {
  const labels: Record<Phase, { label: string; color: string }> = {
    preview:        { label: "预览",   color: "var(--text-tertiary)" },
    running:        { label: "运行中", color: "var(--accent)" },
    manual_pending: { label: "等待粘贴", color: "var(--gold)" },
    committing:     { label: "处理中", color: "var(--accent)" },
    result:         { label: "已就绪", color: "var(--success)" },
    error:          { label: "错误",   color: "var(--danger)" },
  };
  const v = labels[phase];
  return (
    <span style={{
      fontSize: 11, color: v.color,
      padding: "2px 8px", border: `1px solid ${v.color}`,
      borderRadius: 12,
    }}>{v.label}</span>
  );
}


function PreviewPane({
  onUseApi, onUseManual, hasInvokeApi,
}: { onUseApi: () => void; onUseManual: () => void; hasInvokeApi: boolean }) {
  return (
    <>
      <h4 style={{ marginTop: 0 }}>选择执行方式</h4>
      <p style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
        左侧为完整提示词。检查无误后选择如何调用大模型：
      </p>
      <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 16 }}>
        <button
          className="btn primary"
          onClick={onUseApi}
          disabled={!hasInvokeApi}
          style={{ padding: "12px 16px", textAlign: "left" }}
        >
          <div style={{ fontSize: 14, fontWeight: 600 }}>使用大模型 API 提取</div>
          <div style={{ fontSize: 11, opacity: 0.85, marginTop: 4 }}>
            直连当前服务商；运行中可中断 / 续跑
            {!hasInvokeApi && " · 该调用未提供 API 钩子"}
          </div>
        </button>
        <button
          className="btn"
          onClick={onUseManual}
          style={{ padding: "12px 16px", textAlign: "left" }}
        >
          <div style={{ fontSize: 14, fontWeight: 600 }}>使用大模型网页版提取</div>
          <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 4 }}>
            复制提示词到网页大模型运行 → 粘回结果 → 自动解析入库
          </div>
        </button>
      </div>
    </>
  );
}


function RunningPane({ onAbort }: { onAbort: () => void }) {
  return (
    <div style={{ textAlign: "center", padding: 40 }}>
      <div style={{ fontSize: 32, marginBottom: 16 }}>⏳</div>
      <h4>正在调用大模型...</h4>
      <p style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
        中断后会停止生成；重试时将重发同一份提示词。
      </p>
      <button className="btn danger" onClick={onAbort} style={{ marginTop: 16 }}>
        中断
      </button>
    </div>
  );
}


function ManualPastePane({
  pasteBuf, setPasteBuf, onSubmit, onCancel, cancelLabel, submitLabel,
}: {
  pasteBuf: string; setPasteBuf: (v: string) => void;
  onSubmit: () => void; onCancel: () => void;
  cancelLabel?: string; submitLabel?: string;
}) {
  return (
    <>
      <h4 style={{ marginTop: 0 }}>将大模型输出粘贴到此处</h4>
      <p style={{ fontSize: 12, color: "var(--text-tertiary)", lineHeight: 1.6 }}>
        先点左侧「复制提示词」 → 在网页版大模型粘贴并运行 → 将完整回复粘到下方文本框。
      </p>
      <textarea
        value={pasteBuf}
        onChange={e => setPasteBuf(e.target.value)}
        placeholder="在这里粘贴大模型回复..."
        style={{
          flex: 1, marginTop: 8, padding: 10, fontSize: 13,
          fontFamily: "var(--font-mono)",
          background: "var(--bg-surface-2)",
          color: "var(--text-primary)",
          border: "1px solid var(--border)", borderRadius: 4,
          minHeight: 280, resize: "vertical",
          outline: "none",
        }}
      />
      <div style={{
        display: "flex", justifyContent: "space-between",
        alignItems: "center", marginTop: 10,
      }}>
        <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
          {pasteBuf.length} 字
        </span>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn" onClick={onCancel}>{cancelLabel || "取消"}</button>
          <button
            className="btn primary"
            onClick={onSubmit}
            disabled={pasteBuf.trim().length < 10}
          >
            {submitLabel || "提交粘贴"}
          </button>
        </div>
      </div>
    </>
  );
}


function ResultPane({
  text, onTextChange, parsed, source, onCommit, onRetry, committing,
}: {
  text: string;
  onTextChange: (v: string) => void;
  parsed?: any;
  source: string;
  onCommit: () => void;
  onRetry: () => void;
  committing: boolean;
}) {
  return (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
        <h4 style={{ margin: 0 }}>结果就绪（来源：{source}）</h4>
        <span style={{ fontSize: 11, color: "var(--text-tertiary)" }}>{text.length} 字</span>
      </div>
      <p style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
        预览结果，必要时直接编辑。点击提交后写入 / 应用到上层。
      </p>
      <textarea
        value={text}
        onChange={e => onTextChange(e.target.value)}
        style={{
          flex: 1, padding: 10, fontSize: 13,
          fontFamily: "var(--font-mono)",
          background: "var(--bg-surface-2)",
          color: "var(--text-primary)",
          border: "1px solid var(--border)", borderRadius: 4,
          minHeight: 240, resize: "vertical", marginTop: 8,
          outline: "none",
        }}
      />
      {parsed !== undefined && (
        <details style={{ marginTop: 8 }}>
          <summary style={{ cursor: "pointer", fontSize: 11, color: "var(--text-tertiary)" }}>
            已解析的结构
          </summary>
          <pre style={preStyle()}>{JSON.stringify(parsed, null, 2)}</pre>
        </details>
      )}
      <div style={{
        display: "flex", justifyContent: "space-between",
        marginTop: 12, gap: 8,
      }}>
        <button className="btn" onClick={onRetry} disabled={committing}>重来</button>
        <button
          className="btn primary"
          onClick={onCommit}
          disabled={committing || text.trim().length === 0}
        >
          {committing ? "提交中..." : "提交并应用"}
        </button>
      </div>
    </>
  );
}


function ErrorPane({
  msg, onRetry, onCancel, retryLabel, cancelLabel,
}: {
  msg: string; onRetry: () => void; onCancel: () => void;
  retryLabel?: string; cancelLabel?: string;
}) {
  return (
    <div style={{ textAlign: "center", padding: 24 }}>
      <div style={{ fontSize: 32 }}></div>
      <h4>调用失败</h4>
      <pre style={{
        ...preStyle(), maxHeight: 220, textAlign: "left",
        background: "var(--bg-warn-subtle, var(--bg-surface-2))",
      }}>{msg}</pre>
      <div style={{ display: "flex", gap: 8, justifyContent: "center", marginTop: 16 }}>
        <button className="btn" onClick={onCancel}>{cancelLabel || "关闭"}</button>
        <button className="btn primary" onClick={onRetry}>{retryLabel || "续跑 / 重试"}</button>
      </div>
    </div>
  );
}


/**
 * Convenience: hit /api/generation/quick-generate with prompt_only=true
 * to fetch the rendered prompt for inspection without burning a call.
 *
 * Typical usage:
 *   const { prompt, loaderStatus } = await fetchPromptForInspection(...)
 *   then pass into UniversalLLMDialog.
 */
export async function fetchPromptForInspection(params: {
  projectId: string;
  chapterId: string;
  chapterNum?: number;
  synopsis?: string;
  characters?: string[];
}): Promise<{ prompt: string; loaderStatus: { title: string; present: boolean; chars: number }[] }> {
  const res = await apiPost<{ prompt: string }>("/api/generation/quick-generate", {
    project_id: params.projectId,
    chapter_id: params.chapterId,
    chapter_num: params.chapterNum ?? 1,
    synopsis: params.synopsis || "",
    characters: params.characters || [],
    prompt_only: true,
  });
  const prompt = res.prompt || "";
  const EXPECTED = [
    "用户特别要求", "本章大纲", "时间与地点", "本章出场角色",
    "出场角色档案", "世界观设定", "关联参考", "用户写作偏好",
    "未回收伏笔", "故事线", "相关灵感", "客观状态",
    "读者视角记忆", "已有正文", "创作技能", "平台风格",
  ];
  const lines = prompt.split("\n");
  const sections: { title: string; body: string }[] = [];
  let cur: { title: string; body: string } | null = null;
  for (const line of lines) {
    const m = line.match(/^##\s+(.+?)\s*$/);
    if (m) { if (cur) sections.push(cur); cur = { title: m[1], body: "" }; }
    else if (cur) cur.body += line + "\n";
  }
  if (cur) sections.push(cur);
  const byTitle = new Map(sections.map(s => [s.title.trim(), s]));
  const loaderStatus = EXPECTED.map(title => {
    const sec = byTitle.get(title) ||
                [...byTitle.values()].find(x => x.title.startsWith(title));
    const present = !!sec && sec.body.trim().length > 0;
    return { title, present, chars: sec ? sec.body.trim().length : 0 };
  });
  return { prompt, loaderStatus };
}
