import React, { useState } from "react";

export interface FollowUpQuestion {
  text: string;
  options: string[];
}

interface Props {
  questions: FollowUpQuestion[];
  onSubmitAll: (answers: Record<number, string>) => void;
}

export default function FollowUpQuestions({ questions, onSubmitAll }: Props) {
  const [selections, setSelections] = useState<Record<number, string>>({});
  const [customInputIdx, setCustomInputIdx] = useState<number | null>(null);
  const [customText, setCustomText] = useState("");

  const allAnswered = questions.every((_, qi) => selections[qi] !== undefined);

  const selectOption = (qi: number, value: string) => {
    setSelections(prev => ({ ...prev, [qi]: value }));
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {questions.map((q, qi) => {
        const selected = selections[qi];
        return (
          <div key={qi} style={{
            padding: "10px 14px", borderRadius: 10,
            background: "var(--bg-surface)",
            border: selected ? "1px solid var(--accent)" : "1px solid var(--border)",
            transition: "border-color 0.2s",
          }}>
            <div style={{
              fontSize: 13, fontWeight: 600, color: "var(--text-primary)",
              marginBottom: 10, lineHeight: 1.6,
            }}>
              Q{qi + 1}. {q.text}
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              {q.options.map((opt, oi) => {
                const isSelected = selected === opt;
                return (
                  <button
                    key={oi}
                    onClick={() => selectOption(qi, opt)}
                    style={{
                      padding: "8px 14px", borderRadius: 8,
                      border: isSelected ? "1.5px solid var(--accent)" : "1px solid var(--border)",
                      background: isSelected ? "var(--accent-subtle)" : "var(--bg-surface)",
                      color: isSelected ? "var(--accent)" : "var(--text-primary)",
                      fontWeight: isSelected ? 600 : 400,
                      fontSize: 12, textAlign: "left", cursor: "pointer",
                      transition: "all 0.15s", lineHeight: 1.5,
                    }}
                    onMouseEnter={e => {
                      if (!isSelected) {
                        e.currentTarget.style.borderColor = "var(--accent)";
                        e.currentTarget.style.background = "var(--accent-subtle)";
                      }
                    }}
                    onMouseLeave={e => {
                      if (!isSelected) {
                        e.currentTarget.style.borderColor = "var(--border)";
                        e.currentTarget.style.background = "var(--bg-surface)";
                      }
                    }}
                  >
                    {opt}
                  </button>
                );
              })}
              {/* Free input option */}
              {customInputIdx === qi ? (
                <div style={{ display: "flex", gap: 6 }}>
                  <input
                    className="input"
                    value={customText}
                    onChange={e => setCustomText(e.target.value)}
                    onKeyDown={e => {
                      if (e.key === "Enter" && customText.trim()) {
                        selectOption(qi, customText.trim());
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
                        selectOption(qi, customText.trim());
                        setCustomText("");
                        setCustomInputIdx(null);
                      }
                    }}
                    disabled={!customText.trim()}
                  >
                    确认
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
                    background: selected && !q.options.includes(selected) ? "var(--accent-subtle)" : "transparent",
                    color: selected && !q.options.includes(selected) ? "var(--accent)" : "var(--text-tertiary)",
                    fontWeight: selected && !q.options.includes(selected) ? 600 : 400,
                    fontSize: 12, textAlign: "left", cursor: "pointer",
                    transition: "all 0.15s",
                  }}
                  onMouseEnter={e => { e.currentTarget.style.borderColor = "var(--accent)"; e.currentTarget.style.color = "var(--accent)"; }}
                  onMouseLeave={e => {
                    const isCustomSelected = selected && !q.options.includes(selected);
                    e.currentTarget.style.borderColor = "var(--border)";
                    e.currentTarget.style.color = isCustomSelected ? "var(--accent)" : "var(--text-tertiary)";
                  }}
                >
                  {selected && !q.options.includes(selected) ? `✓ ${selected}` : "其他（自由输入）"}
                </button>
              )}
            </div>
            {selected && (
              <div style={{ marginTop: 6, fontSize: 11, color: "var(--accent)" }}>
                ✓ 已选择
              </div>
            )}
          </div>
        );
      })}

      {/* Submit all button */}
      <button
        className="btn-primary"
        disabled={!allAnswered}
        onClick={() => onSubmitAll(selections)}
        style={{
          padding: "10px 20px", fontSize: 13, borderRadius: 8,
          opacity: allAnswered ? 1 : 0.5,
          cursor: allAnswered ? "pointer" : "not-allowed",
          alignSelf: "flex-end",
        }}
      >
        {allAnswered
          ? `提交全部回答（${questions.length}/${questions.length}）`
          : `请回答所有问题（${Object.keys(selections).length}/${questions.length}）`}
      </button>
    </div>
  );
}
