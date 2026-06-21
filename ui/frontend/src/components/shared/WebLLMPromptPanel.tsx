import React, { useEffect, useRef, useState } from "react";
import { useToast } from "./Toast";

interface Props {
  fetchPrompt: () => Promise<string>;
  onApplyResult?: (text: string) => void;
  /** Start expanded (and load the prompt immediately). Default: collapsed. */
  defaultOpen?: boolean;
  applyLabel?: string;
  resultPlaceholder?: string;
  title?: string;
  /** When this key changes (e.g. the live chat input), the panel debounces a
   *  refresh so the displayed prompt always tracks what the user just typed.
   *  Pass a stable string — typically `chatInput`. */
  watchKey?: string;
  /** Debounce window for `watchKey`-triggered refreshes (ms). Default 400. */
  debounceMs?: number;
}

async function copyText(text: string) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } finally { document.body.removeChild(ta); }
  }
}

export default function WebLLMPromptPanel({
  fetchPrompt, onApplyResult, defaultOpen, applyLabel, resultPlaceholder, title,
  watchKey, debounceMs = 400,
}: Props) {
  const { toast } = useToast();
  const [open, setOpen] = useState(!!defaultOpen);
  const [prompt, setPrompt] = useState("");
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [pasteText, setPasteText] = useState("");
  // Hold the latest fetchPrompt in a ref so the debounce timer always calls
  // the current closure (which has the up-to-date chatInput / market info /
  // etc. captured), not the one frozen when the timer was scheduled.
  const fetchRef = useRef(fetchPrompt);
  useEffect(() => { fetchRef.current = fetchPrompt; }, [fetchPrompt]);

  const load = async (showError = true) => {
    setLoading(true);
    try {
      const p = await fetchRef.current();
      setPrompt(p || "");
      setLoaded(true);
    } catch (e: any) {
      if (showError) toast(e?.message || "获取 prompt 失败", "error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (defaultOpen) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Live refresh: whenever `watchKey` changes (typically the chat input) and
  // the panel is open, debounce a fetch so the displayed prompt is the one
  // the user is actually about to send. Errors are swallowed because every
  // keystroke shouldn't pop a toast.
  useEffect(() => {
    if (!open || watchKey === undefined) return;
    const t = setTimeout(() => { load(false); }, debounceMs);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [watchKey, open, debounceMs]);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && !loaded && !loading) load();
  };

  return (
    <div style={{
      border: "1px solid var(--border)",
      borderRadius: 10,
      overflow: "hidden",
      background: open ? "var(--bg-surface)" : "transparent",
      transition: "background 0.15s",
    }}>
      <button
        onClick={toggle}
        style={{
          width: "100%", display: "flex", alignItems: "center", gap: 8,
          padding: "8px 12px",
          background: open ? "var(--bg-surface-2)" : "transparent",
          border: "none",
          borderBottom: open ? "1px solid var(--border)" : "none",
          cursor: "pointer", fontSize: 11.5, fontWeight: 600,
          color: "var(--text-secondary)", textAlign: "left",
          transition: "background 0.15s",
        }}
      >
        <span style={{
          width: 18, height: 18, borderRadius: 4,
          background: open ? "var(--accent)" : "var(--bg-secondary)",
          color: open ? "#fff" : "var(--text-tertiary)",
          display: "inline-flex", alignItems: "center", justifyContent: "center",
          fontSize: 11, fontWeight: 700, flexShrink: 0,
        }}>
          {open ? "▾" : "▸"}
        </span>
        <span style={{ flex: 1 }}>
          {title || "AI prompt · 复制到网页 LLM / 解析返回结果"}
        </span>
      </button>
      {open && (
        <div style={{ padding: 12, background: "var(--bg-surface)" }}>
          {/* ━━━ 1. 渲染后的 prompt — 显示 + 复制 ━━━ */}
          <div style={{
            display: "flex", alignItems: "center", justifyContent: "space-between",
            marginBottom: 8,
          }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
              <span style={{
                width: 20, height: 20, borderRadius: "50%",
                background: "var(--accent-subtle)", color: "var(--accent)",
                fontSize: 10, fontWeight: 700,
                display: "inline-flex", alignItems: "center", justifyContent: "center",
              }}>1</span>
              <span style={{
                fontSize: 11.5, fontWeight: 600, color: "var(--text-primary)",
              }}>渲染后的 prompt</span>
              {loaded && prompt && (
                <span className="text-xs" style={{ color: "var(--text-tertiary)" }}>
                  · {prompt.length} 字
                </span>
              )}
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <button
                className="btn"
                style={{ fontSize: 10.5, padding: "3px 10px" }}
                onClick={() => load()} disabled={loading}
                title="重新拉取最新 prompt"
              >
                {loading ? "加载中..." : loaded ? "刷新" : "加载"}
              </button>
              <button
                className="btn-primary"
                style={{ fontSize: 10.5, padding: "3px 12px" }}
                disabled={!prompt}
                onClick={async () => {
                  await copyText(prompt);
                  toast("已复制 prompt", "success");
                }}
                title="复制完整 prompt 到剪贴板"
              >
                复制
              </button>
            </div>
          </div>
          <pre
            className="font-mono"
            style={{
              margin: 0, padding: 10, fontSize: 11, lineHeight: 1.6,
              background: "var(--bg-app)", borderRadius: 6,
              border: "1px solid var(--border-subtle)",
              color: "var(--text-secondary)",
              maxHeight: 240, overflow: "auto",
              whiteSpace: "pre-wrap", wordBreak: "break-word",
            }}
          >
            {loading && !loaded ? "加载中..." : (loaded ? (prompt || "（空）") : "点击「加载」生成 prompt")}
          </pre>
          {/* ━━━ 2. 粘贴回 — 解析网页 LLM 返回的结果 ━━━ */}
          {onApplyResult && (
            <div style={{ marginTop: 12 }}>
              <div style={{
                display: "flex", alignItems: "center", justifyContent: "space-between",
                marginBottom: 6,
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{
                    width: 20, height: 20, borderRadius: "50%",
                    background: "var(--jade-subtle)", color: "var(--jade)",
                    fontSize: 10, fontWeight: 700,
                    display: "inline-flex", alignItems: "center", justifyContent: "center",
                  }}>2</span>
                  <span style={{
                    fontSize: 11.5, fontWeight: 600, color: "var(--text-primary)",
                  }}>解析网页 LLM 返回结果</span>
                </div>
                <button
                  className="btn-primary"
                  style={{
                    fontSize: 10.5, padding: "3px 14px",
                    background: pasteText.trim() ? "var(--jade, #34a853)" : undefined,
                    border: pasteText.trim() ? "none" : undefined,
                  }}
                  disabled={!pasteText.trim()}
                  onClick={() => { onApplyResult(pasteText); setPasteText(""); }}
                >
                  {applyLabel || "应用结果"}
                </button>
              </div>
              <textarea
                className="input"
                value={pasteText}
                onChange={e => setPasteText(e.target.value)}
                placeholder={resultPlaceholder || "把网页 LLM 返回的内容粘贴到这里"}
                rows={5}
                style={{
                  width: "100%", fontSize: 11, padding: 10, lineHeight: 1.55,
                  resize: "vertical", boxSizing: "border-box",
                  border: "1px solid var(--border-subtle)", borderRadius: 6,
                  background: "var(--bg-app)",
                }}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}
