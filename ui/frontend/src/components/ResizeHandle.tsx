import React from "react";

interface ResizeHandleProps {
  direction: "horizontal" | "vertical";
  isDragging: boolean;
  onMouseDown: (e: React.MouseEvent) => void;
  onTouchStart: (e: React.TouchEvent) => void;
}

export default function ResizeHandle({ direction, isDragging, onMouseDown, onTouchStart }: ResizeHandleProps) {
  const isH = direction === "horizontal";

  return (
    <div
      onMouseDown={onMouseDown}
      onTouchStart={onTouchStart}
      style={{
        position: "relative",
        zIndex: 50,
        flexShrink: 0,
        width: isH ? 5 : "100%",
        height: isH ? "100%" : 5,
        cursor: isH ? "col-resize" : "row-resize",
        background: "transparent",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      {/* Visual indicator line */}
      <div
        style={{
          width: isH ? 1 : "40%",
          height: isH ? "40%" : 1,
          borderRadius: 1,
          background: isDragging ? "var(--accent)" : "var(--border)",
          transition: isDragging ? "none" : "background 0.2s",
          opacity: isDragging ? 1 : 0.6,
        }}
      />
      {/* Wider invisible hit area */}
      <div
        style={{
          position: "absolute",
          top: isH ? 0 : -4,
          left: isH ? -4 : 0,
          width: isH ? 13 : "100%",
          height: isH ? "100%" : 13,
        }}
      />
    </div>
  );
}