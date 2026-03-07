import React from "react";

interface Props {
  values: Record<string, number>;
  onChange: (key: string, val: number) => void;
  labels: Record<string, string>;
}

export default function StyleSliders({ values, onChange, labels }: Props) {
  const keys = Object.keys(labels);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      {keys.map((key) => {
        const val = values[key] ?? 0.5;
        const label = labels[key] || key;

        return (
          <div key={key} className="param-slider">
            <div
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
                marginBottom: 4,
              }}
            >
              <label
                style={{
                  fontSize: 12,
                  fontWeight: 500,
                  color: "var(--text-primary)",
                }}
              >
                {label}
              </label>
              <span
                style={{
                  fontSize: 12,
                  fontFamily: "var(--font-mono)",
                  color: "var(--accent)",
                  fontWeight: 600,
                  minWidth: 36,
                  textAlign: "right",
                }}
              >
                {val.toFixed(2)}
              </span>
            </div>

            <div style={{ position: "relative" }}>
              <div
                style={{
                  position: "absolute",
                  top: "50%",
                  left: 0,
                  right: 0,
                  height: 4,
                  background: "var(--bg-tertiary)",
                  borderRadius: 2,
                  transform: "translateY(-50%)",
                }}
              >
                <div
                  style={{
                    width: `${val * 100}%`,
                    height: "100%",
                    background: "var(--accent)",
                    borderRadius: 2,
                    transition: "width 0.1s",
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
                  width: "100%",
                  opacity: 0,
                  cursor: "pointer",
                  height: 20,
                  position: "relative",
                  zIndex: 1,
                }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}
