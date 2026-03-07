import React from "react";

interface ModelResult {
  model: string;
  text: string;
  tokens: number;
  cost?: number;
}

interface Props {
  results: ModelResult[];
}

export default function ModelCompare({ results }: Props) {
  if (results.length === 0) {
    return (
      <div style={{ padding: 20, textAlign: "center", color: "var(--text-secondary)", fontSize: 13 }}>
        No model results to compare.
      </div>
    );
  }

  return (
    <div
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(${Math.min(results.length, 3)}, 1fr)`,
        gap: 12,
        height: "100%",
      }}
    >
      {results.map((r, i) => (
        <div
          key={i}
          className="card"
          style={{
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
          }}
        >
          {/* Header */}
          <div
            style={{
              padding: "10px 14px",
              borderBottom: "1px solid var(--border)",
              background: "var(--bg-secondary)",
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              flexShrink: 0,
            }}
          >
            <span
              style={{
                fontSize: 13,
                fontWeight: 600,
                color: "var(--text-primary)",
                fontFamily: "var(--font-mono)",
              }}
            >
              {r.model}
            </span>
            <div style={{ display: "flex", gap: 10, fontSize: 10, color: "var(--text-secondary)" }}>
              <span>{r.tokens.toLocaleString()} tokens</span>
              {r.cost != null && <span>${r.cost.toFixed(4)}</span>}
            </div>
          </div>

          {/* Content */}
          <div
            style={{
              flex: 1,
              padding: "14px 16px",
              overflowY: "auto",
              fontSize: 13,
              lineHeight: 1.8,
              color: "var(--text-primary)",
              whiteSpace: "pre-wrap",
              wordBreak: "break-word",
              fontFamily: "'Noto Serif SC', Georgia, serif",
            }}
          >
            {r.text}
          </div>
        </div>
      ))}
    </div>
  );
}
