import React, { useEffect, useState } from "react";
import { useToast } from "./Toast";

interface Props {
  fetchPrompt: () => Promise<string>;
  onApplyResult?: (text: string) => void;
  /** Start expanded (and load the prompt immediately). Default: collapsed. */
  defaultOpen?: boolean;
  applyLabel?: string;
  resultPlaceholder?: string;
  title?: string;
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
}: Props) {
  const { toast } = useToast();
  const [open, setOpen] = useState(!!defaultOpen);
  const [prompt, setPrompt] = useState("");
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [pasteText, setPasteText] = useState("");

  const load = async () => {
    setLoading(true);
    try {
      const p = await fetchPrompt();
      setPrompt(p || "");
      setLoaded(true);
    } catch (e: any) {
      toast(e?.message || "获取 prompt 失败", "error");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (defaultOpen) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggle = () => {
    const next = !open;
    setOpen(next);
    if (next && !loaded && !loading) load();
  };

  return (
    <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-sm)", overflow: "hidden" }}>
      <button
        onClick={toggle}
        style={{
          width: "100%", display: "flex", alignItems: "center", gap: 6, padding: "6px 10px",
          background: open ? "var(--bg-surface-2, var(--bg-surface))" : "transparent",
          border: "none", borderBottom: open ? "1px solid var(--border)" : "none",
          cursor: "pointer", fontSize: 11, fontWeight: 600, color: "var(--text-secondary)", textAlign: "left",
        }}
      >
        <span style={{ fontSize: 9 }}>{open ? "▾" : "▸"}</span>
        <span style={{ flex: 1 }}>{title || "AI prompt · 复制到网页 LLM / 解析返回结果"}</span>
      </button>
      {open && (
        <div style={{ padding: 10, background: "var(--bg-surface)" }}>
          <div className="flex items-center justify-between" style={{ marginBottom: 6 }}>
            <span className="text-xs" style={{ fontWeight: 600, color: "var(--text-secondary)" }}>渲染后的 prompt</span>
            <div className="flex gap-6">
              <button className="btn" style={{ fontSize: 10, padding: "2px 8px" }} onClick={load} disabled={loading}>
                {loading ? "加载中..." : loaded ? "刷新" : "加载"}
              </button>
              <button
                className="btn"
                style={{ fontSize: 10, padding: "2px 8px" }}
                disabled={!prompt}
                onClick={async () => { await copyText(prompt); toast("已复制 prompt，可粘贴到网页 LLM", "success"); }}
              >
                复制
              </button>
            </div>
          </div>
          <pre
            className="font-mono"
            style={{
              margin: 0, padding: 8, fontSize: 11, lineHeight: 1.55,
              background: "var(--bg-app)", borderRadius: 4, color: "var(--text-secondary)",
              maxHeight: 260, overflow: "auto", whiteSpace: "pre-wrap", wordBreak: "break-word",
            }}
          >
            {loading && !loaded ? "加载中..." : (loaded ? (prompt || "（空）") : "点击「加载」生成 prompt")}
          </pre>
          {onApplyResult && (
            <div style={{ marginTop: 8 }}>
              <div className="text-xs" style={{ fontWeight: 600, color: "var(--text-secondary)", marginBottom: 4 }}>
                解析网页 LLM 返回结果
              </div>
              <textarea
                className="input"
                value={pasteText}
                onChange={e => setPasteText(e.target.value)}
                placeholder={resultPlaceholder || "把网页 LLM 返回的内容粘贴到这里"}
                rows={4}
                style={{ width: "100%", fontSize: 11, padding: "4px 8px", resize: "vertical", lineHeight: 1.5, boxSizing: "border-box" }}
              />
              <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 4 }}>
                <button
                  className="btn-primary"
                  style={{ fontSize: 10, padding: "3px 12px" }}
                  disabled={!pasteText.trim()}
                  onClick={() => { onApplyResult(pasteText); setPasteText(""); }}
                >
                  {applyLabel || "应用结果"}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
