/**
 * NotificationFeed — improved notifications surface (P1).
 *
 * Polls the existing /api/notifications endpoint and surfaces actions
 * (mark-read / acknowledge) directly. Supports deep-link callbacks so
 * the parent can route to the right page when the user clicks a row.
 */
import React, { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost } from "../../api/client";
import { useToast } from "./Toast";


interface NotificationItem {
  notification_id: string;
  project_id: string;
  category: string;
  severity: "high" | "medium" | "low";
  title: string;
  body: string;
  status: "unread" | "read" | "acknowledged";
  created_at: string;
  meta?: Record<string, any>;
}


const SEVERITY_COLOR: Record<string, string> = {
  high:   "var(--danger)",
  medium: "var(--gold)",
  low:    "var(--text-tertiary)",
};


const CATEGORY_DEEPLINK: Record<string, string> = {
  edit_learning_batch:   "preferences",
  domain_knowledge:      "domain-learning",
  audit_failed:          "truth-review",
  post_commit_failed:    "editor",
  truth_settlement:      "truth-review",
};


interface Props {
  projectId: string;
  onNavigate?: (page: string, ctx?: any) => void;
  unreadOnly?: boolean;
  limit?: number;
}


export default function NotificationFeed({
  projectId, onNavigate, unreadOnly = false, limit = 50,
}: Props) {
  const { toast } = useToast();
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    try {
      const r = await apiGet<{ notifications: NotificationItem[] }>(
        `/api/notifications?project_id=${encodeURIComponent(projectId)}` +
        `&unread_only=${unreadOnly}&limit=${limit}`,
      );
      setItems(r.notifications || []);
    } catch (e: any) {
      toast(`通知加载失败: ${e.message}`, "error");
    } finally {
      setLoading(false);
    }
  }, [projectId, unreadOnly, limit, toast]);

  useEffect(() => {
    refresh();
    const id = window.setInterval(refresh, 12000);
    return () => window.clearInterval(id);
  }, [refresh]);

  const markRead = useCallback(async (id: string) => {
    try {
      await apiPost(`/api/notifications/${id}/mark-read`, {});
      refresh();
    } catch (_) { /* swallow */ }
  }, [refresh]);

  const acknowledge = useCallback(async (id: string) => {
    try {
      await apiPost(`/api/notifications/${id}/acknowledge`, {});
      refresh();
    } catch (_) { /* swallow */ }
  }, [refresh]);

  const openDeepLink = useCallback((n: NotificationItem) => {
    const page = CATEGORY_DEEPLINK[n.category];
    if (page && onNavigate) onNavigate(page, n.meta);
    if (n.status === "unread") markRead(n.notification_id);
  }, [onNavigate, markRead]);

  const unreadCount = items.filter(i => i.status === "unread").length;

  return (
    <div style={{ padding: 12 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
        <h3 style={{ margin: 0, fontSize: 14 }}>
           通知 {unreadCount > 0 && <span style={{ color: "var(--danger)" }}>({unreadCount})</span>}
        </h3>
        <button className="btn-sm" onClick={refresh} disabled={loading}>
          {loading ? "..." : "↻"}
        </button>
      </div>

      {items.length === 0 ? (
        <p style={{ color: "var(--text-tertiary)", fontSize: 12, padding: 8 }}>
          无通知
        </p>
      ) : (
        <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
          {items.map(n => (
            <li
              key={n.notification_id}
              style={{
                padding: 8,
                marginBottom: 6,
                background: n.status === "unread" ? "var(--bg-surface-2)" : "var(--bg-surface)",
                borderLeft: `3px solid ${SEVERITY_COLOR[n.severity] || "var(--text-tertiary)"}`,
                borderRadius: 4,
                cursor: CATEGORY_DEEPLINK[n.category] ? "pointer" : "default",
                opacity: n.status === "acknowledged" ? 0.5 : 1,
              }}
              onClick={() => openDeepLink(n)}
            >
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 12, fontWeight: 600 }}>
                    {n.status === "unread" && "● "}
                    {n.title}
                  </div>
                  {n.body && (
                    <div style={{ fontSize: 11, color: "var(--text-tertiary)", marginTop: 2 }}>
                      {n.body.slice(0, 120)}{n.body.length > 120 ? "..." : ""}
                    </div>
                  )}
                  <div style={{ fontSize: 10, color: "var(--text-disabled)", marginTop: 4 }}>
                    {n.category} · {new Date(n.created_at).toLocaleString()}
                  </div>
                </div>
                {n.status !== "acknowledged" && (
                  <button
                    className="btn-sm"
                    onClick={(e) => { e.stopPropagation(); acknowledge(n.notification_id); }}
                    style={{ fontSize: 10, padding: "1px 4px", marginLeft: 6 }}
                    title="标记完成"
                  >
                    
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
