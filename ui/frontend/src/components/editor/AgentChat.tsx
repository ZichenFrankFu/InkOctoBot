import React, { useEffect, useRef } from "react";
import type { AgentEvent } from "../../api/types";

interface Props {
  messages: AgentEvent[];
}

const AGENT_COLORS: Record<string, string> = {
  "Scene Director": "#818cf8",
  Actor: "#fb923c",
  "Editor-Writer": "#4ade80",
  Evaluator: "#f87171",
  Marketing: "#facc15",
  "Story Architect": "#38bdf8",
  System: "#94a3b8",
};

function getAgentColor(agent: string): string {
  return AGENT_COLORS[agent] || "#a78bfa";
}

function getInitial(agent: string): string {
  return agent.charAt(0).toUpperCase();
}

export default function AgentChat({ messages }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div
        style={{
          padding: 24,
          textAlign: "center",
          color: "var(--text-secondary)",
          fontSize: 13,
        }}
      >
        No agent messages yet. Start the pipeline to see agent interactions.
      </div>
    );
  }

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 12,
        padding: 16,
        overflowY: "auto",
        maxHeight: "100%",
      }}
    >
      {messages.map((msg, i) => {
        const color = getAgentColor(msg.agent);
        return (
          <div key={i} className="chat-message" style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
            <div
              className="chat-avatar"
              style={{
                width: 32,
                height: 32,
                borderRadius: "50%",
                background: color,
                display: "flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 14,
                fontWeight: 700,
                color: "#fff",
                flexShrink: 0,
              }}
            >
              {getInitial(msg.agent)}
            </div>

            <div style={{ flex: 1, minWidth: 0 }}>
              <div
                className="chat-sender"
                style={{
                  display: "flex",
                  alignItems: "baseline",
                  gap: 8,
                  marginBottom: 4,
                }}
              >
                <span style={{ fontSize: 12, fontWeight: 600, color }}>
                  {msg.agent}
                </span>
                <span style={{ fontSize: 10, color: "var(--text-secondary)" }}>
                  {msg.timestamp}
                </span>
                {msg.severity && msg.severity !== "info" && (
                  <span
                    style={{
                      fontSize: 9,
                      padding: "1px 6px",
                      borderRadius: 6,
                      background:
                        msg.severity === "warning"
                          ? "rgba(250,204,21,0.15)"
                          : "rgba(74,222,128,0.15)",
                      color: msg.severity === "warning" ? "#facc15" : "#4ade80",
                      textTransform: "uppercase",
                      fontWeight: 600,
                    }}
                  >
                    {msg.severity}
                  </span>
                )}
              </div>

              <div
                className="chat-bubble"
                style={{
                  padding: "8px 12px",
                  borderRadius: "4px 12px 12px 12px",
                  background: "var(--bg-secondary)",
                  border: "1px solid var(--border)",
                  fontSize: 13,
                  lineHeight: 1.6,
                  color: "var(--text-primary)",
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                }}
              >
                {msg.message}
              </div>
            </div>
          </div>
        );
      })}
      <div ref={bottomRef} />
    </div>
  );
}
