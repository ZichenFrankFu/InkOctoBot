/**
 * TruthReviewPage — per-chapter audit gate UI (P1).
 *
 * The post-commit settlement / Truth Files pipeline tags chapters
 * with audit_status. audit_failed means the AI text contradicts
 * established state (character ledger / pending hooks / etc.). This
 * page surfaces those failures so the user can decide:
 *  - go back and edit the chapter
 *  - override-audit (accept the contradiction explicitly)
 *  - rollback to a prior version
 *
 * Previously there was no UI for this — the chapters table sat with
 * audit_failed forever and the user had to know to run state-review
 * endpoints from a REPL.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiGet, apiPost } from "../api/client";
import type { ChapterAuditRow } from "../api/typesExt";
import { useToast } from "../components/shared/Toast";


const STATUS_LABEL: Record<string, string> = {
  audit_pending:    "待审",
  audit_passed:     "通过",
  audit_failed:     "失败",
  audit_overridden: "已覆盖",
};


const STATUS_COLOR: Record<string, string> = {
  audit_pending:    "var(--text-tertiary)",
  audit_passed:     "var(--success)",
  audit_failed:     "var(--danger)",
  audit_overridden: "var(--gold)",
};


const SEVERITY_COLOR: Record<string, string> = {
  high:   "var(--danger)",
  medium: "var(--gold)",
  low:    "var(--text-tertiary)",
};


interface Props {
  projectId: string;
  onOpenChapter?: (chapterId: string) => void;
}


export default function TruthReviewPage({ projectId, onOpenChapter }: Props) {
  const { toast } = useToast();
  const [rows, setRows] = useState<ChapterAuditRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [filter, setFilter] = useState<"all" | "failed" | "passed">("failed");

  const refresh = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    try {
      const r = await apiGet<{ items: ChapterAuditRow[] }>(
        `/api/learning/chapter-audits?project_id=${encodeURIComponent(projectId)}`,
      );
      setRows(r.items || []);
    } catch (e: any) {
      toast(`加载失败: ${e.message}`, "error");
    } finally {
      setLoading(false);
    }
  }, [projectId, toast]);

  useEffect(() => { refresh(); }, [refresh]);

  const counts = useMemo(() => {
    const out: Record<string, number> = {
      audit_pending: 0, audit_passed: 0,
      audit_failed: 0, audit_overridden: 0,
    };
    for (const r of rows) {
      const s = r.audit_status || "audit_pending";
      out[s] = (out[s] || 0) + 1;
    }
    return out;
  }, [rows]);

  const filtered = useMemo(() => {
    if (filter === "all") return rows;
    if (filter === "failed") return rows.filter(r => r.audit_status === "audit_failed");
    if (filter === "passed") return rows.filter(r =>
      r.audit_status === "audit_passed" || r.audit_status === "audit_overridden");
    return rows;
  }, [filter, rows]);

  const override = useCallback(async (chapter_id: string) => {
    if (!window.confirm(
      "覆盖意味着你确认这些 audit 问题不是 bug（例如故意设置的矛盾）。\n" +
      "覆盖后章节会被视为已通过。继续？"
    )) return;
    try {
      await apiPost("/api/learning/override-audit", { chapter_id });
      toast("已覆盖", "success");
      refresh();
    } catch (e: any) {
      toast(`失败: ${e.message}`, "error");
    }
  }, [refresh, toast]);

  if (!projectId) {
    return <div style={{ padding: 24 }}>请先选择项目</div>;
  }

  return (
    <div className="page" style={{ padding: 16 }}>
      <header style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <div>
          <h2 style={{ margin: 0 }}> Truth State 审阅</h2>
          <p style={{ color: "var(--text-tertiary)", fontSize: 12, margin: "4px 0 0" }}>
            post-commit Truth Files settlement 检查每章是否与已建立的角色/世界状态矛盾。
            audit_failed 的章节需要你审阅并决定：回编辑器修改 / 覆盖通过 / 回退版本。
          </p>
        </div>
        <button className="btn" onClick={refresh} disabled={loading}>刷新</button>
      </header>

      {/* Status counts */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 8, marginBottom: 16 }}>
        {(["audit_pending", "audit_passed", "audit_failed", "audit_overridden"] as const).map(st => (
          <div
            key={st}
            className="card"
            style={{
              padding: 10, textAlign: "center", cursor: "pointer",
              borderColor: filter === (st === "audit_failed" ? "failed" : st === "audit_passed" ? "passed" : "all") ? STATUS_COLOR[st] : undefined,
            }}
            onClick={() => {
              if (st === "audit_failed") setFilter("failed");
              else if (st === "audit_passed" || st === "audit_overridden") setFilter("passed");
              else setFilter("all");
            }}
          >
            <div style={{ fontSize: 11, color: STATUS_COLOR[st] }}>{STATUS_LABEL[st]}</div>
            <div style={{ fontSize: 22, fontWeight: 600, marginTop: 2 }}>{counts[st] || 0}</div>
          </div>
        ))}
      </div>

      {/* Filter */}
      <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
        {(["all", "failed", "passed"] as const).map(f => (
          <button
            key={f}
            className={filter === f ? "btn primary" : "btn"}
            onClick={() => setFilter(f)}
          >
            {f === "all" ? "全部" : f === "failed" ? "仅失败" : "已通过/已覆盖"}
          </button>
        ))}
      </div>

      {/* Chapter rows */}
      {filtered.length === 0 ? (
        <div className="card" style={{ padding: 24, textAlign: "center", color: "var(--text-tertiary)" }}>
          没有匹配的章节
        </div>
      ) : (
        filtered.map(r => (
          <div key={r.chapter_id} className="card" style={{ padding: 12, marginBottom: 8 }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <h4 style={{ margin: 0 }}>
                    第 {r.chapter_num} 章 · {r.title || "(无标题)"}
                  </h4>
                  <span style={{
                    fontSize: 11, color: STATUS_COLOR[r.audit_status] || "var(--text-tertiary)",
                    padding: "2px 8px", border: `1px solid ${STATUS_COLOR[r.audit_status] || "var(--border)"}`,
                    borderRadius: 12,
                  }}>
                    {STATUS_LABEL[r.audit_status] || r.audit_status}
                  </span>
                </div>
                <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 4 }}>
                  chapter_id: <code>{r.chapter_id}</code> · 更新于 {r.updated_at}
                </div>

                {r.audit_issues && r.audit_issues.length > 0 && (
                  <ul style={{ margin: "10px 0 0", paddingLeft: 16 }}>
                    {r.audit_issues.map((iss, i) => (
                      <li key={i} style={{ fontSize: 12, marginBottom: 4 }}>
                        <span style={{
                          color: SEVERITY_COLOR[iss.severity] || "var(--text-tertiary)",
                          fontWeight: 600, marginRight: 6,
                        }}>
                          [{iss.severity || "?"}]
                        </span>
                        {iss.type && <code style={{ fontSize: 11, marginRight: 6 }}>{iss.type}</code>}
                        {iss.description}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div style={{ display: "flex", gap: 6, flexShrink: 0 }}>
                {onOpenChapter && (
                  <button className="btn" onClick={() => onOpenChapter(r.chapter_id)}>
                    去编辑器
                  </button>
                )}
                {r.audit_status === "audit_failed" && (
                  <button className="btn warn" onClick={() => override(r.chapter_id)}>
                    覆盖通过
                  </button>
                )}
              </div>
            </div>
          </div>
        ))
      )}
    </div>
  );
}
