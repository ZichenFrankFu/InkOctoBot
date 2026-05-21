/**
 * App-wide centered dialog system — replaces the browser-native
 * confirm()/prompt() popups (which render as a misaligned
 * "127.0.0.1 says..." bar at the top of the window). useDialog()
 * exposes Promise-based confirm and prompt helpers rendered as a
 * centered modal consistent with the rest of the app.
 */
import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";

interface ConfirmOptions {
  title?: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
}

interface PromptOptions {
  title?: string;
  message?: string;
  defaultValue?: string;
  placeholder?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  multiline?: boolean;
}

interface DialogContextValue {
  confirm: (opts: ConfirmOptions | string) => Promise<boolean>;
  prompt: (opts: PromptOptions | string) => Promise<string | null>;
}

const DialogContext = createContext<DialogContextValue | null>(null);

export function useDialog(): DialogContextValue {
  const ctx = useContext(DialogContext);
  if (!ctx) throw new Error("useDialog must be used within DialogProvider");
  return ctx;
}

type Pending =
  | {
      kind: "confirm";
      title: string;
      message: string;
      confirmLabel: string;
      cancelLabel: string;
      destructive: boolean;
      resolve: (v: boolean) => void;
    }
  | {
      kind: "prompt";
      title: string;
      message: string;
      placeholder: string;
      confirmLabel: string;
      cancelLabel: string;
      multiline: boolean;
      resolve: (v: string | null) => void;
    };

export function DialogProvider({ children }: { children: React.ReactNode }) {
  const [pending, setPending] = useState<Pending | null>(null);
  const [value, setValue] = useState("");
  const inputRef = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);

  const confirm = useCallback((opts: ConfirmOptions | string): Promise<boolean> => {
    const o = typeof opts === "string" ? { message: opts } : opts;
    return new Promise<boolean>(resolve => {
      setPending({
        kind: "confirm",
        title: o.title || "确认操作",
        message: o.message,
        confirmLabel: o.confirmLabel || "确认",
        cancelLabel: o.cancelLabel || "取消",
        destructive: !!o.destructive,
        resolve,
      });
    });
  }, []);

  const prompt = useCallback((opts: PromptOptions | string): Promise<string | null> => {
    const o = typeof opts === "string" ? { message: opts } : opts;
    setValue(o.defaultValue || "");
    return new Promise<string | null>(resolve => {
      setPending({
        kind: "prompt",
        title: o.title || "请输入",
        message: o.message || "",
        placeholder: o.placeholder || "",
        confirmLabel: o.confirmLabel || "确认",
        cancelLabel: o.cancelLabel || "取消",
        multiline: !!o.multiline,
        resolve,
      });
    });
  }, []);

  useEffect(() => {
    if (pending?.kind === "prompt") {
      const t = setTimeout(() => {
        inputRef.current?.focus();
        inputRef.current?.select();
      }, 30);
      return () => clearTimeout(t);
    }
  }, [pending]);

  const finish = (result: boolean | string | null) => {
    if (pending) {
      if (pending.kind === "confirm") pending.resolve(result as boolean);
      else pending.resolve(result as string | null);
    }
    setPending(null);
  };

  useEffect(() => {
    if (!pending) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        finish(pending.kind === "confirm" ? false : null);
      } else if (e.key === "Enter") {
        if (pending.kind === "confirm") {
          e.preventDefault();
          finish(true);
        } else if (!pending.multiline) {
          e.preventDefault();
          finish(value);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pending, value]);

  const cancelResult = pending?.kind === "prompt" ? null : false;

  return (
    <DialogContext.Provider value={{ confirm, prompt }}>
      {children}
      {pending && (
        <div
          style={overlayStyle}
          onMouseDown={e => { if (e.target === e.currentTarget) finish(cancelResult); }}
          role="dialog"
          aria-modal="true"
          aria-label={pending.title}
        >
          <div style={cardStyle} onMouseDown={e => e.stopPropagation()}>
            <div style={titleStyle}>{pending.title}</div>
            {pending.message && <div style={messageStyle}>{pending.message}</div>}
            {pending.kind === "prompt" && (
              pending.multiline ? (
                <textarea
                  ref={el => { inputRef.current = el; }}
                  className="input"
                  value={value}
                  onChange={e => setValue(e.target.value)}
                  placeholder={pending.placeholder}
                  rows={3}
                  style={{ width: "100%", boxSizing: "border-box", fontSize: 13, marginBottom: 4, resize: "vertical" }}
                />
              ) : (
                <input
                  ref={el => { inputRef.current = el; }}
                  className="input"
                  value={value}
                  onChange={e => setValue(e.target.value)}
                  placeholder={pending.placeholder}
                  style={{ width: "100%", boxSizing: "border-box", fontSize: 13, marginBottom: 4 }}
                />
              )
            )}
            <div style={buttonRowStyle}>
              <button className="btn" onClick={() => finish(cancelResult)}>{pending.cancelLabel}</button>
              <button
                className={pending.kind === "confirm" && pending.destructive ? "btn" : "btn-primary"}
                style={pending.kind === "confirm" && pending.destructive
                  ? { color: "var(--error)", borderColor: "var(--error)" }
                  : undefined}
                onClick={() => finish(pending.kind === "confirm" ? true : value)}
                autoFocus={pending.kind === "confirm"}
              >
                {pending.confirmLabel}
              </button>
            </div>
          </div>
        </div>
      )}
    </DialogContext.Provider>
  );
}

const overlayStyle: React.CSSProperties = {
  position: "fixed", inset: 0, zIndex: 10001,
  background: "rgba(0,0,0,0.55)",
  display: "flex", alignItems: "center", justifyContent: "center",
  padding: 20,
};

const cardStyle: React.CSSProperties = {
  width: "min(440px, 100%)",
  background: "var(--bg-surface)",
  border: "1px solid var(--border)", borderRadius: 10,
  padding: "20px 22px",
  boxShadow: "0 10px 40px rgba(0,0,0,0.45)",
};

const titleStyle: React.CSSProperties = {
  fontSize: 15, fontWeight: 600, color: "var(--text-primary)",
  marginBottom: 8, fontFamily: "var(--font-sans)",
};

const messageStyle: React.CSSProperties = {
  fontSize: 13, lineHeight: 1.65, color: "var(--text-secondary)",
  marginBottom: 14, whiteSpace: "pre-wrap", fontFamily: "var(--font-sans)",
};

const buttonRowStyle: React.CSSProperties = {
  display: "flex", justifyContent: "flex-end", gap: 8, marginTop: 14,
};
