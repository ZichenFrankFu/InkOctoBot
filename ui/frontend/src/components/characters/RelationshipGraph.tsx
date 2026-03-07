import React from "react";
import type { CharacterRelationship } from "../../api/types";

interface Props {
  relationships: CharacterRelationship[];
  onEdit: (idx: number, field: string, value: number | string) => void;
}

export default function RelationshipGraph({ relationships, onEdit }: Props) {
  if (relationships.length === 0) {
    return (
      <div style={{ padding: 20, textAlign: "center", color: "var(--text-secondary)", fontSize: 13 }}>
        No relationships defined.
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {relationships.map((rel, idx) => (
        <div
          key={idx}
          className="card"
          style={{ padding: "14px 16px" }}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
            <div
              style={{
                width: 30,
                height: 30,
                borderRadius: "50%",
                background: "var(--bg-tertiary)",
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 13,
                fontWeight: 700,
                color: "var(--text-primary)",
                flexShrink: 0,
              }}
            >
              {rel.target_name.charAt(0)}
            </div>
            <span style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
              {rel.target_name}
            </span>
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: 12 }}>
            {/* Trust Alpha */}
            <div>
              <label style={{ fontSize: 10, color: "var(--text-secondary)", display: "block", marginBottom: 4 }}>
                Trust (\u03B1)
              </label>
              <input
                type="number"
                min={0}
                max={100}
                step={0.1}
                value={rel.trust_alpha}
                onChange={(e) => onEdit(idx, "trust_alpha", parseFloat(e.target.value) || 0)}
                style={{
                  width: "100%",
                  padding: "4px 8px",
                  border: "1px solid var(--border)",
                  borderRadius: 4,
                  fontSize: 12,
                  fontFamily: "var(--font-mono)",
                  background: "var(--bg-secondary)",
                  color: "var(--text-primary)",
                  outline: "none",
                  boxSizing: "border-box",
                }}
              />
            </div>

            {/* Trust Beta */}
            <div>
              <label style={{ fontSize: 10, color: "var(--text-secondary)", display: "block", marginBottom: 4 }}>
                Trust (\u03B2)
              </label>
              <input
                type="number"
                min={0}
                max={100}
                step={0.1}
                value={rel.trust_beta}
                onChange={(e) => onEdit(idx, "trust_beta", parseFloat(e.target.value) || 0)}
                style={{
                  width: "100%",
                  padding: "4px 8px",
                  border: "1px solid var(--border)",
                  borderRadius: 4,
                  fontSize: 12,
                  fontFamily: "var(--font-mono)",
                  background: "var(--bg-secondary)",
                  color: "var(--text-primary)",
                  outline: "none",
                  boxSizing: "border-box",
                }}
              />
            </div>

            {/* Loyalty */}
            <div>
              <label style={{ fontSize: 10, color: "var(--text-secondary)", display: "block", marginBottom: 4 }}>
                Loyalty
              </label>
              <input
                type="number"
                min={0}
                max={1}
                step={0.01}
                value={rel.loyalty}
                onChange={(e) => onEdit(idx, "loyalty", parseFloat(e.target.value) || 0)}
                style={{
                  width: "100%",
                  padding: "4px 8px",
                  border: "1px solid var(--border)",
                  borderRadius: 4,
                  fontSize: 12,
                  fontFamily: "var(--font-mono)",
                  background: "var(--bg-secondary)",
                  color: "var(--text-primary)",
                  outline: "none",
                  boxSizing: "border-box",
                }}
              />
            </div>
          </div>

          {/* Visual trust bar */}
          <div style={{ marginTop: 10 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 10, color: "var(--text-secondary)" }}>
              <span>Trust: {(rel.trust_alpha / (rel.trust_alpha + rel.trust_beta || 1)).toFixed(2)}</span>
              <div
                style={{
                  flex: 1,
                  height: 4,
                  background: "var(--bg-tertiary)",
                  borderRadius: 2,
                  overflow: "hidden",
                }}
              >
                <div
                  style={{
                    width: `${(rel.trust_alpha / (rel.trust_alpha + rel.trust_beta || 1)) * 100}%`,
                    height: "100%",
                    background: "var(--accent)",
                    borderRadius: 2,
                  }}
                />
              </div>
              <span>Loyalty: {rel.loyalty.toFixed(2)}</span>
            </div>
          </div>

          {rel.notes && (
            <div style={{ marginTop: 8, fontSize: 12, color: "var(--text-secondary)", fontStyle: "italic" }}>
              {rel.notes}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
