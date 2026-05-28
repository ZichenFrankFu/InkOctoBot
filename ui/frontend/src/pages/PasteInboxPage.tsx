/**
 * Manual Paste Inbox — independent page (P0).
 *
 * Every LLMCallSite under Manual Mode registers a paste token. This
 * page lists them globally so the user can pick one up, see the
 * full prompt, paste the response from a web LLM, and resolve the
 * token — at which point the pipeline that was waiting on the LLM
 * call continues. Previously this only lived as an inline panel
 * inside EditorPage which was easy to miss.
 */
import React, { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost } from "../api/client";
import type { PendingPaste, PendingPasteDetail } from "../api/typesExt";
import { useToast } from "../components/shared/Toast";


function formatAge(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s 前`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m 前`;
  return `${Math.floor(seconds / 3600)}h 前`;
}


export default function PasteInboxPage() {
  const { toast } = useToast();
  const [pending, setPending] = useState<PendingPaste[]>([]);
  const [loading, setLoading] = useState(false);
  const [activeToken, setActiveToken] = useState<string | null>(null);
  const [detail, setDetail] = useState<PendingPasteDetail | null>(null);
  const [pasteText, setPasteText] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const r = await apiGet<{ pending: PendingPaste[] }>("/api/llm-paste/pending");
      setPending(r.pending || []);
    } catch (e: any) {
      toast(`刷新失败：${e.message}`, "error");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    refresh();
    // Poll every 4 seconds so new pending tokens surface without manual refresh.
    const id = window.setInterval(refresh, 4000);
    return () => window.clearInterval(id);
  }, [refresh]);

  const openDetail = useCallback(async (token: string) => {
    setActiveToken(token);
    setPasteText("");
    setDetail(null);
    try {
      const d = await apiGet<PendingPasteDetail>(`/api/llm-paste/${token}`);
      setDetail(d);
    } catch (e: any) {
      toast(`无法打开：${e.message}`, "error");
      setActiveToken(null);
    }
  }, [toast]);

  const copyPrompt = useCallback(() => {
    if (!detail) return;
    const full = detail.system ? `${detail.system}\n\n${detail.prompt}` : detail.prompt;
    navigator.clipboard.writeText(full).then(
      () => toast("已复制到剪贴板", "success"),
      () => toast("复制失败，请手动选中", "error"),
    );
  }, [detail, toast]);

  const submit = useCallback(async () => {
    if (!activeToken) return;
    const trimmed = pasteText.trim();
    if (trimmed.length < 20) {
      toast("贴回的内容看起来太短（< 20 字），确定是有效响应？", "error");
      return;
    }
    setSubmitting(true);
    try {
      await apiPost(`/api/llm-paste/${activeToken}`, { response_raw: trimmed });
      toast(`提交成功 (${trimmed.length} 字)`, "success");
      setActiveToken(null);
      setDetail(null);
      setPasteText("");
      refresh();
    } catch (e: any) {
      toast(`提交失败：${e.message}`, "error");
    } finally {
      setSubmitting(false);
    }
  }, [activeToken, pasteText, refresh, toast]);

  const cancel = useCallback(async (token: string) => {
    if (!window.confirm("取消这个 paste token 会让等待的 pipeline 步骤失败。确认？")) return;
    try {
      await apiPost(`/api/llm-paste/${token}/cancel`, {});
      toast("已取消", "success");
      if (activeToken === token) {
        setActiveToken(null);
        setDetail(null);
      }
      refresh();
    } catch (e: any) {
      toast(`取消失败：${e.message}`, "error");
    }
  }, [activeToken, refresh, toast]);

  return (
    <div className="page" style={{ padding: 16 }}>
      <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <h2 style={{ margin: 0 }}>📥 Manual Paste 收件箱</h2>
          <p style={{ color: "var(--text-tertiary)", fontSize: 12, margin: "4px 0 0" }}>
            当 LLM 走 Manual 模式时，每次调用会在这里产生一个待粘贴的 token。
            从这里复制 prompt → 拿到网页 LLM 跑 → 把结果贴回提交，等待的 pipeline 继续。
          </p>
        </div>
        <button className="btn" onClick={refresh} disabled={loading}>
          {loading ? "刷新中..." : "刷新"}
        </button>
      </header>

      <div style={{ display: "grid", gridTemplateColumns: "360px 1fr", gap: 16, alignItems: "start" }}>
        {/* Pending list */}
        <div className="card" style={{ padding: 12, maxHeight: "calc(100vh - 180px)", overflowY: "auto" }}>
          <h3 style={{ marginTop: 0, fontSize: 14 }}>待处理 ({pending.length})</h3>
          {pending.length === 0 ? (
            <p style={{ color: "var(--text-tertiary)", fontSize: 12 }}>
              暂无 pending token。要触发：
              <ol style={{ paddingLeft: 16, marginTop: 6 }}>
                <li>设置页打开「全局 Manual mode」</li>
                <li>在编辑器点任何生成按钮</li>
                <li>每个内部 LLM 调用会在这里出现</li>
              </ol>
            </p>
          ) : (
            <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
              {pending.map(p => (
                <li
                  key={p.paste_token}
                  className={p.paste_token === activeToken ? "card active" : "card"}
                  style={{
                    padding: 10, marginBottom: 8, cursor: "pointer",
                    background: p.paste_token === activeToken ? "var(--accent-subtle)" : undefined,
                  }}
                  onClick={() => openDetail(p.paste_token)}
                >
                  <div style={{ fontSize: 12, fontWeight: 600 }}>{p.call_site_id}</div>
                  <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 4 }}>
                    {p.project_id && <span>项目: {p.project_id} · </span>}
                    {p.chapter_id && <span>章节: {p.chapter_id} · </span>}
                    <span>{p.prompt_chars} 字 prompt</span>
                  </div>
                  <div style={{ fontSize: 10, color: "var(--text-disabled)", marginTop: 4, display: "flex", justifyContent: "space-between" }}>
                    <span>{formatAge(p.age_seconds)}</span>
                    <button
                      className="btn-sm danger"
                      style={{ fontSize: 10, padding: "1px 6px" }}
                      onClick={(e) => { e.stopPropagation(); cancel(p.paste_token); }}
                    >
                      取消
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* Detail / paste workspace */}
        <div className="card" style={{ padding: 16, minHeight: 400 }}>
          {!detail ? (
            <div style={{ color: "var(--text-tertiary)", textAlign: "center", padding: 40 }}>
              从左侧选一条 pending paste，开始工作
            </div>
          ) : (
            <>
              <h3 style={{ marginTop: 0, fontSize: 16 }}>{detail.call_site_id}</h3>
              <div style={{ fontSize: 12, color: "var(--text-tertiary)", marginBottom: 12 }}>
                token: <code>{detail.paste_token.slice(0, 12)}...</code>
                {detail.chapter_id && <> · 章节: <code>{detail.chapter_id}</code></>}
              </div>

              <details style={{ marginBottom: 12 }}>
                <summary style={{ cursor: "pointer", fontSize: 13, fontWeight: 600 }}>
                  Prompt 预览 ({detail.prompt.length + detail.system.length} 字)
                </summary>
                <div style={{ marginTop: 8 }}>
                  <button className="btn-sm" onClick={copyPrompt}>📋 复制完整 prompt (含 system)</button>
                  {detail.system && (
                    <details style={{ marginTop: 8 }}>
                      <summary style={{ fontSize: 12 }}>system prompt ({detail.system.length})</summary>
                      <pre style={{
                        background: "var(--bg-surface-2)", padding: 8, fontSize: 11,
                        maxHeight: 200, overflow: "auto", borderRadius: 4,
                      }}>{detail.system}</pre>
                    </details>
                  )}
                  <details style={{ marginTop: 8 }} open>
                    <summary style={{ fontSize: 12 }}>user prompt ({detail.prompt.length})</summary>
                    <pre style={{
                      background: "var(--bg-surface-2)", padding: 8, fontSize: 11,
                      maxHeight: 300, overflow: "auto", borderRadius: 4,
                    }}>{detail.prompt}</pre>
                  </details>
                </div>
              </details>

              <div style={{ marginTop: 16 }}>
                <label style={{ fontSize: 13, fontWeight: 600 }}>把网页 LLM 的回复粘到这里</label>
                <textarea
                  value={pasteText}
                  onChange={e => setPasteText(e.target.value)}
                  rows={14}
                  placeholder="贴回 LLM 输出..."
                  style={{
                    width: "100%", marginTop: 6, padding: 10, fontSize: 13,
                    border: "1px solid var(--border)", borderRadius: 4,
                    fontFamily: "var(--font-mono)", boxSizing: "border-box",
                  }}
                />
                <div style={{
                  display: "flex", justifyContent: "space-between", alignItems: "center",
                  marginTop: 8,
                }}>
                  <span style={{ fontSize: 12, color: "var(--text-tertiary)" }}>
                    {pasteText.length} 字
                    {pasteText.length > 0 && pasteText.trim().length < 20 && (
                      <span style={{ color: "var(--danger)", marginLeft: 8 }}>
                        ⚠ 看起来太短
                      </span>
                    )}
                  </span>
                  <div style={{ display: "flex", gap: 8 }}>
                    <button className="btn" onClick={() => { setActiveToken(null); setDetail(null); }}>
                      关闭
                    </button>
                    <button
                      className="btn primary"
                      onClick={submit}
                      disabled={submitting || pasteText.trim().length < 20}
                    >
                      {submitting ? "提交中..." : "提交"}
                    </button>
                  </div>
                </div>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
