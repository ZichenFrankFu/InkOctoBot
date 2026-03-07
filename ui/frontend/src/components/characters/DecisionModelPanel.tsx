import React from "react";
import type { CharacterLayerB } from "../../api/types";

interface Props {
  layerB: CharacterLayerB;
  onChange: (key: string, value: number) => void;
}

const PARAM_LABELS: Record<string, string> = {
  loss_aversion: "Loss Aversion",
  risk_aversion_gain: "Risk Aversion (Gain)",
  risk_aversion_loss: "Risk Aversion (Loss)",
  impulse_probability: "Impulse Probability",
  social_frequency: "Social Frequency",
  time_discount: "Time Discount",
};

export default function DecisionModelPanel({ layerB, onChange }: Props) {
  const paramKeys = Object.keys(PARAM_LABELS);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      {/* Quantified parameters */}
      <div>
        <h4 style={{ fontSize: 13, color: "var(--text-primary)", marginBottom: 12, fontWeight: 600 }}>
          Decision Parameters
        </h4>
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          {paramKeys.map((key) => {
            const val = (layerB as unknown as Record<string, number>)[key] ?? 0.5;
            return (
              <div key={key} className="param-slider">
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                  <label style={{ fontSize: 12, color: "var(--text-primary)", fontWeight: 500 }}>
                    {PARAM_LABELS[key]}
                  </label>
                  <span style={{ fontSize: 12, fontFamily: "var(--font-mono)", color: "var(--accent)" }}>
                    {val.toFixed(2)}
                  </span>
                </div>
                <div style={{ position: "relative" }}>
                  <div
                    style={{
                      height: 4,
                      background: "var(--bg-tertiary)",
                      borderRadius: 2,
                      overflow: "hidden",
                    }}
                  >
                    <div
                      style={{
                        width: `${val * 100}%`,
                        height: "100%",
                        background: "var(--accent)",
                        borderRadius: 2,
                      }}
                    />
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={1}
                    step={0.01}
                    value={val}
                    onChange={(e) => onChange(key, parseFloat(e.target.value))}
                    style={{
                      position: "absolute",
                      top: -8,
                      left: 0,
                      width: "100%",
                      opacity: 0,
                      cursor: "pointer",
                      height: 20,
                    }}
                  />
                </div>
              </div>
            );
          })}
        </div>
      </div>

      {/* Value weights */}
      {layerB.value_weights && Object.keys(layerB.value_weights).length > 0 && (
        <div>
          <h4 style={{ fontSize: 13, color: "var(--text-primary)", marginBottom: 12, fontWeight: 600 }}>
            Value Weights
          </h4>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {Object.entries(layerB.value_weights).map(([key, val]) => (
              <div
                key={key}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  padding: "6px 10px",
                  borderRadius: 6,
                  background: "var(--bg-secondary)",
                }}
              >
                <span style={{ fontSize: 12, color: "var(--text-primary)", flex: 1, fontWeight: 500 }}>
                  {key}
                </span>
                <div
                  style={{
                    width: 80,
                    height: 6,
                    background: "var(--bg-tertiary)",
                    borderRadius: 3,
                    overflow: "hidden",
                  }}
                >
                  <div
                    style={{
                      width: `${Math.min(val * 100, 100)}%`,
                      height: "100%",
                      background: "var(--accent)",
                      borderRadius: 3,
                    }}
                  />
                </div>
                <span style={{ fontSize: 11, fontFamily: "var(--font-mono)", color: "var(--text-secondary)", minWidth: 32, textAlign: "right" }}>
                  {val.toFixed(2)}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
