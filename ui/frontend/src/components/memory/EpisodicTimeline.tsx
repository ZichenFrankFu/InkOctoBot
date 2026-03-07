import React from "react";

interface EpisodicEvent {
  event_id: string;
  chapter_num: number;
  event_type: string;
  description: string;
  characters_involved?: string[];
  foreshadow_status?: string;
}

interface Props {
  events?: EpisodicEvent[];
}

const EVENT_COLORS: Record<string, string> = {
  plot: "var(--indigo)",
  character: "var(--purple)",
  foreshadow: "var(--gold)",
  revelation: "var(--accent)",
};

const FORESHADOW_STYLES: Record<string, { bg: string; color: string }> = {
  planted: { bg: "var(--gold-subtle)", color: "var(--gold)" },
  hinted: { bg: "var(--indigo-subtle)", color: "var(--indigo)" },
  resolved: { bg: "var(--jade-subtle)", color: "var(--jade)" },
};

export default function EpisodicTimeline({ events = [] }: Props) {
  if (events.length === 0) {
    return (
      <div className="empty-state">
        <div className="empty-icon">...</div>
        <h4>No events</h4>
        <p>Episodic memory events will appear here as the story progresses.</p>
      </div>
    );
  }

  return (
    <div style={{ position: "relative", paddingLeft: 24 }}>
      {/* Vertical connecting line */}
      <div
        style={{
          position: "absolute",
          left: 11,
          top: 0,
          bottom: 0,
          width: 2,
          background: "var(--border)",
        }}
      />

      {events.map((evt) => {
        const dotColor = EVENT_COLORS[evt.event_type] ?? "var(--text-tertiary)";
        const fsBadge = evt.foreshadow_status
          ? FORESHADOW_STYLES[evt.foreshadow_status]
          : null;

        return (
          <div
            key={evt.event_id}
            style={{
              position: "relative",
              marginBottom: 12,
              paddingLeft: 20,
            }}
          >
            {/* Timeline dot */}
            <div
              style={{
                position: "absolute",
                left: -18,
                top: 14,
                width: 12,
                height: 12,
                borderRadius: "50%",
                background: dotColor,
                border: "2px solid var(--bg-app)",
                boxShadow: `0 0 6px ${dotColor}40`,
              }}
            />

            <div className="card" style={{ padding: 0 }}>
              <div style={{ padding: "12px 16px" }}>
                <div className="flex items-center gap-8 mb-4">
                  <span
                    className="font-mono"
                    style={{
                      fontSize: 11,
                      color: "var(--text-tertiary)",
                      fontWeight: 600,
                    }}
                  >
                    Ch.{evt.chapter_num}
                  </span>
                  <span
                    className="tag"
                    style={{
                      background: `${dotColor}20`,
                      color: dotColor,
                      fontSize: 10,
                    }}
                  >
                    {evt.event_type}
                  </span>
                  {fsBadge && (
                    <span
                      className="tag"
                      style={{
                        background: fsBadge.bg,
                        color: fsBadge.color,
                        fontSize: 10,
                      }}
                    >
                      {evt.foreshadow_status}
                    </span>
                  )}
                </div>

                <div
                  style={{
                    fontSize: 13,
                    color: "var(--text-secondary)",
                    lineHeight: 1.6,
                  }}
                >
                  {evt.description}
                </div>

                {evt.characters_involved && evt.characters_involved.length > 0 && (
                  <div className="flex gap-4" style={{ marginTop: 8, flexWrap: "wrap" }}>
                    {evt.characters_involved.map((ch) => (
                      <span className="tag category" key={ch}>
                        {ch}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}
