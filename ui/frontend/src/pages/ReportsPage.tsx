import React, { useEffect, useState } from "react";
import { apiGet } from "../api/client";

interface ReportItem {
  path: string;
  size: number;
}

export default function ReportsPage() {
  const [items, setItems] = useState<ReportItem[]>([]);
  const [active, setActive] = useState<string | null>(null);
  const [content, setContent] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [contentLoading, setContentLoading] = useState(false);

  useEffect(() => {
    apiGet<{ items: ReportItem[] }>("/api/reports")
      .then((res) => {
        const filtered = res.items.filter(
          (x) =>
            x.path.endsWith(".md") ||
            x.path.endsWith(".html") ||
            x.path.endsWith(".txt")
        );
        setItems(filtered);
        // Auto-open the first report
        if (filtered.length > 0 && !active) {
          openReport(filtered[0].path);
        }
      })
      .catch(console.error)
      .finally(() => setLoading(false));
  }, []);

  async function openReport(path: string) {
    setActive(path);
    setContentLoading(true);
    try {
      const res = await apiGet<{ content: string }>(
        `/api/reports/read?path=${encodeURIComponent(path)}`
      );
      setContent(res.content);
    } catch (e) {
      setContent("加载失败: " + String(e));
    } finally {
      setContentLoading(false);
    }
  }

  function formatSize(bytes: number): string {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function getIcon(path: string): string {
    if (path.endsWith(".md")) return "📄";
    if (path.endsWith(".html")) return "🌐";
    if (path.endsWith(".txt")) return "📝";
    return "📎";
  }

  function getFileName(path: string): string {
    return path.split("/").pop() || path;
  }

  function getFolder(path: string): string {
    const parts = path.split("/");
    if (parts.length > 1) {
      return parts.slice(0, -1).join("/");
    }
    return "";
  }

  // Group items by folder
  const grouped = React.useMemo(() => {
    const groups: Record<string, ReportItem[]> = {};
    for (const item of items) {
      const folder = getFolder(item.path) || "(根目录)";
      if (!groups[folder]) groups[folder] = [];
      groups[folder].push(item);
    }
    return groups;
  }, [items]);

  return (
    <div className="page-container">
      <div className="page-header">
        <h2>趋势报告</h2>
        <p>查看已生成的数据分析报告</p>
      </div>

      {loading ? (
        <div className="loading">
          <div className="loading-spinner" />
          加载报告列表…
        </div>
      ) : items.length === 0 ? (
        <div className="empty-state">
          <div className="empty-icon">📭</div>
          <h4>暂无报告</h4>
          <p>
            还没有生成任何分析报告。请在命令行运行分析脚本生成报告后刷新此页面。
          </p>
        </div>
      ) : (
        <div style={{ display: "flex", gap: 20, minHeight: 600 }}>
          {/* ── File List ── */}
          <div
            className="card"
            style={{
              width: 320,
              flexShrink: 0,
              display: "flex",
              flexDirection: "column",
            }}
          >
            <div className="card-header">
              <h3>报告文件</h3>
              <p>{items.length} 个文件</p>
            </div>
            <div
              style={{
                flex: 1,
                overflowY: "auto",
              }}
            >
              {Object.entries(grouped).map(([folder, files]) => (
                <div key={folder}>
                  {Object.keys(grouped).length > 1 && (
                    <div
                      style={{
                        padding: "8px 16px 4px",
                        fontSize: 11,
                        fontWeight: 600,
                        color: "var(--ink-400)",
                        textTransform: "uppercase",
                        letterSpacing: "0.5px",
                        background: "var(--ink-50)",
                      }}
                    >
                      📁 {folder}
                    </div>
                  )}
                  {files.map((item) => (
                    <div
                      key={item.path}
                      className={`report-list-item${
                        active === item.path ? " active" : ""
                      }`}
                      onClick={() => openReport(item.path)}
                    >
                      <span className="report-icon">{getIcon(item.path)}</span>
                      <span className="report-name" title={item.path}>
                        {getFileName(item.path)}
                      </span>
                      <span className="report-size">{formatSize(item.size)}</span>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </div>

          {/* ── Content Viewer ── */}
          <div className="card" style={{ flex: 1, display: "flex", flexDirection: "column" }}>
            <div className="card-header">
              <h3>{active ? getFileName(active) : "选择一个报告"}</h3>
              {active && (
                <p style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>{active}</p>
              )}
            </div>
            <div
              className="card-body"
              style={{
                flex: 1,
                overflowY: "auto",
                maxHeight: "calc(100vh - 200px)",
              }}
            >
              {contentLoading ? (
                <div className="loading">
                  <div className="loading-spinner" />
                  加载中…
                </div>
              ) : !active ? (
                <div className="empty-state">
                  <div className="empty-icon">👈</div>
                  <h4>选择左侧报告查看内容</h4>
                </div>
              ) : (
                <pre className="report-content">{content}</pre>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
