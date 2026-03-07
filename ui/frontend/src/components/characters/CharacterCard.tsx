import React from "react";
import type { Character } from "../../api/types";

interface Props {
  character: Character;
  onClick: () => void;
  active: boolean;
}

export default function CharacterCard({ character, onClick, active }: Props) {
  const initial = character.name.charAt(0).toUpperCase();

  return (
    <div
      onClick={onClick}
      className="card"
      style={{
        cursor: "pointer",
        border: active ? "1px solid var(--accent)" : "1px solid var(--border)",
        background: active ? "var(--accent-muted)" : "var(--bg-secondary)",
        transition: "border-color 0.15s, background 0.15s, box-shadow 0.15s",
        boxShadow: active ? "0 0 12px rgba(var(--accent-rgb, 99, 102, 241), 0.2)" : "none",
        overflow: "hidden",
      }}
    >
      <div style={{ padding: "16px 16px 12px", display: "flex", gap: 12, alignItems: "flex-start" }}>
        {/* Avatar */}
        <div
          style={{
            width: 42,
            height: 42,
            borderRadius: "50%",
            background: active ? "var(--accent)" : "var(--bg-tertiary)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            fontSize: 18,
            fontWeight: 700,
            color: active ? "#fff" : "var(--text-primary)",
            flexShrink: 0,
          }}
        >
          {initial}
        </div>

        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: 15,
              fontWeight: 600,
              color: "var(--text-primary)",
              marginBottom: 2,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {character.name}
          </div>

          {character.role && (
            <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 6 }}>
              {character.role}
            </div>
          )}

          {character.tags && character.tags.length > 0 && (
            <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
              {character.tags.map((tag, i) => (
                <span
                  key={i}
                  style={{
                    fontSize: 10,
                    padding: "2px 8px",
                    borderRadius: 10,
                    background: "var(--bg-tertiary)",
                    color: "var(--text-secondary)",
                    border: "1px solid var(--border)",
                  }}
                >
                  {tag}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
