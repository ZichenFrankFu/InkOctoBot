/**
 * Lightweight line-based diff using LCS (Longest Common Subsequence).
 * No external dependencies.
 */

export type DiffType = "equal" | "removed" | "added";

export interface DiffLine {
  type: DiffType;
  text: string;
  oldLineNum?: number;
  newLineNum?: number;
}

export interface DiffHunk {
  id: number;
  lines: DiffLine[];
  hasChanges: boolean;
}

/**
 * Compute line-based diff between two texts.
 */
export function computeDiff(oldText: string, newText: string): DiffLine[] {
  const oldLines = oldText.split("\n");
  const newLines = newText.split("\n");

  // Build LCS table
  const m = oldLines.length;
  const n = newLines.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));

  for (let i = 1; i <= m; i++) {
    for (let j = 1; j <= n; j++) {
      if (oldLines[i - 1] === newLines[j - 1]) {
        dp[i][j] = dp[i - 1][j - 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
      }
    }
  }

  // Backtrack to produce diff
  const result: DiffLine[] = [];
  let i = m, j = n;
  const stack: DiffLine[] = [];

  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && oldLines[i - 1] === newLines[j - 1]) {
      stack.push({ type: "equal", text: oldLines[i - 1], oldLineNum: i, newLineNum: j });
      i--; j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      stack.push({ type: "added", text: newLines[j - 1], newLineNum: j });
      j--;
    } else {
      stack.push({ type: "removed", text: oldLines[i - 1], oldLineNum: i });
      i--;
    }
  }

  // Reverse since we built it backwards
  while (stack.length) result.push(stack.pop()!);

  return result;
}

/**
 * Group diff lines into hunks. Each hunk is either a block of unchanged lines
 * or a block of changes (removed + added lines together).
 */
export function groupIntoHunks(diffLines: DiffLine[]): DiffHunk[] {
  const hunks: DiffHunk[] = [];
  let currentLines: DiffLine[] = [];
  let currentHasChanges = false;
  let id = 0;

  const flush = () => {
    if (currentLines.length > 0) {
      hunks.push({ id: id++, lines: currentLines, hasChanges: currentHasChanges });
      currentLines = [];
      currentHasChanges = false;
    }
  };

  for (const line of diffLines) {
    const isChange = line.type !== "equal";
    if (isChange !== currentHasChanges && currentLines.length > 0) {
      flush();
    }
    currentHasChanges = isChange;
    currentLines.push(line);
  }
  flush();

  return hunks;
}

/**
 * Assemble final text from hunks based on user choices.
 * choices: Map<hunkId, "old" | "new"> — only for hunks with hasChanges=true.
 * For unchanged hunks, the equal lines are always kept.
 */
export function assembleFromHunks(hunks: DiffHunk[], choices: Map<number, "old" | "new">): string {
  const lines: string[] = [];

  for (const hunk of hunks) {
    if (!hunk.hasChanges) {
      for (const line of hunk.lines) lines.push(line.text);
    } else {
      const choice = choices.get(hunk.id) ?? "new";
      if (choice === "old") {
        for (const line of hunk.lines) {
          if (line.type === "removed" || line.type === "equal") lines.push(line.text);
        }
      } else {
        for (const line of hunk.lines) {
          if (line.type === "added" || line.type === "equal") lines.push(line.text);
        }
      }
    }
  }

  return lines.join("\n");
}
