import React from "react";

interface EmptyStateProps {
  icon?: string;
  title: string;
  description?: string;
  actionLabel?: string;
  onAction?: () => void;
}

export default function EmptyState({ icon = "\u25A1", title, description, actionLabel, onAction }: EmptyStateProps) {
  return (
    <div style={containerStyle}>
      <div style={iconStyle} aria-hidden="true">{icon}</div>
      <h3 style={titleStyle}>{title}</h3>
      {description && <p style={descStyle}>{description}</p>}
      {actionLabel && onAction && (
        <button onClick={onAction} style={btnStyle} aria-label={actionLabel}>
          {actionLabel}
        </button>
      )}
    </div>
  );
}

const containerStyle: React.CSSProperties = {
  display: "flex", flexDirection: "column", alignItems: "center",
  justifyContent: "center", padding: "48px 24px", textAlign: "center",
  minHeight: 200,
};

const iconStyle: React.CSSProperties = {
  fontSize: 36, opacity: 0.3, marginBottom: 16,
  color: "var(--text-tertiary)",
};

const titleStyle: React.CSSProperties = {
  color: "var(--text-secondary)", fontSize: 16, fontWeight: 500,
  margin: "0 0 8px", fontFamily: "var(--font-sans)",
};

const descStyle: React.CSSProperties = {
  color: "var(--text-tertiary)", fontSize: 13, lineHeight: 1.5,
  margin: "0 0 20px", maxWidth: 320, fontFamily: "var(--font-sans)",
};

const btnStyle: React.CSSProperties = {
  padding: "8px 20px", borderRadius: 8, fontSize: 13, fontWeight: 500,
  cursor: "pointer", border: "1px solid var(--accent)",
  background: "var(--accent-subtle)", color: "var(--accent)",
  fontFamily: "var(--font-sans)", transition: "background 0.15s",
};
