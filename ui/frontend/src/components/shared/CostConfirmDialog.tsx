import React from "react";

interface Props {
  cost: number;
  model: string;
  tokens: number;
  onConfirm: () => void;
  onCancel: () => void;
}

export default function CostConfirmDialog({ cost, model, tokens, onConfirm, onCancel }: Props) {
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 1000,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "rgba(0, 0, 0, 0.6)",
        backdropFilter: "blur(4px)",
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onCancel();
      }}
    >
      <div
        className="card"
        style={{
          minWidth: 360,
          maxWidth: 440,
          boxShadow: "0 20px 60px rgba(0,0,0,0.5)",
        }}
      >
        <div className="card-header">
          <h3 style={{ margin: 0 }}>Cost Confirmation</h3>
          <p style={{ margin: "4px 0 0", fontSize: 12 }}>Review the estimated cost before proceeding</p>
        </div>
        <div className="card-body">
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "auto 1fr",
              gap: "12px 16px",
              fontSize: 13,
              marginBottom: 20,
            }}
          >
            <span style={{ color: "var(--text-secondary)", fontWeight: 500 }}>Model</span>
            <span style={{ color: "var(--text-primary)", fontFamily: "var(--font-mono)", fontSize: 12 }}>
              {model}
            </span>

            <span style={{ color: "var(--text-secondary)", fontWeight: 500 }}>Tokens</span>
            <span style={{ color: "var(--text-primary)", fontFamily: "var(--font-mono)", fontSize: 12 }}>
              {tokens.toLocaleString()}
            </span>

            <span style={{ color: "var(--text-secondary)", fontWeight: 500 }}>Estimated Cost</span>
            <span
              style={{
                color: "var(--accent)",
                fontFamily: "var(--font-mono)",
                fontSize: 16,
                fontWeight: 700,
              }}
            >
              ${cost.toFixed(4)}
            </span>
          </div>

          <div style={{ display: "flex", gap: 12, justifyContent: "flex-end" }}>
            <button
              className="btn-secondary"
              onClick={onCancel}
              style={{ padding: "8px 20px", fontSize: 13 }}
            >
              Cancel
            </button>
            <button
              className="btn-primary"
              onClick={onConfirm}
              style={{ padding: "8px 20px", fontSize: 13 }}
            >
              Confirm
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
