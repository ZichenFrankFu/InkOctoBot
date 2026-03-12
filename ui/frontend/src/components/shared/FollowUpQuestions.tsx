import React, { useState } from "react";

export interface FollowUpQuestion {
  text: string;
  options: string[];
}

interface Props {
  questions: FollowUpQuestion[];
  onSelect: (questionIdx: number, answer: string) => void;
}

export default function FollowUpQuestions({ questions, onSelect }: Props) {
  const [customInputIdx, setCustomInputIdx] = useState<number | null>(null);
  const [customText, setCustomText] = useState("");

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {questions.map((q, qi) => (
        <div key={qi}>
          <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)", marginBottom: 8, lineHeight: 1.6 }}>
            {q.text}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {q.options.map((opt, oi) => (
              <button
                key={oi}
                onClick={() => onSelect(qi, opt)}
                style={{
                  padding: "8px 14px", borderRadius: 8,
                  border: "1px solid var(--border)",
                  background: "var(--bg-surface)",
                  color: "var(--text-primary)",
                  fontSize: 12, textAlign: "left", cursor: "pointer",
                  transition: "all 0.15s", lineHeight: 1.5,
                }}
                onMouseEnter={e => { e.currentTarget.style.borderColor = "var(--accent)"; e.currentTarget.style.background = "var(--accent-subtle)"; }}
                onMouseLeave={e => { e.currentTarget.style.borderColor = "var(--border)"; e.currentTarget.style.background = "var(--bg-surface)"; }}
              >
                {opt}
              </button>
            ))}
            {/* Free input option */}
            {customInputIdx === qi ? (
              <div style={{ display: "flex", gap: 6 }}>
                <input
                  className="input"
                  value={customText}
                  onChange={e => setCustomText(e.target.value)}
                  onKeyDown={e => {
                    if (e.key === "Enter" && customText.trim()) {
                      onSelect(qi, customText.trim());
                      setCustomText("");
                      setCustomInputIdx(null);
                    }
                  }}
                  placeholder="输入你的想法..."
                  autoFocus
                  style={{ flex: 1, fontSize: 12 }}
                />
                <button
                  className="btn-primary"
                  style={{ fontSize: 11, padding: "4px 12px", flexShrink: 0 }}
                  onClick={() => {
                    if (customText.trim()) {
                      onSelect(qi, customText.trim());
                      setCustomText("");
                      setCustomInputIdx(null);
                    }
                  }}
                  disabled={!customText.trim()}
                >
                  发送
                </button>
                <button
                  className="btn"
                  style={{ fontSize: 11, padding: "4px 10px", flexShrink: 0 }}
                  onClick={() => { setCustomInputIdx(null); setCustomText(""); }}
                >
                  取消
                </button>
              </div>
            ) : (
              <button
                onClick={() => { setCustomInputIdx(qi); setCustomText(""); }}
                style={{
                  padding: "8px 14px", borderRadius: 8,
                  border: "1px dashed var(--border)",
                  background: "transparent",
                  color: "var(--text-tertiary)",
                  fontSize: 12, textAlign: "left", cursor: "pointer",
                  transition: "all 0.15s",
                }}
                onMouseEnter={e => { e.currentTarget.style.borderColor = "var(--accent)"; e.currentTarget.style.color = "var(--accent)"; }}
                onMouseLeave={e => { e.currentTarget.style.borderColor = "var(--border)"; e.currentTarget.style.color = "var(--text-tertiary)"; }}
              >
                其他（自由输入）
              </button>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}
