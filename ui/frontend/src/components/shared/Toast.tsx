import React, { createContext, useContext, useState, useCallback, useRef } from "react";

type ToastType = "success" | "error" | "info";

interface ToastItem {
  id: number;
  message: string;
  type: ToastType;
}

interface ToastContextValue {
  toast: (message: string, type?: ToastType) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast must be used within ToastProvider");
  return ctx;
}

const TOAST_STYLES: React.CSSProperties = {
  position: "fixed",
  bottom: 24,
  right: 24,
  zIndex: 10000,
  display: "flex",
  flexDirection: "column",
  gap: 8,
  pointerEvents: "none",
};

const typeColorMap: Record<ToastType, { bg: string; border: string; color: string }> = {
  success: { bg: "var(--jade-subtle)", border: "var(--jade)", color: "var(--jade)" },
  error: { bg: "var(--accent-subtle)", border: "var(--error)", color: "var(--error)" },
  info: { bg: "var(--indigo-subtle)", border: "var(--indigo)", color: "var(--indigo)" },
};

function ToastMessage({ item, onRemove }: { item: ToastItem; onRemove: (id: number) => void }) {
  const [exiting, setExiting] = React.useState(false);

  React.useEffect(() => {
    const dismissTimer = setTimeout(() => {
      setExiting(true);
    }, 2700);
    const removeTimer = setTimeout(() => {
      onRemove(item.id);
    }, 3000);
    return () => {
      clearTimeout(dismissTimer);
      clearTimeout(removeTimer);
    };
  }, [item.id, onRemove]);

  const colors = typeColorMap[item.type];

  const style: React.CSSProperties = {
    pointerEvents: "auto",
    padding: "12px 20px",
    borderRadius: 8,
    background: "var(--bg-surface-2)",
    borderLeft: `3px solid ${colors.border}`,
    color: "var(--text-primary)",
    fontSize: 14,
    fontFamily: "var(--font-sans)",
    boxShadow: "0 4px 24px rgba(0,0,0,0.4)",
    animation: exiting ? "toast-slide-out 0.3s ease forwards" : "toast-slide-in 0.3s ease forwards",
    display: "flex",
    alignItems: "center",
    gap: 10,
    maxWidth: 380,
    minWidth: 240,
  };

  const dotStyle: React.CSSProperties = {
    width: 8,
    height: 8,
    borderRadius: "50%",
    background: colors.color,
    flexShrink: 0,
  };

  return (
    <div style={style}>
      <span style={dotStyle} />
      <span>{item.message}</span>
    </div>
  );
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const idRef = useRef(0);

  const removeToast = useCallback((id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  const toast = useCallback((message: string, type: ToastType = "info") => {
    const id = ++idRef.current;
    setToasts(prev => [...prev, { id, message, type }]);
  }, []);

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <div style={TOAST_STYLES}>
        {toasts.map(item => (
          <ToastMessage key={item.id} item={item} onRemove={removeToast} />
        ))}
      </div>
      <style>{`
        @keyframes toast-slide-in {
          from { opacity: 0; transform: translateX(80px); }
          to   { opacity: 1; transform: translateX(0); }
        }
        @keyframes toast-slide-out {
          from { opacity: 1; transform: translateX(0); }
          to   { opacity: 0; transform: translateX(80px); }
        }
      `}</style>
    </ToastContext.Provider>
  );
}
