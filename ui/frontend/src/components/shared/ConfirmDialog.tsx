import React, { useEffect, useRef } from "react";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function ConfirmDialog({
  open, title, message, confirmLabel = "确认", cancelLabel = "取消",
  destructive = false, onConfirm, onCancel,
}: ConfirmDialogProps) {
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
      if (e.key === "Enter") onConfirm();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onCancel, onConfirm]);

  if (!open) return null;

  return (
    <div style={overlayStyle} onClick={onCancel} role="dialog" aria-modal="true" aria-label={title}>
      <div style={cardStyle} onClick={e => e.stopPropagation()} ref={dialogRef}>
        <h3 style={titleStyle}>{title}</h3>
        <p style={messageStyle}>{message}</p>
        <div style={buttonRowStyle}>
          <button onClick={onCancel} style={cancelBtnStyle} aria-label={cancelLabel}>
            {cancelLabel}
          </button>
          <button
            onClick={onConfirm}
            style={destructive ? destructiveBtnStyle : confirmBtnStyle}
            aria-label={confirmLabel}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}

const overlayStyle: React.CSSProperties = {
  position: "fixed", inset: 0, zIndex: 9999,
  background: "rgba(0,0,0,0.5)", backdropFilter: "blur(4px)",
  display: "flex", alignItems: "center", justifyContent: "center",
};

const cardStyle: React.CSSProperties = {
  background: "var(--bg-surface)", border: "1px solid var(--border)",
  borderRadius: 12, padding: "28px 32px", maxWidth: 420, width: "90%",
  boxShadow: "0 8px 32px rgba(0,0,0,0.4)",
};

const titleStyle: React.CSSProperties = {
  color: "var(--text-primary)", fontSize: 18, fontWeight: 600,
  margin: "0 0 8px", fontFamily: "var(--font-sans)",
};

const messageStyle: React.CSSProperties = {
  color: "var(--text-secondary)", fontSize: 14, lineHeight: 1.6,
  margin: "0 0 24px", fontFamily: "var(--font-sans)",
};

const buttonRowStyle: React.CSSProperties = {
  display: "flex", gap: 12, justifyContent: "flex-end",
};

const baseBtnStyle: React.CSSProperties = {
  padding: "8px 20px", borderRadius: 8, fontSize: 14, fontWeight: 500,
  cursor: "pointer", border: "none", fontFamily: "var(--font-sans)",
  transition: "background 0.15s",
};

const cancelBtnStyle: React.CSSProperties = {
  ...baseBtnStyle, background: "var(--bg-surface-2)", color: "var(--text-primary)",
  border: "1px solid var(--border)",
};

const confirmBtnStyle: React.CSSProperties = {
  ...baseBtnStyle, background: "var(--accent)", color: "#fff",
};

const destructiveBtnStyle: React.CSSProperties = {
  ...baseBtnStyle, background: "var(--error)", color: "#fff",
};
