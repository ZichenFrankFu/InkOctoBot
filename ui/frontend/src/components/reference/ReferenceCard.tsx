import React from "react";

interface ReferenceWork {
  ref_id: string;
  title: string;
  creator?: string;
  media_type: string;
  user_rating?: number;
  preprocessing_status?: string;
}

interface Props {
  work: ReferenceWork;
  onClick: () => void;
  active: boolean;
}

const statusIndicator: Record<string, { color: string; label: string }> = {
  done: { color: "var(--jade)", label: "Ready" },
  processing: { color: "var(--gold)", label: "Processing" },
  pending: { color: "var(--text-tertiary)", label: "Pending" },
  not_applicable: { color: "var(--text-disabled)", label: "N/A" },
};

function renderStars(rating?: number): React.ReactNode {
  const filled = rating ?? 0;
  return (
    <span style={{ fontSize: 13, letterSpacing: 2 }}>
      {[1, 2, 3, 4, 5].map((n) => (
        <span key={n} style={{ color: n <= filled ? "var(--gold)" : "var(--text-disabled)" }}>
          {"\u2605"}
        </span>
      ))}
    </span>
  );
}

export default function ReferenceCard({ work, onClick, active }: Props) {
  const status = statusIndicator[work.preprocessing_status ?? "not_applicable"];

  return (
    <div
      className="card"
      onClick={onClick}
      style={{
        cursor: "pointer",
        borderLeft: active ? "3px solid var(--accent)" : "3px solid transparent",
        transition: "border-color 0.15s, background 0.15s",
      }}
    >
      <div className="card-body">
        <div className="flex items-center justify-between mb-4">
          <span className="truncate" style={{ fontWeight: 600, fontSize: 14, color: "var(--text-primary)", flex: 1, minWidth: 0 }}>
            {work.title}
          </span>
          <span className="tag category" style={{ flexShrink: 0, marginLeft: 8 }}>
            {work.media_type}
          </span>
        </div>

        {work.creator && (
          <div className="text-xs text-muted mb-8">{work.creator}</div>
        )}

        <div className="flex items-center justify-between">
          {renderStars(work.user_rating)}
          {status && (
            <span className="flex items-center gap-4 text-xs">
              <span
                style={{
                  width: 7,
                  height: 7,
                  borderRadius: "50%",
                  background: status.color,
                  display: "inline-block",
                }}
              />
              <span style={{ color: status.color }}>{status.label}</span>
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
