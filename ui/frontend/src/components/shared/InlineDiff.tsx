import React from "react";

interface InlineDiffProps {
  original: string;
  modified: string;
  onAccept?: () => void;
  onReject?: () => void;
}

/** Simple word-level inline diff display */
export default function InlineDiff({ original, modified, onAccept, onReject }: InlineDiffProps) {
  const diff = computeWordDiff(original, modified);

  return (
    <div>
      <div style={diffContainerStyle}>
        {diff.map((part, i) => (
          <span key={i} style={part.type === "add" ? addStyle : part.type === "remove" ? removeStyle : keepStyle}>
            {part.text}
          </span>
        ))}
      </div>
      {(onAccept || onReject) && (
        <div style={{ display: "flex", gap: 8, marginTop: 12 }}>
          {onAccept && (
            <button onClick={onAccept} style={acceptBtnStyle} aria-label="Accept changes">
              Accept
            </button>
          )}
          {onReject && (
            <button onClick={onReject} style={rejectBtnStyle} aria-label="Reject changes">
              Reject
            </button>
          )}
        </div>
      )}
    </div>
  );
}

interface DiffPart {
  type: "keep" | "add" | "remove";
  text: string;
}

function computeWordDiff(a: string, b: string): DiffPart[] {
  const wordsA = a.split(/(\s+)/);
  const wordsB = b.split(/(\s+)/);
  const result: DiffPart[] = [];

  // Simple LCS-based diff
  const m = wordsA.length;
  const n = wordsB.length;

  // For very large texts, fall back to simple comparison
  if (m * n > 100000) {
    if (a === b) return [{ type: "keep", text: a }];
    return [
      { type: "remove", text: a },
      { type: "add", text: b },
    ];
  }

  const dp: number[][] = Array.from({ length: m + 1 }, () => Array(n + 1).fill(0));
  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (wordsA[i - 1] === wordsB[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
  }

  // Backtrack
  const parts: DiffPart[] = [];
  let i = m, j = n;
  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && wordsA[i - 1] === wordsB[j - 1]) {
      parts.unshift({ type: "keep", text: wordsA[i - 1] });
      i--; j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      parts.unshift({ type: "add", text: wordsB[j - 1] });
      j--;
    } else {
      parts.unshift({ type: "remove", text: wordsA[i - 1] });
      i--;
    }
  }

  // Merge consecutive same-type parts
  for (const part of parts) {
    if (result.length > 0 && result[result.length - 1].type === part.type) {
      result[result.length - 1].text += part.text;
    } else {
      result.push({ ...part });
    }
  }

  return result;
}

const diffContainerStyle: React.CSSProperties = {
  padding: "12px 14px", borderRadius: 6,
  background: "var(--bg-surface-2)", border: "1px solid var(--border)",
  fontSize: 13, lineHeight: 1.7, whiteSpace: "pre-wrap", wordBreak: "break-word",
};

const keepStyle: React.CSSProperties = { color: "var(--text-primary)" };

const addStyle: React.CSSProperties = {
  background: "rgba(74, 222, 128, 0.15)", color: "#4ade80",
  textDecoration: "none", borderRadius: 2, padding: "0 1px",
};

const removeStyle: React.CSSProperties = {
  background: "rgba(248, 113, 113, 0.15)", color: "#f87171",
  textDecoration: "line-through", borderRadius: 2, padding: "0 1px",
};

const baseBtnStyle: React.CSSProperties = {
  padding: "6px 16px", borderRadius: 6, fontSize: 12, fontWeight: 500,
  cursor: "pointer", border: "none", fontFamily: "var(--font-sans)",
};

const acceptBtnStyle: React.CSSProperties = {
  ...baseBtnStyle, background: "rgba(74, 222, 128, 0.15)", color: "#4ade80",
  border: "1px solid #4ade80",
};

const rejectBtnStyle: React.CSSProperties = {
  ...baseBtnStyle, background: "var(--bg-surface-2)", color: "var(--text-secondary)",
  border: "1px solid var(--border)",
};
