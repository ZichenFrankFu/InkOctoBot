import React from "react";

interface Props {
  data?: { name: string; score: number; type?: string }[];
}

export default function ShuangdianRank({ data = [] }: Props) {
  if (data.length === 0) {
    return (
      <div className="empty-state">
        <div className="empty-icon">...</div>
        <h4>No ranking data</h4>
        <p>Shuangdian ranking data will appear here when available.</p>
      </div>
    );
  }

  const maxScore = Math.max(...data.map((d) => d.score), 1);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {data.map((item, i) => {
        const rank = i + 1;
        const isTop3 = rank <= 3;

        return (
          <div className="flex items-center gap-10" key={i}>
            <span
              className={`rank-badge ${isTop3 ? "top3" : "normal"}`}
              style={{ minWidth: 32 }}
            >
              {rank}
            </span>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="flex items-center gap-8 mb-4">
                <span
                  style={{
                    fontSize: 13,
                    fontWeight: isTop3 ? 600 : 400,
                    color: isTop3 ? "var(--gold)" : "var(--text-primary)",
                  }}
                >
                  {item.name}
                </span>
                {item.type && <span className="tag category">{item.type}</span>}
              </div>
              <div className="bar-track" style={{ height: 14 }}>
                <div
                  className={`bar-fill ${isTop3 ? "gold" : "indigo"}`}
                  style={{
                    width: `${(item.score / maxScore) * 100}%`,
                    fontSize: 10,
                  }}
                >
                  {item.score.toFixed(1)}
                </div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
