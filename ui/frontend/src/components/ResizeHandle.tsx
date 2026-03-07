import React, { useState } from "react";

interface Props {
  direction: "horizontal" | "vertical";
  onMouseDown: (e: React.MouseEvent) => void;
  onTouchStart?: (e: React.TouchEvent) => void;
  isDragging?: boolean;
}

export default function ResizeHandle({ direction, onMouseDown, onTouchStart, isDragging }: Props) {
  const [hovered, setHovered] = useState(false);
  const isH = direction === "horizontal";
  const active = isDragging || hovered;

  return (
    <div
      onMouseDown={onMouseDown}
      onTouchStart={onTouchStart}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      style={{
        position: "relative",
        zIndex: 50,
        flexShrink: 0,
        width: isH ? 6 : "100%",
        height: isH ? "100%" : 6,
        cursor: isH ? "col-resize" : "row-resize",
        background: "transparent",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      {/* Visible bar */}
      <div
        style={{
          width: isH ? 2 : "40%",
          height: isH ? "40%" : 2,
          borderRadius: 1,
          background: active ? "var(--accent)" : "var(--border)",
          boxShadow: active ? "0 0 6px var(--accent)" : "none",
          transition: isDragging ? "none" : "background 0.2s, box-shadow 0.2s",
          opacity: active ? 1 : 0.5,
        }}
      />
      {/* Wider invisible hit area */}
      <div
        style={{
          position: "absolute",
          top: isH ? 0 : -4,
          left: isH ? -4 : 0,
          width: isH ? 14 : "100%",
          height: isH ? "100%" : 14,
        }}
      />
    </div>
  );
}
