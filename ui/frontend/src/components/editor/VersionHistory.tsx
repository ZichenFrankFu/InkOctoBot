import React from "react";
import type { TextVersion } from "../../api/types";

interface Props {
  versions: TextVersion[];
  activeVersion?: string;
  onSelect: (id: string) => void;
}

const SOURCE_LABELS: Record<string, string> = {
  ai_generated: "AI Generated",
  user_edited: "User Edit",
  targeted_rewrite: "Rewrite",
};

const SOURCE_COLORS: Record<string, string> = {
  ai_generated: "#818cf8",
  user_edited: "#4ade80",
  targeted_rewrite: "#facc15",
};

export default function VersionHistory({ versions, activeVersion, onSelect }: Props) {
  if (versions.length === 0) {
    return (
      <div style={{ padding: 20, textAlign: "center", color: "var(--text-secondary)", fontSize: 13 }}>
        No versions yet.
      </div>
    );
  }

  const sorted = [...versions].sort((a, b) => b.version - a.version);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, padding: 8 }}>
      {sorted.map((v) => {
        const isActive = activeVersion === v.version_id;
        const srcColor = SOURCE_COLORS[v.source] || "var(--text-secondary)";

        return (
          <div
            key={v.version_id}
            onClick={() => onSelect(v.version_id)}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "10px 12px",
              borderRadius: 6,
              cursor: "pointer",
              background: isActive ? "var(--accent-muted)" : "transparent",
              border: isActive ? "1px solid var(--accent)" : "1px solid transparent",
              transition: "background 0.15s",
            }}
          >
            <div
              style={{
                width: 28,
                height: 28,
                borderRadius: "50%",
                background: isActive ? "var(--accent)" : "var(--bg-tertiary)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 12,
                fontWeight: 700,
                color: isActive ? "#fff" : "var(--text-secondary)",
                flexShrink: 0,
              }}
            >
              v{v.version}
            </div>

            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}>
                <span
                  style={{
                    fontSize: 10,
                    padding: "1px 6px",
                    borderRadius: 6,
                    background: `${srcColor}22`,
                    color: srcColor,
                    fontWeight: 600,
                  }}
                >
                  {SOURCE_LABELS[v.source] || v.source}
                </span>
                {v.model_used && (
                  <span style={{ fontSize: 10, color: "var(--text-secondary)", fontFamily: "var(--font-mono)" }}>
                    {v.model_used}
                  </span>
                )}
              </div>
              <div style={{ fontSize: 11, color: "var(--text-secondary)" }}>
                {new Date(v.created_at).toLocaleString()}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
