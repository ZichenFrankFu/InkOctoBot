/**
 * PreferencesPage — Part A visualization (P1).
 *
 * Shows the user what the edit-learning system has actually learned
 * about their style, and how close they are to the next batch
 * extraction. Without this surface they couldn't tell whether the
 * system was learning anything at all.
 */
import React, { useCallback, useEffect, useMemo, useState } from "react";
import { apiGet, apiPost, apiDelete } from "../api/client";
import type { UserStylePreference, EditObservation, PreferenceType } from "../api/typesExt";
import { useToast } from "../components/shared/Toast";


const PREF_TYPE_LABEL: Record<PreferenceType, string> = {
  style:   "风格偏好",
  content: "内容偏好",
  pacing:  "节奏偏好",
};


function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(100, value * 100));
  const color = value >= 0.5 ? "var(--success)"
              : value >= 0.3 ? "var(--accent)"
              : "var(--text-tertiary)";
  return (
    <div style={{ width: 80, height: 6, background: "var(--bg-surface-2)", borderRadius: 3, overflow: "hidden" }}>
      <div style={{ width: `${pct}%`, height: "100%", background: color }} />
    </div>
  );
}


interface Props {
  projectId: string;
}


export default function PreferencesPage({ projectId }: Props) {
  const { toast } = useToast();
  const [prefs, setPrefs] = useState<UserStylePreference[]>([]);
  const [observations, setObservations] = useState<EditObservation[]>([]);
  const [thresholdStatus, setThresholdStatus] = useState<{
    threshold: number;
    unconsumed: number;
    remaining: number;
    will_fire_on_next: boolean;
  } | null>(null);
  const [thresholdEdit, setThresholdEdit] = useState<number>(5);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    try {
      const [p, o, t] = await Promise.all([
        apiGet<{ items: UserStylePreference[] }>(
          `/api/learning/preferences?project_id=${encodeURIComponent(projectId)}`),
        apiGet<{ items: EditObservation[] }>(
          `/api/learning/observations?project_id=${encodeURIComponent(projectId)}`),
        apiGet<any>(
          `/api/learning/threshold-status?project_id=${encodeURIComponent(projectId)}`),
      ]);
      setPrefs(p.items || []);
      setObservations(o.items || []);
      setThresholdStatus(t);
      setThresholdEdit(t.threshold);
    } catch (e: any) {
      toast(`加载失败: ${e.message}`, "error");
    } finally {
      setLoading(false);
    }
  }, [projectId, toast]);

  useEffect(() => { refresh(); }, [refresh]);

  const grouped = useMemo(() => {
    const out: Record<PreferenceType, UserStylePreference[]> = {
      style: [], content: [], pacing: [],
    };
    for (const p of prefs) {
      const t = (p.preference_type as PreferenceType) || "style";
      (out[t] || out.style).push(p);
    }
    return out;
  }, [prefs]);

  const consumedCount = observations.filter(o => o.consumed === 1).length;
  const unconsumedCount = observations.filter(o => o.consumed === 0).length;

  const fireBatch = useCallback(async () => {
    if (!window.confirm("立即触发一次批量提取？会消耗 1 次 LLM 调用。")) return;
    try {
      // Reuses domain-learning's manual trigger which fires the batch.
      await apiPost("/api/domain-learning/suggest", { project_id: projectId });
      toast("已调度，几秒后刷新", "success");
      setTimeout(refresh, 3000);
    } catch (e: any) {
      toast(`失败: ${e.message}`, "error");
    }
  }, [projectId, refresh, toast]);

  const saveThreshold = useCallback(async () => {
    try {
      await apiPost("/api/learning/threshold", { threshold: thresholdEdit });
      toast(`阈值已保存为 ${thresholdEdit}`, "success");
      refresh();
    } catch (e: any) {
      toast(`保存失败: ${e.message}`, "error");
    }
  }, [thresholdEdit, refresh, toast]);

  // 用户偏好·机制4: 确认 gate — 只有 is_confirmed=1 的偏好才会被
  // user_preferences loader 注入生成 prompt。
  const togglePrefConfirm = useCallback(async (pref_id: string, confirmed: boolean) => {
    try {
      await apiPost(`/api/preferences/${pref_id}/${confirmed ? "unconfirm" : "confirm"}`, {});
      await refresh();
    } catch (e: any) {
      toast(`失败: ${e.message}`, "error");
    }
  }, [refresh, toast]);

  const deletePref = useCallback(async (pref_id: string) => {
    if (!window.confirm("删除这条偏好？")) return;
    try {
      await apiDelete(`/api/learning/preferences/${pref_id}`);
      toast("已删除", "success");
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
          <h2 style={{ margin: 0 }}> 写作偏好（从你的编辑中学习）</h2>
          <p style={{ color: "var(--text-tertiary)", fontSize: 12, margin: "4px 0 0" }}>
            每次你保存 ``user_edit`` 版本时，系统会捕获一条 observation。
            积累到阈值时（默认 5 章）批量提取偏好，未来生成会自动注入。
            创作备注（special_req）→ 偏好的起始置信度 0.4；普通编辑 → 0.15。
          </p>
        </div>
        <button className="btn" onClick={refresh} disabled={loading}>刷新</button>
      </header>

      {/* Threshold status */}
      {thresholdStatus && (
        <div className="card" style={{ padding: 12, marginBottom: 16 }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 13 }}>
                <strong>下次批量学习: </strong>
                {thresholdStatus.will_fire_on_next
                  ? <span style={{ color: "var(--success)" }}>下次编辑保存时就会触发 </span>
                  : <span>还差 <strong>{thresholdStatus.remaining}</strong> 章编辑保存</span>
                }
                <span style={{ color: "var(--text-tertiary)", marginLeft: 12 }}>
                  (当前未消费 {thresholdStatus.unconsumed} / 阈值 {thresholdStatus.threshold})
                </span>
              </div>
              {/* Progress bar */}
              <div style={{
                width: "100%", height: 8, background: "var(--bg-surface-2)",
                borderRadius: 4, overflow: "hidden", marginTop: 8,
              }}>
                <div style={{
                  width: `${Math.min(100, (thresholdStatus.unconsumed / thresholdStatus.threshold) * 100)}%`,
                  height: "100%", background: "var(--accent)",
                }} />
              </div>
            </div>
            <button className="btn primary" onClick={fireBatch} style={{ marginLeft: 16 }}>立即触发批量学习</button>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--border)" }}>
            <span style={{ fontSize: 13 }}>批量学习阈值:</span>
            <input
              type="number"
              min={1} max={50}
              value={thresholdEdit}
              onChange={e => setThresholdEdit(parseInt(e.target.value) || 5)}
              style={{
                width: 60, padding: "4px 8px", fontSize: 13,
                border: "1px solid var(--border)", borderRadius: 4,
              }}
            />
            <span style={{ fontSize: 12, color: "var(--text-tertiary)" }}>章</span>
            {thresholdEdit !== thresholdStatus.threshold && (
              <button className="btn-sm primary" onClick={saveThreshold}>保存</button>
            )}
          </div>
        </div>
      )}

      {/* Preferences by type */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12 }}>
        {(Object.keys(PREF_TYPE_LABEL) as PreferenceType[]).map(ptype => (
          <div key={ptype} className="card" style={{ padding: 12 }}>
            <h3 style={{ marginTop: 0, fontSize: 14 }}>
              {PREF_TYPE_LABEL[ptype]} ({grouped[ptype].length})
            </h3>
            {grouped[ptype].length === 0 ? (
              <p style={{ fontSize: 12, color: "var(--text-tertiary)" }}>暂无</p>
            ) : (
              <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {grouped[ptype].map(p => (
                  <li key={p.pref_id} style={{
                    padding: "8px 0", borderBottom: "1px solid var(--border-subtle, var(--border))",
                    display: "flex", justifyContent: "space-between", alignItems: "center",
                  }}>
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ fontSize: 13, fontWeight: 500 }}>{p.description}</div>
                      <div style={{ fontSize: 11, color: "var(--text-tertiary)", display: "flex", alignItems: "center", gap: 6, marginTop: 4 }}>
                        <ConfidenceBar value={p.confidence} />
                        <span>{(p.confidence * 100).toFixed(0)}%</span>
                        <span>· obs {p.observation_count}</span>
                      </div>
                    </div>
                    <span style={{
                      fontSize: 9, padding: "1px 6px", borderRadius: 8, marginLeft: 8, flexShrink: 0,
                      background: (p as any).is_confirmed ? "var(--success-subtle, rgba(45,140,90,0.12))" : "var(--bg-surface-2)",
                      color: (p as any).is_confirmed ? "var(--success, var(--jade))" : "var(--text-tertiary)",
                    }}>
                      {(p as any).is_confirmed ? "已确认·注入中" : "待确认"}
                    </span>
                    <button
                      className="btn-sm"
                      onClick={() => togglePrefConfirm(p.pref_id, !!(p as any).is_confirmed)}
                      style={{ marginLeft: 6, fontSize: 10, padding: "1px 8px" }}
                      title="确认后该偏好才会注入后续章节生成（机制4）"
                    >
                      {(p as any).is_confirmed ? "取消确认" : "确认"}
                    </button>
                    <button
                      className="btn-sm danger"
                      onClick={() => deletePref(p.pref_id)}
                      style={{ marginLeft: 6, fontSize: 10, padding: "1px 6px" }}
                      title="删除该偏好"
                    >
                      ×
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        ))}
      </div>

      {/* Observations log */}
      <details style={{ marginTop: 16 }}>
        <summary style={{ cursor: "pointer", fontSize: 14, fontWeight: 600 }}>
          编辑观察日志（已消费 {consumedCount} / 待消费 {unconsumedCount}）
        </summary>
        <div style={{ marginTop: 8 }}>
          {observations.length === 0 ? (
            <p style={{ color: "var(--text-tertiary)" }}>还没有捕获任何编辑——保存一次 user_edit 版本就会出现。</p>
          ) : (
            <table style={{ width: "100%", fontSize: 12 }}>
              <thead>
                <tr style={{ textAlign: "left", color: "var(--text-tertiary)" }}>
                  <th style={{ padding: "4px 6px" }}>章节</th>
                  <th style={{ padding: "4px 6px" }}>状态</th>
                  <th style={{ padding: "4px 6px" }}>创作备注</th>
                  <th style={{ padding: "4px 6px" }}>diff stats</th>
                  <th style={{ padding: "4px 6px" }}>时间</th>
                </tr>
              </thead>
              <tbody>
                {observations.map(o => {
                  const stats = (o as any).diff_stats || {};
                  return (
                    <tr key={o.obs_id} style={{
                      borderTop: "1px solid var(--border)",
                      opacity: o.consumed ? 0.6 : 1,
                    }}>
                      <td style={{ padding: "6px" }}>ch{o.chapter_num}</td>
                      <td style={{ padding: "6px" }}>
                        {o.consumed ? " 已消费" : "● 待批量"}
                      </td>
                      <td style={{ padding: "6px", maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {o.special_requirement || "（无）"}
                      </td>
                      <td style={{ padding: "6px", fontFamily: "var(--font-mono)" }}>
                        Δ{stats.char_delta || 0} ({(stats.original_chars || 0)}→{stats.edited_chars || 0})
                      </td>
                      <td style={{ padding: "6px" }}>{new Date(o.created_at * 1000).toLocaleString()}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      </details>
    </div>
  );
}
