/**
 * Centered confirm dialog — replaces the browser-default ``confirm()``
 * which sticks to the top of the viewport and looks misaligned with
 * the rest of the app. ``useConfirm`` returns an async function the
 * caller awaits to get true/false.
 */
import React, { useCallback, useRef, useState } from "react";

interface PendingConfirm {
  title: string;
  message: string;
  confirmLabel: string;
  cancelLabel: string;
  destructive: boolean;
  resolve: (v: boolean) => void;
}

interface ConfirmOptions {
  title?: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
}

export function useConfirm(): {
  confirm: (opts: ConfirmOptions | string) => Promise<boolean>;
  ConfirmHost: React.FC;
} {
  const [pending, setPending] = useState<PendingConfirm | null>(null);
  const pendingRef = useRef<PendingConfirm | null>(null);
  pendingRef.current = pending;

  const confirm = useCallback((opts: ConfirmOptions | string): Promise<boolean> => {
    const norm: ConfirmOptions = typeof opts === "string" ? { message: opts } : opts;
    return new Promise<boolean>(resolve => {
      setPending({
        title: norm.title || "确认操作",
        message: norm.message,
        confirmLabel: norm.confirmLabel || "确认",
        cancelLabel: norm.cancelLabel || "取消",
        destructive: !!norm.destructive,
        resolve,
      });
    });
  }, []);

  const ConfirmHost: React.FC = () => {
    if (!pending) return null;
    const close = (v: boolean) => {
      pending.resolve(v);
      setPending(null);
    };
    return (
      <div style={{
        position: "fixed", inset: 0, zIndex: 2000,
        background: "rgba(0,0,0,0.55)",
        display: "flex", alignItems: "center", justifyContent: "center",
        padding: 20,
      }}>
        <div style={{
          width: "min(440px, 100%)",
          background: "var(--bg-app)",
          border: "1px solid var(--border)", borderRadius: 6,
          boxShadow: "0 10px 40px rgba(0,0,0,0.4)",
        }}>
          <div style={{
            padding: "12px 16px", borderBottom: "1px solid var(--border)",
            fontSize: 13, fontWeight: 600, color: "var(--text-primary)",
          }}>{pending.title}</div>
          <div style={{
            padding: 16, fontSize: 12, lineHeight: 1.65,
            color: "var(--text-secondary)", whiteSpace: "pre-wrap",
          }}>{pending.message}</div>
          <div style={{
            padding: "10px 16px", borderTop: "1px solid var(--border)",
            display: "flex", justifyContent: "flex-end", gap: 8,
          }}>
            <button className="btn" onClick={() => close(false)}>{pending.cancelLabel}</button>
            <button className={pending.destructive ? "btn" : "btn-primary"}
                    style={pending.destructive
                      ? { color: "var(--error)", borderColor: "var(--error)" }
                      : undefined}
                    onClick={() => close(true)} autoFocus>
              {pending.confirmLabel}
            </button>
          </div>
        </div>
      </div>
    );
  };

  return { confirm, ConfirmHost };
}
