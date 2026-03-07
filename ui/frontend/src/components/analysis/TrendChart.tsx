import React from "react";

interface Props {
  data?: { label: string; value: number }[];
  color?: string;
}

export default function TrendChart({ data = [], color = "red" }: Props) {
  if (data.length === 0) {
    return (
      <div className="empty-state">
        <div className="empty-icon">...</div>
        <h4>No data</h4>
        <p>Trend data will appear here when available.</p>
      </div>
    );
  }

  const maxVal = Math.max(...data.map((d) => d.value), 1);

  return (
    <div className="bar-chart">
      {data.map((item, i) => (
        <div className="bar-row" key={i}>
          <span className="bar-label">{item.label}</span>
          <div className="bar-track">
            <div
              className={`bar-fill ${color}`}
              style={{ width: `${(item.value / maxVal) * 100}%` }}
            >
              {item.value.toLocaleString()}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
