/**
 * ChapterTimeline — dual-handle (range) and single-handle chapter slider.
 *
 * Shared by the 角色全局图谱 (CharacterManagerPage) and the 故事中世界
 * tabs (StorylandPage) so all chapter-driven views look and behave the
 * same way:
 *   - draggable handles + draggable selected bar
 *   - tick marks for chapters that actually have data
 *   - 「全部 / 最新」shortcuts
 * The component is purely presentational — callers own the chapter list
 * and decide how to filter their data against the chosen window.
 */
import React, { useRef } from "react";


export interface ChapterTimelineProps {
  min: number;
  max: number;
  /** "range" — dual handles for a [from, to] window;
   *  "single" — one handle for a single chapter focus. */
  mode?: "range" | "single";
  /** Range mode: from / to are both required. Single mode: only `from` is
   *  read (and updated via onChange). */
  from: number;
  to: number;
  /** Chapter numbers that have data — rendered as tick marks. */
  marks?: number[];
  onChange: (from: number, to: number) => void;
  /** Optional inline label rendered next to the slider header. */
  label?: React.ReactNode;
  /** Whether to render the 「全部 / 最新」 quick buttons. Default true. */
  showShortcuts?: boolean;
}


export default function ChapterTimeline({
  min, max, mode = "range", from, to, marks = [], onChange, label,
  showShortcuts = true,
}: ChapterTimelineProps) {
  const barRef = useRef<HTMLDivElement>(null);
  const dragging = useRef<"from" | "to" | "bar" | null>(null);
  const startPx = useRef(0);
  const startFromTo = useRef<[number, number]>([from, to]);

  const isRange = mode === "range";
  const span = Math.max(1, max - min);
  const xOf = (v: number) => ((v - min) / span) * 100;

  const onPointerDown = (kind: "from" | "to" | "bar") => (e: React.PointerEvent) => {
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    dragging.current = kind;
    startPx.current = e.clientX;
    startFromTo.current = [from, to];
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const kind = dragging.current;
    if (!kind || !barRef.current) return;
    const rect = barRef.current.getBoundingClientRect();
    if (kind === "bar") {
      const deltaPx = e.clientX - startPx.current;
      const deltaUnits = Math.round((deltaPx / rect.width) * span);
      const [sf, st] = startFromTo.current;
      const width = st - sf;
      const nf = Math.max(min, Math.min(max - width, sf + deltaUnits));
      onChange(nf, nf + width);
    } else {
      const rel = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      const v = Math.round(min + rel * span);
      if (!isRange) {
        onChange(v, v);
      } else if (kind === "from") {
        onChange(Math.min(v, to), to);
      } else {
        onChange(from, Math.max(v, from));
      }
    }
  };
  const onPointerUp = (e: React.PointerEvent) => {
    if (e.currentTarget.hasPointerCapture(e.pointerId)) {
      try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* noop */ }
    }
    dragging.current = null;
  };

  return (
    <div style={{ padding: "10px 4px" }}>
      <div className="flex items-center justify-between" style={{ marginBottom: 6, fontSize: 11, color: "var(--text-secondary)" }}>
        <span>
          {label || "章节窗口"}：
          {isRange ? (
            <>第 <strong style={{ color: "var(--accent)" }}>{from}</strong> ~ <strong style={{ color: "var(--accent)" }}>{to}</strong> 章</>
          ) : (
            <>第 <strong style={{ color: "var(--accent)" }}>{from}</strong> 章</>
          )}
        </span>
        {showShortcuts && (
          <div className="flex gap-6">
            {isRange && (
              <button className="btn" style={{ fontSize: 10, padding: "1px 8px" }}
                onClick={() => onChange(min, max)}
                title="重置到全部章节">全部</button>
            )}
            <button className="btn" style={{ fontSize: 10, padding: "1px 8px" }}
              onClick={() => onChange(max, max)}
              title={isRange ? "收窄到当前结束章节，看最新状态" : "跳到最新章节"}>最新</button>
          </div>
        )}
      </div>
      <div ref={barRef}
        style={{
          position: "relative", height: 28,
          background: "var(--bg-surface-2)",
          borderRadius: 4, border: "1px solid var(--border)",
          touchAction: "none",
        }}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
      >
        {/* selected window (range) or marker stripe (single) */}
        {isRange ? (
          <div
            onPointerDown={onPointerDown("bar")}
            style={{
              position: "absolute", top: 0, bottom: 0,
              left: `${xOf(from)}%`, width: `${Math.max(0, xOf(to) - xOf(from))}%`,
              background: "var(--accent-subtle)",
              borderLeft: "2px solid var(--accent)",
              borderRight: "2px solid var(--accent)",
              cursor: "grab",
            }}
          />
        ) : (
          <div
            style={{
              position: "absolute", top: 0, bottom: 0, left: 0,
              width: `${xOf(from)}%`, background: "var(--accent-subtle)",
              pointerEvents: "none",
            }}
          />
        )}
        {/* tick marks */}
        {marks.map(m => (
          <div key={m}
            title={`第 ${m} 章`}
            style={{
              position: "absolute", top: 22, height: 6, width: 1,
              left: `${xOf(m)}%`, background: "var(--text-tertiary)",
              transform: "translateX(-0.5px)",
            }} />
        ))}
        {/* from handle (always rendered; doubles as single-mode handle) */}
        <div
          onPointerDown={onPointerDown("from")}
          title={`${isRange ? "起始" : "当前"}：第 ${from} 章`}
          style={{
            position: "absolute", top: -2, bottom: -2,
            left: `${xOf(from)}%`, width: 10,
            transform: "translateX(-50%)",
            background: "var(--accent)",
            borderRadius: 3, cursor: "ew-resize", boxShadow: "0 0 0 1px var(--bg-surface)",
          }} />
        {/* to handle — only in range mode */}
        {isRange && (
          <div
            onPointerDown={onPointerDown("to")}
            title={`结束：第 ${to} 章`}
            style={{
              position: "absolute", top: -2, bottom: -2,
              left: `${xOf(to)}%`, width: 10,
              transform: "translateX(-50%)",
              background: "var(--accent)",
              borderRadius: 3, cursor: "ew-resize", boxShadow: "0 0 0 1px var(--bg-surface)",
            }} />
        )}
      </div>
      <div className="flex items-center justify-between" style={{ marginTop: 2, fontSize: 10, color: "var(--text-tertiary)" }}>
        <span>第 {min} 章</span>
        <span>第 {max} 章</span>
      </div>
    </div>
  );
}
