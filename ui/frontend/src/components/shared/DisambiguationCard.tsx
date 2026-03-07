import React, { useState } from "react";

interface Option {
  label: string;
  description: string;
}

interface Props {
  question: string;
  options: Option[];
  onSelect: (idx: number) => void;
}

export default function DisambiguationCard({ question, options, onSelect }: Props) {
  const [selectedIdx, setSelectedIdx] = useState<number | null>(null);

  return (
    <div
      className="card"
      style={{
        border: "1px solid var(--accent)",
        boxShadow: "0 0 12px rgba(var(--accent-rgb, 99, 102, 241), 0.15)",
      }}
    >
      <div className="card-header">
        <h3 style={{ margin: 0, fontSize: 14 }}>Clarification Needed</h3>
      </div>
      <div className="card-body">
        <p style={{ color: "var(--text-primary)", fontSize: 14, lineHeight: 1.6, marginBottom: 16 }}>
          {question}
        </p>

        <div style={{ display: "flex", flexDirection: "column", gap: 8, marginBottom: 16 }}>
          {options.map((opt, idx) => (
            <label
              key={idx}
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: 10,
                padding: "10px 14px",
                borderRadius: 6,
                border:
                  selectedIdx === idx
                    ? "1px solid var(--accent)"
                    : "1px solid var(--border)",
                background:
                  selectedIdx === idx ? "var(--accent-muted)" : "var(--bg-secondary)",
                cursor: "pointer",
                transition: "border-color 0.15s, background 0.15s",
              }}
            >
              <input
                type="radio"
                name="disambiguation"
                checked={selectedIdx === idx}
                onChange={() => setSelectedIdx(idx)}
                style={{
                  marginTop: 3,
                  accentColor: "var(--accent)",
                  flexShrink: 0,
                }}
              />
              <div>
                <div
                  style={{
                    fontSize: 13,
                    fontWeight: 600,
                    color: "var(--text-primary)",
                    marginBottom: 2,
                  }}
                >
                  {opt.label}
                </div>
                <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5 }}>
                  {opt.description}
                </div>
              </div>
            </label>
          ))}
        </div>

        <div style={{ textAlign: "right" }}>
          <button
            className="btn-primary"
            onClick={() => {
              if (selectedIdx !== null) onSelect(selectedIdx);
            }}
            disabled={selectedIdx === null}
            style={{
              padding: "8px 24px",
              fontSize: 13,
              opacity: selectedIdx === null ? 0.5 : 1,
            }}
          >
            Select
          </button>
        </div>
      </div>
    </div>
  );
}
