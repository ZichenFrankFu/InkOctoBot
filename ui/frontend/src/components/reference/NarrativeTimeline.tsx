import React from "react";

interface Props {
  data?: any[];
}

export default function NarrativeTimeline({ data }: Props) {
  if (!data || data.length === 0) {
    return (
      <div className="empty-state">
        <div className="empty-icon">...</div>
        <h4>No narrative data</h4>
        <p>Narrative structure timeline will appear after preprocessing.</p>
      </div>
    );
  }

  return (
    <div style={{ overflowX: "auto", padding: "24px 16px" }}>
      <div
        style={{
          display: "flex",
          alignItems: "flex-start",
          position: "relative",
          minWidth: data.length * 120,
        }}
      >
        {/* Connecting horizontal line */}
        <div
          style={{
            position: "absolute",
            top: 20,
            left: 20,
            right: 20,
            height: 2,
            background: "var(--border)",
          }}
        />

        {data.map((event: any, i: number) => (
          <div
            key={i}
            style={{
              flex: "0 0 120px",
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              position: "relative",
            }}
          >
            <div
              style={{
                width: 12,
                height: 12,
                borderRadius: "50%",
                background: "var(--accent)",
                border: "2px solid var(--bg-card)",
                boxShadow: "0 0 6px var(--accent-glow)",
                zIndex: 1,
                marginBottom: 8,
                marginTop: 14,
              }}
            />
            <div
              style={{
                textAlign: "center",
                fontSize: 11,
                color: "var(--text-secondary)",
                lineHeight: 1.4,
                maxWidth: 100,
                wordBreak: "break-word",
              }}
            >
              {event.title ?? event.label ?? event.description ?? `Event ${i + 1}`}
            </div>
            {event.position && (
              <div
                className="text-xs text-muted"
                style={{ marginTop: 2, textAlign: "center" }}
              >
                {event.position}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
