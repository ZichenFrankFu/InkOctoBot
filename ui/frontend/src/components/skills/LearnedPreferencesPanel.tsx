import React, { useCallback, useEffect, useState } from "react";
import { apiGet, apiPost, apiPut, apiDelete } from "../../api/client";
import { useToast } from "../shared/Toast";

// 自学习用户偏好画像 (spec 4.1)：preference_analyzer 每 N 章批量学习，
// 学到的偏好以"待确认"落库；本面板是确认 gate (用户偏好·机制4) ——
// 只有确认后的偏好才会注入后续章节生成。

interface LearnedPref {
  pref_id: string;
  preference_type: "style" | "content" | "pacing";
  description: string;
  confidence: number;
  observation_count: number;
  is_confirmed: boolean;
  source_chapters: number[];
  source_type: string;
  updated_at?: string;
}

const TYPE_LABELS: Record<string, string> = {
  style: "风格", content: "内容", pacing: "节奏",
};
const SOURCE_LABELS: Record<string, string> = {
  edit_inference: "修改推断", special_requirements: "特殊要求",
};

export default function LearnedPreferencesPanel({ projectId }: { projectId: string }) {
  const { toast } = useToast();
  const [items, setItems] = useState<LearnedPref[]>([]);
  const [pendingCount, setPendingCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [learning, setLearning] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingText, setEditingText] = useState("");
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!projectId) return;
    setLoading(true);
    try {
      const r = await apiGet<{ items: LearnedPref[]; pending_count: number }>(
        `/api/preferences?project_id=${encodeURIComponent(projectId)}`,
      );
      setItems(r.items || []);
      setPendingCount(r.pending_count || 0);
    } catch (e: any) {
      toast(e.message || "加载偏好失败", "error");
    } finally {
      setLoading(false);
    }
  }, [projectId, toast]);

  useEffect(() => { reload(); }, [reload]);

  const runLearning = async () => {
    setLearning(true);
    try {
      const r = await apiPost<any>(
        `/api/preferences/learn?project_id=${encodeURIComponent(projectId)}`, {},
        { timeoutMs: 180000 },
      );
      if (r.skipped === "interval_not_reached") {
        toast(`未到学习间隔（每 ${r.interval} 章一次，已提交 ${r.committed_chapters} 章）`, "info");
      } else if (r.skipped === "no_edit_signal") {
        toast("本批章节没有修改差异或特殊要求，无可学习信号", "info");
      } else {
        toast(`学习完成：新增/合并 ${r.prefs_emitted} 条偏好，等待确认`, "success");
      }
      await reload();
    } catch (e: any) {
      toast(e.message || "学习失败", "error");
    } finally {
      setLearning(false);
    }
  };

  const setConfirmed = async (p: LearnedPref, on: boolean) => {
    try {
      await apiPost(`/api/preferences/${p.pref_id}/${on ? "confirm" : "unconfirm"}`, {});
      await reload();
    } catch (e: any) { toast(e.message || "操作失败", "error"); }
  };

  const saveEdit = async (id: string) => {
    try {
      await apiPut(`/api/preferences/${id}`, { description: editingText });
      setEditingId(null);
      await reload();
    } catch (e: any) { toast(e.message || "保存失败", "error"); }
  };

  const remove = async (id: string) => {
    try {
      await apiDelete(`/api/preferences/${id}`);
      setConfirmDeleteId(null);
      await reload();
    } catch (e: any) { toast(e.message || "删除失败", "error"); }
  };

  const renderItem = (p: LearnedPref) => (
    <div key={p.pref_id} style={{
      padding: "8px 12px", borderBottom: "1px solid var(--border-subtle)",
      borderLeft: `3px solid ${p.is_confirmed ? "var(--jade)" : "var(--gold)"}`,
    }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4, flexWrap: "wrap" }}>
        <span style={{ fontSize: 9, padding: "1px 6px", borderRadius: 8, background: "var(--bg-secondary)", color: "var(--text-secondary)" }}>
          {TYPE_LABELS[p.preference_type] || p.preference_type}
        </span>
        <span style={{
          fontSize: 9, padding: "1px 6px", borderRadius: 8,
          background: p.is_confirmed ? "var(--jade-subtle, rgba(45,140,90,0.12))" : "var(--gold-subtle, rgba(212,168,83,0.12))",
          color: p.is_confirmed ? "var(--jade)" : "var(--gold)",
        }}>
          {p.is_confirmed ? "已确认·注入中" : "待确认"}
        </span>
        <span style={{ fontSize: 9, color: "var(--text-tertiary)" }}>
          来源：{SOURCE_LABELS[p.source_type] || p.source_type}
          {p.source_chapters.length > 0 && ` · 第${p.source_chapters.join("、")}章`}
        </span>
        <span style={{ fontSize: 9, color: "var(--text-disabled)", marginLeft: "auto" }}>
          置信度 {(p.confidence * 100).toFixed(0)}% · 观测 {p.observation_count} 次
        </span>
      </div>
      {editingId === p.pref_id ? (
        <div>
          <textarea className="input" value={editingText}
            onChange={e => setEditingText(e.target.value)}
            rows={2} style={{ fontSize: 11, width: "100%", boxSizing: "border-box", marginBottom: 4 }} />
          <div style={{ display: "flex", gap: 4 }}>
            <button className="btn-primary" style={{ fontSize: 10, padding: "2px 8px" }}
              onClick={() => saveEdit(p.pref_id)}>保存</button>
            <button className="btn" style={{ fontSize: 10, padding: "2px 8px" }}
              onClick={() => setEditingId(null)}>取消</button>
          </div>
        </div>
      ) : (
        <div style={{ fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.5 }}>
          {p.description}
        </div>
      )}
      <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
        <button className="btn" style={{ fontSize: 10, padding: "2px 10px" }}
          onClick={() => setConfirmed(p, !p.is_confirmed)}>
          {p.is_confirmed ? "取消确认" : "确认"}
        </button>
        <button className="btn" style={{ fontSize: 10, padding: "2px 10px" }}
          onClick={() => { setEditingId(p.pref_id); setEditingText(p.description); }}>
          编辑
        </button>
        {confirmDeleteId === p.pref_id ? (
          <>
            <span style={{ fontSize: 10, color: "var(--error)", lineHeight: "20px" }}>确认删除？</span>
            <button className="btn" style={{ fontSize: 10, padding: "2px 8px" }}
              onClick={() => setConfirmDeleteId(null)}>取消</button>
            <button className="btn" style={{ fontSize: 10, padding: "2px 8px", color: "var(--error)", borderColor: "var(--error)" }}
              onClick={() => remove(p.pref_id)}>确认</button>
          </>
        ) : (
          <button className="btn" style={{ fontSize: 10, padding: "2px 10px", color: "var(--error)" }}
            onClick={() => setConfirmDeleteId(p.pref_id)}>删除</button>
        )}
      </div>
    </div>
  );

  const pending = items.filter(i => !i.is_confirmed);
  const confirmed = items.filter(i => i.is_confirmed);

  return (
    <div style={{ padding: "14px 16px", borderTop: "1px solid var(--border-subtle)" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 12 }}>
        <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
          章节修改偏好（自学习）
          {pendingCount > 0 && (
            <span style={{ fontSize: 10, fontWeight: 400, marginLeft: 8, padding: "1px 8px", borderRadius: 10, background: "var(--gold-subtle, rgba(212,168,83,0.12))", color: "var(--gold)" }}>
              {pendingCount} 条待确认
            </span>
          )}
        </div>
        <button className="btn" style={{ fontSize: 11, padding: "3px 10px", marginLeft: "auto" }}
          onClick={runLearning} disabled={learning || !projectId}>
          {learning ? "学习中..." : "立即学习"}
        </button>
      </div>
      <div style={{ padding: "8px 12px", background: "var(--bg-surface)", borderRadius: 6, marginBottom: 12, fontSize: 11, color: "var(--text-secondary)", lineHeight: 1.6 }}>
        系统每提交 5 章自动对比 AI 初稿与你的定稿，归纳风格/内容/节奏偏好。
        学到的偏好需要你确认后才会注入后续章节生成；可编辑、删除或取消确认。
      </div>
      {loading && <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>加载中...</div>}
      {!loading && items.length === 0 && (
        <div style={{ fontSize: 11, color: "var(--text-tertiary)" }}>
          暂无学习到的偏好。提交满 5 章并产生修改后将自动学习，或点击「立即学习」。
        </div>
      )}
      {pending.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 12, fontWeight: 600, color: "var(--gold)", marginBottom: 6 }}>待确认</div>
          {pending.map(renderItem)}
        </div>
      )}
      {confirmed.length > 0 && (
        <div>
          <div style={{ fontSize: 12, fontWeight: 600, color: "var(--jade)", marginBottom: 6 }}>已确认（注入生成）</div>
          {confirmed.map(renderItem)}
        </div>
      )}
    </div>
  );
}
