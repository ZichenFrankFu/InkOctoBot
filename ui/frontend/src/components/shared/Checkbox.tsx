import React from "react";

/** Project-wide checkbox — square 16×16 with 4px corners, 1.5px border,
 *  fills with var(--accent) and shows a white ✓ when checked. The visual
 *  style matches the 代表作 selection in 市场特征提取 / 高级特征提取 so
 *  every checkable thing in the app reads the same way.
 *
 *  Use either the controlled API (checked / onChange) or pair it with
 *  whatever click handler you want via `onClick` on the wrapper.
 */
export interface CheckboxProps {
  checked: boolean;
  onChange?: (next: boolean) => void;
  label?: React.ReactNode;
  disabled?: boolean;
  size?: number;
  /** Spacing between box and label. */
  gap?: number;
  /** Inline styles applied to the outer label wrapper. */
  style?: React.CSSProperties;
  /** Inline styles applied to the visible box. */
  boxStyle?: React.CSSProperties;
  /** Tooltip. */
  title?: string;
  /** Click handler in addition to onChange — useful when the label is
   *  itself interactive (e.g. opens a dialog). */
  onClick?: (e: React.MouseEvent) => void;
}

export default function Checkbox({
  checked, onChange, label, disabled, size = 16, gap = 8,
  style, boxStyle, title, onClick,
}: CheckboxProps) {
  const handle = (e: React.MouseEvent | React.KeyboardEvent) => {
    if (disabled) return;
    onClick?.(e as React.MouseEvent);
    onChange?.(!checked);
  };
  return (
    <label
      title={title}
      onClick={(e) => { e.preventDefault(); handle(e); }}
      onKeyDown={(e) => {
        if ((e.key === " " || e.key === "Enter") && !disabled) {
          e.preventDefault();
          handle(e);
        }
      }}
      tabIndex={disabled ? -1 : 0}
      style={{
        display: "inline-flex", alignItems: "center", gap,
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
        userSelect: "none",
        ...style,
      }}
    >
      <span
        aria-hidden
        style={{
          width: size, height: size,
          borderRadius: 4, flexShrink: 0,
          border: `1.5px solid ${checked ? "var(--accent)" : "var(--border)"}`,
          background: checked ? "var(--accent)" : "transparent",
          color: "#fff",
          fontSize: Math.max(10, size - 5),
          lineHeight: `${size - 2}px`,
          textAlign: "center",
          transition: "background 0.12s, border-color 0.12s",
          ...boxStyle,
        }}
      >
        {checked ? "✓" : ""}
      </span>
      {/* Hidden real checkbox for forms / a11y — kept off-screen so the
          custom box is the only visible affordance. */}
      <input
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={() => {/* handled by wrapper */}}
        tabIndex={-1}
        style={{
          position: "absolute", opacity: 0, pointerEvents: "none",
          width: 0, height: 0,
        }}
      />
      {label != null && (
        <span style={{ fontSize: 12, color: "var(--text-primary)" }}>{label}</span>
      )}
    </label>
  );
}
