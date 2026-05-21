import React, { useEffect, useState } from "react";
import { apiGet, apiPut, apiPost } from "../../api/client";
import { useToast } from "../shared/Toast";
import { useDialog } from "../shared/Dialog";

interface PromptInfo {
  key: string;
  description: string;
  vars: string[];
  default: string;
  current: string;
  has_override: boolean;
}

interface PreviewResponse {
  key: string;
  template: string;
  rendered: string;
  vars: Record<string, any>;
}

/**
 * Shared component used wherever a user can preview / edit an LLM prompt
 * before the next call. Three flows:
 *
 *  - **Inspect**: read the registry template (factory + current override).
 *  - **Preview**: render with the actual vars for the upcoming call
 *    (segment-scoped prompts need ref_id + segment_index).
 *  - **Save**: persist the current edit as the new global default
 *    (calls PUT /api/references/prompts/{key}).
 *  - **Per-call override**: caller is given the edited text via
 *    `onApplyOnce` so they can attach it to the next API call without
 *    persisting.
 *
 * Pure UI — no side effects beyond fetching `/preview` and (when the
 * user clicks 「保存为新默认」) calling PUT.
 */
interface Props {
  promptKey: string;
  /** Required for segment-scoped prompts (reference.characters/settings/rhythm). */
  refId?: string;
  segmentIndex?: number;
  /** Initial edit value (overrides the registry default). Optional. */
  initialEdit?: string;
  /** Called when the user clicks 「仅本次使用此 prompt」. */
  onApplyOnce?: (edited: string) => void;
  /** Called after a successful save-as-new-default. */
  onSaved?: () => void;
  onClose?: () => void;
}

