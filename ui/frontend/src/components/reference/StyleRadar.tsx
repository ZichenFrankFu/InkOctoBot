import React from "react";

interface Props {
  data?: Record<string, number>;
}

export default function StyleRadar({ data }: Props) {
  if (!data || Object.keys(data).length === 0) {
    return (
      <div className="empty-state">
        <div className="empty-icon">...</div>
        <h4>No style data</h4>
        <p>Style fingerprint data will appear after preprocessing.</p>
      </div>
    );
  }

  const maxVal = Math.max(...Object.values(data), 1);

  return (
    <div className="bar-chart">
      {Object.entries(data).map(([key, val]) => (
        <div className="bar-row" key={key}>
          <span className="bar-label">{key}</span>
          <div className="bar-track">
            <div
              className="bar-fill indigo"
              style={{ width: `${(val / maxVal) * 100}%` }}
            >
              {val.toFixed(2)}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
