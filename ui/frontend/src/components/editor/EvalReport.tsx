import React, { useState } from "react";

interface EvalDimension {
  name: string;
  score: number;
  max_score: number;
}

interface EvalIssueItem {
  type: string;
  severity: "high" | "medium" | "low";
  description: string;
  suggestion?: string;
}

interface ProcessLogEntry {
  detector: string;
  status: string;
  detail: string;
}

export interface EvalReportData {
  score: number;
  passed: boolean;
  summary_text?: string;
  dimension_scores?: Record<string, number>;
  issues?: EvalIssueItem[];
  strengths?: string[];
  process_log?: ProcessLogEntry[];
}

interface Props {
  result: EvalReportData;
  compact?: boolean;
}

const SEVERITY_CONFIG = {
  high: { label: "严重", color: "var(--error)", bg: "rgba(255,80,80,0.1)" },
  medium: { label: "中等", color: "var(--gold)", bg: "rgba(255,180,0,0.1)" },
  low: { label: "轻微", color: "var(--text-tertiary)", bg: "var(--bg-surface-2)" },
};

const DIMENSION_LABELS: Record<string, string> = {
  constraint_satisfaction: "约束满足",
  consistency: "一致性",
  knowledge_isolation: "知识隔离",
  repetition: "重复度",
  ai_flavor: "AI味",
  narrative_quality: "叙事质量",
  foreshadowing: "伏笔一致性",
  literary_quality: "文学质量",
};

