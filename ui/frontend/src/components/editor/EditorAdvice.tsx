import React from "react";

interface Suggestion {
  agent: string;
  title: string;
  content: string;
  severity: string;
}

interface Props {
  suggestions: Suggestion[];
}

const SEVERITY_COLORS: Record<string, string> = {
  info: "#38bdf8",
  warning: "#facc15",
  suggestion: "#4ade80",
};

export default function EditorAdvice({ suggestions }: Props) {
  if (suggestions.length === 0) {
    return (
      <div style={{ padding: 20, textAlign: "center", color: "var(--text-secondary)", fontSize: 13 }}>
        No suggestions at the moment.
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, padding: 8 }}>
      {suggestions.map((s, i) => {
        const borderColor = SEVERITY_COLORS[s.severity] || SEVERITY_COLORS.info;

        return (
          <div
            key={i}
            className="card"
            style={{
              borderLeft: `3px solid ${borderColor}`,
              overflow: "hidden",
            }}
          >
            <div style={{ padding: "12px 16px" }}>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  marginBottom: 6,
                }}
              >
                <span
                  style={{
                    fontSize: 10,
                    padding: "2px 8px",
                    borderRadius: 8,
                    background: `${borderColor}22`,
                    color: borderColor,
                    fontWeight: 600,
                    textTransform: "uppercase",
                  }}
                >
                  {s.severity}
                </span>
                <span style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                  {s.agent}
                </span>
              </div>

              <div
                style={{
                  fontSize: 14,
                  fontWeight: 600,
                  color: "var(--text-primary)",
                  marginBottom: 4,
                }}
              >
                {s.title}
              </div>

              <div
                style={{
                  fontSize: 13,
                  color: "var(--text-secondary)",
                  lineHeight: 1.6,
                }}
              >
                {s.content}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