export default function PromptPreview({
  promptKey, refId, segmentIndex, initialEdit, onApplyOnce, onSaved, onClose,
}: Props) {
  const { toast } = useToast();
  const { confirm } = useDialog();
  const [info, setInfo] = useState<PromptInfo | null>(null);
  const [preview, setPreview] = useState<PreviewResponse | null>(null);
  const [edit, setEdit] = useState<string>(initialEdit || "");
  const [loading, setLoading] = useState(true);
  const [previewing, setPreviewing] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      try {
        const i = await apiGet<PromptInfo>(`/api/references/prompts/${promptKey}`);
        if (cancelled) return;
        setInfo(i);
        if (!initialEdit) setEdit(i.current);
        // For prompts with vars, also fetch a rendered preview
        if (i.vars.length > 0) {
          await fetchPreview(i.current);
        } else {
          setPreview({ key: i.key, template: i.current, rendered: i.current, vars: {} });
        }
      } catch (e: any) {
        toast(e?.message || "加载 prompt 失败", "error");
      } finally { if (!cancelled) setLoading(false); }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [promptKey, refId, segmentIndex]);

  const fetchPreview = async (currentText: string) => {
    setPreviewing(true);
    try {
      const params = new URLSearchParams();
      if (refId) params.set("ref_id", refId);
      if (segmentIndex !== undefined) params.set("segment_index", String(segmentIndex));
      const r = await apiGet<PreviewResponse>(
        `/api/references/prompts/${promptKey}/preview?${params}`,
      );
      // If the user has edited locally, re-render with the override
      if (currentText && currentText !== r.template) {
        const rendered = await apiPost<PreviewResponse>(
          `/api/references/prompts/${promptKey}/render`,
          { vars: r.vars, override: currentText },
        );
        setPreview({ ...r, template: currentText, rendered: rendered.rendered });
      } else {
        setPreview(r);
      }
    } catch (e: any) {
      // Some prompts (chat_system) don't need ref/segment; ignore gracefully
      console.warn("prompt preview failed:", e?.message);
    } finally { setPreviewing(false); }
  };

  const refreshPreview = () => fetchPreview(edit);

  const saveAsDefault = async () => {
    setSaving(true);
    try {
      const r = await apiPut<PromptInfo>(`/api/references/prompts/${promptKey}`, { template: edit });
      setInfo(r);
      toast("已保存为新默认", "success");
      onSaved?.();
    } catch (e: any) {
      toast(e?.message || "保存失败", "error");
    } finally { setSaving(false); }
  };

  const resetToFactory = async () => {
    if (!(await confirm({ message: "确定重置为出厂默认？当前的覆盖会被删除。", destructive: true }))) return;
    setSaving(true);
    try {
      const r = await apiPut<PromptInfo>(`/api/references/prompts/${promptKey}`, { template: null });
      setInfo(r);
      setEdit(r.current);
      await fetchPreview(r.current);
      toast("已重置为出厂默认", "success");
    } catch (e: any) {
      toast(e?.message || "重置失败", "error");
    } finally { setSaving(false); }
  };

  const isModified = info && edit !== info.current;

  return (
    <div style={{
      border: "1px solid var(--accent)",
      borderRadius: "var(--radius-sm)",
      padding: 12,
      background: "var(--bg-card)",
    }}>
      <div className="flex items-center justify-between" style={{ marginBottom: 8 }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 600, color: "var(--text-primary)" }}>
            Prompt: {promptKey}
            {info?.has_override && (
              <span className="tag" style={{ marginLeft: 8, fontSize: 10, padding: "1px 6px",
                color: "var(--gold)", background: "var(--bg-surface-2)",
                border: "1px solid var(--gold)" }}>已覆盖</span>
            )}
          </div>
          {info?.description && (
            <div className="text-xs text-muted" style={{ marginTop: 2 }}>{info.description}</div>
          )}
        </div>
        {onClose && (
          <button className="btn-icon" onClick={onClose} style={{ fontSize: 16 }}>&times;</button>
        )}
      </div>

      {loading ? (
        <div className="text-xs text-muted" style={{ padding: 8 }}>加载中...</div>
      ) : info && (
        <>
          <div style={{ marginBottom: 8 }}>
            <div className="flex items-center justify-between" style={{ marginBottom: 4 }}>
              <span className="text-xs text-muted">模板（{info.vars.length > 0 ? `变量: ${info.vars.join(", ")}` : "无变量"}）</span>
              <div className="flex gap-6">
                <button className="btn" style={{ fontSize: 11, padding: "2px 8px" }} onClick={resetToFactory} disabled={saving || !info.has_override}>
                  重置为默认
                </button>
              </div>
            </div>
            <textarea
              className="input font-mono"
              value={edit}
              onChange={e => setEdit(e.target.value)}
              rows={12}
              style={{ width: "100%", fontSize: 11, lineHeight: 1.55, boxSizing: "border-box" }}
            />
          </div>

          {info.vars.length > 0 && (
            <div style={{ marginBottom: 8 }}>
              <div className="flex items-center justify-between" style={{ marginBottom: 4 }}>
                <span className="text-xs text-muted">渲染预览（变量已代入）</span>
                <button className="btn" style={{ fontSize: 11, padding: "2px 8px" }} onClick={refreshPreview} disabled={previewing}>
                  {previewing ? "渲染中..." : "刷新预览"}
                </button>
              </div>
              <pre className="font-mono" style={{
                margin: 0, padding: 10, fontSize: 11, lineHeight: 1.55,
                background: "var(--bg-surface)", borderRadius: 4,
                color: "var(--text-secondary)",
                maxHeight: 260, overflow: "auto",
                whiteSpace: "pre-wrap", wordBreak: "break-word",
              }}>{preview?.rendered || ""}</pre>
            </div>
          )}

          <div className="flex gap-8" style={{ justifyContent: "flex-end" }}>
            {onApplyOnce && (
              <button className="btn" onClick={() => onApplyOnce(edit)} disabled={!isModified}>
                仅本次使用此 prompt
              </button>
            )}
            <button className="btn-primary" onClick={saveAsDefault} disabled={!isModified || saving}>
              {saving ? "保存中..." : "保存为新默认"}
            </button>
          </div>
        </>
      )}
    </div>
  );
}