export default function EvalReport({ result, compact }: Props) {
  const [showProcess, setShowProcess] = useState(false);

  const score = result.score;
  const scoreColor = score >= 80 ? "var(--jade)" : score >= 60 ? "var(--gold)" : "var(--error)";
  const issues = result.issues || [];
  const strengths = result.strengths || [];
  const dimensions = result.dimension_scores || {};
  const processLog = result.process_log || [];

  // Score ring (SVG circle)
  const ringSize = compact ? 48 : 72;
  const strokeWidth = compact ? 4 : 6;
  const radius = (ringSize - strokeWidth) / 2;
  const circumference = 2 * Math.PI * radius;
  const progress = (score / 100) * circumference;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: compact ? 8 : 14 }}>
      {/* Score ring + summary */}
      <div style={{ display: "flex", alignItems: "center", gap: 14 }}>
        <div style={{ position: "relative", width: ringSize, height: ringSize, flexShrink: 0 }}>
          <svg width={ringSize} height={ringSize} style={{ transform: "rotate(-90deg)" }}>
            <circle cx={ringSize / 2} cy={ringSize / 2} r={radius}
              fill="none" stroke="var(--border)" strokeWidth={strokeWidth} />
            <circle cx={ringSize / 2} cy={ringSize / 2} r={radius}
              fill="none" stroke={scoreColor} strokeWidth={strokeWidth}
              strokeDasharray={circumference} strokeDashoffset={circumference - progress}
              strokeLinecap="round" style={{ transition: "stroke-dashoffset 0.8s ease" }} />
          </svg>
          <div style={{
            position: "absolute", inset: 0, display: "flex", alignItems: "center", justifyContent: "center",
            fontSize: compact ? 14 : 20, fontWeight: 700, fontFamily: "var(--font-mono)", color: scoreColor,
          }}>
            {score}
          </div>
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontWeight: 600, fontSize: compact ? 13 : 15, color: "var(--text-primary)", marginBottom: 4 }}>
            {score >= 80 ? "质量优秀" : score >= 60 ? "基本通过" : "需要修改"}
            {result.passed !== undefined && (
              <span style={{ marginLeft: 8, fontSize: 11, padding: "1px 8px", borderRadius: 10,
                background: result.passed ? "var(--jade-subtle)" : "rgba(255,80,80,0.1)",
                color: result.passed ? "var(--jade)" : "var(--error)" }}>
                {result.passed ? "通过" : "未通过"}
              </span>
            )}
          </div>
          {result.summary_text && (
            <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.6 }}>
              {result.summary_text.length > 200 ? result.summary_text.slice(0, 200) + "..." : result.summary_text}
            </div>
          )}
        </div>
      </div>

      {/* Dimension score bars */}
      {Object.keys(dimensions).length > 0 && (
        <div>
          <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-tertiary)", marginBottom: 6, letterSpacing: 1 }}>
            分维度评分
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            {Object.entries(dimensions).map(([dim, dimScore]) => {
              const dimColor = dimScore >= 80 ? "var(--jade)" : dimScore >= 60 ? "var(--gold)" : "var(--error)";
              return (
                <div key={dim} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span style={{ fontSize: 11, color: "var(--text-secondary)", width: 72, flexShrink: 0, textAlign: "right" }}>
                    {DIMENSION_LABELS[dim] || dim}
                  </span>
                  <div style={{ flex: 1, height: 6, borderRadius: 3, background: "var(--border)", overflow: "hidden" }}>
                    <div style={{
                      height: "100%", width: `${dimScore}%`, borderRadius: 3,
                      background: dimColor, transition: "width 0.5s ease",
                    }} />
                  </div>
                  <span style={{ fontSize: 10, fontFamily: "var(--font-mono)", color: dimColor, width: 28, flexShrink: 0 }}>
                    {dimScore}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}

      {/* Issues */}
      {issues.length > 0 && (
        <div>
          <div style={{ fontSize: 11, fontWeight: 600, color: "var(--text-tertiary)", marginBottom: 6, letterSpacing: 1 }}>
            发现问题 ({issues.length})
          </div>
          {(compact ? issues.slice(0, 3) : issues).map((issue, i) => {
            const sev = SEVERITY_CONFIG[issue.severity] || SEVERITY_CONFIG.low;
            return (
              <div key={i} style={{
                padding: "8px 12px", borderRadius: 6, marginBottom: 4,
                background: sev.bg, borderLeft: `3px solid ${sev.color}`,
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 2 }}>
                  <span style={{ fontSize: 10, padding: "1px 6px", borderRadius: 4, fontWeight: 600, background: sev.bg, color: sev.color }}>
                    {sev.label}
                  </span>
                  <span style={{ fontSize: 10, color: "var(--text-tertiary)" }}>{issue.type}</span>
                </div>
                <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5 }}>{issue.description}</div>
                {issue.suggestion && (
                  <div style={{ fontSize: 11, color: "var(--jade)", marginTop: 4 }}>建议：{issue.suggestion}</div>
                )}
              </div>
            );
          })}
          {compact && issues.length > 3 && (
            <div style={{ fontSize: 11, color: "var(--text-tertiary)", textAlign: "center" }}>
              还有 {issues.length - 3} 个问题...
            </div>
          )}
        </div>
      )}

      {/* Strengths */}
      {strengths.length > 0 && (
        <div style={{ padding: "8px 12px", background: "var(--jade-subtle)", borderRadius: 8 }}>
          <div style={{ fontSize: 11, fontWeight: 600, color: "var(--jade)", marginBottom: 4, letterSpacing: 1 }}>优点</div>
          {strengths.map((s, i) => (
            <div key={i} style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.6, paddingLeft: 8 }}>
              + {s}
            </div>
          ))}
        </div>
      )}

      {/* Process log (collapsible) */}
      {processLog.length > 0 && (
        <div>
          <button
            onClick={() => setShowProcess(!showProcess)}
            style={{
              fontSize: 11, color: "var(--text-tertiary)", background: "none", border: "none",
              cursor: "pointer", padding: "4px 0", textDecoration: "underline",
            }}
          >
            {showProcess ? "收起检测过程" : "查看检测过程"}
          </button>
          {showProcess && (
            <div style={{ marginTop: 4, padding: "8px 12px", background: "var(--bg-surface-2)", borderRadius: 6 }}>
              {processLog.map((log, i) => (
                <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, padding: "3px 0", fontSize: 11 }}>
                  <span style={{
                    width: 6, height: 6, borderRadius: "50%", flexShrink: 0,
                    background: log.status === "通过" || log.status === "done" ? "var(--jade)" : log.status === "error" ? "var(--error)" : "var(--gold)",
                  }} />
                  <span style={{ color: "var(--text-secondary)", fontWeight: 500, width: 80, flexShrink: 0 }}>{log.detector}</span>
                  <span style={{ color: "var(--text-tertiary)" }}>{log.status}</span>
                  <span style={{ color: "var(--text-tertiary)", flex: 1, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{log.detail}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
