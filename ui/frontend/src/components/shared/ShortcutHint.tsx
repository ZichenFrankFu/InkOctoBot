import React, { useState, useEffect } from "react";

const isMac = typeof navigator !== "undefined" && /Mac|iPod|iPhone|iPad/.test(navigator.platform);
const MOD = isMac ? "\u2318" : "Ctrl";

const SHORTCUTS = [
  { keys: `${MOD} + K`, description: "打开搜索" },
  { keys: `${MOD} + S`, description: "保存" },
  { keys: "Esc", description: "关闭面板" },
  { keys: "?", description: "显示/隐藏快捷键" },
];

export default function ShortcutHint() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Only toggle on "?" when not typing in an input
      if (
        e.key === "?" &&
        !e.metaKey &&
        !e.ctrlKey &&
        !(e.target instanceof HTMLInputElement) &&
        !(e.target instanceof HTMLTextAreaElement) &&
        !(e.target as HTMLElement)?.isContentEditable
      ) {
        setVisible(v => !v);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, []);

  if (!visible) return null;

  return (
    <div style={overlayStyle} onClick={() => setVisible(false)}>
      <div style={panelStyle} onClick={e => e.stopPropagation()}>
        <div style={headerStyle}>快捷键</div>
        <div style={listStyle}>
          {SHORTCUTS.map(s => (
            <div key={s.keys} style={rowStyle}>
              <kbd style={kbdStyle}>{s.keys}</kbd>
              <span style={descStyle}>{s.description}</span>
            </div>
          ))}
        </div>
        <div style={footerStyle}>
          按 <kbd style={kbdInlineStyle}>?</kbd> 或点击外部关闭
        </div>
      </div>
    </div>
  );
}

const overlayStyle: React.CSSProperties = {
  position: "fixed",
  inset: 0,
  zIndex: 9999,
  background: "rgba(0,0,0,0.4)",
  display: "flex",
  alignItems: "center",
  justifyContent: "center",
  animation: "toast-slide-in 0.2s ease",
};

const panelStyle: React.CSSProperties = {
  background: "var(--bg-surface)",
  border: "1px solid var(--border)",
  borderRadius: 12,
  padding: "24px 28px",
  minWidth: 300,
  boxShadow: "0 8px 40px rgba(0,0,0,0.5)",
};

const headerStyle: React.CSSProperties = {
  color: "var(--text-primary)",
  fontSize: 16,
  fontWeight: 600,
  marginBottom: 16,
  fontFamily: "var(--font-sans)",
};

const listStyle: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: 10,
};

const rowStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  justifyContent: "space-between",
  gap: 24,
};

const kbdStyle: React.CSSProperties = {
  background: "var(--bg-surface-2)",
  border: "1px solid var(--border)",
  borderRadius: 6,
  padding: "4px 10px",
  fontSize: 13,
  fontFamily: "var(--font-mono)",
  color: "var(--text-primary)",
  whiteSpace: "nowrap",
};

const descStyle: React.CSSProperties = {
  color: "var(--text-secondary)",
  fontSize: 14,
  fontFamily: "var(--font-sans)",
};

const footerStyle: React.CSSProperties = {
  marginTop: 18,
  paddingTop: 12,
  borderTop: "1px solid var(--border)",
  color: "var(--text-tertiary)",
  fontSize: 12,
  textAlign: "center",
  fontFamily: "var(--font-sans)",
};

const kbdInlineStyle: React.CSSProperties = {
  background: "var(--bg-surface-2)",
  border: "1px solid var(--border)",
  borderRadius: 4,
  padding: "1px 6px",
  fontSize: 12,
  fontFamily: "var(--font-mono)",
  color: "var(--text-secondary)",
};
