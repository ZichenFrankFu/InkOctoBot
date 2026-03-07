import React, { useEffect, useRef, useState } from "react";
import { apiGet } from "../api/client";

interface Props {
  logPath?: string;
}

interface LogLine {
  text: string;
  level: "info" | "warning" | "error" | "debug";
}

function parseLevel(line: string): LogLine["level"] {
  const upper = line.toUpperCase();
  if (upper.includes("ERROR") || upper.includes("CRITICAL") || upper.includes("FATAL")) return "error";
  if (upper.includes("WARNING") || upper.includes("WARN")) return "warning";
  if (upper.includes("DEBUG")) return "debug";
  return "info";
}

const LEVEL_COLORS: Record<LogLine["level"], string> = {
  info: "#4ade80",
  warning: "#facc15",
  error: "#f87171",
  debug: "#94a3b8",
};

export default function LogViewer({ logPath }: Props) {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [autoScroll, setAutoScroll] = useState(true);
  const containerRef = useRef<HTMLDivElement>(null);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!logPath) {
      setLines([]);
      return;
    }

    const fetchLog = async () => {
      try {
        const res = await apiGet<{ lines: string[] }>(
          `/api/tasks/log?path=${encodeURIComponent(logPath)}`
        );
        const parsed = (res.lines || []).map((text) => ({
          text,
          level: parseLevel(text),
        }));
        setLines(parsed);
      } catch {
        // silently retry on next poll
      }
    };

    fetchLog();
    intervalRef.current = setInterval(fetchLog, 2000);

    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [logPath]);

  useEffect(() => {
    if (autoScroll && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [lines, autoScroll]);

  const handleScroll = () => {
    if (!containerRef.current) return;
    const el = containerRef.current;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    setAutoScroll(atBottom);
  };

  if (!logPath) {
    return (
      <div
        style={{
          padding: 20,
          color: "var(--text-secondary)",
          fontSize: 13,
          fontFamily: "var(--font-mono)",
          background: "var(--bg-secondary)",
          borderRadius: 6,
          minHeight: 120,
        }}
      >
        No log file selected.
      </div>
    );
  }

  return (
    <div style={{ position: "relative" }}>
      <div
        ref={containerRef}
        onScroll={handleScroll}
        style={{
          background: "var(--bg-primary, #0d1117)",
          borderRadius: 6,
          border: "1px solid var(--border)",
          padding: "12px 16px",
          fontFamily: "var(--font-mono)",
          fontSize: 12,
          lineHeight: 1.7,
          overflowY: "auto",
          maxHeight: 400,
          minHeight: 120,
        }}
      >
        {lines.length === 0 ? (
          <div style={{ color: "var(--text-secondary)", fontStyle: "italic" }}>
            Waiting for log output...
          </div>
        ) : (
          lines.map((line, i) => (
            <div
              key={i}
              style={{
                color: LEVEL_COLORS[line.level],
                whiteSpace: "pre-wrap",
                wordBreak: "break-all",
              }}
            >
              {line.text}
            </div>
          ))
        )}
      </div>

      {!autoScroll && (
        <button
          onClick={() => {
            setAutoScroll(true);
            if (containerRef.current) {
              containerRef.current.scrollTop = containerRef.current.scrollHeight;
            }
          }}
          style={{
            position: "absolute",
            bottom: 12,
            right: 12,
            padding: "4px 12px",
            borderRadius: 12,
            border: "1px solid var(--border)",
            background: "var(--bg-tertiary)",
            color: "var(--text-secondary)",
            fontSize: 11,
            cursor: "pointer",
          }}
        >
          Scroll to bottom
        </button>
      )}
    </div>
  );
}
