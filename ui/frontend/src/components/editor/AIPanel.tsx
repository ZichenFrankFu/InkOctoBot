import React, { useState } from "react";
import type { PipelineStatus, EvalResult } from "../../api/types";
import { apiPost } from "../../api/client";

interface Props {
  projectId: string;
  chapterId?: string;
  selectedText?: string;
}

type Tab = "outline" | "inspire" | "rewrite" | "evaluate";

const TAB_LABELS: Record<Tab, string> = {
  outline: "\u5927\u7EB2",
  inspire: "\u7075\u611F",
  rewrite: "\u91CD\u5199",
  evaluate: "\u8BC4\u4F30",
};

export default function AIPanel({ projectId, chapterId, selectedText }: Props) {
  const [activeTab, setActiveTab] = useState<Tab>("outline");
  const [synopsis, setSynopsis] = useState("");
  const [pipelineSteps, setPipelineSteps] = useState<PipelineStatus[]>([]);
  const [pipelineRunning, setPipelineRunning] = useState(false);
  const [rewriteResult, setRewriteResult] = useState("");
  const [rewriting, setRewriting] = useState(false);
  const [evalResult, setEvalResult] = useState<EvalResult | null>(null);
  const [evaluating, setEvaluating] = useState(false);

  const runPipeline = async () => {
    if (!chapterId) return;
    setPipelineRunning(true);
    setPipelineSteps([
      { step: "Scene Planning", status: "running" },
      { step: "Scene Direction", status: "pending" },
      { step: "Actor Performance", status: "pending" },
      { step: "Editor Styling", status: "pending" },
      { step: "Evaluation", status: "pending" },
    ]);
    try {
      await apiPost(`/api/projects/${projectId}/chapters/${chapterId}/generate`, {
        synopsis,
      });
    } catch {
      // handle error
    } finally {
      setPipelineRunning(false);
    }
  };

  const runRewrite = async () => {
    if (!selectedText || !chapterId) return;
    setRewriting(true);
    try {
      const res = await apiPost<{ text: string }>(
        `/api/projects/${projectId}/chapters/${chapterId}/rewrite`,
        { text: selectedText }
      );
      setRewriteResult(res.text);
    } catch {
      setRewriteResult("Rewrite failed.");
    } finally {
      setRewriting(false);
    }
  };

  const runEvaluation = async () => {
    if (!chapterId) return;
    setEvaluating(true);
    try {
      const res = await apiPost<EvalResult>(
        `/api/projects/${projectId}/chapters/${chapterId}/evaluate`,
        {}
      );
      setEvalResult(res);
    } catch {
      setEvalResult(null);
    } finally {
      setEvaluating(false);
    }
  };

  const stepColor = (status: PipelineStatus["status"]) => {
    switch (status) {
      case "done":
        return "var(--accent)";
      case "running":
        return "#facc15";
      case "error":
        return "var(--danger)";
      default:
        return "var(--text-secondary)";
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      {/* Tab bar */}
      <div
        style={{
          display: "flex",
          borderBottom: "1px solid var(--border)",
          background: "var(--bg-secondary)",
          flexShrink: 0,
        }}
      >
        {(Object.keys(TAB_LABELS) as Tab[]).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            style={{
              flex: 1,
              padding: "10px 8px",
              border: "none",
              borderBottom: activeTab === tab ? "2px solid var(--accent)" : "2px solid transparent",
              background: "transparent",
              color: activeTab === tab ? "var(--accent)" : "var(--text-secondary)",
              fontSize: 13,
              fontWeight: activeTab === tab ? 600 : 400,
              cursor: "pointer",
              transition: "color 0.15s, border-color 0.15s",
            }}
          >
            {TAB_LABELS[tab]}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div style={{ flex: 1, overflowY: "auto", padding: 16 }}>
        {/* Outline tab */}
        {activeTab === "outline" && (
          <div>
            <label style={{ fontSize: 12, color: "var(--text-secondary)", display: "block", marginBottom: 8 }}>
              Chapter Synopsis
            </label>
            <textarea
              value={synopsis}
              onChange={(e) => setSynopsis(e.target.value)}
              placeholder="Describe this chapter's plot outline..."
              style={{
                width: "100%",
                minHeight: 200,
                padding: "12px 14px",
                border: "1px solid var(--border)",
                borderRadius: 6,
                background: "var(--bg-primary)",
                color: "var(--text-primary)",
                fontSize: 13,
                lineHeight: 1.7,
                resize: "vertical",
                outline: "none",
                fontFamily: "inherit",
                boxSizing: "border-box",
              }}
            />
          </div>
        )}

        {/* Inspire tab - Creative Writing Pipeline */}
        {activeTab === "inspire" && (
          <div>
            <div style={{ marginBottom: 16 }}>
              <button
                className="btn-primary"
                onClick={runPipeline}
                disabled={pipelineRunning || !chapterId}
                style={{ fontSize: 13, padding: "8px 20px" }}
              >
                {pipelineRunning ? "Running Pipeline..." : "Start Creative Writing Pipeline"}
              </button>
            </div>

            {pipelineSteps.length > 0 && (
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {pipelineSteps.map((step, i) => (
                  <div
                    key={i}
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 12,
                      padding: "10px 14px",
                      borderRadius: 6,
                      background: "var(--bg-secondary)",
                      border: "1px solid var(--border)",
                    }}
                  >
                    <div
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: "50%",
                        background: stepColor(step.status),
                        flexShrink: 0,
                        boxShadow:
                          step.status === "running" ? `0 0 6px ${stepColor(step.status)}` : "none",
                      }}
                    />
                    <span style={{ flex: 1, fontSize: 13, color: "var(--text-primary)" }}>
                      {step.step}
                    </span>
                    <span
                      style={{
                        fontSize: 11,
                        color: stepColor(step.status),
                        fontWeight: 500,
                        textTransform: "capitalize",
                      }}
                    >
                      {step.status}
                    </span>
                    {step.progress != null && (
                      <div
                        style={{
                          width: 60,
                          height: 4,
                          background: "var(--bg-tertiary)",
                          borderRadius: 2,
                          overflow: "hidden",
                        }}
                      >
                        <div
                          style={{
                            width: `${step.progress}%`,
                            height: "100%",
                            background: stepColor(step.status),
                            transition: "width 0.3s",
                          }}
                        />
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Rewrite tab */}
        {activeTab === "rewrite" && (
          <div>
            <label style={{ fontSize: 12, color: "var(--text-secondary)", display: "block", marginBottom: 8 }}>
              Selected Text
            </label>
            {selectedText ? (
              <div
                style={{
                  padding: "12px 14px",
                  borderRadius: 6,
                  background: "var(--bg-secondary)",
                  border: "1px solid var(--border)",
                  fontSize: 13,
                  lineHeight: 1.7,
                  color: "var(--text-primary)",
                  marginBottom: 12,
                  maxHeight: 150,
                  overflowY: "auto",
                  whiteSpace: "pre-wrap",
                }}
              >
                {selectedText}
              </div>
            ) : (
              <div
                style={{
                  padding: 20,
                  textAlign: "center",
                  color: "var(--text-secondary)",
                  fontSize: 13,
                  marginBottom: 12,
                }}
              >
                Select text in the editor to rewrite.
              </div>
            )}

            <button
              className="btn-primary"
              onClick={runRewrite}
              disabled={!selectedText || rewriting}
              style={{
                fontSize: 13,
                padding: "8px 20px",
                marginBottom: 16,
                opacity: !selectedText ? 0.5 : 1,
              }}
            >
              {rewriting ? "Rewriting..." : "Rewrite"}
            </button>

            {rewriteResult && (
              <div>
                <label style={{ fontSize: 12, color: "var(--text-secondary)", display: "block", marginBottom: 8 }}>
                  Rewrite Result
                </label>
                <div
                  style={{
                    padding: "12px 14px",
                    borderRadius: 6,
                    background: "var(--bg-secondary)",
                    border: "1px solid var(--accent)",
                    fontSize: 13,
                    lineHeight: 1.7,
                    color: "var(--text-primary)",
                    whiteSpace: "pre-wrap",
                  }}
                >
                  {rewriteResult}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Evaluate tab */}
        {activeTab === "evaluate" && (
          <div>
            <button
              className="btn-primary"
              onClick={runEvaluation}
              disabled={!chapterId || evaluating}
              style={{ fontSize: 13, padding: "8px 20px", marginBottom: 16 }}
            >
              {evaluating ? "Evaluating..." : "Run Evaluation"}
            </button>

            {evalResult && (
              <div>
                <div
                  style={{
                    display: "flex",
                    alignItems: "center",
                    gap: 12,
                    marginBottom: 16,
                    padding: "10px 14px",
                    borderRadius: 6,
                    background: evalResult.passed
                      ? "rgba(74, 222, 128, 0.1)"
                      : "rgba(248, 113, 113, 0.1)",
                    border: `1px solid ${evalResult.passed ? "#4ade80" : "#f87171"}`,
                  }}
                >
                  <span style={{ fontSize: 24 }}>{evalResult.passed ? "\u2705" : "\u274C"}</span>
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 600, color: "var(--text-primary)" }}>
                      Score: {evalResult.score}/100
                    </div>
                    <div style={{ fontSize: 12, color: "var(--text-secondary)" }}>
                      {evalResult.passed ? "Passed" : "Needs revision"} - {evalResult.issues.length} issues
                    </div>
                  </div>
                </div>

                <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                  {evalResult.issues.map((issue, i) => (
                    <div
                      key={i}
                      style={{
                        padding: "10px 14px",
                        borderRadius: 6,
                        background: "var(--bg-secondary)",
                        borderLeft: `3px solid ${
                          issue.severity === "high"
                            ? "#f87171"
                            : issue.severity === "medium"
                            ? "#facc15"
                            : "#4ade80"
                        }`,
                      }}
                    >
                      <div
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 8,
                          marginBottom: 4,
                        }}
                      >
                        <span
                          style={{
                            fontSize: 10,
                            padding: "1px 6px",
                            borderRadius: 6,
                            background:
                              issue.severity === "high"
                                ? "rgba(248,113,113,0.15)"
                                : issue.severity === "medium"
                                ? "rgba(250,204,21,0.15)"
                                : "rgba(74,222,128,0.15)",
                            color:
                              issue.severity === "high"
                                ? "#f87171"
                                : issue.severity === "medium"
                                ? "#facc15"
                                : "#4ade80",
                            textTransform: "uppercase",
                            fontWeight: 600,
                          }}
                        >
                          {issue.severity}
                        </span>
                        <span style={{ fontSize: 12, fontWeight: 600, color: "var(--text-primary)" }}>
                          {issue.type}
                        </span>
                      </div>
                      <div style={{ fontSize: 12, color: "var(--text-secondary)", lineHeight: 1.5 }}>
                        {issue.description}
                      </div>
                      {issue.suggestion && (
                        <div
                          style={{
                            marginTop: 6,
                            fontSize: 11,
                            color: "var(--accent)",
                            fontStyle: "italic",
                          }}
                        >
                          Suggestion: {issue.suggestion}
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
