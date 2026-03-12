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
            {rel.label && (
              <span style={{ fontSize: 10, padding: "2px 8px", borderRadius: 10, background: "var(--accent-subtle)", color: "var(--accent)" }}>
                {rel.label}
              </span>
            )}
          </div>

          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
            {/* Affinity */}
            <div>
              <label style={{ fontSize: 10, color: "var(--text-secondary)", display: "block", marginBottom: 4 }}>
                好感度
              </label>
              <input
                type="number"
                min={-100}
                max={100}
                step={5}
                value={rel.affinity}
                onChange={(e) => onEdit(idx, "affinity", parseFloat(e.target.value) || 0)}
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

            {/* Priority */}
            <div>
              <label style={{ fontSize: 10, color: "var(--text-secondary)", display: "block", marginBottom: 4 }}>
                优先级
              </label>
              <input
                type="number"
                min={1}
                max={20}
                step={1}
                value={rel.priority}
                onChange={(e) => onEdit(idx, "priority", parseInt(e.target.value) || 1)}
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

          {/* Visual affinity bar */}
          <div style={{ marginTop: 10 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 10, color: "var(--text-secondary)" }}>
              <span>好感度: {rel.affinity > 0 ? "+" : ""}{rel.affinity}</span>
              <div
                style={{
                  flex: 1,
                  height: 4,
                  background: "var(--bg-tertiary)",
                  borderRadius: 2,
                  overflow: "hidden",
                  position: "relative",
                }}
              >
                <div
                  style={{
                    position: "absolute",
                    left: "50%",
                    width: `${Math.abs(rel.affinity) / 2}%`,
                    marginLeft: rel.affinity < 0 ? `-${Math.abs(rel.affinity) / 2}%` : 0,
                    height: "100%",
                    background: rel.affinity >= 0 ? "var(--jade)" : "var(--error)",
                    borderRadius: 2,
                  }}
                />
              </div>
              <span>优先级: #{rel.priority}</span>
            </div>
          </div>

          {rel.chapter && (
            <div style={{ marginTop: 6, fontSize: 10, color: "var(--text-tertiary)" }}>
              时间: {rel.chapter}
            </div>
          )}

          {rel.label && (
            <div style={{ marginTop: 4, fontSize: 11, color: "var(--accent)", fontWeight: 500 }}>
              关系: {rel.label}
            </div>
          )}

          {rel.notes && (
            <div style={{ marginTop: 4, fontSize: 12, color: "var(--text-secondary)", fontStyle: "italic" }}>
              {rel.notes}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
