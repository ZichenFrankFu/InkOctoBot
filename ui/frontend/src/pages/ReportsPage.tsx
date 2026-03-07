import React, { useEffect, useState, useMemo } from "react";
import { apiGet } from "../api/client";
import ResizeHandle from "../components/ResizeHandle";
import { useResizable } from "../hooks/useResizable";

interface ReportFile {
  name: string;
  size: number;
}

interface ReportGroup {
  name: string;
  files: ReportFile[];
}

interface ReportsResponse {
  groups: ReportGroup[];
}

export default function ReportsPage() {
  const [groups, setGroups] = useState<ReportGroup[]>([]);
  const [activeReport, setActiveReport] = useState<string | null>(null);
  const [content, setContent] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [contentLoading, setContentLoading] = useState(false);
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());

  const { size: panelWidth, isDragging, handleProps } = useResizable({
    direction: "horizontal",
    initialSize: 250,
    minSize: 180,
    maxSize: 500,
  });

  useEffect(() => {
    apiGet<ReportsResponse>("/api/reports")
      .then((res) => {
        setGroups(res.groups || []);
        // Auto-open first report
        if (res.groups && res.groups.length > 0) {
          const firstGroup = res.groups[0];
          if (firstGroup.files && firstGroup.files.length > 0) {
            openReport(firstGroup.files[0].name);
          }
        }
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  const openReport = async (name: string) => {
    setActiveReport(name);
    setContentLoading(true);
    try {
      const res = await apiGet<{ content: string }>(`/api/reports/${encodeURIComponent(name)}`);
      setContent(res.content);
    } catch (e) {
      setContent("Failed to load report: " + String(e));
    } finally {
      setContentLoading(false);
    }
  };

  const toggleGroup = (groupName: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(groupName)) {
        next.delete(groupName);
      } else {
        next.add(groupName);
      }
      return next;
    });
  };

  const formatSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const getIcon = (name: string): string => {
    if (name.endsWith(".md")) return "\u{1F4C4}";
    if (name.endsWith(".html")) return "\u{1F310}";
    if (name.endsWith(".txt")) return "\u{1F4DD}";
    if (name.endsWith(".json")) return "\u{1F4CB}";
    return "\u{1F4CE}";
  };

  const totalFiles = useMemo(() => groups.reduce((sum, g) => sum + g.files.length, 0), [groups]);

  if (loading) {
    return (
      <div className="loading" style={{ paddingTop: 120 }}>
        <div className="loading-spinner" />
        Loading reports...
      </div>
    );
  }

  if (totalFiles === 0) {
    return (
      <div className="page-container">
        <div className="page-header">
          <h2>Reports</h2>
          <p>View generated analysis reports</p>
        </div>
        <div className="empty-state">
          <div className="empty-icon">{"\u{1F4ED}"}</div>
          <h4>No Reports</h4>
          <p>No analysis reports have been generated yet.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="page-container">
      <div className="page-header">
        <h2>Reports</h2>
        <p>{totalFiles} report files</p>
      </div>

      <div style={{ display: "flex", minHeight: 600 }}>
        {/* Left panel - file list */}
        <div
          className="card"
          style={{
            width: panelWidth,
            flexShrink: 0,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
          }}
        >
          <div className="card-header">
            <h3>Files</h3>
          </div>
          <div style={{ flex: 1, overflowY: "auto" }}>
            {groups.map((group) => (
              <div key={group.name}>
                <div
                  onClick={() => toggleGroup(group.name)}
                  style={{
                    padding: "8px 14px",
                    fontSize: 11,
                    fontWeight: 600,
                    color: "var(--text-secondary)",
                    background: "var(--bg-tertiary)",
                    cursor: "pointer",
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    userSelect: "none",
                    letterSpacing: "0.3px",
                    textTransform: "uppercase",
                  }}
                >
                  <span
                    style={{
                      display: "inline-block",
                      transition: "transform 0.15s",
                      transform: collapsedGroups.has(group.name) ? "rotate(-90deg)" : "rotate(0deg)",
                      fontSize: 10,
                    }}
                  >
                    {"\u25BC"}
                  </span>
                  {group.name}
                  <span style={{ marginLeft: "auto", fontSize: 10, opacity: 0.6 }}>
                    {group.files.length}
                  </span>
                </div>

                {!collapsedGroups.has(group.name) &&
                  group.files.map((file) => (
                    <div
                      key={`${group.name}/${file.name}`}
                      className={`report-list-item${activeReport === file.name ? " active" : ""}`}
                      onClick={() => openReport(file.name)}
                      style={{
                        display: "flex",
                        alignItems: "center",
                        gap: 8,
                        padding: "8px 14px 8px 24px",
                        cursor: "pointer",
                        fontSize: 13,
                        color: activeReport === file.name ? "var(--accent)" : "var(--text-primary)",
                        background: activeReport === file.name ? "var(--accent-muted)" : "transparent",
                        borderLeft: activeReport === file.name ? "2px solid var(--accent)" : "2px solid transparent",
                        transition: "background 0.15s, border-color 0.15s",
                      }}
                    >
                      <span style={{ flexShrink: 0 }}>{getIcon(file.name)}</span>
                      <span
                        style={{
                          flex: 1,
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                          whiteSpace: "nowrap",
                        }}
                        title={file.name}
                      >
                        {file.name}
                      </span>
                      <span style={{ fontSize: 10, color: "var(--text-secondary)", flexShrink: 0 }}>
                        {formatSize(file.size)}
                      </span>
                    </div>
                  ))}
              </div>
            ))}
          </div>
        </div>

        {/* Resize handle */}
        <ResizeHandle direction="horizontal" isDragging={isDragging} {...handleProps} />

        {/* Right panel - content viewer */}
        <div className="card" style={{ flex: 1, display: "flex", flexDirection: "column", marginLeft: 0 }}>
          <div className="card-header">
            <h3>{activeReport || "Select a report"}</h3>
          </div>
          <div
            className="card-body"
            style={{
              flex: 1,
              overflowY: "auto",
              maxHeight: "calc(100vh - 220px)",
            }}
          >
            {contentLoading ? (
              <div className="loading">
                <div className="loading-spinner" />
                Loading...
              </div>
            ) : !activeReport ? (
              <div className="empty-state">
                <h4>Select a report from the file list</h4>
              </div>
            ) : (
              <pre
                className="report-content"
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 13,
                  lineHeight: 1.6,
                  whiteSpace: "pre-wrap",
                  wordBreak: "break-word",
                  color: "var(--text-primary)",
                  margin: 0,
                }}
              >
                {content}
              </pre>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
