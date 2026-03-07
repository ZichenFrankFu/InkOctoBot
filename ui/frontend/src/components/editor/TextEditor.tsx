import React, { useMemo } from "react";

interface Props {
  content: string;
  onChange: (text: string) => void;
  readOnly?: boolean;
}

export default function TextEditor({ content, onChange, readOnly = false }: Props) {
  const wordCount = useMemo(() => {
    if (!content) return 0;
    // Count both CJK characters and space-separated words
    const cjk = (content.match(/[\u4e00-\u9fff\u3400-\u4dbf]/g) || []).length;
    const words = content
      .replace(/[\u4e00-\u9fff\u3400-\u4dbf]/g, "")
      .trim()
      .split(/\s+/)
      .filter(Boolean).length;
    return cjk + words;
  }, [content]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <textarea
        className="text-editor-area"
        value={content}
        onChange={(e) => onChange(e.target.value)}
        readOnly={readOnly}
        spellCheck={false}
        style={{
          flex: 1,
          width: "100%",
          padding: "24px 32px",
          border: "none",
          outline: "none",
          resize: "none",
          background: "var(--bg-primary)",
          color: "var(--text-primary)",
          fontFamily: "'Noto Serif SC', 'Georgia', 'Times New Roman', serif",
          fontSize: 16,
          lineHeight: 2,
          letterSpacing: "0.02em",
          boxSizing: "border-box",
          overflowY: "auto",
        }}
        placeholder={readOnly ? "" : "Start writing..."}
      />

      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "6px 16px",
          borderTop: "1px solid var(--border)",
          background: "var(--bg-secondary)",
          fontSize: 11,
          color: "var(--text-secondary)",
        }}
      >
        <span>
          {wordCount.toLocaleString()} {wordCount === 1 ? "word" : "words"}
        </span>
        <span>{content.length.toLocaleString()} characters</span>
        {readOnly && (
          <span style={{ color: "var(--accent)", fontWeight: 500 }}>Read Only</span>
        )}
      </div>
    </div>
  );
}
